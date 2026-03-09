import MetaTrader5 as mt5
from datetime import datetime, timedelta, timezone
import pandas as pd
import numpy as np
import time
import json
import os
import sys

# =======================================================
#    PROJECT VUKA — AGENT INGWE  v3.0
#    The leopard does not miss because it does not rush.
#    "Ingwe ayidlozi ngoba ayiphuthi isikhathi."
# =======================================================
# CHANGELOG v3.0 — SILVER BULLET MODE:
#
#   NEW: STRATEGY switch at top of config.
#     STRATEGY = "INGWE"         — original multi-confluence model
#     STRATEGY = "SILVER_BULLET" — ICT Silver Bullet time-precision model
#
#   SILVER BULLET logic:
#     - Three strict 1-hour windows per day (NY session based)
#     - Requires: sweep + FVG inside the window
#     - NO trend filter, NO ADX filter, NO zone filter
#     - Entry at 50% midpoint of the FVG (fvg_50)
#     - SL beyond sweep level + ATR buffer
#     - TP at 1:3
#     - The window itself IS the confluence filter
#
#   SILVER BULLET windows in SAST (UTC+2):
#     Winter (NY = EST = UTC-5):
#       SB_Window1  10:00-11:00  (03:00-04:00 NY)
#       SB_Window2  17:00-18:00  (10:00-11:00 NY)
#       SB_Window3  21:00-22:00  (14:00-15:00 NY)
#     Summer (NY = EDT = UTC-4):
#       SB_Window1  09:00-10:00  (03:00-04:00 NY)
#       SB_Window2  16:00-17:00  (10:00-11:00 NY)
#       SB_Window3  20:00-21:00  (14:00-15:00 NY)
#
#   All shared infrastructure unchanged:
#     Oracle's Eye, risk management, session persistence,
#     timezone handling, weekend guard, news blackouts,
#     logging, MT5 retry — fully inherited by both modes.
#
# CHANGELOG v2.7: NY Open blackout runway fix
# CHANGELOG v2.6: Zone threshold corrected to 50% ICT canonical
# CHANGELOG v2.5: Pre-London blackout 15min runway
# CHANGELOG v2.4: Timestamp UTC bug fix, weekend guard
# CHANGELOG v2.3: SA timezone overhaul, Exness DST-aware
# CHANGELOG v2.2: All 4 killzone windows corrected to ICT
# CHANGELOG v2.1: HARD_LOT_CAP=0.20, RISK_PERCENT=1.5%
# CHANGELOG v2.0: Oracle's Eye, retry, persistence, logging
# =======================================================

# -------------------------------------------------------
# ── STRATEGY SELECTOR ───────────────────────────────────
# Pass strategy as command line argument — no file editing needed.
#   py ingwe.py INGWE
#   py ingwe.py SILVER_BULLET
# Defaults to INGWE if no argument given.
# -------------------------------------------------------
_valid_strategies = ("INGWE", "SILVER_BULLET")
STRATEGY = sys.argv[1].upper() if len(sys.argv) > 1 else "INGWE"
if STRATEGY not in _valid_strategies:
    print(f"❌ Unknown strategy '{STRATEGY}'. Use: INGWE or SILVER_BULLET")
    sys.exit(1)

# -------------------------------------------------------
# CONFIGURATION
# -------------------------------------------------------
SYMBOL              = "EURUSDc"
TIMEFRAME           = mt5.TIMEFRAME_M15
RISK_PERCENT        = 1.5
RISK_REWARD_RATIO   = 3.0
ATR_PERIOD          = 14
ATR_MULTIPLIER      = 1.5
ADX_PERIOD          = 14
ADX_MIN_THRESHOLD   = 0
MIN_SPREAD_PIPS     = 0.0002
MAX_DAILY_LOSS      = 50.0
MAX_DRAWDOWN_PCT    = 10.0
HARD_LOT_CAP        = 0.20
LOG_FILE            = "trades.json"
SESSIONS_FILE       = "sessions_today.json"
SCAN_INTERVAL_SEC   = 900
DATA_STALE_MINUTES  = 30
MT5_RETRY_ATTEMPTS  = 3
MT5_RETRY_DELAY_SEC = 30

# -------------------------------------------------------
# TIMEZONE — South Africa
# SAST = UTC+2, permanently. No DST. Ever.
# -------------------------------------------------------
SA_OFFSET = 2

