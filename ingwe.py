import MetaTrader5 as mt5
from datetime import datetime, timedelta, timezone
from pathlib import Path
import pandas as pd
import numpy as np
import time
import json
import os
import sys
import hashlib
import codecs
from typing import Optional


# ── ARGUMENT PARSING (Moved to top for Logger) ────────────────
_valid_symbols    = ("EURUSD", "GBPUSD", "USDJPY", "BTCUSD")
_valid_strategies = ("INGWE", "SILVER_BULLET", "ICT_M1", "LONDON_OPEN")

_arg_symbol   = sys.argv[1].upper() if len(sys.argv) > 1 else "EURUSD"
_arg_strategy = sys.argv[2].upper() if len(sys.argv) > 2 else "INGWE"

if _arg_symbol not in _valid_symbols:
    print(f"X Unknown symbol '{_arg_symbol}'. Use: {', '.join(_valid_symbols)}")
    sys.exit(1)
if _arg_strategy not in _valid_strategies:
    print(f"X Unknown strategy '{_arg_strategy}'. Use: {', '.join(_valid_strategies)}")
    sys.exit(1)

_instance_tag = f"{_arg_symbol}_{_arg_strategy}"

# Core infrastructure
from health_monitor import HealthMonitor
from kronos_guardian import KronosVetoGate, create_veto_gate
from database_manager import get_db
from unified_logger import get_logger
from memory_manager import MemoryManager
from skills.trading_governor import TradingGovernor
from skills.concept_tracker import record_concept_trade, record_concept_outcome

# Initialize Unified Logger
logger = get_logger(_instance_tag)

# Initialize Memory Manager
MEM_MGR = MemoryManager()

# Phase 1: Event-driven tick engine (replaces polling)
try:
    from tick_engine_v5 import TickEngine
    TICK_ENGINE_AVAILABLE = True
except ImportError:
    TICK_ENGINE_AVAILABLE = False

# Load config from file if available
CONFIG = {}
_config_path = Path("config_v4.6.json")
if _config_path.exists():
    try:
        with open(_config_path) as _f:
            CONFIG = json.load(_f)
    except Exception:
        pass

# Initialize Veto Gate
_veto_cfg = CONFIG.get("veto_gate", {})
_heartbeat_cfg = CONFIG.get("heartbeat", {"enabled": False, "interval_seconds": 60})
KRONOS_VETO_GATE = create_veto_gate({
    "enabled": _veto_cfg.get("enabled", True),
    "mode": _veto_cfg.get("mode", "warn"),
    "threshold": _veto_cfg.get("threshold", 0.30),
    "heartbeat_interval": _heartbeat_cfg.get("interval_seconds", 0) if _heartbeat_cfg.get("enabled", False) else 0,
})

# BUY threshold: lower bar for entry since Kronos is SELL-heavy
BUY_THRESHOLD = _veto_cfg.get("buy_threshold", 0.35)

# Initialize Trading Governor (P0-FULL filter system)
TRADING_GOVERNOR = TradingGovernor(CONFIG)

# Database manager for SQLite consolidation
try:
    DB = get_db()
    DB_AVAILABLE = True
except ImportError:
    DB = None
    DB_AVAILABLE = False
    logger.warn("database_manager not available -- falling back to JSON files")

# =======================================================
#    PROJECT VUKA -- AGENT INGWE  v5.5
#    The leopard does not miss because it does not rush.
#    "Ingwe ayidlozi ngoba ayiphuthi isikhathi."
# =======================================================
# CHANGELOG v5.5 -- AUDIT-DRIVEN FIXES + KRONOS TRAINING:
#
#   FIX-1: place_limit_order() datetime bug (CRITICAL).
#     Replaced timezone-aware datetime with _server_now()
#     naive datetime for MT5 compatibility. Expiry now uses
#     datetime object directly instead of Unix timestamp.
#     Ref: _server_now() at ingwe.py:626.
#
#   FIX-2: Scoring model rebalanced.
#     Trend weight reduced 40→30 (no longer dominant alone).
#     HTF bias increased 10→15. Zone reduced 20→15.
#     New: SESSION_ASYMMETRY_BONUS (+10) encodes the live
#     SELL/BUY asymmetry (~77% vs ~40%) directly into scores.
#
#   FIX-3: ADX backtest contamination eliminated.
#     backtester.py: random.uniform(10,40) fallback removed.
#     Candle skipped when ADX unavailable. Lookback window
#     changed from fixed 50 to proper expanding window.
#     run_backtest.py: cold-start ADX seed now uses Wilder
#     proper mean of first `period` DX values.
#
#   FIX-4: HTF bias backtest guard.
#     get_htf_bias() now returns None in BACKTEST_MODE
#     instead of making live MT5 API calls.
#
#   FIX-5: Kronos trained on ICT pattern sequence + timing.
#     New detect_pattern_sequence() in kronos_server.py
#     identifies the sweep→displacement→retracement wave.
#     get_killzone_quality() scores timing alignment.
#     Both modulate inference confidence in predict-ict.
#
#   FIX-6: Session-direction asymmetry encoded in context.
#     SESSION_ASYMMETRY_BONUS passed to Kronos via
#     confluence_score and dedicated context fields.
#
#   FIX-7: PATTERN_BLACKLIST inversion error (CRITICAL).
#     Removed ("Asian", "SELL", "SWEEP_HIGH") which was blocking
#     a 100% win-rate pattern. Comment said "keep enabled" but
#     blacklist logic was blocking it. Only ("Asian", "BUY", "SWEEP_LOW")
#     at 28% WR remains blocked.
#
#   FIX-8: ADX threshold unified.
#     Removed min_adx_hard_limit=10 magic number. Single ADX_MIN_THRESHOLD
#     from config used for both soft warning and hard block (was split across
#     two values doing different things in same function).
#
#   FIX-9: Kronos veto mode enforced.
#     config_v4.6.json mode changed from "warn" to "enforced".
#     Veto gate now actually blocks trades below confidence threshold.
#
#   FIX-10: Deleted tainted best_params.json (pre-fix optimizer run).
#     Recorded ADX=30, win_rate=76.1% from contaminated backtester.
#     File removed -- must not guide parameter selection.
#
#   FIX-11: Removed dead MIN_ADX_FOR_TRADING=25 constant.
#     Was defined at module level but never referenced in any
#     execution path. Single ADX_MIN_THRESHOLD is now the
#     sole ADX gate.
#
#   FIX-12: log_trade() now pulls actual fill from deal history.
#     Uses mt5.history_deals_get(ticket=result.deal) to get the
#     real executed price instead of result.price (which was None
#     on all 21 live trades). Slippage and effective RR now accurate.
#
# CHANGELOG v4.4 -- PERFORMANCE IMPROVEMENTS:
#
#   FIX-1: Duplicate entry prevention via has_open_position().
#     Prevents placing duplicate market orders when position exists.
#
#   FIX-2: Persistent consecutive loss tracking.
#     Loss counter now persists across days via sessions file.
#     Bot pauses after 2 consecutive losses.
#
#   FIX-3: GBPUSD London Open -- enabled both directions (guard removed per user request).
#
#   FIX-4: SL movement tracking added.
#     All trailing SL moves logged to sl_moves_{symbol}_{strategy}.json
#
# CHANGELOG v4.2 -- MARKET ORDERS REINSTATED:
#
#   v4.0 limit orders (FVG 50% midpoint) produced 0% win rate
#   across all instances. Static limit levels got filled into
#   reversals during range-bound conditions. Reverted to market
#   orders with the same confluence logic.
#
#   - All 4 evaluate_ingwe() paths use place_trade() (market).
#   - Entry price = current market price (bid), not fvg_50.
#   - Price-side guard removed -- no static level constraint.
#   - has_pending_order() guard removed -- market orders fill
#     instantly or fail; no duplicate risk.
#   - place_limit_order() kept for future use.
#
# CHANGELOG v4.1 -- LIMIT ORDER ENTRY AT FVG 50%:
#
#   FIX-1: place_limit_order() added to Section 10.
#   FIX-2: has_pending_order() guard added to Section 10.
#   FIX-3: evaluate_ingwe() -- all 4 paths converted to limit orders.
#
# CHANGELOG v3.9.5 -- TRAILING SL + COMMENT FIX (2 fixes):
#
#   FIX-1: Order comment truncated to 18 chars.
#     Exness enforces a 31-character hard cap on order
#     comments. "Ingwe v3.9.4 EURUSD_SILVER_BULLET" = 34
#     chars -- exceeded the limit, causing MT5 error -2
#     ('Invalid "comment" argument') on every order_send
#     attempt. Both Silver Bullet instances were unable to
#     execute any trade. Fixed to f"Ingwe_{_instance_tag[:14]}"
#     -- max 18 chars across all four instances.
#
#   FIX-2: manage_open_positions() -- trailing SL manager.
#     New function added to Section 10. Runs every scan
#     cycle before session logic, filtered by magic number.
#     At 1:1 profit -> SL moves to breakeven (entry).
#     At 1:2 profit -> SL moves to 1:1 (half target locked).
#     Worst case on any mature trade: secured half the RRR
#     minimum. Helper _modify_sl() handles MT5 SLTP modify
#     with full retcode logging.
#
# CHANGELOG v3.9.4 -- CODE REVIEW HARDENING (5 fixes).
# CHANGELOG v3.9.3 -- TRUE WILDER ADX SMOOTHING.
# CHANGELOG v3.9.2 -- ZONE CONTEXT LOGGING.
# CHANGELOG v3.9.1 -- ENTRY MODEL EXPANSION (HOTFIX).
# CHANGELOG v3.9 -- v3.9 PATCH BLOCK.
# CHANGELOG v3.8.1 -- MULTI-INSTANCE DAILY P&L FIX.
# CHANGELOG v3.8 -- ICT DEPTH UPGRADE.
# CHANGELOG v3.7 -- UNICORN ZONE INTEGRATION.
# CHANGELOG v3.6 -- THIRD CODE REVIEW HARDENING.
# CHANGELOG v3.5 -- SECOND CODE REVIEW HARDENING.
# CHANGELOG v3.4 -- CODE REVIEW HARDENING.
# CHANGELOG v3.3 -- SESSION ARCHITECTURE FIX.
# CHANGELOG v3.2 -- ORDER FILLING + STALE FVG FIX.
# CHANGELOG v3.1 -- MULTI-SYMBOL + MULTI-INSTANCE.
# CHANGELOG v2.x -- Oracle's Eye, persistence, logging, risk.
# =======================================================

# -------------------------------------------------------
# ── STRATEGY & SYMBOL SELECTOR ──────────────────────────
# (Arguments parsed at top for Logger initialization)
STRATEGY = _arg_strategy
_arg_check    = "--check" in sys.argv
_arg_backtest = "--backtest" in sys.argv
_arg_fast = "--fast" in sys.argv
_arg_test     = "--test" in sys.argv

_SYMBOL_MAP = {
    "EURUSD": "EURUSDc",
    "GBPUSD": "GBPUSDc",
    "USDJPY": "USDJPYc",
    "BTCUSD": "BTCUSDc",
}
SYMBOL = _SYMBOL_MAP[_arg_symbol]

_instance_tag   = f"{_arg_symbol}_{STRATEGY}"
_instance_short = f"{_arg_symbol[:3]}{'SB' if STRATEGY == 'SILVER_BULLET' else ('M1' if STRATEGY == 'ICT_M1' else ('LO' if STRATEGY == 'LONDON_OPEN' else 'IW'))}"

# Initialize Unified Logger
logger = get_logger(_instance_tag)

LOG_FILE        = f"trades_{_instance_tag}.json"
SESSIONS_FILE   = f"sessions_{_instance_tag}.json"

def _derive_magic(tag: str) -> int:
    """
    Deterministic magic number from instance tag using SHA-256.
    Stable across restarts -- hash() randomises per process in Python 3.3+.
    Range: 234000-244000. Each instance tag maps to exactly one value.
    """
    digest = hashlib.sha256(tag.encode()).hexdigest()
    return int(digest[:8], 16) % 10000 + 234000

_instance_magic = _derive_magic(_instance_tag)

# -------------------------------------------------------
# CONFIGURATION
# -------------------------------------------------------

if _arg_symbol == "BTCUSD":
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
elif STRATEGY == "ICT_M1":
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
elif STRATEGY == "SILVER_BULLET":
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
elif STRATEGY == "LONDON_OPEN":
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
elif _arg_symbol in ("EURUSD", "USDJPY"):
    TIMEFRAME                = mt5.TIMEFRAME_M15
    RISK_PERCENT             = 1.0
    RISK_REWARD_RATIO        = 3.0   # v5.0: Reduced from 3.5 for better win rate
    ATR_PERIOD               = 14
    ATR_MULTIPLIER           = 3.0
    MIN_SL_ATR_MULTIPLIER    = 0.8   # v5.0: Increased from 0.5 for more breathing room
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
    RISK_REWARD_RATIO        = 3.0   # v5.0: Reduced from 3.5 for better win rate
    ATR_PERIOD               = 14
    ATR_MULTIPLIER           = 1.5
    MIN_SL_ATR_MULTIPLIER    = 0.8   # v5.0: Increased from 0.5 for more breathing room
    LIMIT_ORDER_EXPIRY_CANDLES = 4
    ADX_PERIOD               = 14
    ADX_MIN_THRESHOLD        = 20    # Conservative middle ground -- avoids ranging markets where ICT setups fail
    MIN_SPREAD_PIPS          = 0.0002
    MAX_DAILY_LOSS           = 50.0
    MAX_DRAWDOWN_PCT         = 10.0
    HARD_LOT_CAP             = 0.20
    SCAN_INTERVAL_SEC        = 900
    DATA_STALE_MINUTES       = 30
    DATA_STALE_MINUTES_ASIAN = 90
    MT5_RETRY_ATTEMPTS       = 3
    MT5_RETRY_DELAY_SEC      = 30

