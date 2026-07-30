import MetaTrader5 as mt5
import pandas as pd
import numpy as np
import json
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Optional
from pathlib import Path
from vuka.core.state import s
from vuka.risk.filters import is_eu_summer
from vuka.risk.portfolio import get_spread
from vuka.utils.unified_logger import get_logger
from skills.concept_tracker import record_concept_trade

_logger = get_logger("Orders")
log = _logger.log

def log_trade(direction, entry, sl, tp, result, lot_size, session, context=None, kronos_gate=None):
    """
    v5.5: Log trade to database (primary) or JSON fallback.
    Tracks: fill price, slippage, effective RR based on actual execution.
    Pulls actual fill from MT5 deal history when available.
    v6.1: Retries position_id lookup via positions_get if deal ticket unavailable.
    """
    actual_fill = entry
    position_id = 0
    if result and not s.BACKTEST_MODE:
        deal_ticket = getattr(result, "deal", 0)
        if deal_ticket:
            try:
                deals = mt5.history_deals_get(ticket=deal_ticket)
                if deals and len(deals) > 0:
                    actual_fill = deals[0].price
                    position_id = deals[0].position_id
            except Exception:
                actual_fill = getattr(result, "price", entry)
        else:
            actual_fill = getattr(result, "price", entry)
        # v6.1: fallback — try to get position_id from open positions
        if position_id == 0:
            try:
                positions = mt5.positions_get(symbol=s.SYMBOL)
                if positions:
                    for pos in positions:
                        if pos.magic == s._instance_magic:
                            position_id = pos.ticket
                            break
            except Exception:
                pass
    slippage = abs(actual_fill - entry)
    slippage_pips = slippage * 10000
    
    if direction == "BUY":
        sl_dist_actual = actual_fill - sl
        tp_dist_actual = tp - actual_fill
    else:
        sl_dist_actual = sl - actual_fill
        tp_dist_actual = actual_fill - tp
    
    effective_rr = tp_dist_actual / sl_dist_actual if sl_dist_actual > 0 else 0
    
    # Enrichment fields from live context dict
    htf_bias_val = "SPLIT"
    if context:
        trend_val = context.get("trend", "")
        htf_ok = context.get("htf_bias_ok", False)
        if trend_val and htf_ok:
            htf_bias_val = trend_val
        elif trend_val:
            htf_bias_val = f"{trend_val}_SPLIT"
    
    kronos_decision_val = "ALLOW"
    kronos_confidence_val = 0.0
    circuit_breaker_val = "CLOSED"
    api_latency_val = 0.0
    if kronos_gate and kronos_gate.last_decision:
        kd = kronos_gate.last_decision
        kronos_decision_val = kd.get("decision", "ALLOW")
        kronos_confidence_val = kd.get("confidence", 0.0)
        circuit_breaker_val = kd.get("circuit_breaker_state", "CLOSED")
        api_latency_val = kd.get("api_latency_ms", 0.0)
    
    spread_val = None
    try:
        spread_raw = get_spread()
        if spread_raw is not None:
            spread_val = round(spread_raw * 10000, 1)
    except Exception:
        pass
    
    trade_entry = {
        "symbol":      s.SYMBOL,
        "time":        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "strategy":    s.STRATEGY,
        "market_mode": "summer" if is_eu_summer() else "winter",
        "session":     session,
        "direction":   direction,
        "entry_req":   entry,
        "entry_fill":  actual_fill,
        "slippage":    round(slippage_pips, 1),
        "sl":          sl,
        "tp":          tp,
        "lot_size":    lot_size,
        "effective_rr": round(effective_rr, 2),
        "retcode":     result.retcode,
        "comment":     getattr(result, "comment", ""),
        "position_id": position_id,
        "pnl_usd":     None,
        "htf_bias":    htf_bias_val,
        "kronos_decision": kronos_decision_val,
        "kronos_confidence": kronos_confidence_val,
        "circuit_breaker": circuit_breaker_val,
        "api_latency_ms": api_latency_val,
        "spread_at_entry": spread_val,
    }
    
    if context:
        trade_entry["fvg_confirmed"] = context.get("fvg_type") is not None and context["fvg_type"] not in ("", "UNKNOWN", None)
        trade_entry["ob_present"] = context.get("ob_present", False)
        trade_entry["confluence_score"] = context.get("confluence_score", 0)
        trade_entry["setup_type"] = context.get("setup_type", "")
    
    log(f"[FILL] req={entry} fill={actual_fill} slip={slippage_pips:.1f}p eff_RR={effective_rr:.2f}", "TRADE")
    
    from vuka.core.bot import TRADING_GOVERNOR
    TRADING_GOVERNOR.record_trade()
    
    if s.DB_AVAILABLE:
        try:
            s.DB.insert_trade(trade_entry)
            log(f"Trade logged -> vuka_trading.db", "TRADE")
        except Exception as e:
            log(f"Database write error: {e}. Falling back to JSON.", "WARN")
    
    # JSON fallback (dual-write for safety during transition)
    trade_log = []
    if os.path.exists(s.LOG_FILE):
        try:
            with open(s.LOG_FILE, "r") as f:
                trade_log = json.load(f)
        except json.JSONDecodeError:
            pass
    
    trade_log.append(trade_entry)
    
    tmp = s.LOG_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(trade_log, f, indent=2)
    os.replace(tmp, s.LOG_FILE)
    log(f"Trade logged -> {s.LOG_FILE}", "TRADE")

    # Phase 1c: Enrich concept tracker with full trade context
    try:
        concepts_used = []
        if context:
            fvg = context.get("fvg_type", "")
            if fvg and fvg not in ("", "UNKNOWN", None):
                concepts_used.append(f"fvg_{fvg.lower()}")
            swp = context.get("sweep", "")
            if swp and swp not in ("", "UNKNOWN", None):
                concepts_used.append(f"sweep_{swp.lower()}")
            st = context.get("setup_type", "")
            if st and st not in ("", "UNKNOWN", None):
                concepts_used.append(f"setup_{st.lower()}")
            sess = context.get("session", "")
            if sess and sess not in ("", "UNKNOWN", None):
                concepts_used.append(f"session_{sess.lower().replace(' ', '_')}")
            tr = context.get("trend", "")
            if tr and tr not in ("", "UNKNOWN", None):
                concepts_used.append(f"trend_{tr.lower()}")
        if not concepts_used:
            concepts_used.append("unknown")
        record_concept_trade(
            str(position_id) if position_id else trade_entry.get("time", "unknown"),
            direction,
            concepts_used,
            kronos_decision_val,
            setup_type=context.get("setup_type", "UNKNOWN") if context else "UNKNOWN"
        )
        # Phase 2b: Store data-driven trail config in trade entry
        from skills.concept_tracker import ConceptTracker
        _ct = ConceptTracker()
        _conf_score = _ct.get_confidence_score(concepts_used[0]) if concepts_used else 0.25
        trade_entry["concept_confidence"] = round(_conf_score, 2)
        # High confidence (>0.6) -> trail BE at 2:1; otherwise BE at 1:1
        trade_entry["trail_be_at"] = 2.0 if _conf_score > 0.6 else 1.0
    except Exception as e:
        log(f"Concept tracker record_trade error: {e}", "WARN")


