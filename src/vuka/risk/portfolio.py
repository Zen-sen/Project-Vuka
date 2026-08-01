import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone

import MetaTrader5 as mt5

from vuka.core.state import s

logger = logging.getLogger(__name__)

def _log(msg: str, level: str = "INFO"):
    logger.log(getattr(logging, level, logging.INFO), msg)


def get_last_sunday(year: int, month: int):
    if month == 12:
        last_day = datetime(year + 1, 1, 1) - timedelta(days=1)
    else:
        last_day = datetime(year, month + 1, 1) - timedelta(days=1)
    while last_day.weekday() != 6:
        last_day -= timedelta(days=1)
    return last_day


def is_eu_summer():
    today = datetime.now()
    return get_last_sunday(today.year, 3) <= today <= get_last_sunday(today.year, 10)


SA_OFFSET = 2


def now_sast():
    return datetime.now(timezone.utc) + timedelta(hours=SA_OFFSET)


def get_exness_server_offset():
    return 3 if is_eu_summer() else 2

def mt5_fetch_with_retry(fetch_fn, *args, **kwargs):
    for attempt in range(1, s.MT5_RETRY_ATTEMPTS + 1):
        result = fetch_fn(*args, **kwargs)
        if result is not None:
            return result
        error = mt5.last_error()
        _log(f"MT5 fetch failed (attempt {attempt}/{s.MT5_RETRY_ATTEMPTS}). "
            f"Error: {error}. Waiting {s.MT5_RETRY_DELAY_SEC}s...", "WARN")
        time.sleep(s.MT5_RETRY_DELAY_SEC)
    _log("All MT5 fetch attempts exhausted.", "ERROR")
    return None


def get_initial_equity() -> float:
    if s.initial_equity is None:
        account = mt5.account_info()
        if account:
            s.initial_equity = account.equity
            _log(f"Initial equity locked: {s.initial_equity:.2f} USC "
                f"(= ${s.initial_equity/100:.2f} USD)")
    return s.initial_equity or 0.0


def check_equity_drawdown() -> bool:
    account = mt5.account_info()
    initial = get_initial_equity()
    if account is None or initial == 0:
        return False
    pct = ((initial - account.equity) / initial) * 100
    if pct > s.MAX_DRAWDOWN_PCT:
        _log(f"DRAWDOWN LIMIT EXCEEDED ({pct:.2f}%). Ingwe stands down.", "GUARD")
        return True
    return False


def _server_midnight() -> datetime:
    """
    v3.9.4 FIX-3: Returns broker-server-time midnight as a naive datetime.
    MT5 history_deals_get() interprets naive datetimes as broker server time.
    Exness server = UTC+2 (winter) or UTC+3 (EU summer).
    SAST machine is always UTC+2. In EU summer, a naive datetime.now()
    on a SAST machine maps to UTC+2 but MT5 expects UTC+3 -- 1-hour drift.
    Using server offset here eliminates that drift.
    """
    server_offset   = get_exness_server_offset()
    server_now      = datetime.now(timezone.utc) + timedelta(hours=server_offset)
    server_midnight = server_now.replace(
        hour=0, minute=0, second=0, microsecond=0, tzinfo=None
    )
    return server_midnight


def _server_now() -> datetime:
    """Broker server time as naive datetime (matches _server_midnight() convention)."""
    server_offset = get_exness_server_offset()
    return (datetime.now(timezone.utc) + timedelta(hours=server_offset)).replace(tzinfo=None)


def get_daily_pnl() -> float:
    """
    v3.9.4 FIX-3: uses server-offset-aware datetimes.
    v3.8.1: filters deals by _instance_magic.
    Each instance tracks only its own trades independently.
    """
    midnight = _server_midnight()
    deals    = mt5_fetch_with_retry(mt5.history_deals_get, midnight, _server_now())
    if deals is None:
        return 0.0
    return sum(d.profit for d in deals if d.magic == s._instance_magic)


def check_consecutive_losses() -> bool:
    """
    v5.1 FIX: Persistent consecutive loss tracking across days.
    Loads saved loss count, checks against threshold (3 losses = pause).
    """
    loss_count, _ = load_consecutive_losses()
    if loss_count >= 3:
        _log(f"Consecutive loss limit reached ({loss_count} losses) -- Ingwe pauses.", "GUARD")
        return True
    return False