# -------------------------------------------------------
# INGWE — KILLZONES (SAST)
# -------------------------------------------------------
KILLZONES_WINTER = {
    "Asian":         (2,  6),
    "London Open":   (10, 13),
    "New York Open": (16, 19),
    "London Close":  (17, 19),
}
KILLZONES_SUMMER = {
    "Asian":         (2,  6),
    "London Open":   (9,  12),
    "New York Open": (15, 18),
    "London Close":  (16, 18),
}

# Ingwe news blackouts — 15min runway before each session
INGWE_BLACKOUTS_WINTER = [
    (8,  30,  9, 45),   # London Open starts 10:00
    (14, 30, 15, 45),   # NY Open starts 16:00
]
INGWE_BLACKOUTS_SUMMER = [
    (7,  30,  8, 45),   # London Open starts 09:00
    (13, 30, 14, 45),   # NY Open starts 15:00
]

# -------------------------------------------------------
# SILVER BULLET — WINDOWS (SAST)
# Three 1-hour windows. The window is the confluence.
# -------------------------------------------------------
SB_WINDOWS_WINTER = {
    "SB_Window1": (10, 11),   # 03:00-04:00 NY EST
    "SB_Window2": (17, 18),   # 10:00-11:00 NY EST
    "SB_Window3": (21, 22),   # 14:00-15:00 NY EST
}
SB_WINDOWS_SUMMER = {
    "SB_Window1": (9,  10),   # 03:00-04:00 NY EDT
    "SB_Window2": (16, 17),   # 10:00-11:00 NY EDT
    "SB_Window3": (20, 21),   # 14:00-15:00 NY EDT
}

# Silver Bullet blackouts — 15min runway before each window
SB_BLACKOUTS_WINTER = [
    (9,  45, 10,  0),   # SB_Window1 starts 10:00
    (16, 45, 17,  0),   # SB_Window2 starts 17:00
    (20, 45, 21,  0),   # SB_Window3 starts 21:00
]
SB_BLACKOUTS_SUMMER = [
    (8,  45,  9,  0),   # SB_Window1 starts 09:00
    (15, 45, 16,  0),   # SB_Window2 starts 16:00
    (19, 45, 20,  0),   # SB_Window3 starts 20:00
]

# -------------------------------------------------------
# GLOBALS
# -------------------------------------------------------
initial_equity        = None
consecutive_losses    = 0
sessions_traded_today = set()


# =======================================================
#  SECTION 1 — UTILITIES & TIMEZONE
# =======================================================

def log(msg: str, level: str = "INFO"):
    prefix = {
        "INFO":  "   ",
        "WARN":  "⚠️  ",
        "ERROR": "🔴 ",
        "TRADE": "🐆 ",
        "GUARD": "🛡️  ",
    }.get(level, "   ")
    print(f"{prefix}{msg}")


def get_last_sunday(year: int, month: int) -> datetime:
    if month == 12:
        last_day = datetime(year + 1, 1, 1) - timedelta(days=1)
    else:
        last_day = datetime(year, month + 1, 1) - timedelta(days=1)
    while last_day.weekday() != 6:
        last_day -= timedelta(days=1)
    return last_day


def is_eu_summer() -> bool:
    """European markets DST. SA does NOT observe DST."""
    today = datetime.now()
    return get_last_sunday(today.year, 3) <= today <= get_last_sunday(today.year, 10)


def get_exness_server_offset() -> int:
    return 3 if is_eu_summer() else 2


def now_sast() -> datetime:
    """South African time. Always UTC+2. Never changes."""
    return datetime.now(timezone.utc) + timedelta(hours=SA_OFFSET)


def get_active_killzones() -> dict:
    return KILLZONES_SUMMER if is_eu_summer() else KILLZONES_WINTER


def get_active_sb_windows() -> dict:
    return SB_WINDOWS_SUMMER if is_eu_summer() else SB_WINDOWS_WINTER


def get_active_blackouts() -> list:
    """Strategy-aware blackout windows."""
    if STRATEGY == "SILVER_BULLET":
        return SB_BLACKOUTS_SUMMER if is_eu_summer() else SB_BLACKOUTS_WINTER
    return INGWE_BLACKOUTS_SUMMER if is_eu_summer() else INGWE_BLACKOUTS_WINTER


def is_market_open() -> bool:
    return now_sast().weekday() not in (5, 6)  # 5=Sat, 6=Sun


# =======================================================
#  SECTION 2 — DATA INTEGRITY (THE ORACLE'S EYE)
# =======================================================