def place_trade(direction, entry, sl, tp, lot_size, session="unknown"):
    if s.BACKTEST_MODE:
        class MockResult:
            retcode = mt5.TRADE_RETCODE_DONE
            comment = "BACKTEST FILLED"
        return MockResult()

    if has_open_position():
        log(f"Position already open for {s._instance_tag} -- skipping duplicate entry.", "GUARD")
        return None

    # Phase 5a: Per-symbol-per-session dedup lock (prevents double-firing)
    if s.DB_AVAILABLE and session != "unknown":
        dedup_ok = s.DB.dedup_check_and_lock(s.SYMBOL, session, s.STRATEGY)
        if not dedup_ok:
            log(f"Session '{session}' already traded for {s.SYMBOL} today. Dedup lock active.", "GUARD")
            return None
    
    order_type = mt5.ORDER_TYPE_BUY if direction == "BUY" else mt5.ORDER_TYPE_SELL

    base_order = {
        "action":       mt5.TRADE_ACTION_DEAL,
        "symbol":       s.SYMBOL,
        "volume":       lot_size,
        "type":         order_type,
        "price":        entry,
        "sl":           sl,
        "tp":           tp,
        "deviation":    10,
        "magic":        s._instance_magic,
        "comment":      s._instance_short,   # v4.0 FIX: use short tag (e.g., "EURS", "GBPS")
        "type_time":    mt5.ORDER_TIME_GTC,
    }

    filling_modes = [
        mt5.ORDER_FILLING_RETURN,
        mt5.ORDER_FILLING_IOC,
        mt5.ORDER_FILLING_FOK,
    ]

    for i, filling_mode in enumerate(filling_modes, 1):
        order  = {**base_order, "type_filling": filling_mode}
        result = mt5.order_send(order)

        if result is None:
            log(f"Order send returned None (attempt {i}/3). "
                f"MT5 error: {mt5.last_error()}", "ERROR")
            continue

        if result.retcode == mt5.TRADE_RETCODE_DONE:
            log(f"Order filled with filling mode {i}/3 (RETURN/IOC/FOK)", "TRADE")
            return result

        if result.retcode == 10030:
            if i < 3:
                log(f"Filling mode rejected (10030) -- trying fallback {i+1}/3...", "WARN")
            continue

        log(f"Order failed. Retcode: {result.retcode}, "
            f"Comment: {getattr(result, 'comment', 'N/A')}", "ERROR")
        return result

    log("All filling modes exhausted. Order failed.", "ERROR")
    return None