# =======================================================
# PATTERN BLACKLIST (Based on Backtest Analysis)
# =======================================================
# Win rate <35% patterns - auto-blocked to prevent losses
PATTERN_BLACKLIST = [
    ("Asian", "BUY", "SWEEP_LOW"),       # 28% WR -- block
]

# -------------------------------------------------------
# BACKTEST MODE (Option B) -- CSV Replay
# -------------------------------------------------------
BACKTEST_MODE            = False
BACKTEST_CSV            = os.environ.get("BT_CSV", "eurusd_m15_march2026.csv")
BACKTEST_SPEED          = int(os.environ.get("BT_SPEED", "1"))
_backtest_index         = 0
_backtest_data          = None

# -------------------------------------------------------
# TIMEZONE -- South Africa
# SAST = UTC+2, permanently. No DST. Ever.
# -------------------------------------------------------
SA_OFFSET = 2

# -------------------------------------------------------
# INGWE -- KILLZONES (SAST)
# -------------------------------------------------------
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

# -------------------------------------------------------
# SILVER BULLET -- WINDOWS (SAST)
# -------------------------------------------------------
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

# -------------------------------------------------------
# BTCUSD SCALPING KILLZONES (M1) - 24/7 active
# -------------------------------------------------------
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

# -------------------------------------------------------
# GLOBALS
# -------------------------------------------------------
initial_equity        = None
consecutive_losses    = 0
sessions_traded_today = set()

# HTF bias cache — refreshed at most once per hour to prevent MT5 throttling
_htf_bias_cache: dict = {"bias": None, "timestamp": 0.0}


# =======================================================
#  SECTION 1 -- UTILITIES & TIMEZONE
# =======================================================

def log(msg: str, level: str = "INFO"):
    """Wrapper for UnifiedLogger to maintain compatibility with existing calls."""
    logger.log(level=level, message=msg, symbol=_arg_symbol, strategy=STRATEGY)


def get_last_sunday(year: int, month: int) -> datetime:
    if month == 12:
        last_day = datetime(year + 1, 1, 1) - timedelta(days=1)
    else:
        last_day = datetime(year, month + 1, 1) - timedelta(days=1)
    while last_day.weekday() != 6:
        last_day -= timedelta(days=1)
    return last_day


def is_eu_summer() -> bool:
    today = datetime.now()
    return get_last_sunday(today.year, 3) <= today <= get_last_sunday(today.year, 10)


def get_exness_server_offset() -> int:
    return 3 if is_eu_summer() else 2


def now_sast() -> datetime:
    return datetime.now(timezone.utc) + timedelta(hours=SA_OFFSET)


def update_memory():
    """
    Synchronizes current bot state to Memory.md and handover.json.
    """
    try:
        account = mt5.account_info()
        equity = account.equity if account else 0.0
        
        now = now_sast()
        today = now.strftime("%Y-%m-%d")
        
        # 1. Update Memory.md
        state_data = {
            "Current State": {
                "last_updated": now.isoformat(),
                "active_instances": [_instance_tag],
                "current_equity": equity,
                "bot_status": "RUNNING",
                "environment": "LIVE"
            },
            "Today's Stats": {
                "date": today,
                "daily_pnl": get_daily_pnl(),
                "sessions_traded": list(sessions_traded_today)
            },
            "Recent Activity": {
                "last_scan": now.strftime("%Y-%m-%d %H:%M:%S")
            }
        }
        MEM_MGR.update_state(state_data)
        
        # 2. Update handover.json
        handover = {
            "bot": "Ingwe",
            "instance": _instance_tag,
            "last_updated": now.isoformat(),
            "equity": equity,
            "daily_pnl": get_daily_pnl(),
            "active_sessions": list(sessions_traded_today),
            "status": "HEALTHY"
        }
        with open("handover.json", "w") as f:
            json.dump(handover, f, indent=2)
            
    except Exception as e:
        log(f"Memory sync failed: {e}", "WARN")


def get_active_killzones() -> dict:

    if STRATEGY == "ICT_M1":
        return ICT_M1_SESSIONS
    if _arg_symbol == "BTCUSD":
        return BTC_KILLZONES
    return KILLZONES_SUMMER if is_eu_summer() else KILLZONES_WINTER


def get_active_sb_windows() -> dict:
    return SB_WINDOWS_SUMMER if is_eu_summer() else SB_WINDOWS_WINTER


def get_active_blackouts() -> list:
    if STRATEGY == "SILVER_BULLET":
        return SB_BLACKOUTS_SUMMER if is_eu_summer() else SB_BLACKOUTS_WINTER
    return INGWE_BLACKOUTS_SUMMER if is_eu_summer() else INGWE_BLACKOUTS_WINTER


def is_market_open() -> bool:
    if STRATEGY == "ICT_M1" or _arg_symbol == "BTCUSD":
        return True  # M1 scalping + Crypto trade 24/7
    return now_sast().weekday() not in (5, 6)


# =======================================================
#  SECTION 2 -- DATA INTEGRITY (THE ORACLE'S EYE)
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


def is_data_fresh(df: pd.DataFrame, session: Optional[str] = None) -> bool:
    if df is None or df.empty:
        return False
    last_utc = df["time"].iloc[-1]
    if pd.isnull(last_utc):
        return False
    if last_utc.tzinfo is None:
        last_utc = last_utc.replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - last_utc).total_seconds() / 60
    stale_threshold = DATA_STALE_MINUTES_ASIAN if session == "Asian" else DATA_STALE_MINUTES
    if age > stale_threshold:
        log(f"Data stale -- last candle {age:.1f} min ago (session: {session or 'N/A'}).", "WARN")
        return False
    return True


def has_frozen_prices(df: pd.DataFrame, lookback: int = 4) -> bool:
    if df is None or len(df) < lookback:
        return False
    closes = df["close"].tail(lookback).values
    if len(set(closes)) == 1:
        log(f"FROZEN FEED -- {lookback} identical closes ({closes[0]:.5f}).", "GUARD")
        return True
    return False


def validate_candles(df: pd.DataFrame, session: Optional[str] = None) -> bool:
    if df is None or len(df) < 50:
        log("Insufficient candle data (need 50+).", "WARN")
        return False
    
    # v5.1: Gap Detection
    # Detect if there's a gap larger than 2*ATR between consecutive candles
    atr = calculate_atr(df)
    if atr:
        # Check last 5 candles for abnormal gaps
        recent = df.tail(5)
        for i in range(1, len(recent)):
            gap = abs(recent.iloc[i]["open"] - recent.iloc[i-1]["close"])
            if gap > atr * 2:
                log(f"Abnormal price gap detected ({gap:.5f} > {atr*2:.5f}).", "GUARD")
                return False

    return is_data_fresh(df, session) and not has_frozen_prices(df)


# =======================================================
#  SECTION 3 -- SESSION PERSISTENCE
# =======================================================

def load_sessions() -> set:
    """Load sessions traded today from database or JSON fallback."""
    if DB_AVAILABLE:
        try:
            today = datetime.now().strftime("%Y-%m-%d")
            conn = DB._get_connection()
            cursor = conn.execute("""
                SELECT session_name FROM sessions
                WHERE date = ? AND symbol = ? AND strategy = ? AND traded = TRUE
            """, (today, _arg_symbol, STRATEGY))
            return {row[0] for row in cursor.fetchall()}
        except Exception as e:
            log(f"Database read error: {e}. Falling back to JSON.", "WARN")
    
    # JSON fallback
    if os.path.exists(SESSIONS_FILE):
        try:
            with open(SESSIONS_FILE, "r") as f:
                data = json.load(f)
            if data.get("date") == datetime.now().strftime("%Y-%m-%d"):
                return set(data.get("sessions", []))
        except (json.JSONDecodeError, KeyError):
            log("Sessions file corrupted -- starting fresh.", "WARN")
    return set()


def save_sessions(sessions: set):
    """
    Save sessions traded today to database (primary) or JSON fallback.
    Atomic writes prevent corruption under concurrent access.
    """
    today = datetime.now().strftime("%Y-%m-%d")
    
    if DB_AVAILABLE:
        try:
            for session_name in sessions:
                DB.mark_session_traded(today, _arg_symbol, STRATEGY, session_name)
            return
        except Exception as e:
            log(f"Database write error: {e}. Falling back to JSON.", "WARN")
    
    # JSON fallback (dual-write for safety during transition)
    payload = json.dumps({
        "date":     today,
        "sessions": list(sessions)
    }, indent=2)
    tmp = SESSIONS_FILE + ".tmp"
    with open(tmp, "w") as f:
        f.write(payload)
    os.replace(tmp, SESSIONS_FILE)


# =======================================================
#  SECTION 4 -- ACCOUNT & RISK MANAGEMENT
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
    return sum(d.profit for d in deals if d.magic == _instance_magic)


def check_consecutive_losses() -> bool:
    """
    v5.1 FIX: Persistent consecutive loss tracking across days.
    Loads saved loss count, checks against threshold (3 losses = pause).
    """
    loss_count, _ = load_consecutive_losses()
    if loss_count >= 3:
        log(f"Consecutive loss limit reached ({loss_count} losses) -- Ingwe pauses.", "GUARD")
        return True
    return False


def load_consecutive_losses() -> tuple:
    """
    Load (consecutive_losses, last_counted_ticket) from database or JSON fallback.
    Persists across days for multi-day drawdown protection.
    """
    today = datetime.now().strftime("%Y-%m-%d")
    
    if DB_AVAILABLE:
        try:
            return DB.get_loss_tracking(today, _arg_symbol, STRATEGY)
        except Exception as e:
            log(f"Database read error: {e}. Falling back to JSON.", "WARN")
    
    # JSON fallback
    if os.path.exists(SESSIONS_FILE):
        try:
            with open(SESSIONS_FILE, "r") as f:
                data = json.load(f)
            if data.get("date") == today:
                return (data.get("consecutive_losses", 0), data.get("last_counted_ticket", 0))
        except (json.JSONDecodeError, KeyError):
            pass
    return (0, 0)


def save_consecutive_losses(count: int, last_ticket: int = 0):
    """
    Save consecutive loss count and last counted deal ticket to database (primary)
    or JSON fallback.
    """
    today = datetime.now().strftime("%Y-%m-%d")
    
    if DB_AVAILABLE:
        try:
            DB.update_loss_tracking(today, _arg_symbol, STRATEGY, count, last_ticket)
            return
        except Exception as e:
            log(f"Database write error: {e}. Falling back to JSON.", "WARN")
    
    # JSON fallback
    payload = {
        "date": today,
        "sessions": list(sessions_traded_today),
        "consecutive_losses": count,
        "last_counted_ticket": last_ticket
    }
    tmp = SESSIONS_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(payload, f, indent=2)
    os.replace(tmp, SESSIONS_FILE)


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
    
    own_deals = [d for d in deals if d.magic == _instance_magic and d.profit != 0]
    if not own_deals:
        return
    
    last_deal = own_deals[-1]
    current_count, last_ticket = load_consecutive_losses()
    
    if last_deal.ticket == last_ticket:
        return
    
    if last_deal.profit < 0:
        new_count = current_count + 1
        save_consecutive_losses(new_count, last_deal.ticket)
        log(f"Loss recorded -- consecutive losses: {new_count}", "INFO")
    else:
        if current_count > 0:
            save_consecutive_losses(0, last_deal.ticket)
            log("Win recorded -- consecutive loss counter reset.", "INFO")


def get_spread() -> float | None:
    if BACKTEST_MODE:
        return 0.00010  # 1 pip fixed spread for backtest
    tick = mt5.symbol_info_tick(SYMBOL)
    return (tick.ask - tick.bid) if tick else None


# =======================================================
#  SECTION 5 -- TREND & MARKET STRUCTURE
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


def get_htf_bias() -> str | None:
    """
    v3.9: Daily/H4 structural bias layer.
    D1 + H4 EMA10/30 must agree for a confirmed bias.
    Returns 'BULLISH', 'BEARISH', or None (conflicted).
    v5.5: BACKTEST_MODE guard -- skips live MT5 calls during backtest.
    v6.0: HTF data cached for 1 hour to prevent MT5 throttling.
    """
    global _htf_bias_cache
    if BACKTEST_MODE:
        return None

    now = time.time()
    cache_age = now - _htf_bias_cache["timestamp"]
    if cache_age < 3600 and _htf_bias_cache["bias"] is not None:
        return _htf_bias_cache["bias"]

    d1_rates = mt5_fetch_with_retry(
        mt5.copy_rates_from_pos, SYMBOL, mt5.TIMEFRAME_D1, 0, 50
    )
    h4_rates = mt5_fetch_with_retry(
        mt5.copy_rates_from_pos, SYMBOL, mt5.TIMEFRAME_H4, 0, 50
    )
    
    if d1_rates is None or len(d1_rates) < 30:
        log("D1 data unavailable for HTF bias.", "WARN")
        return None
    if h4_rates is None or len(h4_rates) < 30:
        log("H4 data unavailable for HTF bias.", "WARN")
        return None
    
    d1 = pd.DataFrame(d1_rates)
    h4 = pd.DataFrame(h4_rates)
    
    d1_ema10 = d1["close"].ewm(span=10, adjust=False).mean().iloc[-1]
    d1_ema30 = d1["close"].ewm(span=30, adjust=False).mean().iloc[-1]
    h4_ema10 = h4["close"].ewm(span=10, adjust=False).mean().iloc[-1]
    h4_ema30 = h4["close"].ewm(span=30, adjust=False).mean().iloc[-1]
    
    d1_bias = "BULLISH" if d1_ema10 > d1_ema30 else ("BEARISH" if d1_ema10 < d1_ema30 else None)
    h4_bias = "BULLISH" if h4_ema10 > h4_ema30 else ("BEARISH" if h4_ema10 < h4_ema30 else None)
    
    if d1_bias and h4_bias and d1_bias == h4_bias:
        _htf_bias_cache = {"bias": d1_bias, "timestamp": now}
        return d1_bias
    
    log(f"HTF bias split -- D1: {d1_bias}  H4: {h4_bias}. No HTF confirmation.")
    _htf_bias_cache = {"bias": None, "timestamp": now}
    return None