def mt5_fetch_with_retry(fetch_fn, *args, **kwargs):
    for attempt in range(1, MT5_RETRY_ATTEMPTS + 1):
        result = fetch_fn(*args, **kwargs)
        if result is not None:
            return result
        error = mt5.last_error()
        log(f"MT5 fetch failed (attempt {attempt}/{MT5_RETRY_ATTEMPTS}). "
            f"Error: {error}. Waiting {MT5_RETRY_DELAY_SEC}s...", "WARN")
        time.sleep(MT5_RETRY_DELAY_SEC)
    log("All MT5 fetch attempts exhausted.", "ERROR")
    return None


def is_data_fresh(df: pd.DataFrame) -> bool:
    if df is None or df.empty:
        return False
    last_utc = df["time"].iloc[-1]
    if pd.isnull(last_utc):
        return False
    if last_utc.tzinfo is None:
        last_utc = last_utc.replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - last_utc).total_seconds() / 60
    if age > DATA_STALE_MINUTES:
        log(f"Data stale — last candle {age:.1f} min ago.", "WARN")
        return False
    return True


def has_frozen_prices(df: pd.DataFrame, lookback: int = 4) -> bool:
    if df is None or len(df) < lookback:
        return False
    closes = df["close"].tail(lookback).values
    if len(set(closes)) == 1:
        log(f"FROZEN FEED — {lookback} identical closes ({closes[0]:.5f}).", "GUARD")
        return True
    return False


def validate_candles(df: pd.DataFrame) -> bool:
    if df is None or len(df) < 50:
        log("Insufficient candle data (need 50+).", "WARN")
        return False
    return is_data_fresh(df) and not has_frozen_prices(df)


# =======================================================
#  SECTION 3 — SESSION PERSISTENCE
# =======================================================

def load_sessions() -> set:
    if os.path.exists(SESSIONS_FILE):
        try:
            with open(SESSIONS_FILE, "r") as f:
                data = json.load(f)
            if data.get("date") == datetime.now().strftime("%Y-%m-%d"):
                return set(data.get("sessions", []))
        except (json.JSONDecodeError, KeyError):
            log("Sessions file corrupted — starting fresh.", "WARN")
    return set()


def save_sessions(sessions: set):
    with open(SESSIONS_FILE, "w") as f:
        json.dump({
            "date":     datetime.now().strftime("%Y-%m-%d"),
            "sessions": list(sessions)
        }, f, indent=2)


# =======================================================
#  SECTION 4 — ACCOUNT & RISK MANAGEMENT
# =======================================================

def get_initial_equity() -> float:
    global initial_equity
    if initial_equity is None:
        account = mt5.account_info()
        if account:
            initial_equity = account.equity
            log(f"Initial equity locked: {initial_equity:.2f} USC "
                f"(= ${initial_equity/100:.2f} USD)")
    return initial_equity or 0.0


def check_equity_drawdown() -> bool:
    account = mt5.account_info()
    initial = get_initial_equity()
    if account is None or initial == 0:
        return False
    pct = ((initial - account.equity) / initial) * 100
    if pct > MAX_DRAWDOWN_PCT:
        log(f"DRAWDOWN LIMIT EXCEEDED ({pct:.2f}%). Ingwe stands down.", "GUARD")
        return True
    return False


def get_daily_pnl() -> float:
    midnight = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    deals = mt5_fetch_with_retry(mt5.history_deals_get, midnight, datetime.now())
    return sum(d.profit for d in deals) if deals else 0.0


def check_consecutive_losses() -> bool:
    midnight = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    deals = mt5_fetch_with_retry(mt5.history_deals_get, midnight, datetime.now())
    if deals is None or len(deals) < 2:
        return False
    return all(d.profit < 0 for d in list(deals)[-2:])


def get_spread() -> float | None:
    tick = mt5.symbol_info_tick(SYMBOL)
    return (tick.ask - tick.bid) if tick else None


# =======================================================
#  SECTION 5 — TREND & MARKET STRUCTURE
# =======================================================

def get_h1_trend() -> str | None:
    """EMA10 vs EMA30 on H1. Used by Ingwe mode only."""
    rates = mt5_fetch_with_retry(
        mt5.copy_rates_from_pos, SYMBOL, mt5.TIMEFRAME_H1, 0, 50
    )
    if rates is None or len(rates) < 30:
        return None
    df    = pd.DataFrame(rates)
    ema10 = df["close"].ewm(span=10, adjust=False).mean().iloc[-1]
    ema30 = df["close"].ewm(span=30, adjust=False).mean().iloc[-1]
    if ema10 > ema30:   return "BULLISH"
    if ema10 < ema30:   return "BEARISH"
    return None