def has_pending_order() -> bool:
    """
    v4.0: Returns True if this instance already has an active pending order.
    Guards against placing duplicate limit orders on consecutive scan cycles.
    The leopard does not set two traps in the same clearing.
    """
    if s.BACKTEST_MODE:
        return False
    
    orders = mt5.orders_get(symbol=s.SYMBOL)
    if not orders:
        return False
    return any(o.magic == s._instance_magic for o in orders)


def has_open_position() -> bool:
    """
    v4.4 FIX-1: Returns True if this instance has an open position.
    Guards against placing duplicate market orders on consecutive scan cycles.
    """
    if s.BACKTEST_MODE:
        return False
    
    positions = mt5.positions_get(symbol=s.SYMBOL)
    if not positions:
        return False
    return any(p.magic == s._instance_magic for p in positions)


def place_limit_order(direction: str, entry: float, sl: float,
                      tp: float, lot_size: float):
    """
    v4.0: Pending limit order at FVG 50% midpoint.
    BUY_LIMIT / SELL_LIMIT via TRADE_ACTION_PENDING.
    Expiry: s.LIMIT_ORDER_EXPIRY_CANDLES x s.SCAN_INTERVAL_SEC from now
    (default 4 x 15min = 1hr) in broker server time.
    Single submission -- pending orders do not use filling modes.
    
    BACKTEST MODE: Simulates limit order fill based on price retracement.
    """
    if s.BACKTEST_MODE:
        filled = check_backtest_limit_fill(direction, entry, s.LIMIT_ORDER_EXPIRY_CANDLES)
        class MockResult:
            retcode = mt5.TRADE_RETCODE_DONE if filled else 10025
            comment = "BACKTEST FILLED" if filled else "BACKTEST EXPIRED"
        return MockResult()
    
    order_type = (mt5.ORDER_TYPE_BUY_LIMIT
                  if direction == "BUY"
                  else mt5.ORDER_TYPE_SELL_LIMIT)

    expiry_dt = _server_now() + timedelta(
        seconds=s.LIMIT_ORDER_EXPIRY_CANDLES * s.SCAN_INTERVAL_SEC
    )

    order = {
        "action":          mt5.TRADE_ACTION_PENDING,
        "symbol":          s.SYMBOL,
        "volume":          lot_size,
        "type":            order_type,
        "price":           entry,
        "sl":              sl,
        "tp":              tp,
        "deviation":       10,
        "magic":           s._instance_magic,
        "comment":         s._instance_short,
        "type_time":       mt5.ORDER_TIME_SPECIFIED,
        "expiration":      expiry_dt,
    }

    result = mt5.order_send(order)
    if result is None:
        err = mt5.last_error()
        log(f"Limit order send returned None. MT5 error: {err}", "ERROR")
        log(f"Order details: price={entry}, sl={sl}, tp={tp}, type={order_type}", "ERROR")
        return None
    if result.retcode == mt5.TRADE_RETCODE_DONE:
        log(f"Limit order placed (expires {expiry_dt.isoformat()}).", "TRADE")
    else:
        log(f"Limit order failed. Retcode: {result.retcode}  "
            f"Comment: {getattr(result, 'comment', 'N/A')}", "ERROR")
    return result