def get_draw_on_liquidity(direction: str) -> tuple[str, float] | tuple[None, None]:
    """
    v5.2: Identifies the most likely target (Draw on Liquidity).
    Checks PDH/PDL, Asian Range, and Session Extremes.
    """
    pdh, pdl = get_pdh_pdl()
    asian_h, asian_l = get_asian_range(get_candles())
    
    if direction == "BUY":
        targets = []
        if pdh: targets.append(("PDH", pdh))
        if asian_h: targets.append(("Asian High", asian_h))
        # Sort targets by proximity (nearest high is the immediate DOL)
        if not targets: return None, None
        return min(targets, key=lambda x: x[1])
    
    elif direction == "SELL":
        targets = []
        if pdl: targets.append(("PDL", pdl))
        if asian_l: targets.append(("Asian Low", asian_l))
        if not targets: return None, None
        return min(targets, key=lambda x: x[1])
    
    return None, None



def get_candles() -> pd.DataFrame | None:
    global _backtest_index, _backtest_data
    
    if BACKTEST_MODE:
        if _backtest_data is None:
            csv_path = Path(BACKTEST_CSV)
            if not csv_path.exists():
                log(f"Backtest CSV not found: {BACKTEST_CSV}", "ERROR")
                return None
            _backtest_data = pd.read_csv(csv_path)
            _backtest_data["time"] = pd.to_datetime(_backtest_data["time"], utc=True)
            _backtest_index = 0
            log(f"Backtest loaded: {len(_backtest_data)} candles from {BACKTEST_CSV}")
        
        if _backtest_index >= len(_backtest_data):
            log("Backtest complete.", "INFO")
            return None
        
        return _backtest_data.iloc[:_backtest_index + 1]
    
    rates = mt5_fetch_with_retry(
        mt5.copy_rates_from_pos, SYMBOL, TIMEFRAME, 0, 200
    )
    if rates is None:
        return None
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
    if "volume" not in df.columns:
        if "tick_volume" in df.columns:
            df["volume"] = df["tick_volume"]
        elif "real_volume" in df.columns:
            df["volume"] = df["real_volume"]
        else:
            df["volume"] = 0.0
    return df


def get_pdh_pdl() -> tuple[float, float] | tuple[None, None]:
    """Previous Day High / Low. Uses iloc[-2] -- today's candle is always incomplete."""
    rates = mt5_fetch_with_retry(
        mt5.copy_rates_from_pos, SYMBOL, mt5.TIMEFRAME_D1, 0, 3
    )
    if rates is None or len(rates) < 2:
        return None, None
    df        = pd.DataFrame(rates)
    yesterday = df.iloc[-2]
    return float(yesterday["high"]), float(yesterday["low"])


def get_asian_range(df: pd.DataFrame) -> tuple[float, float] | tuple[None, None]:
    """Asian Session Range High/Low. Window: 02:00-06:00 SAST = 00:00-04:00 UTC."""
    utc_now      = datetime.now(timezone.utc)
    utc_midnight = utc_now.replace(hour=0, minute=0, second=0, microsecond=0)
    asian_start  = utc_midnight
    asian_end    = utc_midnight + timedelta(hours=4)

    asian = df[
        (df["time"] >= asian_start) &
        (df["time"] <  asian_end)
    ]
    if asian.empty or len(asian) < 4:
        return None, None
    return float(asian["high"].max()), float(asian["low"].min())


# -------------------------------------------------------
# BACKTEST HELPERS (Option B)
# -------------------------------------------------------
_backtest_pending_orders = []

def get_backtest_price() -> tuple[float, float] | None:
    """Get current bid/ask from backtest CSV data."""
    global _backtest_index, _backtest_data
    if _backtest_data is None or _backtest_index >= len(_backtest_data):
        return None
    row = _backtest_data.iloc[_backtest_index]
    bid = row["close"]
    ask = bid + 0.00010
    return bid, ask


def check_backtest_limit_fill(direction: str, entry_price: float, expiry_candles: int = 4) -> bool:
    """
    Simulate limit order fill: price must retrace to entry within expiry_candles.
    Returns True if filled, False if expired.
    """
    global _backtest_index, _backtest_data, _backtest_pending_orders
    if _backtest_data is None:
        return False
    
    candle_time = _backtest_index
    _backtest_pending_orders.append({
        "direction": direction,
        "entry": entry_price,
        "placed_at": candle_time,
        "expiry": candle_time + expiry_candles
    })
    
    for i in range(expiry_candles):
        check_idx = _backtest_index + i + 1
        if check_idx >= len(_backtest_data):
            return False
        
        high = _backtest_data.iloc[check_idx]["high"]
        low = _backtest_data.iloc[check_idx]["low"]
        
        if direction == "BUY" and low <= entry_price <= high:
            log(f"BACKTEST: BUY LIMIT filled at {entry_price}", "TRADE")
            return True
        if direction == "SELL" and low <= entry_price <= high:
            log(f"BACKTEST: SELL LIMIT filled at {entry_price}", "TRADE")
            return True
    
    log(f"BACKTEST: LIMIT expired unfilled at {entry_price}", "INFO")
    return False


def run_backtest_step():
    """Advance backtest by one candle (call this instead of sleep in backtest mode).
    Returns True if backtest is still running, False when complete."""
    global _backtest_index, _backtest_data, BACKTEST_SPEED
    if not BACKTEST_MODE or _backtest_data is None:
        return False
    
    _backtest_index += BACKTEST_SPEED
    if _backtest_index >= len(_backtest_data):
        log("Backtest complete.", "INFO")
        return False
    
    current_candle = _backtest_data.iloc[_backtest_index]
    log(f"Backtest: {current_candle['time']} | O:{current_candle['open']} H:{current_candle['high']} L:{current_candle['low']} C:{current_candle['close']}")
    return True


def detect_liquidity_sweep(df: pd.DataFrame):
    recent    = df.tail(5)
    prev_high = recent["high"].iloc[:-1].max()
    prev_low  = recent["low"].iloc[:-1].min()
    last      = recent.iloc[-1]
    if last["high"] > prev_high:   return "SWEEP_HIGH", prev_high
    if last["low"]  < prev_low:    return "SWEEP_LOW",  prev_low
    return None, None


def check_displacement_validity(c1: pd.Series, c2: pd.Series) -> bool:
    """
    v3.8: range expansion AND body dominance both required.
    c2 range > c1 range × 1.5, c2 body >= 60% of c2 range.
    """
    c1_range = c1["high"] - c1["low"]
    c2_range = c2["high"] - c2["low"]
    if c1_range == 0 or c2_range == 0:
        return True
    range_ok = c2_range > (c1_range * 1.5)
    body     = abs(c2["close"] - c2["open"])
    body_ok  = (body / c2_range) >= 0.6
    return range_ok and body_ok


def detect_fvg(df: pd.DataFrame, max_age: int = 0) -> list:
    """
    Detects Fair Value Gaps and calculates fvg_50 (50% midpoint).
    max_age=4 for Silver Bullet (window-fresh), max_age=20 for Ingwe (5hrs).
    """
    fvgs  = []
    start = max(2, len(df) - max_age - 1) if max_age > 0 else 2
    for i in range(start, len(df) - 1):
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


def detect_immediate_fvg(df: pd.DataFrame) -> list:
    """
    v4.0 FIX: Detect FVGs in the most recent 3 candles only.
    Used when price moves too fast for standard FVG detection.
    """
    fvgs = []
    for i in range(max(2, len(df) - 3), len(df) - 1):
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


def detect_breaker_blocks(df: pd.DataFrame, lookback: int = 30) -> list:
    """
    Breaker Blocks -- former Order Blocks whose polarity has flipped.
    Invalidation guard: zone spent if price broke back through extreme.
    """
    breakers = []
    start    = max(0, len(df) - lookback)

    for i in range(start, len(df) - 5):
        candle     = df.iloc[i]
        subsequent = df.iloc[i + 1:]
        post_break = df.iloc[i + 5:]

        if candle["close"] < candle["open"]:
            if subsequent["high"].max() > candle["high"]:
                if not post_break.empty and (post_break["low"] < candle["low"]).any():
                    continue
                breakers.append(("BULLISH_BREAKER", candle["low"], candle["high"], i))

        if candle["close"] > candle["open"]:
            if subsequent["low"].min() < candle["low"]:
                if not post_break.empty and (post_break["high"] > candle["high"]).any():
                    continue
                breakers.append(("BEARISH_BREAKER", candle["low"], candle["high"], i))

    return breakers[-5:]


def detect_unicorn_zone(fvgs: list, breakers: list) -> list:
    """
    Unicorn: Breaker Block overlapping FVG of matching polarity.
    Temporal gap guard: >15 candles apart = structural disconnect.
    """
    unicorns = []

    for fvg_type, fvg_low, fvg_high, fvg_idx, ob, fvg_50 in fvgs:
        for bb_type, bb_low, bb_high, bb_idx in breakers:

            temporal_gap = abs(fvg_idx - bb_idx)
            if temporal_gap > 15:
                continue

            if fvg_type == "BULLISH_FVG" and bb_type == "BULLISH_BREAKER":
                overlap_low  = max(fvg_low,  bb_low)
                overlap_high = min(fvg_high, bb_high)
                if overlap_low < overlap_high:
                    unicorn_mid = overlap_low + (overlap_high - overlap_low) * 0.5
                    unicorns.append((
                        "BULLISH_UNICORN",
                        overlap_low, overlap_high, unicorn_mid,
                        bb_low, bb_high
                    ))

            if fvg_type == "BEARISH_FVG" and bb_type == "BEARISH_BREAKER":
                overlap_low  = max(fvg_low,  bb_low)
                overlap_high = min(fvg_high, bb_high)
                if overlap_low < overlap_high:
                    unicorn_mid = overlap_low + (overlap_high - overlap_low) * 0.5
                    unicorns.append((
                        "BEARISH_UNICORN",
                        overlap_low, overlap_high, unicorn_mid,
                        bb_low, bb_high
                    ))

    return unicorns


def detect_m15_bos(df: pd.DataFrame, lookback: int = 20) -> str | None:
    """
    M15 Break of Structure.
    v3.9.1: 1-bar pivot (was 2-bar). BOS requires a CLOSE beyond swing.
    v3.9: accepts optional lookback. London Open uses 12, others use 20.
    Returns BULLISH_BOS, BEARISH_BOS, or None.
    """
    if df is None or len(df) < lookback + 2:
        return None

    recent = df.tail(lookback).reset_index(drop=True)
    n      = len(recent)

    swing_highs: list[tuple[int, float]] = []
    swing_lows:  list[tuple[int, float]] = []

    for i in range(1, n - 1):
        h = recent.iloc[i]["high"]
        l = recent.iloc[i]["low"]
        if (h > recent.iloc[i - 1]["high"] and
                h > recent.iloc[i + 1]["high"]):
            swing_highs.append((i, h))
        if (l < recent.iloc[i - 1]["low"] and
                l < recent.iloc[i + 1]["low"]):
            swing_lows.append((i, l))

    if not swing_highs and not swing_lows:
        return None

    last_close = recent.iloc[-1]["close"]

    if swing_highs and last_close > swing_highs[-1][1]:
        return "BULLISH_BOS"
    if swing_lows  and last_close < swing_lows[-1][1]:
        return "BEARISH_BOS"

    return None


# =======================================================
#  SECTION 6 -- INDICATORS
# =======================================================

from indicators import calculate_adx_wilder as _calculate_adx_wilder

def calculate_adx_wilder(df: pd.DataFrame, period: int = 14):
    if df is None or len(df) < period * 2 + 1:
        return None, None, None
    return _calculate_adx_wilder(
        df["high"].values, df["low"].values, df["close"].values, period
    )


def calculate_atr(df: pd.DataFrame, period: int = 14) -> float | None:
    """
    v3.9.4 FIX-2: True Wilder ATR smoothing. Matches MT5 native ATR.
    Minimum data: period*2+1 bars.
    """
    if df is None or len(df) < period * 2 + 1:
        return None

    high  = df["high"].values
    low   = df["low"].values
    close = df["close"].values
    n     = len(df)

    tr = np.zeros(n)
    tr[0] = high[0] - low[0]
    for i in range(1, n):
        tr[i] = max(
            high[i] - low[i],
            abs(high[i] - close[i - 1]),
            abs(low[i]  - close[i - 1])
        )

    atr_val = float(np.mean(tr[1:period + 1]))

    for i in range(period + 1, n):
        atr_val = (atr_val * (period - 1) + tr[i]) / period

    return round(atr_val, 6)


# =======================================================
#  SECTION 7 -- FILTERS
# =======================================================

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
    
    if _arg_symbol == "BTCUSD":
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
            if spread > MIN_SPREAD_PIPS * 2:
                log(f"Spread too wide: {spread*10000:.1f}p.", "GUARD")
                return False
    return True


# =======================================================
#  SECTION 8 -- POSITION SIZING
# =======================================================

def calculate_lot_size(sl_distance: float | None = None) -> float:
    """
    v5.1: Dynamic lot sizing based on risk per trade.
    Formula: LotSize = (Equity * Risk%) / (SL_distance * TickValue)
    """
    if sl_distance is None or sl_distance <= 0:
        # Default fallback: use ATR-based distance if no specific SL provided
        try:
            df = get_candles()
            atr = calculate_atr(df)
            if atr:
                sl_distance = atr * ATR_MULTIPLIER
            else:
                return 0.01
        except:
            return 0.01
    
    account = mt5.account_info()
    symbol_info = mt5.symbol_info(SYMBOL)
    
    if not account or not symbol_info:
        log("Account or symbol info unavailable for lot calculation.", "ERROR")
        return 0.01

    equity = account.equity
    risk_amount = equity * (RISK_PERCENT / 100.0)
    
    tick_value = symbol_info.trade_tick_value
    tick_size = symbol_info.trade_tick_size
    
    if tick_value == 0 or tick_size == 0:
        return 0.01

    sl_ticks = sl_distance / tick_size
    lot_size = risk_amount / (sl_ticks * tick_value)
    
    min_lot = symbol_info.volume_min
    max_lot = symbol_info.volume_max
    final_lot = max(min_lot, min(lot_size, max_lot, HARD_LOT_CAP))
    
    lot_step = symbol_info.volume_step
    final_lot = round(final_lot / lot_step) * lot_step
    
    return float(final_lot)