def get_candles() -> pd.DataFrame | None:
    """MT5 unix timestamps are always UTC — no offset applied."""
    rates = mt5_fetch_with_retry(
        mt5.copy_rates_from_pos, SYMBOL, TIMEFRAME, 0, 200
    )
    if rates is None:
        return None
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
    return df


def detect_liquidity_sweep(df: pd.DataFrame):
    recent    = df.tail(5)
    prev_high = recent["high"].iloc[:-1].max()
    prev_low  = recent["low"].iloc[:-1].min()
    last      = recent.iloc[-1]
    if last["high"] > prev_high:   return "SWEEP_HIGH", prev_high
    if last["low"]  < prev_low:    return "SWEEP_LOW",  prev_low
    return None, None


def check_displacement_validity(c1: pd.Series, c2: pd.Series) -> bool:
    c1_range = c1["high"] - c1["low"]
    if c1_range == 0:
        return True
    return (c2["high"] - c2["low"]) > (c1_range * 1.5)


def detect_fvg(df: pd.DataFrame) -> list:
    """
    Detects Fair Value Gaps and calculates fvg_50 (50% midpoint).
    fvg_50 is the Silver Bullet entry level — always calculated,
    used by SB mode, available to Ingwe mode for reference.
    """
    fvgs = []
    for i in range(2, len(df) - 1):
        c1, c2, c3 = df.iloc[i-2], df.iloc[i-1], df.iloc[i]
        if not check_displacement_validity(c1, c2):
            continue
        if c1["high"] < c3["low"]:
            gap    = c3["low"] - c1["high"]
            fvg_50 = c1["high"] + gap * 0.5
            fvgs.append(("BULLISH_FVG", c1["high"], c3["low"], i, c2, fvg_50))
        if c3["high"] < c1["low"]:
            gap    = c1["low"] - c3["high"]
            fvg_50 = c1["low"] - gap * 0.5
            fvgs.append(("BEARISH_FVG", c3["high"], c1["low"], i, c2, fvg_50))
    return fvgs[-3:] if fvgs else []


# =======================================================
#  SECTION 6 — INDICATORS
# =======================================================

def calculate_adx_wilder(df: pd.DataFrame, period: int = 14):
    """Wilder's ADX. Returns (adx, plus_di, minus_di). Ingwe mode only."""
    if df is None or len(df) < period * 2:
        return None, None, None
    high_diff  = df["high"].diff()
    low_diff   = -df["low"].diff()
    plus_dm    = np.where((high_diff > low_diff) & (high_diff > 0), high_diff, 0.0)
    minus_dm   = np.where((low_diff > high_diff) & (low_diff  > 0), low_diff,  0.0)
    prev_close = df["close"].shift(1)
    tr = np.maximum(
        df["high"] - df["low"],
        np.maximum(np.abs(df["high"] - prev_close), np.abs(df["low"] - prev_close))
    )
    tr_sum = float(pd.Series(tr).tail(period).sum())
    if tr_sum == 0:
        return 0.0, 0.0, 0.0
    plus_di  = 100.0 * float(np.sum(plus_dm[-period:]))  / tr_sum
    minus_di = 100.0 * float(np.sum(minus_dm[-period:])) / tr_sum
    di_sum   = plus_di + minus_di
    dx       = 100.0 * abs(plus_di - minus_di) / di_sum if di_sum != 0 else 0.0
    return round(dx, 1), round(plus_di, 1), round(minus_di, 1)


def calculate_atr(df: pd.DataFrame, period: int = 14) -> float | None:
    """ATR used by both modes for stop sizing."""
    if df is None or len(df) < period:
        return None
    high, low, close = df["high"].values, df["low"].values, df["close"].values
    tr    = np.zeros(len(df))
    tr[0] = high[0] - low[0]
    for i in range(1, len(df)):
        tr[i] = max(high[i]-low[i], abs(high[i]-close[i-1]), abs(low[i]-close[i-1]))
    return float(np.mean(tr[-period:]))


# =======================================================
#  SECTION 7 — FILTERS
# =======================================================

def get_current_session() -> str | None:
    hour = now_sast().hour
    for session, (s, e) in get_active_killzones().items():
        if s <= hour < e:
            return session
    return None


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
    """ICT canonical 50% midpoint threshold."""
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