def _modify_sl(pos, new_sl: float, label: str):
    """
    v4.4 FIX-3: SL movement tracking added.
    v3.9.5: Sends TRADE_ACTION_SLTP to move stop loss on open position.
    Preserves existing TP. Logs result with ticket and new SL level.
    """
    result = mt5.order_send({
        "action":   mt5.TRADE_ACTION_SLTP,
        "position": pos.ticket,
        "symbol":   s.SYMBOL,
        "sl":       new_sl,
        "tp":       pos.tp,
    })
    if result and result.retcode == mt5.TRADE_RETCODE_DONE:
        log(f"TRAIL [{label}]  Ticket={pos.ticket}  SL -> {new_sl:.5f}", "TRADE")
        log_sl_move(pos.ticket, pos.price_open, pos.sl, new_sl, label)
    else:
        log(f"SL modify failed. Ticket={pos.ticket}  "
            f"Code={result.retcode if result else 'N/A'}", "ERROR")


def log_sl_move(ticket: int, entry: float, old_sl: float, new_sl: float, label: str):
    """
    Log SL movements to database (primary) or JSON fallback.
    Tracks stop loss adjustments for risk management analysis.
    """
    sl_move_entry = {
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "ticket": ticket,
        "symbol": s._arg_symbol,
        "strategy": s.STRATEGY,
        "entry": entry,
        "old_sl": old_sl,
        "new_sl": new_sl,
        "movement": round(new_sl - old_sl, 5),
        "label": label
    }
    
    if s.DB_AVAILABLE:
        try:
            s.DB.insert_sl_movement(sl_move_entry)
            return
        except Exception as e:
            log(f"Database write error: {e}. Falling back to JSON.", "WARN")
    
    # JSON fallback (dual-write for safety during transition)
    log_file = Path(f"sl_moves_{s._instance_tag}.json")
    moves = []
    if log_file.exists():
        try:
            with open(log_file, "r") as f:
                moves = json.load(f)
        except:
            moves = []
    
    moves.append(sl_move_entry)
    
    with open(log_file, "w") as f:
        json.dump(moves, f, indent=2)


def round_to_tick(price: float, symbol: str) -> float:
    symbol_info = mt5.symbol_info(symbol)
    if symbol_info is None:
        return round(price, 5)
    tick_size = symbol_info.trade_tick_size
    if tick_size <= 0:
        return round(price, symbol_info.digits)
    remainder = price % tick_size
    if remainder < (tick_size / 2):
        normalized_price = price - remainder
    else:
        normalized_price = price + (tick_size - remainder)
    return round(normalized_price, symbol_info.digits)