def get_overlap_multiplier() -> float:
    hour = now_sast().hour
    return 1.2 if (16 <= hour < 19 if not is_eu_summer() else 15 <= hour < 18) else 1.0


# =======================================================
#  SECTION 9 -- CONFLUENCE SCORING (INGWE MODE)
# =======================================================

# v5.5: Updated session-direction performance from live data (~77% SELL vs ~40% BUY)
SESSION_PERFORMANCE = {
    "Asian": {"buy": 0.29, "sell": 1.00},    # SELL dominant, BUX weak
    "London": {"buy": 0.14, "sell": 0.00},   # BUY very weak, SELL undefined
    "New York": {"buy": 0.00, "sell": 0.58}, # BUX zero, SELL decent
}

# v5.5: Asymmetry bonus for session-direction pairs with documented high win rate
SESSION_ASYMMETRY_BONUS = {
    ("asian", "sell"): 10,
    ("new york", "sell"): 10,
}

def get_session_multiplier(session: str, direction: str) -> float:
    """v5.0: Return threshold multiplier based on historical session performance."""
    key = session.lower()
    if key not in SESSION_PERFORMANCE:
        return 1.0
    dir_key = direction.lower()
    wr = SESSION_PERFORMANCE.get(key, {}).get(dir_key, 0.5)
    if wr >= 0.60:
        return 0.9   # Easy entry for good sessions
    elif wr >= 0.40:
        return 1.0   # Normal
    elif wr >= 0.25:
        return 1.15  # Stricter for moderate sessions
    else:
        return 1.30  # Much stricter for weak sessions


def get_confluence_threshold(adx: float, session: str = "", direction: str = "") -> int:
    if adx is None or adx < ADX_MIN_THRESHOLD:  return 80
    
    base = 70
    if adx > 40:
        base = 60
    
    # v5.0: Apply session-specific multiplier
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
    if trend in ("BULLISH", "BEARISH"):  score += 30
    if fvg_ok:                           score += 30
    if zone_ok:                          score += 15
    if spread_ok:                        score += 10
    if level_sweep:                      score += 5
    if bos_aligned:                      score += 5
    if htf_bias_ok:                      score += 15
    key = (session.lower().strip(), direction.lower().strip())
    if key in SESSION_ASYMMETRY_BONUS:   score += SESSION_ASYMMETRY_BONUS[key]
    return score


# =======================================================
#  SECTION 10 -- TRADE EXECUTION & LOGGING
# =======================================================

def log_trade(direction, entry, sl, tp, result, lot_size, session, context=None, kronos_gate=None):
    """
    Log trade to database (primary) or JSON fallback.
    Tracks: fill price, slippage, effective RR based on actual execution.
    v5.5: Pulls actual fill from MT5 deal history when available.
    v6.1: Retries position_id lookup via positions_get if deal ticket unavailable.
    """
    actual_fill = entry
    position_id = 0
    if result and not BACKTEST_MODE:
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
                positions = mt5.positions_get(symbol=SYMBOL)
                if positions:
                    for pos in positions:
                        if pos.magic == _instance_magic:
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
        "symbol":      SYMBOL,
        "time":        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "strategy":    STRATEGY,
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
    
    TRADING_GOVERNOR.record_trade()
    
    if DB_AVAILABLE:
        try:
            DB.insert_trade(trade_entry)
            log(f"Trade logged -> vuka_trading.db", "TRADE")
        except Exception as e:
            log(f"Database write error: {e}. Falling back to JSON.", "WARN")
    
    # JSON fallback (dual-write for safety during transition)
    trade_log = []
    if os.path.exists(LOG_FILE):
        try:
            with open(LOG_FILE, "r") as f:
                trade_log = json.load(f)
        except json.JSONDecodeError:
            pass
    
    trade_log.append(trade_entry)
    
    tmp = LOG_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(trade_log, f, indent=2)
    os.replace(tmp, LOG_FILE)
    log(f"Trade logged -> {LOG_FILE}", "TRADE")

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
        # High confidence (>0.6) → trail BE at 2:1; otherwise BE at 1:1
        trade_entry["trail_be_at"] = 2.0 if _conf_score > 0.6 else 1.0
    except Exception as e:
        log(f"Concept tracker record_trade error: {e}", "WARN")


def place_trade(direction, entry, sl, tp, lot_size, session="unknown"):
    if BACKTEST_MODE:
        class MockResult:
            retcode = mt5.TRADE_RETCODE_DONE
            comment = "BACKTEST FILLED"
        return MockResult()

    if has_open_position():
        log(f"Position already open for {_instance_tag} -- skipping duplicate entry.", "GUARD")
        return None

    # Phase 5a: Per-symbol-per-session dedup lock (prevents double-firing)
    if DB_AVAILABLE and session != "unknown":
        dedup_ok = DB.dedup_check_and_lock(SYMBOL, session, STRATEGY)
        if not dedup_ok:
            log(f"Session '{session}' already traded for {SYMBOL} today. Dedup lock active.", "GUARD")
            return None
    
    order_type = mt5.ORDER_TYPE_BUY if direction == "BUY" else mt5.ORDER_TYPE_SELL

    base_order = {
        "action":       mt5.TRADE_ACTION_DEAL,
        "symbol":       SYMBOL,
        "volume":       lot_size,
        "type":         order_type,
        "price":        entry,
        "sl":           sl,
        "tp":           tp,
        "deviation":    10,
        "magic":        _instance_magic,
        "comment":      _instance_short,   # v4.0 FIX: use short tag (e.g., "EURS", "GBPS")
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
    if BACKTEST_MODE:
        return False
    
    orders = mt5.orders_get(symbol=SYMBOL)
    if not orders:
        return False
    return any(o.magic == _instance_magic for o in orders)


def has_open_position() -> bool:
    """
    v4.4 FIX-1: Returns True if this instance has an open position.
    Guards against placing duplicate market orders on consecutive scan cycles.
    """
    if BACKTEST_MODE:
        return False
    
    positions = mt5.positions_get(symbol=SYMBOL)
    if not positions:
        return False
    return any(p.magic == _instance_magic for p in positions)


def place_limit_order(direction: str, entry: float, sl: float,
                      tp: float, lot_size: float):
    """
    v4.0: Pending limit order at FVG 50% midpoint.
    BUY_LIMIT / SELL_LIMIT via TRADE_ACTION_PENDING.
    Expiry: LIMIT_ORDER_EXPIRY_CANDLES × SCAN_INTERVAL_SEC from now
    (default 4 × 15min = 1hr) in broker server time.
    Single submission -- pending orders do not use filling modes.
    
    BACKTEST MODE: Simulates limit order fill based on price retracement.
    """
    if BACKTEST_MODE:
        filled = check_backtest_limit_fill(direction, entry, LIMIT_ORDER_EXPIRY_CANDLES)
        class MockResult:
            retcode = mt5.TRADE_RETCODE_DONE if filled else 10025
            comment = "BACKTEST FILLED" if filled else "BACKTEST EXPIRED"
        return MockResult()
    
    order_type = (mt5.ORDER_TYPE_BUY_LIMIT
                  if direction == "BUY"
                  else mt5.ORDER_TYPE_SELL_LIMIT)

    expiry_dt = _server_now() + timedelta(
        seconds=LIMIT_ORDER_EXPIRY_CANDLES * SCAN_INTERVAL_SEC
    )

    order = {
        "action":          mt5.TRADE_ACTION_PENDING,
        "symbol":          SYMBOL,
        "volume":          lot_size,
        "type":            order_type,
        "price":           entry,
        "sl":              sl,
        "tp":              tp,
        "deviation":       10,
        "magic":           _instance_magic,
        "comment":         _instance_short,
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
        "symbol":   SYMBOL,
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
        "symbol": _arg_symbol,
        "strategy": STRATEGY,
        "entry": entry,
        "old_sl": old_sl,
        "new_sl": new_sl,
        "movement": round(new_sl - old_sl, 5),
        "label": label
    }
    
    if DB_AVAILABLE:
        try:
            DB.insert_sl_movement(sl_move_entry)
            return
        except Exception as e:
            log(f"Database write error: {e}. Falling back to JSON.", "WARN")
    
    # JSON fallback (dual-write for safety during transition)
    log_file = Path(f"sl_moves_{_instance_tag}.json")
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


def manage_open_positions():
    """
    v3.9.5 FIX-2: Trailing SL manager.
    Runs every scan cycle before session logic.
    Filtered by _instance_magic -- each instance manages only its own trades.

    Rules:
      1:1 profit hit -> SL moves to breakeven (entry). Worst case: 0.
      1:2 profit hit -> SL moves to 1:1. Worst case: secured half RRR minimum.

    The leopard does not give back what it has already taken.
    """
    positions = mt5.positions_get(symbol=SYMBOL)
    if not positions:
        return

    # Phase 2b: Load trade log for data-driven trail config
    _trail_config = {}
    if os.path.exists(LOG_FILE):
        try:
            with open(LOG_FILE, "r") as _f:
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
        if pos.magic != _instance_magic:
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

    # ── P&L backfill for closed trades ───────────────────
    if not BACKTEST_MODE:
        closed = mt5.history_deals_get(_server_midnight(), _server_now())
        if closed:
            pnl_by_pos = {}
            exit_price_by_pos = {}
            deal_list = []
            for d in closed:
                if d.magic == _instance_magic and d.profit != 0:
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
                if not os.path.exists(LOG_FILE):
                    return
                try:
                    with open(LOG_FILE, "r") as f:
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
                    tmp = LOG_FILE + ".tmp"
                    with open(tmp, "w") as f:
                        json.dump(trade_log_data, f, indent=2)
                    os.replace(tmp, LOG_FILE)
                    log(f"P&L updated for {len(updated)} closed trade(s)", "TRADE")

                    # Phase 1a: Wire record_outcome() — close the feedback loop
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
                                "symbol": t.get("symbol", SYMBOL),
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

                    # Phase 1b: Update DB with PnL and exit info
                    if DB_AVAILABLE:
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
                                        t.get("symbol", SYMBOL),
                                        t.get("strategy", STRATEGY),
                                        t.get("time", ""),
                                        t.get("direction", ""),
                                        t["pnl_usd"],
                                        exit_price=t.get("exit_price"),
                                        exit_reason=t.get("exit_reason"),
                                        exit_time=t.get("exit_time")
                                    )
                            except Exception as e:
                                log(f"DB PnL update error for trade: {e}", "WARN")


# =======================================================
#  SECTION 11 -- DAILY RESET
# =======================================================

def reset_daily_sessions():
    global consecutive_losses, sessions_traded_today
    local = now_sast()
    if local.hour == 0 and local.minute < 15 and sessions_traded_today:
        sessions_traded_today.clear()
        save_sessions(sessions_traded_today)
        consecutive_losses = 0
        log("Midnight reset -- sessions and loss counter cleared.")


# =======================================================
#  SECTION 12A -- SETUP EVALUATION: INGWE MODE
# =======================================================