def check_pre_trade_spread() -> bool:
    spread = get_spread()
    if spread is None:
        log("Spread unavailable.", "WARN")
        return False
    if spread > MIN_SPREAD_PIPS * 2:
        log(f"Spread too wide: {spread*10000:.1f}p.", "GUARD")
        return False
    return True


# =======================================================
#  SECTION 8 — POSITION SIZING
# =======================================================

def calculate_lot_size(atr: float | None = None) -> float:
    account     = mt5.account_info()
    symbol_info = mt5.symbol_info(SYMBOL)
    if account is None or symbol_info is None:
        return 0.01
    if not atr:
        atr = 0.0005
    risk    = account.equity * (RISK_PERCENT / 100)
    stop    = atr * ATR_MULTIPLIER
    lot     = risk / (stop * 100000)
    return round(min(max(lot, 0.01), HARD_LOT_CAP), 2)


def get_overlap_multiplier() -> float:
    """1.2x boost during London/NY overlap. Ingwe mode only."""
    hour = now_sast().hour
    return 1.2 if (16 <= hour < 19 if not is_eu_summer() else 15 <= hour < 18) else 1.0


# =======================================================
#  SECTION 9 — CONFLUENCE SCORING (INGWE MODE)
# =======================================================

def get_confluence_threshold(adx: float) -> int:
    if adx is None or adx < ADX_MIN_THRESHOLD:  return 80
    if adx > 40:                                 return 60
    return 70


def calculate_confluence_score(trend, fvg_ok, zone_ok, spread_ok, adx_ok) -> int:
    score = 0
    if trend in ("BULLISH", "BEARISH"):  score += 40
    if fvg_ok:                           score += 30
    if zone_ok:                          score += 20
    if spread_ok:                        score += 10
    return score


# =======================================================
#  SECTION 10 — TRADE EXECUTION & LOGGING
# =======================================================

def log_trade(direction, entry, sl, tp, result, lot_size, session):
    trade_log = []
    if os.path.exists(LOG_FILE):
        try:
            with open(LOG_FILE, "r") as f:
                trade_log = json.load(f)
        except json.JSONDecodeError:
            pass
    trade_log.append({
        "time":        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "sast":        now_sast().strftime("%Y-%m-%d %H:%M:%S"),
        "strategy":    STRATEGY,
        "market_mode": "summer" if is_eu_summer() else "winter",
        "session":     session,
        "direction":   direction,
        "entry":       entry,
        "sl":          sl,
        "tp":          tp,
        "lot_size":    lot_size,
        "retcode":     result.retcode,
        "comment":     getattr(result, "comment", "")
    })
    with open(LOG_FILE, "w") as f:
        json.dump(trade_log, f, indent=2)
    log(f"Trade logged → {LOG_FILE}", "TRADE")


def place_trade(direction, entry, sl, tp, lot_size):
    return mt5.order_send({
        "action":       mt5.TRADE_ACTION_DEAL,
        "symbol":       SYMBOL,
        "volume":       lot_size,
        "type":         mt5.ORDER_TYPE_BUY if direction == "BUY" else mt5.ORDER_TYPE_SELL,
        "price":        entry,
        "sl":           sl,
        "tp":           tp,
        "deviation":    10,
        "magic":        234000,
        "comment":      f"Ingwe v3.0 {STRATEGY[:2]} — Vuka",
        "type_time":    mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_FOK,
    })


# =======================================================
#  SECTION 11 — DAILY RESET
# =======================================================

def reset_daily_sessions():
    global consecutive_losses, sessions_traded_today
    local = now_sast()
    if local.hour == 0 and local.minute < 15 and sessions_traded_today:
        sessions_traded_today.clear()
        save_sessions(sessions_traded_today)
        consecutive_losses = 0
        log("Midnight reset — sessions and loss counter cleared.")


# =======================================================
#  SECTION 12A — SETUP EVALUATION: INGWE MODE
# =======================================================