def load_consecutive_losses() -> tuple:
    """
    Load (consecutive_losses, last_counted_ticket) once into RAM and cache it
    on the shared state. Subsequent calls in the same scan cycle are pure
    cache hits -- no disk or SQLite I/O on the hot path. The cache is
    invalidated at the daily reset so a fresh day reloads cleanly.
    """
    if s.consecutive_losses is not None:
        return (s.consecutive_losses, s.last_counted_ticket)

    today = datetime.now().strftime("%Y-%m-%d")

    if s.DB_AVAILABLE:
        try:
            from vuka.core.bot import DB
            count, ticket = DB.get_loss_tracking(today, s._arg_symbol, s.STRATEGY)
            s.consecutive_losses = count
            s.last_counted_ticket = ticket
            return (count, ticket)
        except Exception as e:
            _log(f"Database read error: {e}. Falling back to JSON.", "WARN")

    # JSON fallback
    count, ticket = 0, 0
    if os.path.exists(s.SESSIONS_FILE):
        try:
            with open(s.SESSIONS_FILE) as f:
                data = json.load(f)
            if data.get("date") == today:
                count = data.get("consecutive_losses", 0)
                ticket = data.get("last_counted_ticket", 0)
        except (json.JSONDecodeError, KeyError):
            pass

    s.consecutive_losses = count
    s.last_counted_ticket = ticket
    return (count, ticket)


def save_consecutive_losses(count: int, last_ticket: int = 0):
    """
    Cache the loss state in RAM and offload the DB/JSON write to the
    TelemetryQueue worker thread. Only called on state change, so the scan
    loop never blocks on loss-track I/O.
    """
    s.consecutive_losses = count
    s.last_counted_ticket = last_ticket
    from vuka.utils.telemetry_queue import get_telemetry
    get_telemetry().submit("loss_tracking", {
        "count": count,
        "last_ticket": last_ticket,
        "date": datetime.now().strftime("%Y-%m-%d"),
    })


def update_consecutive_losses():
    """
    v5.1 FIX: Update consecutive loss counter based on today's closed trades.
    Tracks the last counted deal ticket to avoid re-counting the same deal
    across multiple scan cycles.
    """
    midnight = _server_midnight()
    deals = mt5_fetch_with_retry(mt5.history_deals_get, midnight, _server_now())
    if deals is None:
        return

    own_deals = [d for d in deals if d.magic == s._instance_magic and d.profit != 0]
    if not own_deals:
        return

    last_deal = own_deals[-1]
    current_count, last_ticket = load_consecutive_losses()

    if last_deal.ticket == last_ticket:
        return

    if last_deal.profit < 0:
        new_count = current_count + 1
        save_consecutive_losses(new_count, last_deal.ticket)
        _log(f"Loss recorded -- consecutive losses: {new_count}", "INFO")
    else:
        if current_count > 0:
            save_consecutive_losses(0, last_deal.ticket)
            _log("Win recorded -- consecutive loss counter reset.", "INFO")


def get_spread() -> float | None:
    if s.BACKTEST_MODE:
        return 0.00010  # 1 pip fixed spread for backtest
    tick = mt5.symbol_info_tick(s.SYMBOL)
    return (tick.ask - tick.bid) if tick else None


def calculate_lot_size(sl_distance: float | None = None) -> float:
    """
    v5.1: Dynamic lot sizing based on risk per trade.
    Formula: LotSize = (Equity * Risk%) / (SL_distance * TickValue)
    """
    if sl_distance is None or sl_distance <= 0:
        # Default fallback: use ATR-based distance if no specific SL provided
        try:
            from vuka.market_structure.ict import calculate_atr
            rates = mt5_fetch_with_retry(mt5.copy_rates_from_pos, s.SYMBOL, s.TIMEFRAME, 0, 200)
            if rates is None:
                return 0.01
            import pandas as pd
            df = pd.DataFrame(rates)
            df["time"] = pd.to_datetime(df["time"], unit="s")
            atr = calculate_atr(df)
            if atr:
                sl_distance = atr * s.ATR_MULTIPLIER
            else:
                return 0.01
        except Exception as e:
            _log(f"Lot calc fallback triggered: {e}", "WARN")
            return 0.01

    account = mt5.account_info()
    symbol_info = mt5.symbol_info(s.SYMBOL)

    if not account or not symbol_info:
        _log("Account or symbol info unavailable for lot calculation.", "ERROR")
        return 0.01

    equity = account.equity
    risk_amount = equity * (s.RISK_PERCENT / 100.0)

    tick_value = symbol_info.trade_tick_value
    tick_size = symbol_info.trade_tick_size

    if tick_value == 0 or tick_size == 0:
        return 0.01

    sl_ticks = sl_distance / tick_size
    lot_size = risk_amount / (sl_ticks * tick_value)

    min_lot = symbol_info.volume_min
    max_lot = symbol_info.volume_max
    final_lot = max(min_lot, min(lot_size, max_lot, s.HARD_LOT_CAP))

    lot_step = symbol_info.volume_step
    final_lot = round(final_lot / lot_step) * lot_step

    return float(final_lot)


def get_overlap_multiplier() -> float:
    hour = now_sast().hour
    if is_eu_summer():
        return 1.2 if 15 <= hour < 18 else 1.0
    return 1.2 if 16 <= hour < 19 else 1.0