def evaluate_ingwe(df, fvgs, sweep, sweep_level, price, atr, lot_size, session):
    """
    Full multi-confluence model.
    v4.3:   Three hard gates added after GBPUSD loss (ADX<20, D1 bias
            conflict, SL min distance). All block entry regardless of score.
    v4.2:   Market orders reinstated -- same confluence logic, no static
            limit levels. Reverted from v4.0 which produced 0% win rate.
    v3.9.4: FIX-1 zone check removed from Paths C/D.
    v3.9.3: True Wilder ADX.
    v3.9.2: Zone context logging.
    v3.9.1: Four paths. 1-bar BOS pivot.
    v3.9:   ATR guard 0.5×. Dynamic BOS lookback. HTF bias gate + +10.
    """
    adx, plus_di, minus_di = calculate_adx_wilder(df)
    if adx is None:
        log("ADX unavailable.", "WARN")
        return
    log(f"ADX: {adx:.1f}  |  +DI: {plus_di:.1f}  |  -DI: {minus_di:.1f}")

    # ── ADX GATE ──────────────────────────────────────────
    if adx < ADX_MIN_THRESHOLD:
        log(f"ADX {adx} below minimum ({ADX_MIN_THRESHOLD}) -- Extreme chop. Standing down.", "GUARD")
        return
    
    # ── PATTERN BLACKLIST CHECK (STILL A HARD GATE) ────────────────────────────
    # Block toxic patterns identified in backtest (win rate <35%)
    blacklist_blocked = False
    for pattern in PATTERN_BLACKLIST:
        blk_session, blk_direction, blk_sweep = pattern
        if session == blk_session and sweep == blk_sweep:
            for fvg_type, _, _, _, _, _ in fvgs:
                if sweep == "SWEEP_LOW" and fvg_type == "BULLISH_FVG":
                    current_direction = "BUY"
                elif sweep == "SWEEP_HIGH" and fvg_type == "BEARISH_FVG":
                    current_direction = "SELL"
                elif sweep == "SWEEP_LOW" and fvg_type == "BEARISH_FVG":
                    current_direction = "SELL"
                elif sweep == "SWEEP_HIGH" and fvg_type == "BULLISH_FVG":
                    current_direction = "BUY"
                else:
                    continue
                if current_direction == blk_direction:
                    log(f"PATTERN BLACKLIST: {session} {current_direction} {sweep} -- "
                        f"historically <35% win rate. Standing down.", "GUARD")
                    blacklist_blocked = True
                    break
            if blacklist_blocked:
                break
    
    if blacklist_blocked:
        return

    spread      = get_spread()
    spread_pips = spread * 10000 if spread else 0
    spread_ok   = spread is not None and spread < MIN_SPREAD_PIPS
    multiplier  = get_overlap_multiplier()
    if multiplier > 1.0:
        lot_size = min(round(lot_size * multiplier, 2), HARD_LOT_CAP)
        log(f"London/NY Overlap -> Lot: {lot_size} (1.2x)")

    # v5.0: Get session-aware threshold
    threshold = get_confluence_threshold(adx, session, "BUY" if sweep == "SWEEP_LOW" else "SELL")
    log(f"Price: {price:.5f}  |  ATR: {atr:.5f}  |  "
        f"Lot: {lot_size}  |  Spread: {spread_pips:.1f}p  |  Threshold: {threshold}/120")

    trend = get_h1_trend()
    if not trend:
        log("H1 trend unclear.", "WARN")
        return
    log(f"H1 Trend: {trend}")

    # ── FIX-2: HTF BIAS (v5.2 - Weighted Context) ────────────────
    # Instead of blocking, we flag bias conflicts for Kronos to decide.
    htf_bias = get_htf_bias()
    htf_bias_ok = True
    if not htf_bias:
        log("HTF bias unavailable -- flagged for Kronos review.", "WARN")
        htf_bias_ok = False
    elif htf_bias != trend:
        log(f"HTF bias ({htf_bias}) conflicts with H1 trend ({trend}) -- flagged for Kronos review.", "WARN")
        htf_bias_ok = False
    else:
        log(f"HTF bias confirms H1 trend -- full top-down alignment.  [+10]")


    # ── FIX-3: SL MINIMUM DISTANCE (v4.3) ───────────────
    # v4.3: SL must be at least MIN_SL_ATR_MULTIPLIER × ATR.
    # v5.0: Increase for weak sessions (more breathing room)
    sl_multi = MIN_SL_ATR_MULTIPLIER * get_session_multiplier(session, "BUY" if sweep == "SWEEP_LOW" else "SELL")
    min_sl = atr * sl_multi

    # ── KEY LEVEL CONTEXT ────────────────────────────────
    pdh, pdl = get_pdh_pdl()
    if pdh and pdl:
        log(f"PDH: {pdh:.5f}  |  PDL: {pdl:.5f}")

    asian_high, asian_low = get_asian_range(df)
    if asian_high and asian_low:
        log(f"Asian Range: {asian_low:.5f}-{asian_high:.5f}")

    # ── LEVEL SWEEP -- 0.5 ATR guard (v3.9) ──────────────
    level_sweep = False
    if pdh and pdl:
        if sweep == "SWEEP_HIGH" and abs(sweep_level - pdh) < atr * 0.5:
            level_sweep = True
            log(f"PDH SWEEP: {sweep_level:.5f} ~ PDH {pdh:.5f}  [+5]")
        elif sweep == "SWEEP_LOW" and abs(sweep_level - pdl) < atr * 0.5:
            level_sweep = True
            log(f"PDL SWEEP: {sweep_level:.5f} ~ PDL {pdl:.5f}  [+5]")
    if not level_sweep and asian_high and asian_low:
        if sweep == "SWEEP_HIGH" and abs(sweep_level - asian_high) < atr * 0.5:
            level_sweep = True
            log(f"ASIAN HIGH SWEEP: {sweep_level:.5f} ~ AR {asian_high:.5f}  [+5]")
        elif sweep == "SWEEP_LOW" and abs(sweep_level - asian_low) < atr * 0.5:
            level_sweep = True
            log(f"ASIAN LOW SWEEP: {sweep_level:.5f} ~ AR {asian_low:.5f}  [+5]")

    # ── M15 BOS -- dynamic lookback (v3.9) ────────────────
    bos_lookback = 12 if session == "London Open" else 20
    m15_bos      = detect_m15_bos(df, lookback=bos_lookback)
    if m15_bos:
        log(f"M15 BOS: {m15_bos}  (lookback={bos_lookback})")
    else:
        log(f"M15 BOS: none confirmed  (lookback={bos_lookback})")

    # ── ZONE CONTEXT HELPER (v3.9.2) ─────────────────────
    def _zone_context(df: pd.DataFrame, price: float) -> str:
        recent = df.iloc[-20:]
        hi  = recent["high"].max()
        lo  = recent["low"].min()
        mid = lo + (hi - lo) * 0.5
        zone = "PREMIUM" if price >= mid else "DISCOUNT"
        return f"{zone} (price={price:.5f}, mid={mid:.5f})"

    # ── FVG LOOP ─────────────────────────────────────────
    for fvg_type, fvg_low, fvg_high, fvg_idx, ob, fvg_50 in fvgs:
        if check_panic_candle(df, atr):
            continue

        # ── PATH A: BUY REVERSAL ─────────────────────────
        if sweep == "SWEEP_LOW" and fvg_type == "BULLISH_FVG" and trend == "BULLISH":
            # P0-B: Direction filter gate (data-driven)
            dir_allowed, dir_reason = TRADING_GOVERNOR.check_direction(
                "BUY", htf_bias, _instance_tag,
                symbol=SYMBOL, session=session, setup_type="REVERSAL"
            )
            if not dir_allowed:
                log(f"Direction blocked: BUY ({dir_reason}).", "GUARD")
                continue
            if plus_di is None or minus_di is None or plus_di <= minus_di:
                log(f"DI filter: +DI({plus_di}) <= -DI({minus_di}). Skip.", "GUARD")
                continue
            if not check_premium_discount_zone(df, price, "BUY"):
                log("Not in discount zone. Skip.", "GUARD")
                continue
            # v5.0 FIX: Require strong HTF bias for BUY signals
            if not htf_bias_ok:
                log("HTF bias required for BUY. No D1/H4 confirmation. Skip.", "GUARD")
                continue
            if htf_bias != "BULLISH":
                log(f"HTF bias ({htf_bias}) not bullish. Skip BUY.", "GUARD")
                continue
            bos_aligned = (m15_bos == "BULLISH_BOS")
            score = calculate_confluence_score(
                trend, True, True, spread_ok, True,
                level_sweep, bos_aligned, htf_bias_ok,
                session, "BUY"
            )
            bonus_label = (
                (" [+PDH/PDL/AR]" if level_sweep  else "") +
                (" [+BOS]"        if bos_aligned  else "") +
                (" [+HTF]"        if htf_bias_ok  else "")
            )
            log(f"Confluence [BUY REVERSAL]: {score}/120{bonus_label}")
            if score < threshold:
                log(f"Score {score} < {threshold}. Flagging for Kronos review.", "WARN")
                score_ok = False
            else:
                score_ok = True
            if not check_pre_trade_spread(atr):
                continue
            
            def _build_context(dir, setup_type, fvg_t, fvg_50_val):
                return {
                    "direction": dir,
                    "setup_type": setup_type,
                    "sweep": sweep,
                    "fvg_type": fvg_t,
                    "fvg_position": "below_50" if price <= fvg_50_val else ("50%" if abs(price - fvg_50_val) < atr * 0.1 else "above_50"),
                    "bos_aligned": bos_aligned,
                    "htf_bias_ok": htf_bias_ok,
                    "adx_ok": True,
                    "score_ok": score_ok,
                    "confluence_score": score,
                    "session": session,
                    "atr": atr,
                    "spread_ok": spread_ok,
                    "trend": trend,
                    "level_sweep": level_sweep,
                    "ob_present": ob is not None,
                    "fvg_low": fvg_low,
                    "fvg_high": fvg_high,
                    "fvg_50": fvg_50_val
                }
            
            ctx = _build_context("BUY", "REVERSAL", fvg_type, fvg_50)
            if KRONOS_VETO_GATE is not None:
                allowed, reason = KRONOS_VETO_GATE.validate(ctx, df, SYMBOL)
                log(f"[KRONOS] BUY signal: {reason}", "GUARD")
                if not allowed:
                    log(f"Kronos vetoed BUY. Skipping trade.", "GUARD")
                    return
            
            stop  = max(atr * ATR_MULTIPLIER, min_sl)
            entry = round_to_tick(price, SYMBOL)
            sl    = round_to_tick(entry - stop, SYMBOL)
            tp    = round_to_tick(entry + stop * RISK_REWARD_RATIO, SYMBOL)
            res   = place_trade("BUY", entry, sl, tp, lot_size, session=session)
            if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                log(f"BUY MARKET  Entry={entry}  SL={sl}  TP={tp}  Lot={lot_size}", "TRADE")
                log_trade("BUY", entry, sl, tp, res, lot_size, session, context=ctx, kronos_gate=KRONOS_VETO_GATE)
                sessions_traded_today.add(session)
                save_sessions(sessions_traded_today)
            else:
                log(f"BUY MARKET FAILED. Code={res.retcode if res else 'N/A'}.", "ERROR")
            return

        # ── PATH B: SELL REVERSAL ────────────────────────
        if sweep == "SWEEP_HIGH" and fvg_type == "BEARISH_FVG" and trend == "BEARISH":
            if plus_di is None or minus_di is None or minus_di <= plus_di:
                log(f"DI filter: -DI({minus_di}) <= +DI({plus_di}). Skip.", "GUARD")
                continue
            if not check_premium_discount_zone(df, price, "SELL"):
                log("Not in premium zone. Skip.", "GUARD")
                continue
            bos_aligned = (m15_bos == "BEARISH_BOS")
            score = calculate_confluence_score(
                trend, True, True, spread_ok, True,
                level_sweep, bos_aligned, htf_bias_ok,
                session, "SELL"
            )
            bonus_label = (
                (" [+PDH/PDL/AR]" if level_sweep  else "") +
                (" [+BOS]"        if bos_aligned  else "") +
                (" [+HTF]"        if htf_bias_ok  else "")
            )
            log(f"Confluence [SELL REVERSAL]: {score}/120{bonus_label}")
            if score < threshold:
                log(f"Score {score} < {threshold}. Waiting.", "GUARD")
                continue
            if not check_pre_trade_spread(atr):
                continue
            ctx = {
                "direction": "SELL",
                "setup_type": "REVERSAL",
                "sweep": sweep,
                "fvg_type": fvg_type,
                "fvg_position": "above_50" if price >= fvg_50 else ("50%" if abs(price - fvg_50) < atr * 0.1 else "below_50"),
                "bos_aligned": bos_aligned,
                "htf_bias_ok": htf_bias_ok,
                "adx_ok": True,
                "score_ok": True,
                "confluence_score": score,
                "session": session,
                "atr": atr,
                "spread_ok": spread_ok,
                "trend": trend,
                "level_sweep": level_sweep,
                "ob_present": ob is not None,
                "fvg_low": fvg_low,
                "fvg_high": fvg_high,
                "fvg_50": fvg_50
            }
            if KRONOS_VETO_GATE is not None:
                allowed, reason = KRONOS_VETO_GATE.validate(ctx, df, SYMBOL)
                log(f"[KRONOS] SELL signal: {reason}", "GUARD")
                if not allowed:
                    log(f"Kronos vetoed SELL. Skipping trade.", "GUARD")
                    return
            stop  = max(atr * ATR_MULTIPLIER, min_sl)
            entry = round_to_tick(price, SYMBOL)
            sl    = round_to_tick(entry + stop, SYMBOL)
            tp    = round_to_tick(entry - stop * RISK_REWARD_RATIO, SYMBOL)
            res   = place_trade("SELL", entry, sl, tp, lot_size, session=session)
            if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                log(f"SELL MARKET  Entry={entry}  SL={sl}  TP={tp}  Lot={lot_size}", "TRADE")
                log_trade("SELL", entry, sl, tp, res, lot_size, session, context=ctx, kronos_gate=KRONOS_VETO_GATE)
                sessions_traded_today.add(session)
                save_sessions(sessions_traded_today)
            else:
                log(f"SELL MARKET FAILED. Code={res.retcode if res else 'N/A'}.", "ERROR")
            return

        # ── PATH C: SELL CONTINUATION (v3.9.1) ──────────
        if sweep == "SWEEP_LOW" and fvg_type == "BEARISH_FVG" and trend == "BEARISH":
            if plus_di is None or minus_di is None or minus_di <= plus_di:
                log(f"DI filter: -DI({minus_di}) <= +DI({plus_di}). Skip.", "GUARD")
                continue
            log(f"Zone context: {_zone_context(df, price)}")
            bos_aligned = (m15_bos == "BEARISH_BOS")
            score = calculate_confluence_score(
                trend, True, True, spread_ok, True,
                level_sweep, bos_aligned, htf_bias_ok,
                session, "SELL"
            )
            bonus_label = (
                (" [+PDH/PDL/AR]" if level_sweep  else "") +
                (" [+BOS]"        if bos_aligned  else "") +
                (" [+HTF]"        if htf_bias_ok  else "")
            )
            log(f"Confluence [SELL CONTINUATION]: {score}/120{bonus_label}")
            if score < threshold:
                log(f"Score {score} < {threshold}. Waiting.", "GUARD")
                continue
            if not check_pre_trade_spread(atr):
                continue
            
            ctx = {
                "direction": "SELL",
                "setup_type": "CONTINUATION",
                "sweep": sweep,
                "fvg_type": fvg_type,
                "fvg_position": "above_50" if price >= fvg_50 else ("50%" if abs(price - fvg_50) < atr * 0.1 else "below_50"),
                "bos_aligned": bos_aligned,
                "htf_bias_ok": htf_bias_ok,
                "adx_ok": True,
                "score_ok": True,
                "confluence_score": score,
                "session": session,
                "atr": atr,
                "spread_ok": spread_ok,
                "trend": trend,
                "level_sweep": level_sweep,
                "ob_present": ob is not None,
                "fvg_low": fvg_low,
                "fvg_high": fvg_high,
                "fvg_50": fvg_50
            }
            if KRONOS_VETO_GATE is not None:
                allowed, reason = KRONOS_VETO_GATE.validate(ctx, df, SYMBOL)
                log(f"[KRONOS] SELL signal: {reason}", "GUARD")
                if not allowed:
                    log(f"Kronos vetoed SELL. Skipping trade.", "GUARD")
                    return
            
            stop  = max(atr * ATR_MULTIPLIER, min_sl)
            entry = round_to_tick(price, SYMBOL)
            sl    = round_to_tick(entry + stop, SYMBOL)
            tp    = round_to_tick(entry - stop * RISK_REWARD_RATIO, SYMBOL)
            res   = place_trade("SELL", entry, sl, tp, lot_size, session=session)
            if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                log(f"SELL MARKET  Entry={entry}  SL={sl}  TP={tp}  Lot={lot_size}", "TRADE")
                log_trade("SELL", entry, sl, tp, res, lot_size, session, context=ctx, kronos_gate=KRONOS_VETO_GATE)
                sessions_traded_today.add(session)
                save_sessions(sessions_traded_today)
            else:
                log(f"SELL MARKET FAILED. Code={res.retcode if res else 'N/A'}.", "ERROR")
            return

        # ── PATH D: BUY CONTINUATION (v3.9.1) ───────────
        if sweep == "SWEEP_HIGH" and fvg_type == "BULLISH_FVG" and trend == "BULLISH":
            # P0-B: Direction filter gate (data-driven)
            dir_allowed, dir_reason = TRADING_GOVERNOR.check_direction(
                "BUY", htf_bias, _instance_tag,
                symbol=SYMBOL, session=session, setup_type="CONTINUATION"
            )
            if not dir_allowed:
                log(f"Direction blocked: BUY ({dir_reason}).", "GUARD")
                continue
            if plus_di is None or minus_di is None or plus_di <= minus_di:
                log(f"DI filter: +DI({plus_di}) <= -DI({minus_di}). Skip.", "GUARD")
                continue
            # v6.0: HTF bias is a soft warning, not a hard block.
            # Kronos will receive the flag and decide based on buy_threshold.
            effective_buy_threshold = BUY_THRESHOLD
            if not htf_bias_ok:
                if session and "asian" in session.lower():
                    effective_buy_threshold = 0.60
                    log(f"Asian split HTF -- raising Kronos threshold to {effective_buy_threshold}.", "GUARD")
                log(f"HTF bias ({htf_bias}) not confirmed. Allowing Kronos to decide with BUY threshold {effective_buy_threshold}.", "WARN")
            if htf_bias != "BULLISH":
                log(f"HTF bias ({htf_bias}) not bullish. Flagging for Kronos review.", "WARN")
            log(f"Zone context: {_zone_context(df, price)}")
            bos_aligned = (m15_bos == "BULLISH_BOS")
            score = calculate_confluence_score(
                trend, True, True, spread_ok, True,
                level_sweep, bos_aligned, htf_bias_ok,
                session, "BUY"
            )
            bonus_label = (
                (" [+PDH/PDL/AR]" if level_sweep  else "") +
                (" [+BOS]"        if bos_aligned  else "") +
                (" [+HTF]"        if htf_bias_ok  else "")
            )
            log(f"Confluence [BUY CONTINUATION]: {score}/120{bonus_label}")
            if score < threshold:
                log(f"Score {score} < {threshold}. Waiting.", "GUARD")
                continue
            if not check_pre_trade_spread(atr):
                continue
            
            ctx = {
                "direction": "BUY",
                "setup_type": "CONTINUATION",
                "sweep": sweep,
                "fvg_type": fvg_type,
                "fvg_position": "below_50" if price <= fvg_50 else ("50%" if abs(price - fvg_50) < atr * 0.1 else "above_50"),
                "bos_aligned": bos_aligned,
                "htf_bias_ok": htf_bias_ok,
                "adx_ok": True,
                "score_ok": True,
                "confluence_score": score,
                "session": session,
                "atr": atr,
                "spread_ok": spread_ok,
                "trend": trend,
                "level_sweep": level_sweep,
                "ob_present": ob is not None,
                "fvg_low": fvg_low,
                "fvg_high": fvg_high,
                "fvg_50": fvg_50,
                "buy_threshold": effective_buy_threshold
            }
            if KRONOS_VETO_GATE is not None:
                allowed, reason = KRONOS_VETO_GATE.validate(ctx, df, SYMBOL)
                log(f"[KRONOS] BUY signal: {reason}", "GUARD")
                if not allowed:
                    log(f"Kronos vetoed BUY. Skipping trade.", "GUARD")
                    return
            
            stop  = max(atr * ATR_MULTIPLIER, min_sl)
            entry = round_to_tick(price, SYMBOL)
            sl    = round_to_tick(entry - stop, SYMBOL)
            tp    = round_to_tick(entry + stop * RISK_REWARD_RATIO, SYMBOL)
            res   = place_trade("BUY", entry, sl, tp, lot_size, session=session)
            if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                log(f"BUY MARKET  Entry={entry}  SL={sl}  TP={tp}  Lot={lot_size}", "TRADE")
                log_trade("BUY", entry, sl, tp, res, lot_size, session, context=ctx, kronos_gate=KRONOS_VETO_GATE)
                sessions_traded_today.add(session)
                save_sessions(sessions_traded_today)
            else:
                log(f"BUY MARKET FAILED. Code={res.retcode if res else 'N/A'}.", "ERROR")
            return

    # =======================================================
    #  SECTION 13 -- SILVER BULLET SETUP EVALUATION
    # =======================================================