def evaluate_ingwe(df, fvgs, sweep, sweep_level, price, atr, lot_size, session):
    """
    Full multi-confluence model.
    Trend + ADX + Zone + Sweep + FVG → market price entry.
    """
    adx, plus_di, minus_di = calculate_adx_wilder(df)
    if adx is None:
        log("ADX unavailable.", "WARN")
        return
    log(f"ADX: {adx:.1f}  |  +DI: {plus_di:.1f}  |  -DI: {minus_di:.1f}")

    spread      = get_spread()
    spread_pips = spread * 10000 if spread else 0
    spread_ok   = spread is not None and spread < MIN_SPREAD_PIPS
    multiplier  = get_overlap_multiplier()
    if multiplier > 1.0:
        lot_size = min(round(lot_size * multiplier, 2), HARD_LOT_CAP)
        log(f"London/NY Overlap → Lot: {lot_size} (1.2x)")

    threshold = get_confluence_threshold(adx)
    log(f"Price: {price:.5f}  |  ATR: {atr:.5f}  |  "
        f"Lot: {lot_size}  |  Spread: {spread_pips:.1f}p  |  Threshold: {threshold}/100")

    trend = get_h1_trend()
    if not trend:
        log("H1 trend unclear.", "WARN")
        return
    log(f"H1 Trend: {trend}")

    for fvg_type, fvg_low, fvg_high, fvg_idx, ob, fvg_50 in fvgs:
        if check_panic_candle(df, atr):
            continue

        # BULLISH
        if sweep == "SWEEP_LOW" and fvg_type == "BULLISH_FVG" and trend == "BULLISH":
            if plus_di <= minus_di:
                log(f"DI filter: +DI({plus_di}) <= -DI({minus_di}). Skip.", "GUARD")
                continue
            if not check_premium_discount_zone(df, price, "BUY"):
                log("Not in discount zone. Skip.", "GUARD")
                continue
            score = calculate_confluence_score(trend, True, True, spread_ok, True)
            log(f"Confluence: {score}/100")
            if score < threshold:
                log(f"Score {score} < {threshold}. Waiting.", "GUARD")
                continue
            if not check_pre_trade_spread():
                continue
            stop  = atr * ATR_MULTIPLIER
            entry = round(price, 5)
            sl    = round(entry - stop, 5)
            tp    = round(entry + stop * RISK_REWARD_RATIO, 5)
            res   = place_trade("BUY", entry, sl, tp, lot_size)
            if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                log(f"BUY  Entry={entry}  SL={sl}  TP={tp}  Lot={lot_size}", "TRADE")
                log_trade("BUY", entry, sl, tp, res, lot_size, session)
                sessions_traded_today.add(session)
                save_sessions(sessions_traded_today)
            else:
                log(f"BUY FAILED. Code={res.retcode if res else 'N/A'}.", "ERROR")
            return

        # BEARISH
        if sweep == "SWEEP_HIGH" and fvg_type == "BEARISH_FVG" and trend == "BEARISH":
            if minus_di <= plus_di:
                log(f"DI filter: -DI({minus_di}) <= +DI({plus_di}). Skip.", "GUARD")
                continue
            if not check_premium_discount_zone(df, price, "SELL"):
                log("Not in premium zone. Skip.", "GUARD")
                continue
            score = calculate_confluence_score(trend, True, True, spread_ok, True)
            log(f"Confluence: {score}/100")
            if score < threshold:
                log(f"Score {score} < {threshold}. Waiting.", "GUARD")
                continue
            if not check_pre_trade_spread():
                continue
            stop  = atr * ATR_MULTIPLIER
            entry = round(price, 5)
            sl    = round(entry + stop, 5)
            tp    = round(entry - stop * RISK_REWARD_RATIO, 5)
            res   = place_trade("SELL", entry, sl, tp, lot_size)
            if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                log(f"SELL  Entry={entry}  SL={sl}  TP={tp}  Lot={lot_size}", "TRADE")
                log_trade("SELL", entry, sl, tp, res, lot_size, session)
                sessions_traded_today.add(session)
                save_sessions(sessions_traded_today)
            else:
                log(f"SELL FAILED. Code={res.retcode if res else 'N/A'}.", "ERROR")
            return

    log("Ingwe conditions not aligned. Waiting...")


# =======================================================
#  SECTION 12B — SETUP EVALUATION: SILVER BULLET MODE
# =======================================================

