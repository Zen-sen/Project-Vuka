import MetaTrader5 as mt5
import pandas as pd
import numpy as np
import json
import os
import time
from datetime import datetime, timezone, timedelta
from typing import Optional, Tuple, Any
from vuka.core.state import s

def evaluate_silver_bullet(df, fvgs, sweep, sweep_level, price, atr,
                           lot_size, window, unicorn_zones=None):
    """
    ICT Silver Bullet -- time-precision model.
    No trend filter. No ADX. No zone filter.
    The 1-hour window is the primary confluence filter.
    Market orders retained in v4.0 -- limit order conversion is INGWE only.
    """
    if unicorn_zones is None:
        unicorn_zones = []

    spread      = get_spread()
    spread_pips = spread * 10000 if spread else 0
    log(f"Price: {price:.5f}  |  ATR: {atr:.5f}  |  "
        f"Lot: {lot_size}  |  Spread: {spread_pips:.1f}p")

    # ── UNICORN PATH ─────────────────────────────────────
    if unicorn_zones:
        unicorn_zones_sorted = sorted(
            unicorn_zones,
            key=lambda u: abs(price - (u[1] + u[2]) / 2)
        )

        for u_type, u_low, u_high, u_mid, bb_low, bb_high in unicorn_zones_sorted:

            if u_type == "BULLISH_UNICORN" and sweep == "SWEEP_LOW":
                log(f"UNICORN BULLISH zone: {u_low:.5f}-{u_high:.5f}  |  "
                    f"Mid: {u_mid:.5f}  |  BB: {bb_low:.5f}-{bb_high:.5f}")
                if price > u_high:
                    log("Price above Unicorn zone -- waiting for retracement.", "GUARD")
                    continue
                if price < u_low:
                    log("Price below Unicorn zone -- not yet in range.", "GUARD")
                    continue
                if check_panic_candle(df, atr):
                    continue
                if not check_pre_trade_spread(atr):
                    continue
                
                ctx = {
                    "direction": "BUY",
                    "setup_type": "UNICORN",
                    "sweep": sweep,
                    "fvg_type": u_type,
                    "fvg_position": "in_zone",
                    "bos_aligned": False,
                    "htf_bias_ok": False,
                    "confluence_score": 80,
                    "session": window,
                    "atr": atr,
                    "spread_ok": check_pre_trade_spread(atr),
                    "trend": "BULLISH",
                    "level_sweep": True,
                    "ob_present": False,
                    "fvg_low": 0,
                    "fvg_high": 0,
                    "fvg_50": 0
                }
                if s.KRONOS_VETO_GATE is not None:
                    allowed, reason = KRONOS_VETO_GATE.validate(ctx, df, s.SYMBOL)
                    log(f"[KRONOS] BUY signal: {reason}", "GUARD")
                    if not allowed:
                        log(f"Kronos vetoed BUY. Skipping trade.", "GUARD")
                        return
                
                entry   = round_to_tick(price, s.SYMBOL)
                sl      = round_to_tick(bb_low - atr * s.ATR_MULTIPLIER, s.SYMBOL)
                sl_dist = abs(entry - sl)
                tp      = round_to_tick(entry + sl_dist * s.RISK_REWARD_RATIO, s.SYMBOL)
                res     = place_trade("BUY", entry, sl, tp, lot_size, session=window)
                if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                    log(f"[UNICORN] UNICORN BUY  Entry={entry}  SL={sl}  TP={tp}  "
                        f"Lot={lot_size}", "TRADE")
                    log_trade("BUY", entry, sl, tp, res, lot_size, window, context=ctx, kronos_gate=s.KRONOS_VETO_GATE)
                    sessions_traded_today.add(window)
                    save_sessions(s.sessions_traded_today)
                else:
                    log(f"UNICORN BUY FAILED. "
                        f"Code={res.retcode if res else 'N/A'}.", "ERROR")
                return

            if u_type == "BEARISH_UNICORN" and sweep == "SWEEP_HIGH":
                log(f"UNICORN BEARISH zone: {u_low:.5f}-{u_high:.5f}  |  "
                    f"Mid: {u_mid:.5f}  |  BB: {bb_low:.5f}-{bb_high:.5f}")
                if price < u_low:
                    log("Price below Unicorn zone -- waiting for retracement.", "GUARD")
                    continue
                if price > u_high:
                    log("Price above Unicorn zone -- not yet in range.", "GUARD")
                    continue
                if check_panic_candle(df, atr):
                    continue
                if not check_pre_trade_spread(atr):
                    continue
                
                ctx = {
                    "direction": "SELL",
                    "setup_type": "UNICORN",
                    "sweep": sweep,
                    "fvg_type": u_type,
                    "fvg_position": "in_zone",
                    "bos_aligned": False,
                    "htf_bias_ok": False,
                    "confluence_score": 80,
                    "session": window,
                    "atr": atr,
                    "spread_ok": check_pre_trade_spread(atr),
                    "trend": "BEARISH",
                    "level_sweep": True,
                    "ob_present": False,
                    "fvg_low": 0,
                    "fvg_high": 0,
                    "fvg_50": 0
                }
                if s.KRONOS_VETO_GATE is not None:
                    allowed, reason = KRONOS_VETO_GATE.validate(ctx, df, s.SYMBOL)
                    log(f"[KRONOS] SELL signal: {reason}", "GUARD")
                    if not allowed:
                        log(f"Kronos vetoed SELL. Skipping trade.", "GUARD")
                        return
                
                entry   = round_to_tick(price, s.SYMBOL)
                sl      = round_to_tick(bb_high + atr * s.ATR_MULTIPLIER, s.SYMBOL)
                sl_dist = abs(sl - entry)
                tp      = round_to_tick(entry - sl_dist * s.RISK_REWARD_RATIO, s.SYMBOL)
                res     = place_trade("SELL", entry, sl, tp, lot_size, session=window)
                if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                    log(f"[UNICORN] UNICORN SELL  Entry={entry}  SL={sl}  TP={tp}  "
                        f"Lot={lot_size}", "TRADE")
                    log_trade("SELL", entry, sl, tp, res, lot_size, window, context=ctx, kronos_gate=s.KRONOS_VETO_GATE)
                    sessions_traded_today.add(window)
                    save_sessions(s.sessions_traded_today)
                else:
                    log(f"UNICORN SELL FAILED. "
                        f"Code={res.retcode if res else 'N/A'}.", "ERROR")
                return

        log("Unicorn zones present but not aligned. Falling back to FVG path.")

    # ── STANDARD SILVER BULLET PATH ──────────────────────
    for fvg_type, fvg_low, fvg_high, fvg_idx, ob, fvg_50 in fvgs:
        if check_panic_candle(df, atr):
            continue

        if sweep == "SWEEP_LOW" and fvg_type == "BULLISH_FVG":
            log(f"SB Bullish FVG: {fvg_low:.5f}-{fvg_high:.5f}  |  50%: {fvg_50:.5f}")
            if price > fvg_high:
                log("Price above FVG -- waiting for retracement into gap.", "GUARD")
                continue
            if price > fvg_50:
                log(f"Price in FVG but above 50% ({fvg_50:.5f}) -- waiting deeper.",
                    "GUARD")
                continue
            if not check_pre_trade_spread(atr):
                continue

            dol_name, dol_price = get_draw_on_liquidity("BUY")
            ctx = {
                "direction": "BUY",
                "setup_type": "SILVER_BULLET",
                "sweep": sweep,
                "fvg_type": fvg_type,
                "fvg_position": "below_50" if price <= fvg_50 else ("50%" if abs(price - fvg_50) < atr * 0.1 else "above_50"),
                "bos_aligned": False,
                "htf_bias_ok": False,
                "confluence_score": 70,
                "session": window,
                "atr": atr,
                "spread_ok": check_pre_trade_spread(atr),
                "trend": "BULLISH",
                "level_sweep": True,
                "draw_on_liquidity": dol_name,
                "dol_price": dol_price,
                "distance_to_dol": abs(dol_price - price) if dol_price else None,
                "sb_window": window,
                "ob_present": ob is not None,
                "fvg_low": fvg_low,
                "fvg_high": fvg_high,
                "fvg_50": fvg_50
            }
            if s.KRONOS_VETO_GATE is not None:
                allowed, reason = KRONOS_VETO_GATE.validate(ctx, df, s.SYMBOL)
                log(f"[KRONOS] BUY signal: {reason}", "GUARD")
                if not allowed:
                    log(f"Kronos vetoed BUY. Skipping trade.", "GUARD")
                    return

            entry   = round_to_tick(price, s.SYMBOL)
            calculated_sl = sweep_level - atr * s.ATR_MULTIPLIER
            min_sl_distance = atr * s.MIN_SL_ATR_MULTIPLIER
            if (entry - calculated_sl) < min_sl_distance:
                sl = round_to_tick(entry - min_sl_distance, s.SYMBOL)
            else:
                sl = round_to_tick(calculated_sl, s.SYMBOL)
            sl_dist = abs(entry - sl)
            tp      = round_to_tick(entry + sl_dist * s.RISK_REWARD_RATIO, s.SYMBOL)
            res     = place_trade("BUY", entry, sl, tp, lot_size, session=window)
            if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                log(f"SB BUY  Entry={entry}  SL={sl}  TP={tp}  Lot={lot_size}", "TRADE")
                log_trade("BUY", entry, sl, tp, res, lot_size, window, context=ctx, kronos_gate=s.KRONOS_VETO_GATE)
                sessions_traded_today.add(window)
                save_sessions(s.sessions_traded_today)
            else:
                log(f"SB BUY FAILED. Code={res.retcode if res else 'N/A'}.", "ERROR")
            return

        if sweep == "SWEEP_HIGH" and fvg_type == "BEARISH_FVG":
            log(f"SB Bearish FVG: {fvg_low:.5f}-{fvg_high:.5f}  |  50%: {fvg_50:.5f}")
            if price < fvg_low:
                log("Price below FVG -- waiting for retracement into gap.", "GUARD")
                continue
            if price < fvg_50:
                log(f"Price in FVG but below 50% ({fvg_50:.5f}) -- waiting deeper.",
                    "GUARD")
                continue
            if not check_pre_trade_spread(atr):
                continue

            dol_name, dol_price = get_draw_on_liquidity("SELL")
            ctx = {
                "direction": "SELL",
                "setup_type": "SILVER_BULLET",
                "sweep": sweep,
                "fvg_type": fvg_type,
                "fvg_position": "above_50" if price >= fvg_50 else ("50%" if abs(price - fvg_50) < atr * 0.1 else "below_50"),
                "bos_aligned": False,
                "htf_bias_ok": False,
                "confluence_score": 70,
                "session": window,
                "atr": atr,
                "spread_ok": check_pre_trade_spread(atr),
                "trend": "BEARISH",
                "level_sweep": True,
                "draw_on_liquidity": dol_name,
                "dol_price": dol_price,
                "distance_to_dol": abs(dol_price - price) if dol_price else None,
                "sb_window": window,
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

            entry   = round_to_tick(price, s.SYMBOL)
            calculated_sl = sweep_level + atr * s.ATR_MULTIPLIER
            min_sl_distance = atr * s.MIN_SL_ATR_MULTIPLIER
            if (calculated_sl - entry) < min_sl_distance:
                sl = round_to_tick(entry + min_sl_distance, s.SYMBOL)
            else:
                sl = round_to_tick(calculated_sl, s.SYMBOL)
            sl_dist = abs(sl - entry)
            tp      = round_to_tick(entry - sl_dist * s.RISK_REWARD_RATIO, s.SYMBOL)
            res     = place_trade("SELL", entry, sl, tp, lot_size, session=window)
            if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                log(f"SB SELL  Entry={entry}  SL={sl}  TP={tp}  Lot={lot_size}", "TRADE")
                log_trade("SELL", entry, sl, tp, res, lot_size, window, context=ctx, kronos_gate=s.KRONOS_VETO_GATE)
                sessions_traded_today.add(window)
                save_sessions(s.sessions_traded_today)
            else:
                log(f"SB SELL FAILED. Code={res.retcode if res else 'N/A'}.", "ERROR")
            return

    log("SB: No valid FVG retracement. Ingwe waits...")
