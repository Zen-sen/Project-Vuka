import MetaTrader5 as mt5
import hashlib
import json
from datetime import datetime, timedelta
import os
from pathlib import Path

# ── PATHS ─────────────────────────────────────────────────────
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
SESSION_PERFORMANCE_FILE = _PROJECT_ROOT / "session_performance.json"


# ── SYMBOL MAP ────────────────────────────────────────────────
_SYMBOL_MAP = {
    "EURUSD": "EURUSDc",
    "GBPUSD": "GBPUSDc",
    "USDJPY": "USDJPYc",
    "BTCUSD": "BTCUSDc",
}


# ── MAGIC NUMBER DERIVATION ─────────────────────────────────────
def _derive_magic(tag: str) -> int:
    """
    Deterministic magic number from instance tag using SHA-256.
    Stable across restarts -- hash() randomises per process in Python 3.3+.
    Range: 234000-244000. Each instance tag maps to exactly one value.
    """
    digest = hashlib.sha256(tag.encode()).hexdigest()
    return int(digest[:8], 16) % 10000 + 234000


# ── SESSION PERFORMANCE (data-driven, calibrator-updated) ──────
# Defaults used ONLY when session_performance.json is missing.
# The calibrator (Step 3) rewrites that file after each N trades, so
# these numbers drift with live performance instead of going stale.
_DEFAULT_SESSION_PERFORMANCE = {
    "asian": {"buy": 0.29, "sell": 1.00},
    "london": {"buy": 0.14, "sell": 0.00},
    "new york": {"buy": 0.00, "sell": 0.58},
}

_DEFAULT_SESSION_ASYMMETRY_BONUS = {
    ("asian", "sell"): 10,
    ("new york", "sell"): 10,
}


def load_session_performance() -> tuple[dict, dict]:
    """
    Load (session_performance, session_asymmetry_bonus) from JSON.

    The JSON file uses string keys ("asian:sell") because tuples are not
    JSON-serialisable; the loader rehydrates them into ("asian", "sell").
    Falls back to the hardcoded defaults if the file is missing/corrupt.
    """
    try:
        if SESSION_PERFORMANCE_FILE.exists():
            raw = json.loads(SESSION_PERFORMANCE_FILE.read_text(encoding="utf-8"))
            perf = raw.get("session_performance") or _DEFAULT_SESSION_PERFORMANCE
            asym_raw = raw.get("session_asymmetry_bonus") or {}
            asym = {}
            for key, value in asym_raw.items():
                parts = key.split(":")
                if len(parts) == 2:
                    asym[(parts[0].strip().lower(), parts[1].strip().lower())] = int(value)
            if not asym:
                asym = dict(_DEFAULT_SESSION_ASYMMETRY_BONUS)
            return perf, asym
    except Exception as e:
        print(f"[config] Failed to load session performance table: {e}")
    return dict(_DEFAULT_SESSION_PERFORMANCE), dict(_DEFAULT_SESSION_ASYMMETRY_BONUS)


