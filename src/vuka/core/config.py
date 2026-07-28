import MetaTrader5 as mt5
import hashlib
from datetime import datetime, timedelta
import os

# ── SYMBOL MAP ────────────────────────────────────────
_SYMBOL_MAP = {
    "EURUSD": "EURUSDc",
    "GBPUSD": "GBPUSDc",
    "USDJPY": "USDJPYc",
    "BTCUSD": "BTCUSDc",
}


# ── MAGIC NUMBER DERIVATION ─────────────────────────────
def _derive_magic(tag: str) -> int:
    """
    Deterministic magic number from instance tag using SHA-256.
    Stable across restarts -- hash() randomises per process in Python 3.3+.
    Range: 234000-244000. Each instance tag maps to exactly one value.
    """
    digest = hashlib.sha256(tag.encode()).hexdigest()
    return int(digest[:8], 16) % 10000 + 234000


# ── CONFIG LOADER ────────────────────────────────────────
# Module-level globals set at runtime by load_config().
# Kept so that legacy functions (e.g. confluence scoring) can
# reference them directly without parameter changes.
TIMEFRAME                = None
RISK_PERCENT             = None
RISK_REWARD_RATIO        = None
ATR_PERIOD               = None
ATR_MULTIPLIER           = None
MIN_SL_ATR_MULTIPLIER    = None
LIMIT_ORDER_EXPIRY_CANDLES = None
ADX_PERIOD               = None
ADX_MIN_THRESHOLD        = None
MIN_SPREAD_PIPS          = None
MAX_DAILY_LOSS           = None
MAX_DRAWDOWN_PCT         = None
HARD_LOT_CAP             = None
SCAN_INTERVAL_SEC        = None
DATA_STALE_MINUTES       = None
DATA_STALE_MINUTES_ASIAN = None
MT5_RETRY_ATTEMPTS       = None
MT5_RETRY_DELAY_SEC      = None


