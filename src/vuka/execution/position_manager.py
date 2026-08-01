import MetaTrader5 as mt5

from vuka.core.state import s
from vuka.execution.orders import _modify_sl
from vuka.risk.portfolio import _server_midnight, _server_now
from vuka.utils.telemetry_queue import get_telemetry


def manage_open_positions():
    """
    v3.9.5 FIX-2: Trailing SL manager.
    Runs every scan cycle before session logic.
    Filtered by s._instance_magic -- each instance manages only its own trades.

    Rules:
      1:1 profit hit -> SL moves to breakeven (entry). Worst case: 0.
      1:2 profit hit -> SL moves to 1:1. Worst case: secured half RRR minimum.

    v6.2: Trail configuration (trail_be_at / concept_confidence) is read from
    the in-memory s.active_trails dict -- injected at trade placement -- never
    from the JSON trade log on disk. The scan loop does not touch the hard
    drive to compute trailing stops.

    The leopard does not give back what it has already taken.
    """
    positions = mt5.positions_get(symbol=s.SYMBOL)
    if not positions:
        return

    for pos in positions:
        if pos.magic != s._instance_magic:
            continue

        entry   = pos.price_open
        sl      = pos.sl
        current = pos.price_current
        sl_dist = abs(entry - sl)

        if sl_dist == 0:
            continue

        # Look up trail config from RAM -- injected by log_trade at fill time.
        _tc = s.active_trails.get(pos.identifier, {})
        trail_be_at = _tc.get("trail_be_at", 1.0)
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
    # MT5 deal history is fetched here (main thread only -- the TelemetryQueue
    # worker must never call MT5). The JSON read-modify-write, concept tracker
    # feedback loop, and DB updates all run on the background worker thread.
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
                get_telemetry().submit("pnl_backfill", {
                    "log_file": s.LOG_FILE,
                    "pnl_by_pos": pnl_by_pos,
                    "exit_price_by_pos": exit_price_by_pos,
                    "deal_list": deal_list,
                })