#  SECTION 12B -- SETUP EVALUATION: SILVER BULLET MODE
# =======================================================

def evaluate_silver_bullet(df, fvgs, sweep, sweep_level, price, atr,
                           lot_size, window, unicorn_zones=None):
    """
    ICT Silver Bullet -- time-precision model.
    No trend filter. No ADX. No zone filter.
    The 1-hour window is the primary confluence filter.
    Market orders retained in v4.0 -- limit order conversion is INGWE only.
    """
    if unicorn_zones is None:
        unicorn_zones = []

    spread      = get_spread()
    spread_pips = spread * 10000 if spread else 0
    log(f"Price: {price:.5f}  |  ATR: {atr:.5f}  |  "
        f"Lot: {lot_size}  |  Spread: {spread_pips:.1f}p")

    # ── UNICORN PATH ─────────────────────────────────────
    if unicorn_zones:
        unicorn_zones_sorted = sorted(
            unicorn_zones,
            key=lambda u: abs(price - (u[1] + u[2]) / 2)
        )

        for u_type, u_low, u_high, u_mid, bb_low, bb_high in unicorn_zones_sorted:

            if u_type == "BULLISH_UNICORN" and sweep == "SWEEP_LOW":
                log(f"UNICORN BULLISH zone: {u_low:.5f}-{u_high:.5f}  |  "
                    f"Mid: {u_mid:.5f}  |  BB: {bb_low:.5f}-{bb_high:.5f}")
                if price > u_high:
                    log("Price above Unicorn zone -- waiting for retracement.", "GUARD")
                    continue
                if price < u_low:
                    log("Price below Unicorn zone -- not yet in range.", "GUARD")
                    continue
                if check_panic_candle(df, atr):
                    continue
                if not check_pre_trade_spread(atr):
                    continue
                
                ctx = {
                    "direction": "BUY",
                    "setup_type": "UNICORN",
                    "sweep": sweep,
                    "fvg_type": u_type,
                    "fvg_position": "in_zone",
                    "bos_aligned": False,
                    "htf_bias_ok": False,
                    "confluence_score": 80,
                    "session": window,
                    "atr": atr,
                    "spread_ok": check_pre_trade_spread(atr),
                    "trend": "BULLISH",
                    "level_sweep": True,
                    "ob_present": False,
                    "fvg_low": 0,
                    "fvg_high": 0,
                    "fvg_50": 0
                }
                if KRONOS_VETO_GATE is not None:
                    allowed, reason = KRONOS_VETO_GATE.validate(ctx, df, SYMBOL)
                    log(f"[KRONOS] BUY signal: {reason}", "GUARD")
                    if not allowed:
                        log(f"Kronos vetoed BUY. Skipping trade.", "GUARD")
                        return
                
                entry   = round_to_tick(price, SYMBOL)
                sl      = round_to_tick(bb_low - atr * ATR_MULTIPLIER, SYMBOL)
                sl_dist = abs(entry - sl)
                tp      = round_to_tick(entry + sl_dist * RISK_REWARD_RATIO, SYMBOL)
                res     = place_trade("BUY", entry, sl, tp, lot_size, session=window)
                if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                    log(f"[UNICORN] UNICORN BUY  Entry={entry}  SL={sl}  TP={tp}  "
                        f"Lot={lot_size}", "TRADE")
                    log_trade("BUY", entry, sl, tp, res, lot_size, window, context=ctx, kronos_gate=KRONOS_VETO_GATE)
                    sessions_traded_today.add(window)
                    save_sessions(sessions_traded_today)
                else:
                    log(f"UNICORN BUY FAILED. "
                        f"Code={res.retcode if res else 'N/A'}.", "ERROR")
                return

            if u_type == "BEARISH_UNICORN" and sweep == "SWEEP_HIGH":
                log(f"UNICORN BEARISH zone: {u_low:.5f}-{u_high:.5f}  |  "
                    f"Mid: {u_mid:.5f}  |  BB: {bb_low:.5f}-{bb_high:.5f}")
                if price < u_low:
                    log("Price below Unicorn zone -- waiting for retracement.", "GUARD")
                    continue
                if price > u_high:
                    log("Price above Unicorn zone -- not yet in range.", "GUARD")
                    continue
                if check_panic_candle(df, atr):
                    continue
                if not check_pre_trade_spread(atr):
                    continue
                
                ctx = {
                    "direction": "SELL",
                    "setup_type": "UNICORN",
                    "sweep": sweep,
                    "fvg_type": u_type,
                    "fvg_position": "in_zone",
                    "bos_aligned": False,
                    "htf_bias_ok": False,
                    "confluence_score": 80,
                    "session": window,
                    "atr": atr,
                    "spread_ok": check_pre_trade_spread(atr),
                    "trend": "BEARISH",
                    "level_sweep": True,
                    "ob_present": False,
                    "fvg_low": 0,
                    "fvg_high": 0,
                    "fvg_50": 0
                }
                if KRONOS_VETO_GATE is not None:
                    allowed, reason = KRONOS_VETO_GATE.validate(ctx, df, SYMBOL)
                    log(f"[KRONOS] SELL signal: {reason}", "GUARD")
                    if not allowed:
                        log(f"Kronos vetoed SELL. Skipping trade.", "GUARD")
                        return
                
                entry   = round_to_tick(price, SYMBOL)
                sl      = round_to_tick(bb_high + atr * ATR_MULTIPLIER, SYMBOL)
                sl_dist = abs(sl - entry)
                tp      = round_to_tick(entry - sl_dist * RISK_REWARD_RATIO, SYMBOL)
                res     = place_trade("SELL", entry, sl, tp, lot_size, session=window)
                if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                    log(f"[UNICORN] UNICORN SELL  Entry={entry}  SL={sl}  TP={tp}  "
                        f"Lot={lot_size}", "TRADE")
                    log_trade("SELL", entry, sl, tp, res, lot_size, window, context=ctx, kronos_gate=KRONOS_VETO_GATE)
                    sessions_traded_today.add(window)
                    save_sessions(sessions_traded_today)
                else:
                    log(f"UNICORN SELL FAILED. "
                        f"Code={res.retcode if res else 'N/A'}.", "ERROR")
                return

        log("Unicorn zones present but not aligned. Falling back to FVG path.")

    # ── STANDARD SILVER BULLET PATH ──────────────────────
    for fvg_type, fvg_low, fvg_high, fvg_idx, ob, fvg_50 in fvgs:
        if check_panic_candle(df, atr):
            continue

        if sweep == "SWEEP_LOW" and fvg_type == "BULLISH_FVG":
            log(f"SB Bullish FVG: {fvg_low:.5f}-{fvg_high:.5f}  |  50%: {fvg_50:.5f}")
            if price > fvg_high:
                log("Price above FVG -- waiting for retracement into gap.", "GUARD")
                continue
            if price > fvg_50:
                log(f"Price in FVG but above 50% ({fvg_50:.5f}) -- waiting deeper.",
                    "GUARD")
                continue
            if not check_pre_trade_spread(atr):
                continue

            dol_name, dol_price = get_draw_on_liquidity("BUY")
            ctx = {
                "direction": "BUY",
                "setup_type": "SILVER_BULLET",
                "sweep": sweep,
                "fvg_type": fvg_type,
                "fvg_position": "below_50" if price <= fvg_50 else ("50%" if abs(price - fvg_50) < atr * 0.1 else "above_50"),
                "bos_aligned": False,
                "htf_bias_ok": False,
                "confluence_score": 70,
                "session": window,
                "atr": atr,
                "spread_ok": check_pre_trade_spread(atr),
                "trend": "BULLISH",
                "level_sweep": True,
                "draw_on_liquidity": dol_name,
                "dol_price": dol_price,
                "distance_to_dol": abs(dol_price - price) if dol_price else None,
                "sb_window": window,
                "ob_present": ob is not None,
                "fvg_low": fvg_low,
                "fvg_high": fvg_high,
                "fvg_50": fvg_50
            }
            if KRONOS_VETO_GATE is not None:
                allowed, reason = KRONOS_VETO_GATE.validate(ctx, df, SYMBOL)
                log(f"[KRONOS] BUY signal: {reason}", "GUARD")
                if not allowed:
                    log(f"Kronos vetoed BUY. Skipping trade.", "GUARD")
                    return

            entry   = round_to_tick(price, SYMBOL)
            calculated_sl = sweep_level - atr * ATR_MULTIPLIER
            min_sl_distance = atr * MIN_SL_ATR_MULTIPLIER
            if (entry - calculated_sl) < min_sl_distance:
                sl = round_to_tick(entry - min_sl_distance, SYMBOL)
            else:
                sl = round_to_tick(calculated_sl, SYMBOL)
            sl_dist = abs(entry - sl)
            tp      = round_to_tick(entry + sl_dist * RISK_REWARD_RATIO, SYMBOL)
            res     = place_trade("BUY", entry, sl, tp, lot_size, session=window)
            if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                log(f"SB BUY  Entry={entry}  SL={sl}  TP={tp}  Lot={lot_size}", "TRADE")
                log_trade("BUY", entry, sl, tp, res, lot_size, window, context=ctx, kronos_gate=KRONOS_VETO_GATE)
                sessions_traded_today.add(window)
                save_sessions(sessions_traded_today)
            else:
                log(f"SB BUY FAILED. Code={res.retcode if res else 'N/A'}.", "ERROR")
            return

        if sweep == "SWEEP_HIGH" and fvg_type == "BEARISH_FVG":
            log(f"SB Bearish FVG: {fvg_low:.5f}-{fvg_high:.5f}  |  50%: {fvg_50:.5f}")
            if price < fvg_low:
                log("Price below FVG -- waiting for retracement into gap.", "GUARD")
                continue
            if price < fvg_50:
                log(f"Price in FVG but below 50% ({fvg_50:.5f}) -- waiting deeper.",
                    "GUARD")
                continue
            if not check_pre_trade_spread(atr):
                continue

            dol_name, dol_price = get_draw_on_liquidity("SELL")
            ctx = {
                "direction": "SELL",
                "setup_type": "SILVER_BULLET",
                "sweep": sweep,
                "fvg_type": fvg_type,
                "fvg_position": "above_50" if price >= fvg_50 else ("50%" if abs(price - fvg_50) < atr * 0.1 else "below_50"),
                "bos_aligned": False,
                "htf_bias_ok": False,
                "confluence_score": 70,
                "session": window,
                "atr": atr,
                "spread_ok": check_pre_trade_spread(atr),
                "trend": "BEARISH",
                "level_sweep": True,
                "draw_on_liquidity": dol_name,
                "dol_price": dol_price,
                "distance_to_dol": abs(dol_price - price) if dol_price else None,
                "sb_window": window,
                "ob_present": ob is not None,
                "fvg_low": fvg_low,
                "fvg_high": fvg_high,
                "fvg_50": fvg_50
            }
            if KRONOS_VETO_GATE is not None:
                allowed, reason = KRONOS_VETO_GATE.validate(ctx, df, SYMBOL)
                log(f"[KRONOS] SELL signal: {reason}", "GUARD")
                if not allowed:
                    log(f"Kronos vetoed SELL. Skipping trade.", "GUARD")
                    return

            entry   = round_to_tick(price, SYMBOL)
            calculated_sl = sweep_level + atr * ATR_MULTIPLIER
            min_sl_distance = atr * MIN_SL_ATR_MULTIPLIER
            if (calculated_sl - entry) < min_sl_distance:
                sl = round_to_tick(entry + min_sl_distance, SYMBOL)
            else:
                sl = round_to_tick(calculated_sl, SYMBOL)
            sl_dist = abs(sl - entry)
            tp      = round_to_tick(entry - sl_dist * RISK_REWARD_RATIO, SYMBOL)
            res     = place_trade("SELL", entry, sl, tp, lot_size, session=window)
            if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                log(f"SB SELL  Entry={entry}  SL={sl}  TP={tp}  Lot={lot_size}", "TRADE")
                log_trade("SELL", entry, sl, tp, res, lot_size, window, context=ctx, kronos_gate=KRONOS_VETO_GATE)
                sessions_traded_today.add(window)
                save_sessions(sessions_traded_today)
            else:
                log(f"SB SELL FAILED. Code={res.retcode if res else 'N/A'}.", "ERROR")
            return

    log("SB: No valid FVG retracement. Ingwe waits...")