def load_config(symbol: str, strategy: str, instance_tag: str, arg_symbol: str) -> dict:
    global TIMEFRAME, RISK_PERCENT, RISK_REWARD_RATIO
    global ATR_PERIOD, ATR_MULTIPLIER, MIN_SL_ATR_MULTIPLIER
    global LIMIT_ORDER_EXPIRY_CANDLES, ADX_PERIOD, ADX_MIN_THRESHOLD
    global MIN_SPREAD_PIPS, MAX_DAILY_LOSS, MAX_DRAWDOWN_PCT
    global HARD_LOT_CAP, SCAN_INTERVAL_SEC, DATA_STALE_MINUTES
    global DATA_STALE_MINUTES_ASIAN, MT5_RETRY_ATTEMPTS, MT5_RETRY_DELAY_SEC

    if arg_symbol == "BTCUSD":
        TIMEFRAME                = mt5.TIMEFRAME_M1
        RISK_PERCENT             = 1.0
        RISK_REWARD_RATIO        = 3.5
        ATR_PERIOD               = 14
        ATR_MULTIPLIER           = 1.5
        MIN_SL_ATR_MULTIPLIER    = 0.5
        LIMIT_ORDER_EXPIRY_CANDLES = 4
        ADX_PERIOD               = 14
        ADX_MIN_THRESHOLD        = 25
        MIN_SPREAD_PIPS          = 1.0
        MAX_DAILY_LOSS           = 50.0
        MAX_DRAWDOWN_PCT         = 10.0
        HARD_LOT_CAP             = 0.20
        SCAN_INTERVAL_SEC        = 60
        DATA_STALE_MINUTES       = 5
        DATA_STALE_MINUTES_ASIAN = 10
        MT5_RETRY_ATTEMPTS       = 3
        MT5_RETRY_DELAY_SEC      = 10
    elif strategy == "ICT_M1":
        TIMEFRAME                = mt5.TIMEFRAME_M1
        RISK_PERCENT             = 1.0
        RISK_REWARD_RATIO        = 2.0
        ATR_PERIOD               = 14
        ATR_MULTIPLIER           = 1.0
        MIN_SL_ATR_MULTIPLIER    = 0.5
        LIMIT_ORDER_EXPIRY_CANDLES = 4
        ADX_PERIOD               = 14
        ADX_MIN_THRESHOLD        = 25
        MIN_SPREAD_PIPS          = 0.0002
        MAX_DAILY_LOSS           = 50.0
        HARD_LOT_CAP             = 0.20
        SCAN_INTERVAL_SEC        = 60
        DATA_STALE_MINUTES       = 5
        DATA_STALE_MINUTES_ASIAN = 10
        MT5_RETRY_ATTEMPTS       = 3
        MT5_RETRY_DELAY_SEC      = 10
    elif strategy == "SILVER_BULLET":
        TIMEFRAME                = mt5.TIMEFRAME_M15
        RISK_PERCENT             = 1.0
        RISK_REWARD_RATIO        = 2.0
        ATR_PERIOD               = 14
        ATR_MULTIPLIER           = 1.5
        MIN_SL_ATR_MULTIPLIER    = 0.8
        LIMIT_ORDER_EXPIRY_CANDLES = 4
        ADX_PERIOD               = 14
        ADX_MIN_THRESHOLD        = 25
        MIN_SPREAD_PIPS          = 0.0002
        MAX_DAILY_LOSS           = 50.0
        MAX_DRAWDOWN_PCT         = 10.0
        HARD_LOT_CAP             = 0.20
        SCAN_INTERVAL_SEC        = 60
        DATA_STALE_MINUTES       = 5
        DATA_STALE_MINUTES_ASIAN = 10
        MT5_RETRY_ATTEMPTS       = 3
        MT5_RETRY_DELAY_SEC      = 10
    elif strategy == "LONDON_OPEN":
        TIMEFRAME                = mt5.TIMEFRAME_M15
        RISK_PERCENT             = 1.0
        RISK_REWARD_RATIO        = 2.5
        ATR_PERIOD               = 14
        ATR_MULTIPLIER           = 2.0
        MIN_SL_ATR_MULTIPLIER    = 1.0
        LIMIT_ORDER_EXPIRY_CANDLES = 4
        ADX_PERIOD               = 14
        ADX_MIN_THRESHOLD        = 25
        MIN_SPREAD_PIPS          = 0.0002
        MAX_DAILY_LOSS           = 50.0
        MAX_DRAWDOWN_PCT         = 10.0
        HARD_LOT_CAP             = 0.20
        SCAN_INTERVAL_SEC        = 900
        DATA_STALE_MINUTES       = 30
        DATA_STALE_MINUTES_ASIAN = 90
        MT5_RETRY_ATTEMPTS       = 3
        MT5_RETRY_DELAY_SEC      = 30
    elif arg_symbol in ("EURUSD", "USDJPY"):
        TIMEFRAME                = mt5.TIMEFRAME_M15
        RISK_PERCENT             = 1.0
        RISK_REWARD_RATIO        = 3.0
        ATR_PERIOD               = 14
        ATR_MULTIPLIER           = 3.0
        MIN_SL_ATR_MULTIPLIER    = 0.8
        LIMIT_ORDER_EXPIRY_CANDLES = 4
        ADX_PERIOD               = 14
        ADX_MIN_THRESHOLD        = 25
        MIN_SPREAD_PIPS          = 0.0002
        MAX_DAILY_LOSS           = 50.0
        MAX_DRAWDOWN_PCT         = 10.0
        HARD_LOT_CAP             = 0.20
        SCAN_INTERVAL_SEC        = 900
        DATA_STALE_MINUTES       = 30
        DATA_STALE_MINUTES_ASIAN = 90
        MT5_RETRY_ATTEMPTS       = 3
        MT5_RETRY_DELAY_SEC      = 30
    else:
        TIMEFRAME                = mt5.TIMEFRAME_M15
        RISK_PERCENT             = 1.0
        RISK_REWARD_RATIO        = 3.0
        ATR_PERIOD               = 14
        ATR_MULTIPLIER           = 1.5
        MIN_SL_ATR_MULTIPLIER    = 0.8
        LIMIT_ORDER_EXPIRY_CANDLES = 4
        ADX_PERIOD               = 14
        ADX_MIN_THRESHOLD        = 20
        MIN_SPREAD_PIPS          = 0.0002
        MAX_DAILY_LOSS           = 50.0
        MAX_DRAWDOWN_PCT         = 10.0
        HARD_LOT_CAP             = 0.20
        SCAN_INTERVAL_SEC        = 900
        DATA_STALE_MINUTES       = 30
        DATA_STALE_MINUTES_ASIAN = 90
        MT5_RETRY_ATTEMPTS       = 3
        MT5_RETRY_DELAY_SEC      = 30

    return {
        "TIMEFRAME": TIMEFRAME,
        "RISK_PERCENT": RISK_PERCENT,
        "RISK_REWARD_RATIO": RISK_REWARD_RATIO,
        "ATR_PERIOD": ATR_PERIOD,
        "ATR_MULTIPLIER": ATR_MULTIPLIER,
        "MIN_SL_ATR_MULTIPLIER": MIN_SL_ATR_MULTIPLIER,
        "LIMIT_ORDER_EXPIRY_CANDLES": LIMIT_ORDER_EXPIRY_CANDLES,
        "ADX_PERIOD": ADX_PERIOD,
        "ADX_MIN_THRESHOLD": ADX_MIN_THRESHOLD,
        "MIN_SPREAD_PIPS": MIN_SPREAD_PIPS,
        "MAX_DAILY_LOSS": MAX_DAILY_LOSS,
        "MAX_DRAWDOWN_PCT": MAX_DRAWDOWN_PCT,
        "HARD_LOT_CAP": HARD_LOT_CAP,
        "SCAN_INTERVAL_SEC": SCAN_INTERVAL_SEC,
        "DATA_STALE_MINUTES": DATA_STALE_MINUTES,
        "DATA_STALE_MINUTES_ASIAN": DATA_STALE_MINUTES_ASIAN,
        "MT5_RETRY_ATTEMPTS": MT5_RETRY_ATTEMPTS,
        "MT5_RETRY_DELAY_SEC": MT5_RETRY_DELAY_SEC,
    }


