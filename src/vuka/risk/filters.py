from vuka.core.state import s
def get_current_session() -> str | None:
    hour = now_sast().hour
    for session, (s, e) in get_active_killzones().items():
        if s <= hour < e if s <= e else (hour >= s or hour < e):
            return session
    return None


def is_in_dead_zone() -> bool:
    """Winter: 13:00-16:00 SAST. Summer: 12:00-15:00 SAST."""
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


def check_panic_candle(df: pd.DataFrame, atr: float) -> bool:
    if atr is None or df.empty:
        return False
    last = df.iloc[-1]
    if (last["high"] - last["low"]) > atr * 2:
        log(f"Panic candle detected. Ingwe does not chase.", "GUARD")
        return True
    return False


def check_premium_discount_zone(df: pd.DataFrame, price: float, direction: str) -> bool:
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


def check_pre_trade_spread(atr: float | None = None) -> bool:
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