def evaluate_silver_bullet(df, fvgs, sweep, sweep_level, price, atr, lot_size, window):
    """
    ICT Silver Bullet — time-precision model.
    No trend filter. No ADX. No zone filter.
    The 1-hour window is the confluence.

    Entry: price retrace to at or below/above the 50% FVG level.
    SL:    beyond sweep level + ATR buffer.
    TP:    1:3 from entry.
    """
    spread      = get_spread()
    spread_pips = spread * 10000 if spread else 0
    log(f"Price: {price:.5f}  |  ATR: {atr:.5f}  |  "
        f"Lot: {lot_size}  |  Spread: {spread_pips:.1f}p")

    for fvg_type, fvg_low, fvg_high, fvg_idx, ob, fvg_50 in fvgs:
        if check_panic_candle(df, atr):
            continue

        # ── BULLISH SILVER BULLET ────────────────────────
        # Sweep LOW → BULLISH FVG → price retraces to fvg_50
        if sweep == "SWEEP_LOW" and fvg_type == "BULLISH_FVG":
            log(f"SB Bullish FVG: {fvg_low:.5f}–{fvg_high:.5f}  |  50%: {fvg_50:.5f}")
            if price > fvg_high:
                log(f"Price above FVG — waiting for retracement into gap.", "GUARD")
                continue
            if price > fvg_50:
                log(f"Price in FVG but above 50% ({fvg_50:.5f}) — waiting deeper.", "GUARD")
                continue
            if not check_pre_trade_spread():
                continue
            entry   = round(price, 5)
            sl      = round(sweep_level - atr * ATR_MULTIPLIER, 5)
            sl_dist = abs(entry - sl)
            tp      = round(entry + sl_dist * RISK_REWARD_RATIO, 5)
            res     = place_trade("BUY", entry, sl, tp, lot_size)
            if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                log(f"SB BUY  Entry={entry}  SL={sl}  TP={tp}  Lot={lot_size}", "TRADE")
                log_trade("BUY", entry, sl, tp, res, lot_size, window)
                sessions_traded_today.add(window)
                save_sessions(sessions_traded_today)
            else:
                log(f"SB BUY FAILED. Code={res.retcode if res else 'N/A'}.", "ERROR")
            return

        # ── BEARISH SILVER BULLET ────────────────────────
        # Sweep HIGH → BEARISH FVG → price retraces to fvg_50
        if sweep == "SWEEP_HIGH" and fvg_type == "BEARISH_FVG":
            log(f"SB Bearish FVG: {fvg_low:.5f}–{fvg_high:.5f}  |  50%: {fvg_50:.5f}")
            if price < fvg_low:
                log(f"Price below FVG — waiting for retracement into gap.", "GUARD")
                continue
            if price < fvg_50:
                log(f"Price in FVG but below 50% ({fvg_50:.5f}) — waiting deeper.", "GUARD")
                continue
            if not check_pre_trade_spread():
                continue
            entry   = round(price, 5)
            sl      = round(sweep_level + atr * ATR_MULTIPLIER, 5)
            sl_dist = abs(sl - entry)
            tp      = round(entry - sl_dist * RISK_REWARD_RATIO, 5)
            res     = place_trade("SELL", entry, sl, tp, lot_size)
            if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                log(f"SB SELL  Entry={entry}  SL={sl}  TP={tp}  Lot={lot_size}", "TRADE")
                log_trade("SELL", entry, sl, tp, res, lot_size, window)
                sessions_traded_today.add(window)
                save_sessions(sessions_traded_today)
            else:
                log(f"SB SELL FAILED. Code={res.retcode if res else 'N/A'}.", "ERROR")
            return

    log("SB: No valid FVG retracement. Ingwe waits...")


# =======================================================
#  SECTION 12 — MAIN SCAN LOOP
# =======================================================