# =========================================================
# PATTERN BLACKLIST
# =========================================================
PATTERN_BLACKLIST = [
    ("Asian", "BUY", "SWEEP_LOW"),
]

# =========================================================
# BACKTEST MODE
# =========================================================
BACKTEST_MODE            = False
BACKTEST_CSV            = os.environ.get("BT_CSV", "eurusd_m15_march2026.csv")
BACKTEST_SPEED          = int(os.environ.get("BT_SPEED", "1"))

# =========================================================
# TIMEZONE -- South Africa (SAST = UTC+2, no DST)
# =========================================================
SA_OFFSET = 2

# =========================================================
# KILLZONES (SAST)
# =========================================================
KILLZONES_WINTER = {
    "Asian":         (2,  6),
    "London Open":   (10, 13),
    "New York Open": (16, 19),
    "London Close":  (19, 22),
}
KILLZONES_SUMMER = {
    "Asian":         (2,  6),
    "London Open":   (9,  12),
    "New York Open": (15, 18),
    "London Close":  (18, 21),
}

INGWE_BLACKOUTS_WINTER = [
    (8,  30,  9, 45),
    (13,  0, 15, 45),
]
INGWE_BLACKOUTS_SUMMER = [
    (7,  30,  8, 45),
    (12,  0, 14, 45),
]

# =========================================================
# SILVER BULLET -- WINDOWS (SAST)
# =========================================================
SB_WINDOWS_WINTER = {
    "SB_Window1": (10, 11),
    "SB_Window2": (17, 18),
    "SB_Window3": (21, 22),
}
SB_WINDOWS_SUMMER = {
    "SB_Window1": (9,  10),
    "SB_Window2": (16, 17),
    "SB_Window3": (20, 21),
}

SB_BLACKOUTS_WINTER = [
    (9,  45, 10,  0),
    (16, 45, 17,  0),
    (20, 45, 21,  0),
]
SB_BLACKOUTS_SUMMER = [
    (8,  45,  9,  0),
    (15, 45, 16,  0),
    (19, 45, 20,  0),
]

# =========================================================
# BTCUSD SCALPING KILLZONES / ICT M1 SESSIONS
# =========================================================
BTC_KILLZONES = {
    "Asian":     (2,  6),
    "London":   (9,  12),
    "NY_Open":  (15, 18),
    "NY_Session": (18, 22),
    "Late_NY":  (22, 2),
}

ICT_M1_SESSIONS = {
    "Asian":     (2,  6),
    "London":    (9, 12),
    "NY_Open":  (15, 18),
    "NY_Session": (18, 22),
    "Late_NY":  (22, 2),
}

# =========================================================
# SECTION 9 -- CONFLUENCE SCORING
# =========================================================

SESSION_PERFORMANCE = {
    "Asian": {"buy": 0.29, "sell": 1.00},
    "London": {"buy": 0.14, "sell": 0.00},
    "New York": {"buy": 0.00, "sell": 0.58},
}

SESSION_ASYMMETRY_BONUS = {
    ("asian", "sell"): 10,
    ("new york", "sell"): 10,
}


def get_session_multiplier(session: str, direction: str) -> float:
    """Return threshold multiplier based on historical session performance."""
    key = session.lower()
    if key not in SESSION_PERFORMANCE:
        return 1.0
    dir_key = direction.lower()
    wr = SESSION_PERFORMANCE.get(key, {}).get(dir_key, 0.5)
    if wr >= 0.60:
        return 0.9
    elif wr >= 0.40:
        return 1.0
    elif wr >= 0.25:
        return 1.15
    else:
        return 1.30


def get_confluence_threshold(adx: float, session: str = "", direction: str = "") -> int:
    if adx is None or adx < ADX_MIN_THRESHOLD:
        return 80

    base = 70
    if adx > 40:
        base = 60

    multiplier = get_session_multiplier(session, direction)
    return int(base * multiplier)


def calculate_confluence_score(trend, fvg_ok, zone_ok, spread_ok, adx_ok,
                                level_sweep: bool = False,
                                bos_aligned: bool = False,
                                htf_bias_ok: bool = False,
                                session: str = "",
                                direction: str = "") -> int:
    """
    v5.5: Rebalanced weights, asymmetry encoding added. Max score 120.
      Trend +30 | FVG +30 | Zone +15 | Spread +10
      Key level sweep +5 | BOS +5 | HTF bias +15
      Session-direction asymmetry +10 (for high-probability pairs)
    """
    score = 0
    if trend in ("BULLISH", "BEARISH"):
        score += 30
    if fvg_ok:
        score += 30
    if zone_ok:
        score += 15
    if spread_ok:
        score += 10
    if level_sweep:
        score += 5
    if bos_aligned:
        score += 5
    if htf_bias_ok:
        score += 15
    key = (session.lower().strip(), direction.lower().strip())
    if key in SESSION_ASYMMETRY_BONUS:
        score += SESSION_ASYMMETRY_BONUS[key]
    return score