def save_session_performance(perf: dict | None = None, asym: dict | None = None) -> Path:
    """
    Persist the session performance table for the calibrator (Step 3).
    ``perf`` / ``asym`` default to the currently-loaded tables.
    """
    perf = perf if perf is not None else SESSION_PERFORMANCE
    asym = asym if asym is not None else SESSION_ASYMMETRY_BONUS
    data = {
        "session_performance": perf,
        "session_asymmetry_bonus": {":".join(k): v for k, v in asym.items()},
    }
    SESSION_PERFORMANCE_FILE.write_text(
        json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return SESSION_PERFORMANCE_FILE


SESSION_PERFORMANCE, SESSION_ASYMMETRY_BONUS = load_session_performance()


# ── CONFLUENCE THRESHOLDS (ICT rationale) ──────────────────────
# ADX > ADX_STRONG_TREND means the trend is strong and setups are
# more reliable, so we require LESS confluence (lower base threshold).
# ADX in the 25-40 band is trending but unreliable -- require more.
BASE_THRESHOLD_STRONG_TREND = 60  # ADX > 40: strong trend, setups more reliable
BASE_THRESHOLD_DEFAULT = 70       # ADX 25-40: require higher confluence
ADX_STRONG_TREND = 40             # ADX above this = strong directional trend
ADX_BELOW_MIN_RETURN = 80         # ADX < min: block unless extremely high score
ADX_MIN_THRESHOLD = 25            # default ADX gate; per-instance value comes from load_config()
MAX_CONFLUENCE_SCORE = 120        # hard ceiling on calculate_confluence_score()


# ── CONFIG LOADER ──────────────────────────────────────────────
def load_config(symbol: str, strategy: str, instance_tag: str, arg_symbol: str) -> dict:
    """
    Build the strategy/symbol config as a plain dict.

    No module-level globals are mutated -- each caller owns the returned
    dict (bot.py stores it on the shared state). This removes the
    import-order/None-globals and multi-bot cross-contamination bugs.
    """
    if arg_symbol == "BTCUSD":
        return {
            "TIMEFRAME": mt5.TIMEFRAME_M1,
            "RISK_PERCENT": 1.0,
            "RISK_REWARD_RATIO": 3.5,
            "ATR_PERIOD": 14,
            "ATR_MULTIPLIER": 1.5,
            "MIN_SL_ATR_MULTIPLIER": 0.5,
            "LIMIT_ORDER_EXPIRY_CANDLES": 4,
            "ADX_PERIOD": 14,
            "ADX_MIN_THRESHOLD": 25,
            "MIN_SPREAD_PIPS": 1.0,
            "MAX_DAILY_LOSS": 50.0,
            "MAX_DRAWDOWN_PCT": 10.0,
            "MAX_CONSECUTIVE_LOSSES": 3,
            "HARD_LOT_CAP": 0.20,
            "SCAN_INTERVAL_SEC": 60,
            "DATA_STALE_MINUTES": 5,
            "DATA_STALE_MINUTES_ASIAN": 10,
            "MT5_RETRY_ATTEMPTS": 3,
            "MT5_RETRY_DELAY_SEC": 10,
        }
    if strategy == "ICT_M1":
        return {
            "TIMEFRAME": mt5.TIMEFRAME_M1,
            "RISK_PERCENT": 1.0,
            "RISK_REWARD_RATIO": 2.0,
            "ATR_PERIOD": 14,
            "ATR_MULTIPLIER": 1.0,
            "MIN_SL_ATR_MULTIPLIER": 0.5,
            "LIMIT_ORDER_EXPIRY_CANDLES": 4,
            "ADX_PERIOD": 14,
            "ADX_MIN_THRESHOLD": 25,
            "MIN_SPREAD_PIPS": 0.0002,
            "MAX_DAILY_LOSS": 50.0,
            "HARD_LOT_CAP": 0.20,
            "SCAN_INTERVAL_SEC": 60,
            "DATA_STALE_MINUTES": 5,
            "DATA_STALE_MINUTES_ASIAN": 10,
            "MT5_RETRY_ATTEMPTS": 3,
            "MT5_RETRY_DELAY_SEC": 10,
        }
    if strategy == "SILVER_BULLET":
        return {
            "TIMEFRAME": mt5.TIMEFRAME_M15,
            "RISK_PERCENT": 1.0,
            "RISK_REWARD_RATIO": 2.0,
            "ATR_PERIOD": 14,
            "ATR_MULTIPLIER": 1.5,
            "MIN_SL_ATR_MULTIPLIER": 0.8,
            "LIMIT_ORDER_EXPIRY_CANDLES": 4,
            "ADX_PERIOD": 14,
            "ADX_MIN_THRESHOLD": 25,
            "MIN_SPREAD_PIPS": 0.0002,
            "MAX_DAILY_LOSS": 50.0,
            "MAX_DRAWDOWN_PCT": 10.0,
            "MAX_CONSECUTIVE_LOSSES": 3,
            "HARD_LOT_CAP": 0.20,
            "SCAN_INTERVAL_SEC": 60,
            "DATA_STALE_MINUTES": 5,
            "DATA_STALE_MINUTES_ASIAN": 10,
            "MT5_RETRY_ATTEMPTS": 3,
            "MT5_RETRY_DELAY_SEC": 10,
        }
    if strategy == "LONDON_OPEN":
        return {
            "TIMEFRAME": mt5.TIMEFRAME_M15,
            "RISK_PERCENT": 1.0,
            "RISK_REWARD_RATIO": 2.5,
            "ATR_PERIOD": 14,
            "ATR_MULTIPLIER": 2.0,
            "MIN_SL_ATR_MULTIPLIER": 1.0,
            "LIMIT_ORDER_EXPIRY_CANDLES": 4,
            "ADX_PERIOD": 14,
            "ADX_MIN_THRESHOLD": 25,
            "MIN_SPREAD_PIPS": 0.0002,
            "MAX_DAILY_LOSS": 50.0,
            "MAX_DRAWDOWN_PCT": 10.0,
            "MAX_CONSECUTIVE_LOSSES": 3,
            "HARD_LOT_CAP": 0.20,
            "SCAN_INTERVAL_SEC": 900,
            "DATA_STALE_MINUTES": 30,
            "DATA_STALE_MINUTES_ASIAN": 90,
            "MT5_RETRY_ATTEMPTS": 3,
            "MT5_RETRY_DELAY_SEC": 30,
        }
    if arg_symbol in ("EURUSD", "USDJPY"):
        return {
            "TIMEFRAME": mt5.TIMEFRAME_M15,
            "RISK_PERCENT": 1.0,
            "RISK_REWARD_RATIO": 3.0,
            "ATR_PERIOD": 14,
            "ATR_MULTIPLIER": 3.0,
            "MIN_SL_ATR_MULTIPLIER": 0.8,
            "LIMIT_ORDER_EXPIRY_CANDLES": 4,
            "ADX_PERIOD": 14,
            "ADX_MIN_THRESHOLD": 20,
            "MIN_SPREAD_PIPS": 0.0002,
            "MAX_DAILY_LOSS": 50.0,
            "MAX_DRAWDOWN_PCT": 10.0,
            "MAX_CONSECUTIVE_LOSSES": 3,
            "HARD_LOT_CAP": 0.20,
            "SCAN_INTERVAL_SEC": 900,
            "DATA_STALE_MINUTES": 30,
            "DATA_STALE_MINUTES_ASIAN": 90,
            "MT5_RETRY_ATTEMPTS": 3,
            "MT5_RETRY_DELAY_SEC": 30,
        }
    # Fallback: any symbol/strategy not matched above
    return {
        "TIMEFRAME": mt5.TIMEFRAME_M15,
        "RISK_PERCENT": 1.0,
        "RISK_REWARD_RATIO": 3.0,
        "ATR_PERIOD": 14,
        "ATR_MULTIPLIER": 1.5,
        "MIN_SL_ATR_MULTIPLIER": 0.8,
        "LIMIT_ORDER_EXPIRY_CANDLES": 4,
        "ADX_PERIOD": 14,
        "ADX_MIN_THRESHOLD": 20,
        "MIN_SPREAD_PIPS": 0.0002,
        "MAX_DAILY_LOSS": 50.0,
        "MAX_DRAWDOWN_PCT": 10.0,
        "HARD_LOT_CAP": 0.20,
        "SCAN_INTERVAL_SEC": 900,
        "DATA_STALE_MINUTES": 30,
        "DATA_STALE_MINUTES_ASIAN": 90,
        "MT5_RETRY_ATTEMPTS": 3,
        "MT5_RETRY_DELAY_SEC": 30,
    }


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


def get_confluence_threshold(adx: float, session: str = "", direction: str = "",
                             adx_min_threshold: float = ADX_MIN_THRESHOLD) -> int:
    """
    Return the confluence score required to trade.

    ICT rationale for the base values:
      - ADX > 40: strong directional trend -- entries are more reliable,
        so a LOWER bar (60) is acceptable.
      - ADX 25-40: trending but noisy -- require MORE confluence (70).
      - ADX < min threshold: no trend -- 80 blocks unless the score is
        exceptionally high (reached via the session multiplier only).
    """
    if adx is None or adx < adx_min_threshold:
        return ADX_BELOW_MIN_RETURN

    base = BASE_THRESHOLD_DEFAULT
    if adx > ADX_STRONG_TREND:
        base = BASE_THRESHOLD_STRONG_TREND

    multiplier = get_session_multiplier(session, direction)
    return int(base * multiplier)


def calculate_confluence_score(trend, fvg_ok, zone_ok, spread_ok, adx_ok,
                                level_sweep: bool = False,
                                bos_aligned: bool = False,
                                htf_bias_ok: bool = False,
                                session: str = "",
                                direction: str = "") -> int:
    """
    v5.5: Rebalanced weights, asymmetry encoding added. Capped at 120.
      Trend +30 | FVG +30 | Zone +15 | Spread +10
      Key level sweep +5 | BOS +5 | HTF bias +15
      Session-direction asymmetry +10 (for high-probability pairs)

    The return is clamped to MAX_CONFLUENCE_SCORE (120) so a loaded
    asymmetry bonus can never push the score above the documented scale.
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
    return min(score, MAX_CONFLUENCE_SCORE)