# =======================================================
#  SECTION 12C -- SETUP EVALUATION: LONDON BREAKOUT MODE
# =======================================================

def evaluate_london_breakout(df, fvgs, sweep, sweep_level, price, atr,
                             lot_size, session):
    """
    ICT London Breakout pattern.
    1. Asian range established (00:00-04:00 UTC)
    2. Breakout of Asian range during London Open killzone
    3. Retest of the break level (Asian range high/low)
    4. Entry on retest confirmation near the break level

    Key levels: Asian Range High/Low.
    No HTF bias or ADX -- breakout direction is the trend.
    """
    pdh, pdl = get_pdh_pdl()
    asian_high, asian_low = get_asian_range(df)

    if not asian_high or not asian_low:
        log("London Breakout: No Asian range established. Waiting...")
        return

    if pdh and pdl:
        log(f"PDH: {pdh:.5f}  |  PDL: {pdl:.5f}")
    log(f"Asian Range: {asian_low:.5f} - {asian_high:.5f}")

    spread = get_spread()
    spread_pips = spread * 10000 if spread else 0
    spread_ok = spread is not None and spread < MIN_SPREAD_PIPS
    log(f"Price: {price:.5f}  |  ATR: {atr:.5f}  |  "
        f"Lot: {lot_size}  |  Spread: {spread_pips:.1f}p")

    for fvg_type, fvg_low, fvg_high, fvg_idx, ob, fvg_50 in fvgs:
        if check_panic_candle(df, atr):
            continue

        # ── LONDON BREAKOUT: BUY ─────────────────────────
        # Sweep below Asian low / PDL, then bullish FVG above Asian high = breakout
        if sweep == "SWEEP_LOW" and fvg_type == "BULLISH_FVG":
            if fvg_low < asian_high:
                log("London Breakout: FVG not above Asian high -- not a confirmed breakout. Waiting.", "GUARD")
                continue

            level_sweep = False
            if pdl and abs(sweep_level - pdl) < atr * 0.5:
                level_sweep = True
                log(f"PDL SWEEP: {sweep_level:.5f} ~ PDL {pdl:.5f}  [+5]")
            if asian_low and abs(sweep_level - asian_low) < atr * 0.5:
                level_sweep = True
                log(f"ASIAN LOW SWEEP: {sweep_level:.5f} ~ AR Low {asian_low:.5f}  [+5]")

            retest_zone_low = asian_high - atr * 0.3
            retest_zone_high = asian_high + atr * 0.3
            in_retest = retest_zone_low <= price <= retest_zone_high

            if not in_retest:
                log(f"London Breakout: Price {price:.5f} outside retest zone "
                    f"({retest_zone_low:.5f}-{retest_zone_high:.5f}). Waiting for retest.", "GUARD")
                continue

            if not check_premium_discount_zone(df, price, "BUY"):
                log("Not in discount zone. Skip.", "GUARD")
                continue

            score = 70
            if level_sweep:
                score += 10
            if spread_ok:
                score += 10
            if fvg_low > asian_high + atr * 0.5:
                score += 10

            log(f"Confluence [LONDON BREAKOUT BUY]: {score}/100")

            if not check_pre_trade_spread(atr):
                continue

            ctx = {
                "direction": "BUY",
                "setup_type": "LONDON_BREAKOUT",
                "sweep": sweep,
                "fvg_type": fvg_type,
                "fvg_position": "below_50" if price <= fvg_50 else ("50%" if abs(price - fvg_50) < atr * 0.1 else "above_50"),
                "bos_aligned": False,
                "htf_bias_ok": False,
                "confluence_score": score,
                "session": session,
                "atr": atr,
                "spread_ok": spread_ok,
                "trend": "BULLISH",
                "level_sweep": level_sweep,
                "asian_high": asian_high,
                "asian_low": asian_low,
                "ob_present": ob is not None,
                "fvg_low": fvg_low,
                "fvg_high": fvg_high,
                "fvg_50": fvg_50
            }
            if KRONOS_VETO_GATE is not None:
                allowed, reason = KRONOS_VETO_GATE.validate(ctx, df, SYMBOL)
                log(f"[KRONOS] BUY signal: {reason}", "GUARD")
                if not allowed:
                    log(f"Kronos vetoed BUY. Skipping trade.", "GUARD")
                    return

            stop = max(atr * ATR_MULTIPLIER, atr * MIN_SL_ATR_MULTIPLIER)
            entry = round_to_tick(price, SYMBOL)
            if asian_low:
                sl = round_to_tick(max(entry - stop, asian_low - atr * 0.3), SYMBOL)
            else:
                sl = round_to_tick(entry - stop, SYMBOL)
            if score >= 90:
                dynamic_rr = RISK_REWARD_RATIO
            elif score >= 80:
                dynamic_rr = RISK_REWARD_RATIO - 0.5
            else:
                dynamic_rr = RISK_REWARD_RATIO - 1.0
            tp = round_to_tick(entry + stop * dynamic_rr, SYMBOL)
            print(f"[DEBUG_ENG] Symbol={SYMBOL} | Strategy=LONDON_OPEN | "
                  f"Dir=BUY | Entry={entry} | SL={sl} | TP={tp} | "
                  f"Stop={stop} | Active_RRR={dynamic_rr}")
            res = place_trade("BUY", entry, sl, tp, lot_size, session=session)
            if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                log(f"LONDON BREAKOUT BUY  Entry={entry}  SL={sl}  TP={tp}  Lot={lot_size}", "TRADE")
                log_trade("BUY", entry, sl, tp, res, lot_size, session, context=ctx, kronos_gate=KRONOS_VETO_GATE)
                sessions_traded_today.add(session)
                save_sessions(sessions_traded_today)
            else:
                log(f"LONDON BREAKOUT BUY FAILED. Code={res.retcode if res else 'N/A'}.", "ERROR")
            return

        # ── LONDON BREAKOUT: SELL ────────────────────────
        # Sweep above Asian high / PDH, then bearish FVG below Asian low = breakout
        if sweep == "SWEEP_HIGH" and fvg_type == "BEARISH_FVG":
            if fvg_high > asian_low:
                log("London Breakout: FVG not below Asian low -- not a confirmed breakout. Waiting.", "GUARD")
                continue

            level_sweep = False
            if pdh and abs(sweep_level - pdh) < atr * 0.5:
                level_sweep = True
                log(f"PDH SWEEP: {sweep_level:.5f} ~ PDH {pdh:.5f}  [+5]")
            if asian_high and abs(sweep_level - asian_high) < atr * 0.5:
                level_sweep = True
                log(f"ASIAN HIGH SWEEP: {sweep_level:.5f} ~ AR High {asian_high:.5f}  [+5]")

            retest_zone_low = asian_low - atr * 0.3
            retest_zone_high = asian_low + atr * 0.3
            in_retest = retest_zone_low <= price <= retest_zone_high

            if not in_retest:
                log(f"London Breakout: Price {price:.5f} outside retest zone "
                    f"({retest_zone_low:.5f}-{retest_zone_high:.5f}). Waiting for retest.", "GUARD")
                continue

            if not check_premium_discount_zone(df, price, "SELL"):
                log("Not in premium zone. Skip.", "GUARD")
                continue

            score = 70
            if level_sweep:
                score += 10
            if spread_ok:
                score += 10
            if fvg_high < asian_low - atr * 0.5:
                score += 10

            log(f"Confluence [LONDON BREAKOUT SELL]: {score}/100")

            if not check_pre_trade_spread(atr):
                continue

            ctx = {
                "direction": "SELL",
                "setup_type": "LONDON_BREAKOUT",
                "sweep": sweep,
                "fvg_type": fvg_type,
                "fvg_position": "above_50" if price >= fvg_50 else ("50%" if abs(price - fvg_50) < atr * 0.1 else "below_50"),
                "bos_aligned": False,
                "htf_bias_ok": False,
                "confluence_score": score,
                "session": session,
                "atr": atr,
                "spread_ok": spread_ok,
                "trend": "BEARISH",
                "level_sweep": level_sweep,
                "asian_high": asian_high,
                "asian_low": asian_low,
                "ob_present": ob is not None,
                "fvg_low": fvg_low,
                "fvg_high": fvg_high,
                "fvg_50": fvg_50
            }
            if KRONOS_VETO_GATE is not None:
                allowed, reason = KRONOS_VETO_GATE.validate(ctx, df, SYMBOL)
                log(f"[KRONOS] SELL signal: {reason}", "GUARD")
                if not allowed:
                    log(f"Kronos vetoed SELL. Skipping trade.", "GUARD")
                    return

            stop = max(atr * ATR_MULTIPLIER, atr * MIN_SL_ATR_MULTIPLIER)
            entry = round_to_tick(price, SYMBOL)
            if asian_high:
                sl = round_to_tick(min(entry + stop, asian_high + atr * 0.3), SYMBOL)
            else:
                sl = round_to_tick(entry + stop, SYMBOL)
            if score >= 90:
                dynamic_rr = RISK_REWARD_RATIO
            elif score >= 80:
                dynamic_rr = RISK_REWARD_RATIO - 0.5
            else:
                dynamic_rr = RISK_REWARD_RATIO - 1.0
            tp = round_to_tick(entry - stop * dynamic_rr, SYMBOL)
            print(f"[DEBUG_ENG] Symbol={SYMBOL} | Strategy=LONDON_OPEN | "
                  f"Dir=SELL | Entry={entry} | SL={sl} | TP={tp} | "
                  f"Stop={stop} | Active_RRR={dynamic_rr}")
            res = place_trade("SELL", entry, sl, tp, lot_size, session=session)
            if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                log(f"LONDON BREAKOUT SELL  Entry={entry}  SL={sl}  TP={tp}  Lot={lot_size}", "TRADE")
                log_trade("SELL", entry, sl, tp, res, lot_size, session, context=ctx, kronos_gate=KRONOS_VETO_GATE)
                sessions_traded_today.add(session)
                save_sessions(sessions_traded_today)
            else:
                log(f"LONDON BREAKOUT SELL FAILED. Code={res.retcode if res else 'N/A'}.", "ERROR")
            return

    log("London Breakout: No valid setup. Ingwe waits...")


