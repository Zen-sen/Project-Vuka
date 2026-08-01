
import MetaTrader5 as mt5

from vuka.core.state import s
from vuka.execution.orders import log_trade, place_trade, round_to_tick
from vuka.risk.filters import (
    check_panic_candle,
    check_pre_trade_spread,
    check_premium_discount_zone,
)
from vuka.risk.portfolio import get_spread
from vuka.utils.unified_logger import get_logger

_logger = get_logger("LondonOpen")

def _log(msg: str, level: str = "INFO"):
    _logger.log(level=level, message=msg)


def _mark_session_traded(session: str):
    """Record a traded session against the shared singleton, then persist it.
    bot.py is imported lazily to avoid a circular import at module load."""
    from vuka.core.bot import save_sessions
    s.sessions_traded_today.add(session)
    save_sessions(s.sessions_traded_today)


def evaluate_london_breakout(df, fvgs, sweep, sweep_level, price, atr,
                             lot_size, session, market_phase="UNKNOWN", phase_adj=None):
    """
    ICT London Breakout pattern.
    1. Asian range established (00:00-04:00 UTC)
    2. Breakout of Asian range during London Open killzone
    3. Retest of the break level (Asian range high/low)
    4. Entry on retest confirmation near the break level

    Key levels: Asian Range High/Low.
    No HTF bias or ADX -- breakout direction is the trend.
    """
    # bot.py helpers are imported lazily -- this function runs only after bot.py
    # has finished loading, so no circular-import risk.
    from vuka.core.bot import get_asian_range, get_pdh_pdl

    pdh, pdl = get_pdh_pdl()
    asian_high, asian_low = get_asian_range(df)

    if not asian_high or not asian_low:
        _log("London Breakout: No Asian range established. Waiting...")
        return

    if pdh and pdl:
        _log(f"PDH: {pdh:.5f}  |  PDL: {pdl:.5f}")
    _log(f"Asian Range: {asian_low:.5f} - {asian_high:.5f}")

    spread = get_spread()
    spread_pips = spread * 10000 if spread else 0
    spread_ok = spread is not None and spread < s.MIN_SPREAD_PIPS
    _log(f"Price: {price:.5f}  |  ATR: {atr:.5f}  |  "
        f"Lot: {lot_size}  |  Spread: {spread_pips:.1f}p  |  Phase: {market_phase}")

    for fvg_type, fvg_low, fvg_high, fvg_idx, ob, fvg_50 in fvgs:
        if check_panic_candle(df, atr):
            continue

        # ── LONDON BREAKOUT: BUY ─────────────────────────
        # Sweep below Asian low / PDL, then bullish FVG above Asian high = breakout
        if sweep == "SWEEP_LOW" and fvg_type == "BULLISH_FVG":
            if fvg_low < asian_high:
                _log("London Breakout: FVG not above Asian high -- not a confirmed breakout. Waiting.", "GUARD")
                continue

            level_sweep = False
            if pdl and abs(sweep_level - pdl) < atr * 0.5:
                level_sweep = True
                _log(f"PDL SWEEP: {sweep_level:.5f} ~ PDL {pdl:.5f}  [+5]")
            if asian_low and abs(sweep_level - asian_low) < atr * 0.5:
                level_sweep = True
                _log(f"ASIAN LOW SWEEP: {sweep_level:.5f} ~ AR Low {asian_low:.5f}  [+5]")

            retest_zone_low = asian_high - atr * 0.3
            retest_zone_high = asian_high + atr * 0.3
            in_retest = retest_zone_low <= price <= retest_zone_high

            if not in_retest:
                _log(f"London Breakout: Price {price:.5f} outside retest zone "
                    f"({retest_zone_low:.5f}-{retest_zone_high:.5f}). Waiting for retest.", "GUARD")
                continue

            if not check_premium_discount_zone(df, price, "BUY"):
                _log("Not in discount zone. Skip.", "GUARD")
                continue

            score = 70
            if level_sweep:
                score += 10
            if spread_ok:
                score += 10
            if fvg_low > asian_high + atr * 0.5:
                score += 10

            _log(f"Confluence [LONDON BREAKOUT BUY]: {score}/100")

            if not check_pre_trade_spread(atr):
                continue

            ctx = {
                "direction": "BUY",
                "setup_type": "LONDON_BREAKOUT",
                "sweep": sweep,
                "fvg_type": fvg_type,
                "fvg_position": "below_50" if price <= fvg_50 else ("50%" if abs(price - fvg_50) < atr * 0.1 else "above_50"),
                "bos_aligned": False,
                "htf_bias_ok": False,
                "confluence_score": score,
                "session": session,
                "atr": atr,
                "spread_ok": spread_ok,
                "trend": "BULLISH",
                "level_sweep": level_sweep,
                "asian_high": asian_high,
                "asian_low": asian_low,
                "ob_present": ob is not None,
                "market_phase": market_phase,
                "fvg_low": fvg_low,
                "fvg_high": fvg_high,
                "fvg_50": fvg_50
            }
            if s.KRONOS_VETO_GATE is not None:
                allowed, reason = s.KRONOS_VETO_GATE.validate(ctx, df, s.SYMBOL)
                _log(f"[KRONOS] BUY signal: {reason}", "GUARD")
                if not allowed:
                    _log("Kronos vetoed BUY. Skipping trade.", "GUARD")
                    return

            stop = max(atr * s.ATR_MULTIPLIER, atr * s.MIN_SL_ATR_MULTIPLIER)
            entry = round_to_tick(price, s.SYMBOL)
            if asian_low:
                sl = round_to_tick(max(entry - stop, asian_low - atr * 0.3), s.SYMBOL)
            else:
                sl = round_to_tick(entry - stop, s.SYMBOL)
            if score >= 90:
                dynamic_rr = s.RISK_REWARD_RATIO
            elif score >= 80:
                dynamic_rr = s.RISK_REWARD_RATIO - 0.5
            else:
                dynamic_rr = s.RISK_REWARD_RATIO - 1.0
            tp = round_to_tick(entry + stop * dynamic_rr, s.SYMBOL)
            _log(f"[DEBUG_ENG] Symbol={s.SYMBOL} | Strategy=LONDON_OPEN | "
                  f"Dir=BUY | Entry={entry} | SL={sl} | TP={tp} | "
                  f"Stop={stop} | Active_RRR={dynamic_rr}", "DEBUG")
            res = place_trade("BUY", entry, sl, tp, lot_size, session=session)
            if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                _log(f"LONDON BREAKOUT BUY  Entry={entry}  SL={sl}  TP={tp}  Lot={lot_size}", "TRADE")
                log_trade("BUY", entry, sl, tp, res, lot_size, session, context=ctx, kronos_gate=s.KRONOS_VETO_GATE)
                _mark_session_traded(session)
            else:
                _log(f"LONDON BREAKOUT BUY FAILED. Code={res.retcode if res else 'N/A'}.", "ERROR")
            return

        # ── LONDON BREAKOUT: SELL ────────────────────────
        # Sweep above Asian high / PDH, then bearish FVG below Asian low = breakout
        if sweep == "SWEEP_HIGH" and fvg_type == "BEARISH_FVG":
            if fvg_high > asian_low:
                _log("London Breakout: FVG not below Asian low -- not a confirmed breakout. Waiting.", "GUARD")
                continue

            level_sweep = False
            if pdh and abs(sweep_level - pdh) < atr * 0.5:
                level_sweep = True
                _log(f"PDH SWEEP: {sweep_level:.5f} ~ PDH {pdh:.5f}  [+5]")
            if asian_high and abs(sweep_level - asian_high) < atr * 0.5:
                level_sweep = True
                _log(f"ASIAN HIGH SWEEP: {sweep_level:.5f} ~ AR High {asian_high:.5f}  [+5]")

            retest_zone_low = asian_low - atr * 0.3
            retest_zone_high = asian_low + atr * 0.3
            in_retest = retest_zone_low <= price <= retest_zone_high

            if not in_retest:
                _log(f"London Breakout: Price {price:.5f} outside retest zone "
                    f"({retest_zone_low:.5f}-{retest_zone_high:.5f}). Waiting for retest.", "GUARD")
                continue

            if not check_premium_discount_zone(df, price, "SELL"):
                _log("Not in premium zone. Skip.", "GUARD")
                continue

            score = 70
            if level_sweep:
                score += 10
            if spread_ok:
                score += 10
            if fvg_high < asian_low - atr * 0.5:
                score += 10

            _log(f"Confluence [LONDON BREAKOUT SELL]: {score}/100")

            if not check_pre_trade_spread(atr):
                continue

            ctx = {
                "direction": "SELL",
                "setup_type": "LONDON_BREAKOUT",
                "sweep": sweep,
                "fvg_type": fvg_type,
                "fvg_position": "above_50" if price >= fvg_50 else ("50%" if abs(price - fvg_50) < atr * 0.1 else "below_50"),
                "bos_aligned": False,
                "htf_bias_ok": False,
                "confluence_score": score,
                "session": session,
                "atr": atr,
                "spread_ok": spread_ok,
                "trend": "BEARISH",
                "level_sweep": level_sweep,
                "asian_high": asian_high,
                "asian_low": asian_low,
                "ob_present": ob is not None,
                "market_phase": market_phase,
                "fvg_low": fvg_low,
                "fvg_high": fvg_high,
                "fvg_50": fvg_50
            }
            if s.KRONOS_VETO_GATE is not None:
                allowed, reason = s.KRONOS_VETO_GATE.validate(ctx, df, s.SYMBOL)
                _log(f"[KRONOS] SELL signal: {reason}", "GUARD")
                if not allowed:
                    _log("Kronos vetoed SELL. Skipping trade.", "GUARD")
                    return

            stop = max(atr * s.ATR_MULTIPLIER, atr * s.MIN_SL_ATR_MULTIPLIER)
            entry = round_to_tick(price, s.SYMBOL)
            if asian_high:
                sl = round_to_tick(min(entry + stop, asian_high + atr * 0.3), s.SYMBOL)
            else:
                sl = round_to_tick(entry + stop, s.SYMBOL)
            if score >= 90:
                dynamic_rr = s.RISK_REWARD_RATIO
            elif score >= 80:
                dynamic_rr = s.RISK_REWARD_RATIO - 0.5
            else:
                dynamic_rr = s.RISK_REWARD_RATIO - 1.0
            tp = round_to_tick(entry - stop * dynamic_rr, s.SYMBOL)
            _log(f"[DEBUG_ENG] Symbol={s.SYMBOL} | Strategy=LONDON_OPEN | "
                  f"Dir=SELL | Entry={entry} | SL={sl} | TP={tp} | "
                  f"Stop={stop} | Active_RRR={dynamic_rr}", "DEBUG")
            res = place_trade("SELL", entry, sl, tp, lot_size, session=session)
            if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                _log(f"LONDON BREAKOUT SELL  Entry={entry}  SL={sl}  TP={tp}  Lot={lot_size}", "TRADE")
                log_trade("SELL", entry, sl, tp, res, lot_size, session, context=ctx, kronos_gate=s.KRONOS_VETO_GATE)
                _mark_session_traded(session)
            else:
                _log(f"LONDON BREAKOUT SELL FAILED. Code={res.retcode if res else 'N/A'}.", "ERROR")
            return

    _log("London Breakout: No valid setup. Ingwe waits...")
