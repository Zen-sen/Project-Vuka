import MetaTrader5 as mt5
import pandas as pd
import numpy as np
import json
import os
import time
from datetime import datetime, timezone, timedelta
from typing import Optional, Tuple, Any
from vuka.core.state import s

def evaluate_ict_m1(df, fvgs, sweep, sweep_level, price, atr,
                    lot_size, session):
    """
    ICT M1 Scalper pattern.
    - M1 timeframe, tight SL, quick entries
    - Sweep + FVG on M1 candles
    - No trend/ADX/HTF bias -- pure price action on M1
    - Scans every 15 seconds across all killzones
    """
    spread = get_spread()
    spread_pips = spread * 10000 if spread else 0
    log(f"Price: {price:.5f}  |  ATR: {atr:.5f}  |  "
        f"Lot: {lot_size}  |  Spread: {spread_pips:.1f}p")

    for fvg_type, fvg_low, fvg_high, fvg_idx, ob, fvg_50 in fvgs:
        if check_panic_candle(df, atr):
            continue

        # ── M1 BUY: sweep low + bullish FVG ──────────────
        if sweep == "SWEEP_LOW" and fvg_type == "BULLISH_FVG":
            if not check_pre_trade_spread(atr):
                continue

            log(f"M1 Bullish FVG: {fvg_low:.5f}-{fvg_high:.5f}  |  50%: {fvg_50:.5f}")

            ctx = {
                "direction": "BUY",
                "setup_type": "ICT_M1",
                "sweep": sweep,
                "fvg_type": fvg_type,
                "fvg_position": "below_50" if price <= fvg_50 else ("50%" if abs(price - fvg_50) < atr * 0.1 else "above_50"),
                "bos_aligned": False,
                "htf_bias_ok": False,
                "confluence_score": 75,
                "session": session,
                "atr": atr,
                "spread_ok": spread is not None and spread < s.MIN_SPREAD_PIPS,
                "trend": "BULLISH",
                "level_sweep": True,
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

            stop = max(atr * s.ATR_MULTIPLIER, atr * s.MIN_SL_ATR_MULTIPLIER)
            entry = round_to_tick(price, s.SYMBOL)
            sl = round_to_tick(entry - stop, s.SYMBOL)
            tp = round_to_tick(entry + stop * s.RISK_REWARD_RATIO, s.SYMBOL)
            res = place_trade("BUY", entry, sl, tp, lot_size, session=session)
            if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                log(f"M1 BUY  Entry={entry}  SL={sl}  TP={tp}  Lot={lot_size}", "TRADE")
                log_trade("BUY", entry, sl, tp, res, lot_size, session, context=ctx, kronos_gate=s.KRONOS_VETO_GATE)
                sessions_traded_today.add(session)
                save_sessions(s.sessions_traded_today)
            else:
                log(f"M1 BUY FAILED. Code={res.retcode if res else 'N/A'}.", "ERROR")
            return

        # ── M1 SELL: sweep high + bearish FVG ─────────────
        if sweep == "SWEEP_HIGH" and fvg_type == "BEARISH_FVG":
            if not check_pre_trade_spread(atr):
                continue

            log(f"M1 Bearish FVG: {fvg_low:.5f}-{fvg_high:.5f}  |  50%: {fvg_50:.5f}")

            ctx = {
                "direction": "SELL",
                "setup_type": "ICT_M1",
                "sweep": sweep,
                "fvg_type": fvg_type,
                "fvg_position": "above_50" if price >= fvg_50 else ("50%" if abs(price - fvg_50) < atr * 0.1 else "below_50"),
                "bos_aligned": False,
                "htf_bias_ok": False,
                "confluence_score": 75,
                "session": session,
                "atr": atr,
                "spread_ok": spread is not None and spread < s.MIN_SPREAD_PIPS,
                "trend": "BEARISH",
                "level_sweep": True,
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

            stop = max(atr * s.ATR_MULTIPLIER, atr * s.MIN_SL_ATR_MULTIPLIER)
            entry = round_to_tick(price, s.SYMBOL)
            sl = round_to_tick(entry + stop, s.SYMBOL)
            tp = round_to_tick(entry - stop * s.RISK_REWARD_RATIO, s.SYMBOL)
            res = place_trade("SELL", entry, sl, tp, lot_size, session=session)
            if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                log(f"M1 SELL  Entry={entry}  SL={sl}  TP={tp}  Lot={lot_size}", "TRADE")
                log_trade("SELL", entry, sl, tp, res, lot_size, session, context=ctx, kronos_gate=s.KRONOS_VETO_GATE)
                sessions_traded_today.add(session)
                save_sessions(s.sessions_traded_today)
            else:
                log(f"M1 SELL FAILED. Code={res.retcode if res else 'N/A'}.", "ERROR")
            return

    log("M1: No valid FVG sweep. Ingwe waits...")
