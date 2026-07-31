import MetaTrader5 as mt5
import pandas as pd
import numpy as np
import json
import os
import time
from datetime import datetime, timezone, timedelta
from typing import Optional, Tuple, Any
from vuka.core.state import s
from vuka.market_structure.ict import calculate_adx_wilder, calculate_atr
from vuka.risk.portfolio import get_spread, get_overlap_multiplier
from vuka.risk.filters import get_current_session, check_premium_discount_zone, check_panic_candle, check_pre_trade_spread
from vuka.utils.unified_logger import get_logger

_logger = get_logger("Ingwe")

def log(msg: str, level: str = "INFO"):
    _logger.log(level=level, message=msg)

def evaluate_ingwe(df, fvgs, sweep, sweep_level, price, atr, lot_size, session,
                    market_phase="UNKNOWN", phase_adj=None):
    """
    Full multi-confluence model.
    v4.3:   Three hard gates added after GBPUSD loss (ADX<20, D1 bias
            conflict, SL min distance). All block entry regardless of score.
    v4.2:   Market orders reinstated -- same confluence logic, no static
            limit levels. Reverted from v4.0 which produced 0% win rate.
    v3.9.4: FIX-1 zone check removed from Paths C/D.
    v3.9.3: True Wilder ADX.
    v3.9.2: Zone context logging.
    v3.9.1: Four paths. 1-bar BOS pivot.
    v3.9:   ATR guard 0.5x. Dynamic BOS lookback. HTF bias gate + +10.
    v5.6:   Market circuit phase awareness — phase-based threshold + bonus.
    """
    if phase_adj is None:
        phase_adj = {"threshold_mod": 0, "score_bonus": 0, "direction_favor": "NONE"}
    adx, plus_di, minus_di = calculate_adx_wilder(df)
    if adx is None:
        log("ADX unavailable.", "WARN")
        return
    log(f"ADX: {adx:.1f}  |  +DI: {plus_di:.1f}  |  -DI: {minus_di:.1f}")

    # ── ADX GATE ──────────────────────────────────────────
    if adx < s.ADX_MIN_THRESHOLD:
        log(f"ADX {adx} below minimum ({s.ADX_MIN_THRESHOLD}) -- Extreme chop. Standing down.", "GUARD")
        return

    # ── MARKET CIRCUIT: PHASE DIRECTION FILTER ──────────────
    _phase_direction = phase_adj["direction_favor"]
    if _phase_direction != "NONE":
        log(f"MARKET CIRCUIT: {market_phase} favors {_phase_direction} setups", "GUARD")
    
    # Pattern veto is handled upstream by TradingGovernor.check_market_phase()
    # and KronosGuardian.validate_signal() via concept_tracker's should_auto_veto().
    # The old hardcoded PATTERN_BLACKLIST was removed in favor of data-driven gates.

    spread      = get_spread()
    spread_pips = spread * 10000 if spread else 0
    spread_ok   = spread is not None and spread < s.MIN_SPREAD_PIPS
    multiplier  = get_overlap_multiplier()
    if multiplier > 1.0:
        lot_size = min(round(lot_size * multiplier, 2), s.HARD_LOT_CAP)
        log(f"London/NY Overlap -> Lot: {lot_size} (1.2x)")

    # v5.0: Get session-aware threshold
    threshold = get_confluence_threshold(adx, session, "BUY" if sweep == "SWEEP_LOW" else "SELL")
    threshold = max(40, min(90, threshold + phase_adj["threshold_mod"]))
    log(f"Price: {price:.5f}  |  ATR: {atr:.5f}  |  "
        f"Lot: {lot_size}  |  Spread: {spread_pips:.1f}p  |  "
        f"Threshold: {threshold}/120  |  Phase: {market_phase}")

    trend = get_h1_trend()
    if not trend:
        log("H1 trend unclear.", "WARN")
        return
    log(f"H1 Trend: {trend}")

    # ── FIX-2: HTF BIAS (v5.2 - Weighted Context) ────────────────
    # Instead of blocking, we flag bias conflicts for Kronos to decide.
    htf_bias = get_htf_bias()
    htf_bias_ok = True
    if not htf_bias:
        log("HTF bias unavailable -- flagged for Kronos review.", "WARN")
        htf_bias_ok = False
    elif htf_bias != trend:
        log(f"HTF bias ({htf_bias}) conflicts with H1 trend ({trend}) -- flagged for Kronos review.", "WARN")
        htf_bias_ok = False
    else:
        log(f"HTF bias confirms H1 trend -- full top-down alignment.  [+10]")


    # ── FIX-3: SL MINIMUM DISTANCE (v4.3) ───────────────
    # v4.3: SL must be at least s.MIN_SL_ATR_MULTIPLIER × ATR.
    # v5.0: Increase for weak sessions (more breathing room)
    sl_multi = s.MIN_SL_ATR_MULTIPLIER * get_session_multiplier(session, "BUY" if sweep == "SWEEP_LOW" else "SELL")
    min_sl = atr * sl_multi

    # ── KEY LEVEL CONTEXT ────────────────────────────────
    pdh, pdl = get_pdh_pdl()
    if pdh and pdl:
        log(f"PDH: {pdh:.5f}  |  PDL: {pdl:.5f}")

    asian_high, asian_low = get_asian_range(df)
    if asian_high and asian_low:
        log(f"Asian Range: {asian_low:.5f}-{asian_high:.5f}")

    # ── LEVEL SWEEP -- 0.5 ATR guard (v3.9) ──────────────
    level_sweep = False
    if pdh and pdl:
        if sweep == "SWEEP_HIGH" and abs(sweep_level - pdh) < atr * 0.5:
            level_sweep = True
            log(f"PDH SWEEP: {sweep_level:.5f} ~ PDH {pdh:.5f}  [+5]")
        elif sweep == "SWEEP_LOW" and abs(sweep_level - pdl) < atr * 0.5:
            level_sweep = True
            log(f"PDL SWEEP: {sweep_level:.5f} ~ PDL {pdl:.5f}  [+5]")
    if not level_sweep and asian_high and asian_low:
        if sweep == "SWEEP_HIGH" and abs(sweep_level - asian_high) < atr * 0.5:
            level_sweep = True
            log(f"ASIAN HIGH SWEEP: {sweep_level:.5f} ~ AR {asian_high:.5f}  [+5]")
        elif sweep == "SWEEP_LOW" and abs(sweep_level - asian_low) < atr * 0.5:
            level_sweep = True
            log(f"ASIAN LOW SWEEP: {sweep_level:.5f} ~ AR {asian_low:.5f}  [+5]")

    # ── M15 BOS -- dynamic lookback (v3.9) ────────────────
    bos_lookback = 12 if session == "London Open" else 20
    m15_bos      = detect_m15_bos(df, lookback=bos_lookback)
    if m15_bos:
        log(f"M15 BOS: {m15_bos}  (lookback={bos_lookback})")
        try:
            from skills.market_circuit import get_circuit
            get_circuit()._bos = m15_bos
        except Exception:
            pass
    else:
        log(f"M15 BOS: none confirmed  (lookback={bos_lookback})")

    # ── ZONE CONTEXT HELPER (v3.9.2) ─────────────────────
    def _zone_context(df: pd.DataFrame, price: float) -> str:
        recent = df.iloc[-20:]
        hi  = recent["high"].max()
        lo  = recent["low"].min()
        mid = lo + (hi - lo) * 0.5
        zone = "PREMIUM" if price >= mid else "DISCOUNT"
        return f"{zone} (price={price:.5f}, mid={mid:.5f})"

    # ── FVG LOOP ─────────────────────────────────────────
    for fvg_type, fvg_low, fvg_high, fvg_idx, ob, fvg_50 in fvgs:
        if check_panic_candle(df, atr):
            continue

        # ── PATH A: BUY REVERSAL ─────────────────────────
        if sweep == "SWEEP_LOW" and fvg_type == "BULLISH_FVG" and trend == "BULLISH":
            # P0-B: Direction filter gate (data-driven)
            dir_allowed, dir_reason = s.TRADING_GOVERNOR.check_direction(
                "BUY", htf_bias, s._instance_tag,
                symbol=s.SYMBOL, session=session, setup_type="REVERSAL"
            )
            if not dir_allowed:
                log(f"Direction blocked: BUY ({dir_reason}).", "GUARD")
                continue
            if plus_di is None or minus_di is None or plus_di <= minus_di:
                log(f"DI filter: +DI({plus_di}) <= -DI({minus_di}). Skip.", "GUARD")
                continue
            if not check_premium_discount_zone(df, price, "BUY"):
                log("Not in discount zone. Skip.", "GUARD")
                continue
            # v5.0 FIX: Require strong HTF bias for BUY signals
            if not htf_bias_ok:
                log("HTF bias required for BUY. No D1/H4 confirmation. Skip.", "GUARD")
                continue
            if htf_bias != "BULLISH":
                log(f"HTF bias ({htf_bias}) not bullish. Skip BUY.", "GUARD")
                continue
            bos_aligned = (m15_bos == "BULLISH_BOS")
            score = calculate_confluence_score(
                trend, True, True, spread_ok, True,
                level_sweep, bos_aligned, htf_bias_ok,
                session, "BUY"
            )
            score += phase_adj["score_bonus"]
            bonus_label = (
                (" [+PDH/PDL/AR]" if level_sweep  else "") +
                (" [+BOS]"        if bos_aligned  else "") +
                (" [+HTF]"        if htf_bias_ok  else "") +
                (f" [+PHASE:{market_phase}]" if phase_adj["score_bonus"] else "")
            )
            log(f"Confluence [BUY REVERSAL]: {score}/120{bonus_label}")
            if score < threshold:
                log(f"Score {score} < {threshold}. Flagging for Kronos review.", "WARN")
                score_ok = False
            else:
                score_ok = True
            if not check_pre_trade_spread(atr):
                continue
            
            def _build_context(dir, setup_type, fvg_t, fvg_50_val):
                return {
                    "direction": dir,
                    "setup_type": setup_type,
                    "sweep": sweep,
                    "fvg_type": fvg_t,
                    "fvg_position": "below_50" if price <= fvg_50_val else ("50%" if abs(price - fvg_50_val) < atr * 0.1 else "above_50"),
                    "bos_aligned": bos_aligned,
                    "htf_bias_ok": htf_bias_ok,
                    "adx_ok": True,
                    "score_ok": score_ok,
                    "confluence_score": score,
                    "session": session,
                    "atr": atr,
                    "spread_ok": spread_ok,
                    "trend": trend,
                    "level_sweep": level_sweep,
                    "ob_present": ob is not None,
                    "fvg_low": fvg_low,
                    "fvg_high": fvg_high,
                    "fvg_50": fvg_50_val,
                    "market_phase": market_phase,
                }
            
            ctx = _build_context("BUY", "REVERSAL", fvg_type, fvg_50)
            if s.KRONOS_VETO_GATE is not None:
                allowed, reason = KRONOS_VETO_GATE.validate(ctx, df, s.SYMBOL)
                log(f"[KRONOS] BUY signal: {reason}", "GUARD")
                if not allowed:
                    log(f"Kronos vetoed BUY. Skipping trade.", "GUARD")
                    return
            
            stop  = max(atr * s.ATR_MULTIPLIER, min_sl)
            entry = round_to_tick(price, s.SYMBOL)
            sl    = round_to_tick(entry - stop, s.SYMBOL)
            tp    = round_to_tick(entry + stop * s.RISK_REWARD_RATIO, s.SYMBOL)
            res   = place_trade("BUY", entry, sl, tp, lot_size, session=session)
            if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                log(f"BUY MARKET  Entry={entry}  SL={sl}  TP={tp}  Lot={lot_size}", "TRADE")
                log_trade("BUY", entry, sl, tp, res, lot_size, session, context=ctx, kronos_gate=s.KRONOS_VETO_GATE)
                sessions_traded_today.add(session)
                save_sessions(s.sessions_traded_today)
            else:
                log(f"BUY MARKET FAILED. Code={res.retcode if res else 'N/A'}.", "ERROR")
            return

        # ── PATH B: SELL REVERSAL ────────────────────────
        if sweep == "SWEEP_HIGH" and fvg_type == "BEARISH_FVG" and trend == "BEARISH":
            if plus_di is None or minus_di is None or minus_di <= plus_di:
                log(f"DI filter: -DI({minus_di}) <= +DI({plus_di}). Skip.", "GUARD")
                continue
            if not check_premium_discount_zone(df, price, "SELL"):
                log("Not in premium zone. Skip.", "GUARD")
                continue
            bos_aligned = (m15_bos == "BEARISH_BOS")
            score = calculate_confluence_score(
                trend, True, True, spread_ok, True,
                level_sweep, bos_aligned, htf_bias_ok,
                session, "SELL"
            )
            score += phase_adj["score_bonus"]
            bonus_label = (
                (" [+PDH/PDL/AR]" if level_sweep  else "") +
                (" [+BOS]"        if bos_aligned  else "") +
                (" [+HTF]"        if htf_bias_ok  else "") +
                (f" [+PHASE:{market_phase}]" if phase_adj["score_bonus"] else "")
            )
            log(f"Confluence [SELL REVERSAL]: {score}/120{bonus_label}")
            if score < threshold:
                log(f"Score {score} < {threshold}. Waiting.", "GUARD")
                continue
            if not check_pre_trade_spread(atr):
                continue
            ctx = {
                "direction": "SELL",
                "setup_type": "REVERSAL",
                "sweep": sweep,
                "fvg_type": fvg_type,
                "fvg_position": "above_50" if price >= fvg_50 else ("50%" if abs(price - fvg_50) < atr * 0.1 else "below_50"),
                "bos_aligned": bos_aligned,
                "htf_bias_ok": htf_bias_ok,
                "adx_ok": True,
                "score_ok": True,
                "confluence_score": score,
                "session": session,
                "atr": atr,
                "spread_ok": spread_ok,
                "trend": trend,
                "level_sweep": level_sweep,
                "ob_present": ob is not None,
                "fvg_low": fvg_low,
                "fvg_high": fvg_high,
                "fvg_50": fvg_50
            }
            if s.KRONOS_VETO_GATE is not None:
                allowed, reason = KRONOS_VETO_GATE.validate(ctx, df, s.SYMBOL)
                log(f"[KRONOS] SELL signal: {reason}", "GUARD")
                if not allowed:
                    log(f"Kronos vetoed SELL. Skipping trade.", "GUARD")
                    return
            stop  = max(atr * s.ATR_MULTIPLIER, min_sl)
            entry = round_to_tick(price, s.SYMBOL)
            sl    = round_to_tick(entry + stop, s.SYMBOL)
            tp    = round_to_tick(entry - stop * s.RISK_REWARD_RATIO, s.SYMBOL)
            res   = place_trade("SELL", entry, sl, tp, lot_size, session=session)
            if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                log(f"SELL MARKET  Entry={entry}  SL={sl}  TP={tp}  Lot={lot_size}", "TRADE")
                log_trade("SELL", entry, sl, tp, res, lot_size, session, context=ctx, kronos_gate=s.KRONOS_VETO_GATE)
                sessions_traded_today.add(session)
                save_sessions(s.sessions_traded_today)
            else:
                log(f"SELL MARKET FAILED. Code={res.retcode if res else 'N/A'}.", "ERROR")
            return

        # ── PATH C: SELL CONTINUATION (v3.9.1) ──────────
        if sweep == "SWEEP_LOW" and fvg_type == "BEARISH_FVG" and trend == "BEARISH":
            if plus_di is None or minus_di is None or minus_di <= plus_di:
                log(f"DI filter: -DI({minus_di}) <= +DI({plus_di}). Skip.", "GUARD")
                continue
            log(f"Zone context: {_zone_context(df, price)}")
            bos_aligned = (m15_bos == "BEARISH_BOS")
            score = calculate_confluence_score(
                trend, True, True, spread_ok, True,
                level_sweep, bos_aligned, htf_bias_ok,
                session, "SELL"
            )
            score += phase_adj["score_bonus"]
            bonus_label = (
                (" [+PDH/PDL/AR]" if level_sweep  else "") +
                (" [+BOS]"        if bos_aligned  else "") +
                (" [+HTF]"        if htf_bias_ok  else "") +
                (f" [+PHASE:{market_phase}]" if phase_adj["score_bonus"] else "")
            )
            log(f"Confluence [SELL CONTINUATION]: {score}/120{bonus_label}")
            if score < threshold:
                log(f"Score {score} < {threshold}. Waiting.", "GUARD")
                continue
            if not check_pre_trade_spread(atr):
                continue
            
            ctx = {
                "direction": "SELL",
                "setup_type": "CONTINUATION",
                "sweep": sweep,
                "fvg_type": fvg_type,
                "fvg_position": "above_50" if price >= fvg_50 else ("50%" if abs(price - fvg_50) < atr * 0.1 else "below_50"),
                "bos_aligned": bos_aligned,
                "htf_bias_ok": htf_bias_ok,
                "adx_ok": True,
                "score_ok": True,
                "confluence_score": score,
                "session": session,
                "atr": atr,
                "spread_ok": spread_ok,
                "trend": trend,
                "level_sweep": level_sweep,
                "ob_present": ob is not None,
                "fvg_low": fvg_low,
                "fvg_high": fvg_high,
                "fvg_50": fvg_50,
                "market_phase": market_phase,
            }
            if s.KRONOS_VETO_GATE is not None:
                allowed, reason = KRONOS_VETO_GATE.validate(ctx, df, s.SYMBOL)
                log(f"[KRONOS] SELL signal: {reason}", "GUARD")
                if not allowed:
                    log(f"Kronos vetoed SELL. Skipping trade.", "GUARD")
                    return
            
            stop  = max(atr * s.ATR_MULTIPLIER, min_sl)
            entry = round_to_tick(price, s.SYMBOL)
            sl    = round_to_tick(entry + stop, s.SYMBOL)
            tp    = round_to_tick(entry - stop * s.RISK_REWARD_RATIO, s.SYMBOL)
            res   = place_trade("SELL", entry, sl, tp, lot_size, session=session)
            if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                log(f"SELL MARKET  Entry={entry}  SL={sl}  TP={tp}  Lot={lot_size}", "TRADE")
                log_trade("SELL", entry, sl, tp, res, lot_size, session, context=ctx, kronos_gate=s.KRONOS_VETO_GATE)
                sessions_traded_today.add(session)
                save_sessions(s.sessions_traded_today)
            else:
                log(f"SELL MARKET FAILED. Code={res.retcode if res else 'N/A'}.", "ERROR")
            return

        # ── PATH D: BUY CONTINUATION (v3.9.1) ───────────
        if sweep == "SWEEP_HIGH" and fvg_type == "BULLISH_FVG" and trend == "BULLISH":
            # P0-B: Direction filter gate (data-driven)
            dir_allowed, dir_reason = s.TRADING_GOVERNOR.check_direction(
                "BUY", htf_bias, s._instance_tag,
                symbol=s.SYMBOL, session=session, setup_type="CONTINUATION"
            )
            if not dir_allowed:
                log(f"Direction blocked: BUY ({dir_reason}).", "GUARD")
                continue
            if plus_di is None or minus_di is None or plus_di <= minus_di:
                log(f"DI filter: +DI({plus_di}) <= -DI({minus_di}). Skip.", "GUARD")
                continue
            # v6.0: HTF bias is a soft warning, not a hard block.
            # Kronos will receive the flag and decide based on buy_threshold.
            effective_buy_threshold = s.BUY_THRESHOLD
            if not htf_bias_ok:
                if session and "asian" in session.lower():
                    effective_buy_threshold = 0.60
                    log(f"Asian split HTF -- raising Kronos threshold to {effective_buy_threshold}.", "GUARD")
                log(f"HTF bias ({htf_bias}) not confirmed. Allowing Kronos to decide with BUY threshold {effective_buy_threshold}.", "WARN")
            if htf_bias != "BULLISH":
                log(f"HTF bias ({htf_bias}) not bullish. Flagging for Kronos review.", "WARN")
            log(f"Zone context: {_zone_context(df, price)}")
            bos_aligned = (m15_bos == "BULLISH_BOS")
            score = calculate_confluence_score(
                trend, True, True, spread_ok, True,
                level_sweep, bos_aligned, htf_bias_ok,
                session, "BUY"
            )
            score += phase_adj["score_bonus"]
            bonus_label = (
                (" [+PDH/PDL/AR]" if level_sweep  else "") +
                (" [+BOS]"        if bos_aligned  else "") +
                (" [+HTF]"        if htf_bias_ok  else "") +
                (f" [+PHASE:{market_phase}]" if phase_adj["score_bonus"] else "")
            )
            log(f"Confluence [BUY CONTINUATION]: {score}/120{bonus_label}")
            if score < threshold:
                log(f"Score {score} < {threshold}. Waiting.", "GUARD")
                continue
            if not check_pre_trade_spread(atr):
                continue
            
            ctx = {
                "direction": "BUY",
                "setup_type": "CONTINUATION",
                "sweep": sweep,
                "fvg_type": fvg_type,
                "fvg_position": "below_50" if price <= fvg_50 else ("50%" if abs(price - fvg_50) < atr * 0.1 else "above_50"),
                "bos_aligned": bos_aligned,
                "htf_bias_ok": htf_bias_ok,
                "adx_ok": True,
                "score_ok": True,
                "confluence_score": score,
                "session": session,
                "atr": atr,
                "spread_ok": spread_ok,
                "trend": trend,
                "level_sweep": level_sweep,
                "ob_present": ob is not None,
                "fvg_low": fvg_low,
                "fvg_high": fvg_high,
                "fvg_50": fvg_50,
                "buy_threshold": effective_buy_threshold,
                "market_phase": market_phase,
            }
            if s.KRONOS_VETO_GATE is not None:
                allowed, reason = KRONOS_VETO_GATE.validate(ctx, df, s.SYMBOL)
                log(f"[KRONOS] BUY signal: {reason}", "GUARD")
                if not allowed:
                    log(f"Kronos vetoed BUY. Skipping trade.", "GUARD")
                    return
            
            stop  = max(atr * s.ATR_MULTIPLIER, min_sl)
            entry = round_to_tick(price, s.SYMBOL)
            sl    = round_to_tick(entry - stop, s.SYMBOL)
            tp    = round_to_tick(entry + stop * s.RISK_REWARD_RATIO, s.SYMBOL)
            res   = place_trade("BUY", entry, sl, tp, lot_size, session=session)
            if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                log(f"BUY MARKET  Entry={entry}  SL={sl}  TP={tp}  Lot={lot_size}", "TRADE")
                log_trade("BUY", entry, sl, tp, res, lot_size, session, context=ctx, kronos_gate=s.KRONOS_VETO_GATE)
                sessions_traded_today.add(session)
                save_sessions(s.sessions_traded_today)
            else:
                log(f"BUY MARKET FAILED. Code={res.retcode if res else 'N/A'}.", "ERROR")
            return
