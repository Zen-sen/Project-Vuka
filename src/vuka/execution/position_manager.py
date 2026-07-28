import MetaTrader5 as mt5
import pandas as pd
import numpy as np
import json
import os
import time
from datetime import datetime
from typing import Optional
from vuka.core.state import s

def manage_open_positions():
    """
    v3.9.5 FIX-2: Trailing SL manager.
    Runs every scan cycle before session logic.
    Filtered by s._instance_magic -- each instance manages only its own trades.

    Rules:
      1:1 profit hit -> SL moves to breakeven (entry). Worst case: 0.
      1:2 profit hit -> SL moves to 1:1. Worst case: secured half RRR minimum.

    The leopard does not give back what it has already taken.
    """
    positions = mt5.positions_get(symbol=s.SYMBOL)
    if not positions:
        return

    # Phase 2b: Load trade log for data-driven trail config
    _trail_config = {}
    if os.path.exists(s.LOG_FILE):
        try:
            with open(s.LOG_FILE, "r") as _f:
                _trade_log = json.load(_f)
            for _t in _trade_log:
                _pid = _t.get("position_id", 0)
                if _pid:
                    _trail_config[_pid] = {
                        "trail_be_at": _t.get("trail_be_at", 1.0),
                        "concept_confidence": _t.get("concept_confidence", 0.25)
                    }
        except Exception:
            pass

    for pos in positions:
        if pos.magic != s._instance_magic:
            continue

        entry   = pos.price_open
        sl      = pos.sl
        current = pos.price_current
        sl_dist = abs(entry - sl)

        if sl_dist == 0:
            continue

        # Look up trail config for this position
        _tc = _trail_config.get(pos.identifier, {})
        trail_be_at = _tc.get("trail_be_at", 1.0)
        conf_score = _tc.get("concept_confidence", 0.25)
        trail_label = f"BE@{trail_be_at:.1f}R" if trail_be_at > 1.0 else "BE"

        if pos.type == mt5.ORDER_TYPE_BUY:
            at_be_target = current >= entry + sl_dist * trail_be_at
            at_2r = current >= entry + sl_dist * 2
            at_1r = current >= entry + sl_dist
            sl_below_1r = sl < entry + sl_dist
            sl_below_be = sl < entry

            if trail_be_at > 1.0:
                # High confidence: trail BE at N:1, secure profits later
                if at_be_target and sl_below_be:
                    new_sl = round(entry, 5)
                    if new_sl > sl:
                        _modify_sl(pos, new_sl, f"{trail_label} -> SL to BE")
                elif at_2r and sl_below_1r:
                    new_sl = round(entry + sl_dist, 5)
                    if new_sl > sl:
                        _modify_sl(pos, new_sl, "1:2 -> SL to 1:1")
            else:
                # Standard: trail BE at 1:1, secure 1:1 at 2:1
                if at_2r and sl_below_1r:
                    new_sl = round(entry + sl_dist, 5)
                    if new_sl > sl:
                        _modify_sl(pos, new_sl, "1:2 -> SL to 1:1")
                elif at_1r and sl_below_be:
                    new_sl = round(entry, 5)
                    if new_sl > sl:
                        _modify_sl(pos, new_sl, "1:1 -> SL to BE")

        elif pos.type == mt5.ORDER_TYPE_SELL:
            at_be_target = current <= entry - sl_dist * trail_be_at
            at_2r = current <= entry - sl_dist * 2
            at_1r = current <= entry - sl_dist
            sl_above_1r = sl > entry - sl_dist
            sl_above_be = sl > entry

            if trail_be_at > 1.0:
                if at_be_target and sl_above_be:
                    new_sl = round(entry, 5)
                    if new_sl < sl:
                        _modify_sl(pos, new_sl, f"{trail_label} -> SL to BE")
                elif at_2r and sl_above_1r:
                    new_sl = round(entry - sl_dist, 5)
                    if new_sl < sl:
                        _modify_sl(pos, new_sl, "1:2 -> SL to 1:1")
            else:
                if at_2r and sl_above_1r:
                    new_sl = round(entry - sl_dist, 5)
                    if new_sl < sl:
                        _modify_sl(pos, new_sl, "1:2 -> SL to 1:1")
                elif at_1r and sl_above_be:
                    new_sl = round(entry, 5)
                    if new_sl < sl:
                        _modify_sl(pos, new_sl, "1:1 -> SL to BE")

    # -- P&L backfill for closed trades ------------------
    if not s.BACKTEST_MODE:
        closed = mt5.history_deals_get(_server_midnight(), _server_now())
        if closed:
            pnl_by_pos = {}
            exit_price_by_pos = {}
            deal_list = []
            for d in closed:
                if d.magic == s._instance_magic and d.profit != 0:
                    pnl_by_pos[d.position_id] = d.profit
                    exit_price_by_pos[d.position_id] = d.price
                    deal_list.append({
                        "position_id": d.position_id,
                        "profit": d.profit,
                        "price": d.price,
                        "volume": d.volume,
                        "type": d.type,  # 0=BUY, 1=SELL
                    })
            if pnl_by_pos:
                if not os.path.exists(s.LOG_FILE):
                    return
                try:
                    with open(s.LOG_FILE, "r") as f:
                        trade_log_data = json.load(f)
                except (json.JSONDecodeError, IOError):
                    return
                updated = []
                for t in trade_log_data:
                    pos_id = t.get("position_id", 0)
                    if pos_id in pnl_by_pos and t.get("pnl_usd") is None:
                        pnl_val = round(pnl_by_pos[pos_id], 2)
                        t["pnl_usd"] = pnl_val
                        t["exit_price"] = round(exit_price_by_pos.get(pos_id, 0), 5)
                        exit_time_str = datetime.now().isoformat()
                        t["exit_time"] = exit_time_str
                        if pnl_val > 0:
                            t["exit_reason"] = "TP_HIT"
                        elif pnl_val < 0:
                            t["exit_reason"] = "SL_HIT"
                        else:
                            t["exit_reason"] = "BE_SCRATCH"
                        updated.append(t)
                # v6.1: fallback match for trades with position_id=0
                for t in trade_log_data:
                    if t.get("pnl_usd") is not None:
                        continue
                    if t.get("position_id", 0) != 0:
                        continue
                    t_dir = 0 if t.get("direction") == "BUY" else 1
                    t_lot = t.get("lot_size", 0)
                    t_fill = t.get("entry_fill", 0)
                    for d in deal_list:
                        if d["position_id"] in [x.get("position_id", 0) for x in updated]:
                            continue
                        if d["type"] != t_dir:
                            continue
                        if abs(d["volume"] - t_lot) > 0.01:
                            continue
                        if abs(d["price"] - t_fill) > 0.002:
                            continue
                        pnl_val = round(d["profit"], 2)
                        t["pnl_usd"] = pnl_val
                        t["exit_price"] = round(d["price"], 5)
                        t["exit_time"] = datetime.now().isoformat()
                        if pnl_val > 0:
                            t["exit_reason"] = "TP_HIT"
                        elif pnl_val < 0:
                            t["exit_reason"] = "SL_HIT"
                        else:
                            t["exit_reason"] = "BE_SCRATCH"
                        updated.append(t)
                        break
                if updated:
                    tmp = s.LOG_FILE + ".tmp"
                    with open(tmp, "w") as f:
                        json.dump(trade_log_data, f, indent=2)
                    os.replace(tmp, s.LOG_FILE)
                    log(f"P&L updated for {len(updated)} closed trade(s)", "TRADE")

                    # Phase 1a: Wire record_outcome() -- close the feedback loop
                    for t in updated:
                        try:
                            t_pos_id = t.get("position_id", 0)
                            pnl_val = t["pnl_usd"]
                            if pnl_val > 0:
                                outcome = "win"
                            elif pnl_val < 0:
                                outcome = "loss"
                            else:
                                outcome = "breakeven"
                            rr_achieved = t.get("effective_rr", 0) or 0
                            market_context = {
                                "symbol": t.get("symbol", s.SYMBOL),
                                "session": t.get("session", "unknown"),
                                "direction": t.get("direction", "unknown"),
                                "setup_type": t.get("setup_type", "UNKNOWN"),
                                "confluence_score": t.get("confluence_score", 0),
                                "volatility": "normal",
                                "exit_reason": t.get("exit_reason", "UNKNOWN"),
                            }
                            record_concept_outcome(
                                str(t_pos_id) if t_pos_id else t.get("time", "unknown"),
                                outcome,
                                rr_achieved,
                                pnl_val,
                                market_context
                            )
                            log(f"Concept outcome recorded: {outcome} PnL={pnl_val}", "TRADE")
                        except Exception as e:
                            log(f"Concept tracker record_outcome error: {e}", "WARN")

                    # Phase 1b: Update s.DB with PnL and exit info
                    if s.DB_AVAILABLE:
                        for t in updated:
                            try:
                                t_pos_id = t.get("position_id", 0)
                                if t_pos_id:
                                    DB.update_trade_pnl_by_position_id(
                                        t_pos_id,
                                        t["pnl_usd"],
                                        exit_price=t.get("exit_price"),
                                        exit_reason=t.get("exit_reason"),
                                        exit_time=t.get("exit_time")
                                    )
                                else:
                                    # v6.1: fallback using (symbol, strategy, time, direction)
                                    DB.update_trade_pnl(
                                        t.get("symbol", s.SYMBOL),
                                        t.get("strategy", s.STRATEGY),
                                        t.get("time", ""),
                                        t.get("direction", ""),
                                        t["pnl_usd"],
                                        exit_price=t.get("exit_price"),
                                        exit_reason=t.get("exit_reason"),
                                        exit_time=t.get("exit_time")
                                    )
                            except Exception as e:
                                log(f"s.DB PnL update error for trade: {e}", "WARN")
