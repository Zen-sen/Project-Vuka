from datetime import datetime, timedelta, timezone

from vuka.core.state import s
from vuka.risk.portfolio import get_spread, is_eu_summer
from vuka.utils.unified_logger import get_logger

_logger = get_logger("Filters")


def log(msg: str, level: str = "INFO"):
    _logger.log(level=level, message=msg, symbol=s._arg_symbol, strategy=s.STRATEGY)


def now_sast():
    return datetime.now(timezone.utc) + timedelta(hours=getattr(s, "SA_OFFSET", 2))


def get_active_killzones():
    if s.STRATEGY == "ICT_M1":
        return s.ICT_M1_SESSIONS
    if s._arg_symbol == "BTCUSD":
        return s.BTC_KILLZONES
    return s.KILLZONES_SUMMER if is_eu_summer() else s.KILLZONES_WINTER


def get_active_sb_windows():
    return s.SB_WINDOWS_SUMMER if is_eu_summer() else s.SB_WINDOWS_WINTER


def get_active_blackouts():
    if s.STRATEGY == "SILVER_BULLET":
        return s.SB_BLACKOUTS_SUMMER if is_eu_summer() else s.SB_BLACKOUTS_WINTER
    return s.INGWE_BLACKOUTS_SUMMER if is_eu_summer() else s.INGWE_BLACKOUTS_WINTER


def get_current_session() -> str | None:
    hour = now_sast().hour
    for session, (s, e) in get_active_killzones().items():
        if s <= hour < e if s <= e else (hour >= s or hour < e):
            return session
    return None


def is_in_dead_zone() -> bool:
    hour = now_sast().hour
    if is_eu_summer():
        return 12 <= hour < 15
    return 13 <= hour < 16


def get_current_sb_window() -> str | None:
    hour = now_sast().hour
    for window, (s, e) in get_active_sb_windows().items():
        if s <= hour < e:
            return window
    return None


def is_in_news_blackout() -> bool:
    h, m = now_sast().hour, now_sast().minute
    for (sh, sm, eh, em) in get_active_blackouts():
        if (sh < h < eh) or (sh == h and m >= sm) or (eh == h and m < em):
            return True
    return False


def check_panic_candle(df, atr: float) -> bool:
    if atr is None or df.empty:
        return False
    last = df.iloc[-1]
    if (last["high"] - last["low"]) > atr * 2:
        log(f"Panic candle detected. Ingwe does not chase.", "GUARD")
        return True
    return False


def check_premium_discount_zone(df, price: float, direction: str) -> bool:
    if len(df) < 20:
        return True
    recent = df.iloc[-20:]
    hi, lo = recent["high"].max(), recent["low"].min()
    rng    = hi - lo
    if rng == 0:
        return True
    if direction == "BUY":
        return price <= (lo + rng * 0.50)
    return price >= (hi - rng * 0.50)


def check_pre_trade_spread(atr=None) -> bool:
    spread = get_spread()
    if spread is None:
        log("Spread unavailable.", "WARN")
        return False

    if s._arg_symbol == "BTCUSD":
        max_spread = 2.0
        if spread > max_spread:
            log(f"Spread too wide: ${spread:.2f} (max: ${max_spread})", "GUARD")
            return False
    else:
        if atr is not None:
            max_spread = atr * 0.3
            if spread > max_spread:
                log(f"Spread too wide: {spread*10000:.1f}p (ATR={atr:.5f}, max={max_spread:.5f}).", "GUARD")
                return False
        else:
            if spread > s.MIN_SPREAD_PIPS * 2:
                log(f"Spread too wide: {spread*10000:.1f}p.", "GUARD")
                return False
    return True