def run_agent():
    sast_now = now_sast()
    mkt_mode = "SUMMER" if is_eu_summer() else "WINTER"

    print(f"\n{'─' * 60}")
    log(f"Scan: {sast_now.strftime('%Y-%m-%d %H:%M')} SAST  "
        f"| {STRATEGY}  | {mkt_mode}  | Exness UTC+{get_exness_server_offset()}")
    print(f"{'─' * 60}")

    reset_daily_sessions()

    if not is_market_open():
        log(f"Weekend — market closed ({sast_now.strftime('%A')}). Ingwe sleeps.")
        return
    if check_equity_drawdown():
        return
    if check_consecutive_losses():
        log("2 consecutive losses — Ingwe pauses.", "GUARD")
        return
    if is_in_news_blackout():
        log("News blackout — Ingwe waits...")
        return

    daily_pnl = get_daily_pnl()
    log(f"Daily P&L: {daily_pnl:.2f} USC")
    if daily_pnl <= -MAX_DAILY_LOSS:
        log(f"Daily loss limit reached. Ingwe rests.", "GUARD")
        return

    # ── ACTIVE WINDOW (strategy-aware) ──────────────────
    if STRATEGY == "SILVER_BULLET":
        active = get_current_sb_window()
        if not active:
            log("No Silver Bullet window active. Ingwe watches...")
            return
        s, e = get_active_sb_windows()[active]
        log(f"SB WINDOW: {active} ({s:02d}:00–{e:02d}:00 SAST)")
    else:
        active = get_current_session()
        if not active:
            log("No killzone active. Ingwe watches...")
            return
        s, e = get_active_killzones()[active]
        log(f"KILLZONE: {active} ({s:02d}:00–{e:02d}:00 SAST)")

    if active in sessions_traded_today:
        log(f"Already traded {active} today. Ingwe waits.")
        return

    # ── CANDLES ──────────────────────────────────────────
    df = get_candles()
    if not validate_candles(df):
        log("Data validation failed. Ingwe will not trade on uncertain ground.", "GUARD")
        return

    # ── SWEEP ────────────────────────────────────────────
    sweep, sweep_level = detect_liquidity_sweep(df)
    if not sweep:
        log("No sweep detected. Ingwe waits...")
        return
    log(f"SWEEP: {sweep} at {sweep_level:.5f}")

    # ── FVG ──────────────────────────────────────────────
    fvgs = detect_fvg(df)
    if not fvgs:
        log("No FVG confirmed. Ingwe waits...")
        return

    # ── TICK ─────────────────────────────────────────────
    tick = mt5.symbol_info_tick(SYMBOL)
    if tick is None:
        log("Tick unavailable.", "WARN")
        return
    price = tick.bid

    atr = calculate_atr(df, ATR_PERIOD)
    if atr is None:
        log("ATR unavailable.", "WARN")
        return

    lot_size = calculate_lot_size(atr)

    # ── STRATEGY BRANCH ──────────────────────────────────
    if STRATEGY == "SILVER_BULLET":
        evaluate_silver_bullet(df, fvgs, sweep, sweep_level, price, atr, lot_size, active)
    else:
        evaluate_ingwe(df, fvgs, sweep, sweep_level, price, atr, lot_size, active)


# =======================================================
#  SECTION 13 — BOOT SEQUENCE
# =======================================================

if __name__ == "__main__":

    summer = is_eu_summer()
    print("=" * 60)
    print("   PROJECT VUKA — AGENT INGWE  v3.0")
    print("   The leopard does not miss because it does not rush.")
    print("=" * 60)
    print()
    print(f"   Location:       South Africa (SAST = UTC+2, no DST)")
    print(f"   Broker:         Exness MT5  (USC cent account)")
    print(f"   Strategy:       {STRATEGY}")
    print(f"   Market mode:    {'SUMMER (EU DST active)' if summer else 'WINTER (EU standard time)'}")
    print(f"   Exness server:  UTC+{get_exness_server_offset()}")
    print()

    if STRATEGY == "SILVER_BULLET":
        print("   ACTIVE SILVER BULLET WINDOWS (SAST):")
        for name, (s, e) in get_active_sb_windows().items():
            # show the NY equivalent
            ny_offset = -5 if not summer else -4
            ny_s = (s + ny_offset) % 24
            ny_e = (e + ny_offset) % 24
            print(f"     {name:<14} {s:02d}:00–{e:02d}:00 SAST   ({ny_s:02d}:00–{ny_e:02d}:00 NY)")
    else:
        print("   ACTIVE KILLZONES (SAST):")
        for name, (s, e) in get_active_killzones().items():
            print(f"     {name:<18} {s:02d}:00–{e:02d}:00")
    print()

    if not mt5.initialize():
        log(f"MT5 FAILED. Error: {mt5.last_error()}", "ERROR")
        exit(1)

    log("MT5 connected.")
    mt5.symbol_select(SYMBOL, True)
    sessions_traded_today = load_sessions()
    get_initial_equity()

    log(f"Sessions traded today:  {sessions_traded_today or 'none'}")
    log(f"Risk per trade:         {RISK_PERCENT}%")
    log(f"Hard lot cap:           {HARD_LOT_CAP} lots")
    log(f"Scan interval:          {SCAN_INTERVAL_SEC // 60} minutes")
    log(f"Ingwe is awake. Hunting in {STRATEGY} mode.\n")

    try:
        while True:
            run_agent()
            time.sleep(SCAN_INTERVAL_SEC)
    except KeyboardInterrupt:
        log("Keyboard interrupt. Ingwe stands down gracefully.")
    finally:
        mt5.shutdown()
        log("MT5 disconnected. Until next sunrise.")