# =======================================================
#  SECTION 12D -- SETUP EVALUATION: ICT M1 SCALPER MODE
# =======================================================

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
                "spread_ok": spread is not None and spread < MIN_SPREAD_PIPS,
                "trend": "BULLISH",
                "level_sweep": True,
                "ob_present": ob is not None,
                "fvg_low": fvg_low,
                "fvg_high": fvg_high,
                "fvg_50": fvg_50
            }
            if KRONOS_VETO_GATE is not None:
                allowed, reason = KRONOS_VETO_GATE.validate(ctx, df, SYMBOL)
                log(f"[KRONOS] BUY signal: {reason}", "GUARD")
                if not allowed:
                    log(f"Kronos vetoed BUY. Skipping trade.", "GUARD")
                    return

            stop = max(atr * ATR_MULTIPLIER, atr * MIN_SL_ATR_MULTIPLIER)
            entry = round_to_tick(price, SYMBOL)
            sl = round_to_tick(entry - stop, SYMBOL)
            tp = round_to_tick(entry + stop * RISK_REWARD_RATIO, SYMBOL)
            res = place_trade("BUY", entry, sl, tp, lot_size, session=session)
            if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                log(f"M1 BUY  Entry={entry}  SL={sl}  TP={tp}  Lot={lot_size}", "TRADE")
                log_trade("BUY", entry, sl, tp, res, lot_size, session, context=ctx, kronos_gate=KRONOS_VETO_GATE)
                sessions_traded_today.add(session)
                save_sessions(sessions_traded_today)
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
                "spread_ok": spread is not None and spread < MIN_SPREAD_PIPS,
                "trend": "BEARISH",
                "level_sweep": True,
                "ob_present": ob is not None,
                "fvg_low": fvg_low,
                "fvg_high": fvg_high,
                "fvg_50": fvg_50
            }
            if KRONOS_VETO_GATE is not None:
                allowed, reason = KRONOS_VETO_GATE.validate(ctx, df, SYMBOL)
                log(f"[KRONOS] SELL signal: {reason}", "GUARD")
                if not allowed:
                    log(f"Kronos vetoed SELL. Skipping trade.", "GUARD")
                    return

            stop = max(atr * ATR_MULTIPLIER, atr * MIN_SL_ATR_MULTIPLIER)
            entry = round_to_tick(price, SYMBOL)
            sl = round_to_tick(entry + stop, SYMBOL)
            tp = round_to_tick(entry - stop * RISK_REWARD_RATIO, SYMBOL)
            res = place_trade("SELL", entry, sl, tp, lot_size, session=session)
            if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                log(f"M1 SELL  Entry={entry}  SL={sl}  TP={tp}  Lot={lot_size}", "TRADE")
                log_trade("SELL", entry, sl, tp, res, lot_size, session, context=ctx, kronos_gate=KRONOS_VETO_GATE)
                sessions_traded_today.add(session)
                save_sessions(sessions_traded_today)
            else:
                log(f"M1 SELL FAILED. Code={res.retcode if res else 'N/A'}.", "ERROR")
            return

    log("M1: No valid FVG sweep. Ingwe waits...")


# =======================================================
#  SECTION 12 -- MAIN SCAN LOOP
# =======================================================

def run_agent():
    update_memory()
    sast_now = now_sast()
    mkt_mode = "SUMMER" if is_eu_summer() else "WINTER"

    print(f"\n{'-' * 60}")
    log(f"Scan: {sast_now.strftime('%Y-%m-%d %H:%M')} SAST  "
        f"| {STRATEGY}  | {mkt_mode}  | Exness UTC+{get_exness_server_offset()}")
    print(f"{'-' * 60}")

    reset_daily_sessions()

    if not is_market_open():
        log(f"Weekend -- market closed ({sast_now.strftime('%A')}). Ingwe sleeps.")
        return
    if check_equity_drawdown():
        return
    
    update_consecutive_losses()
    if check_consecutive_losses():
        log("Consecutive loss limit reached -- Ingwe pauses.", "GUARD")
        return

    # ── v3.9.5: Manage open positions every cycle ────────
    manage_open_positions()

    if is_in_news_blackout():
        log("News blackout -- Ingwe waits...")
        return
    if is_in_dead_zone():
        dz = "13:00-16:00" if not is_eu_summer() else "12:00-15:00"
        log(f"Dead zone ({dz} SAST) -- no strategy hunts here. Ingwe waits.")
        return

    daily_pnl = get_daily_pnl()
    log(f"Daily P&L [{_instance_tag}]: {daily_pnl:.2f} USC")
    if daily_pnl <= -MAX_DAILY_LOSS:
        log(f"Daily loss limit reached. Ingwe rests.", "GUARD")
        return
    
    # P0-FULL: Circuit breaker check (weekly cap)
    cb_allowed, cb_reason = TRADING_GOVERNOR.check_circuit_breakers(daily_pnl, _instance_tag)
    if not cb_allowed:
        log(f"Circuit breaker: {cb_reason}. Ingwe rests.", "GUARD")
        return

    # ── ACTIVE WINDOW (strategy-aware) ──────────────────
    if STRATEGY == "SILVER_BULLET":
        active = get_current_sb_window()
        if not active:
            log("No Silver Bullet window active. Ingwe watches...")
            return
        s, e = get_active_sb_windows()[active]
        log(f"SB WINDOW: {active} ({s:02d}:00-{e:02d}:00 SAST)")
    else:
        active = get_current_session()
        if not active:
            log("No killzone active. Ingwe watches...")
            return
        
        # P0-A / P0-C: Session filter gate
        allowed, reason = TRADING_GOVERNOR.check_session(active, _instance_tag)
        if not allowed:
            log(f"Session filtered: {active} ({reason}). Ingwe waits.", "GUARD")
            return
        
        s, e = get_active_killzones()[active]
        log(f"KILLZONE: {active} ({s:02d}:00-{e:02d}:00 SAST)")

    if active in sessions_traded_today:
        log(f"Already traded {active} today. Ingwe waits.")
        return

    # ── CANDLES ──────────────────────────────────────────
    df = get_candles()
    session_for_staleness = "Asian" if active and "Asian" in str(active) else None
    if not validate_candles(df, session_for_staleness):
        log("Data validation failed. Ingwe will not trade on uncertain ground.", "GUARD")
        return

    # ── SWEEP ────────────────────────────────────────────
    sweep, sweep_level = detect_liquidity_sweep(df)
    if not sweep:
        log("No sweep detected. Ingwe waits...")
        return
    log(f"SWEEP: {sweep} at {sweep_level:.5f}")

    # ── FVG ──────────────────────────────────────────────
    if STRATEGY == "SILVER_BULLET":
        fvg_lookback = 8
    elif STRATEGY == "ICT_M1":
        fvg_lookback = 40
    else:
        fvg_lookback = 20
    fvgs = detect_fvg(df, max_age=fvg_lookback)
    if not fvgs:
        fvgs = detect_immediate_fvg(df)
        if not fvgs:
            label = "within current window" if STRATEGY == "SILVER_BULLET" else "within 5hr lookback"
            log(f"No FVG {label}. Ingwe waits...")
            return
        log("FVG found via immediate detection (post-sweep).")

    # ── BREAKER BLOCKS & UNICORN ZONES (SILVER BULLET only) ─
    breakers      = detect_breaker_blocks(df) if STRATEGY == "SILVER_BULLET" else []
    unicorn_zones = detect_unicorn_zone(fvgs, breakers) if breakers else []
    if unicorn_zones:
        log(f"UNICORN ZONES DETECTED: {len(unicorn_zones)} -- highest confluence active.")

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
        evaluate_silver_bullet(df, fvgs, sweep, sweep_level, price, atr,
                               lot_size, active, unicorn_zones)
    elif STRATEGY == "LONDON_OPEN":
        evaluate_london_breakout(df, fvgs, sweep, sweep_level, price, atr,
                                 lot_size, active)
    elif STRATEGY == "ICT_M1":
        evaluate_ict_m1(df, fvgs, sweep, sweep_level, price, atr,
                        lot_size, active)
    else:
        evaluate_ingwe(df, fvgs, sweep, sweep_level, price, atr, lot_size, active)


# =======================================================
#  SECTION 13 -- EXECUTION MODES
# =======================================================

def fallback_polling_loop():
    """Legacy polling loop fallback if tick engine unavailable"""
    log("Running in polling fallback mode (fixed interval scanning)")
    try:
        while True:
            run_agent()
            print(); import sys; sys.stdout.flush()
            time.sleep(SCAN_INTERVAL_SEC)
    except KeyboardInterrupt:
        log("Polling loop interrupted by user. Ingwe stands down gracefully.")


# =======================================================
#  SECTION 14 -- BOOT SEQUENCE
# =======================================================

if __name__ == "__main__":

    _errors = []
    if not (0 < RISK_PERCENT <= 5.0):
        _errors.append(f"RISK_PERCENT {RISK_PERCENT} out of range (0-5%)")
    if not (0.01 <= HARD_LOT_CAP <= 1.0):
        _errors.append(f"HARD_LOT_CAP {HARD_LOT_CAP} out of range (0.01-1.0)")
    if RISK_REWARD_RATIO < 1.0:
        _errors.append(f"RISK_REWARD_RATIO {RISK_REWARD_RATIO} must be >= 1.0")
    if MAX_DAILY_LOSS <= 0:
        _errors.append(f"MAX_DAILY_LOSS {MAX_DAILY_LOSS} must be positive")
    if MAX_DRAWDOWN_PCT <= 0 or MAX_DRAWDOWN_PCT > 50:
        _errors.append(f"MAX_DRAWDOWN_PCT {MAX_DRAWDOWN_PCT} out of range (0-50%)")
    min_scan = 15 if STRATEGY == "ICT_M1" else 60
    if SCAN_INTERVAL_SEC < min_scan:
        _errors.append(f"SCAN_INTERVAL_SEC {SCAN_INTERVAL_SEC} dangerously low (min {min_scan}s)")
    if ATR_PERIOD < 5:
        _errors.append(f"ATR_PERIOD {ATR_PERIOD} too low -- minimum 5 for meaningful ATR")
    if DATA_STALE_MINUTES < SCAN_INTERVAL_SEC / 60:
        _errors.append(
            f"DATA_STALE_MINUTES ({DATA_STALE_MINUTES}) must be >= scan interval "
            f"({SCAN_INTERVAL_SEC/60:.0f} min)"
        )
    if _errors:
        print("[ERROR] CONFIG VALIDATION FAILED:")
        for e in _errors:
            print(f"   - {e}")
        sys.exit(1)

    summer = is_eu_summer()
    print("=" * 60)
    print("   PROJECT VUKA -- AGENT INGWE  v4.4")
    print("   The leopard does not miss because it does not rush.")
    print("=" * 60)
    print()
    print(f"   Symbol:         {SYMBOL}  ({_arg_symbol})")
    print(f"   Strategy:       {STRATEGY}")
    print(f"   Instance:       {_instance_tag}")
    print(f"   Magic number:   {_instance_magic}  (SHA-256, stable across restarts)")
    print(f"   Location:       South Africa (SAST = UTC+2, no DST)")
    print(f"   Broker:         Exness MT5  (USC cent account)")
    print(f"   Market mode:    {'SUMMER (EU DST active)' if summer else 'WINTER (EU standard time)'}")
    print(f"   Exness server:  UTC+{get_exness_server_offset()}")
    print(f"   Log file:       {LOG_FILE}")
    print()

    if STRATEGY == "SILVER_BULLET":
        print("   ACTIVE SILVER BULLET WINDOWS (SAST):")
        for name, (s, e) in get_active_sb_windows().items():
            ny_offset = -6 if summer else -7
            ny_s = (s + ny_offset) % 24
            ny_e = (e + ny_offset) % 24
            print(f"     {name:<14} {s:02d}:00-{e:02d}:00 SAST   ({ny_s:02d}:00-{ny_e:02d}:00 NY)")
    else:
        print("   ACTIVE KILLZONES (SAST):")
        for name, (s, e) in get_active_killzones().items():
            print(f"     {name:<18} {s:02d}:00-{e:02d}:00")
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
    log(f"Daily P&L tracking:     {_instance_tag} only (magic: {_instance_magic})")
    log(f"Trailing SL:            1:1 -> BE  |  1:2 -> 1:1  (v3.9.5)")
    log(f"Entry mode:            MARKET orders  (v4.2 -- reverted from limit)")
    log(f"Ingwe is awake. [{_instance_tag}] hunting begins.\n")

    try:
        if _arg_check or _arg_test:
            log("CHECK MODE: Running single scan and exiting.")
            print()
            run_agent()
            print(); import sys; sys.stdout.flush()
            log("Check complete. Ingwe stands down.")
        elif _arg_backtest:
            log("BACKTEST MODE: Polling loop active.")
            if _arg_fast:
                log("FAST MODE: No real-time delay. Processing at maximum speed.")
            while True:
                run_agent()
                print(); import sys; sys.stdout.flush()
                if not run_backtest_step():
                    break
                if not _arg_fast:
                    time.sleep(SCAN_INTERVAL_SEC)
            log("Backtest finished. See trade log and JSON results.")
        else:
            # Phase 1: Event-driven tick engine (production)
            if TICK_ENGINE_AVAILABLE:
                try:
                    def on_candle_open(candle_time):
                        """Callback triggered on new candle from MT5 tick stream"""
                        run_agent()
                    
                    engine = TickEngine(
                        symbol=SYMBOL,
                        timeframe=TIMEFRAME,
                        callback=on_candle_open,
                        verbose=False,
                        max_idle_seconds=CONFIG.get("tick_engine", {}).get("heartbeat_seconds", 180),
                        api_timeout=CONFIG.get("tick_engine", {}).get("api_timeout", 30),
                    )
                    log(f"Starting event-driven loop: {SYMBOL} @ {TIMEFRAME}")
                    engine.run()  # Blocks forever, executes on candle events
                except KeyboardInterrupt:
                    log("Tick engine interrupted by user. Ingwe stands down gracefully.")
                except Exception as e:
                    log(f"Tick engine error: {e}. Falling back to polling mode.", "ERROR")
                    fallback_polling_loop()
            else:
                # Fallback: Polling mode (no tick engine available)
                log("Tick engine unavailable. Using polling fallback.")
                fallback_polling_loop()
    except KeyboardInterrupt:
        log("Keyboard interrupt. Ingwe stands down gracefully.")
    finally:
        mt5.shutdown()
        log("MT5 disconnected. Until next sunrise.")