import argparse
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


__version__ = "6.1.0"

# Shared runtime state -- all extracted modules use s.NAME to see updates
from vuka.core.state import s


# Project root for importing skills/
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# ---- ARGUMENT PARSING ------------------------------------------------
def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Agent Ingwe -- ICT Trading Bot")
    parser.add_argument("symbol", choices=["EURUSD", "GBPUSD", "USDJPY", "BTCUSD"])
    parser.add_argument("strategy", choices=["INGWE", "SILVER_BULLET", "ICT_M1", "LONDON_OPEN"])
    parser.add_argument("--backtest", action="store_true")
    parser.add_argument("--test", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--fast", action="store_true")
    return parser


# Import-safe defaults. parse_args() runs only inside main(), so importing
# this module (dashboard, monitor, test harness) never touches sys.argv and
# never exits with SystemExit. main() overwrites these with real values.
_arg_symbol = ""
_arg_strategy = ""
_arg_check = False
_arg_backtest = False
_arg_fast = False
_arg_test = False

# Core infrastructure
from vuka.core.health_monitor import HealthMonitor
from vuka.ai.kronos_guardian import KronosVetoGate, create_veto_gate
from vuka.data.database_manager import get_db
from vuka.utils.unified_logger import get_logger
from skills.trading_governor import TradingGovernor
from skills.concept_tracker import record_concept_trade, record_concept_outcome
from skills.market_circuit import get_circuit

# Initialize Memory Manager
# =======================================================
# IMPORT EXTRACTED MODULES (from monolithic ingwe.py)
# =======================================================
from vuka.risk.portfolio import (
    get_initial_equity, check_equity_drawdown, get_daily_pnl,
    check_consecutive_losses, load_consecutive_losses,
    save_consecutive_losses, update_consecutive_losses, get_spread,
    _server_midnight, _server_now, calculate_lot_size, get_overlap_multiplier,
)
from vuka.market_structure.ict import (
    detect_liquidity_sweep, check_displacement_validity,
    detect_fvg, detect_immediate_fvg, detect_breaker_blocks,
    detect_unicorn_zone, detect_m15_bos,
    calculate_adx_wilder, calculate_atr,
)
from vuka.risk.filters import (
    get_current_session, is_in_dead_zone, get_current_sb_window,
    is_in_news_blackout, check_panic_candle,
    check_premium_discount_zone, check_pre_trade_spread,
)
from vuka.execution.orders import (
    log_trade, place_trade, has_pending_order, has_open_position,
    place_limit_order, _modify_sl, log_sl_move, round_to_tick,
)
from vuka.execution.position_manager import manage_open_positions
from vuka.strategies.ingwe import evaluate_ingwe
from vuka.strategies.silver_bullet import evaluate_silver_bullet
from vuka.strategies.london_open import evaluate_london_breakout
from vuka.strategies.ict_m1 import evaluate_ict_m1
from vuka.core.config import (
    get_session_multiplier, get_confluence_threshold, calculate_confluence_score,
    load_config, _derive_magic, _SYMBOL_MAP,
)


# Phase 1: Event-driven tick engine (replaces polling)
try:
    from vuka.core.tick_engine_v5 import TickEngine
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
    "mode": _veto_cfg.get("mode", "enforced"),
    "threshold": _veto_cfg.get("threshold", 0.40),
    "heartbeat_interval": _heartbeat_cfg.get("interval_seconds", 0) if _heartbeat_cfg.get("enabled", False) else 0,
})

# BUY threshold: lower bar for entry since Kronos is SELL-heavy
BUY_THRESHOLD = _veto_cfg.get("buy_threshold", 0.35)

# Initialize Trading Governor (P0-FULL filter system)
TRADING_GOVERNOR = TradingGovernor(CONFIG)
s.TRADING_GOVERNOR = TRADING_GOVERNOR

# Database manager for SQLite consolidation
try:
    DB = get_db()
    DB_AVAILABLE = True
except Exception:
    DB = None
    DB_AVAILABLE = False
    get_logger("Ingwe").warn("database_manager not available -- falling back to JSON files")

# =======================================================
#    PROJECT VUKA -- AGENT INGWE  v{__version__}
#    The leopard does not miss because it does not rush.
# =======================================================
# Full changelog preserved in git history (pre-restructure-20260728 tag).
#
#   FIX-1: place_limit_order() datetime bug (CRITICAL).
#     Replaced timezone-aware datetime with _server_now()
#     naive datetime for MT5 compatibility. Expiry now uses
#     datetime object directly instead of Unix timestamp.
#     Ref: _server_now() at ingwe.py:626.
#
# [changelog continued in git history]
# =======================================================

# -------------------------------------------------------
# ── STRATEGY & SYMBOL SELECTOR ──────────────────────────
STRATEGY = _arg_strategy

SYMBOL = _SYMBOL_MAP.get(_arg_symbol, "")

_instance_tag   = f"{_arg_symbol}_{STRATEGY}"
_instance_short = f"{_arg_symbol[:3]}{'SB' if STRATEGY == 'SILVER_BULLET' else ('M1' if STRATEGY == 'ICT_M1' else ('LO' if STRATEGY == 'LONDON_OPEN' else 'IW'))}"

LOG_FILE        = f"trades_{_instance_tag}.json"
SESSIONS_FILE   = f"sessions_{_instance_tag}.json"

_instance_magic = _derive_magic(_instance_tag)

# -------------------------------------------------------
# CONFIGURATION (applied in main() once args are parsed)
# -------------------------------------------------------

def _apply_strategy_config():
    """Load strategy-specific risk/scan constants from config.py.

    Single source of truth: config.load_config() returns a dict for this
    symbol/strategy; we copy it onto module globals (for legacy references)
    and onto the shared state so extracted modules see the same values.
    """
    cfg = load_config(_arg_symbol, STRATEGY, _instance_tag, _arg_symbol)
    globals().update(cfg)
    s.CONFIG = cfg
    for key, value in cfg.items():
        setattr(s, key, value)

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

# Sync all config to shared state (so extracted modules see the values).
# Runs at import (with import-safe defaults) and again inside main() once the
# real symbol/strategy have been parsed.
def _sync_state():
    _g = globals()
    for _key in ('STRATEGY', 'SYMBOL', '_arg_symbol', '_instance_tag', '_instance_short',
                 '_instance_magic', 'LOG_FILE', 'SESSIONS_FILE',
                 'TIMEFRAME', 'RISK_PERCENT', 'RISK_REWARD_RATIO',
                 'ATR_PERIOD', 'ATR_MULTIPLIER', 'MIN_SL_ATR_MULTIPLIER',
                 'LIMIT_ORDER_EXPIRY_CANDLES', 'ADX_PERIOD', 'ADX_MIN_THRESHOLD',
                 'MIN_SPREAD_PIPS', 'MAX_DAILY_LOSS', 'MAX_DRAWDOWN_PCT',
                 'HARD_LOT_CAP', 'SCAN_INTERVAL_SEC', 'DATA_STALE_MINUTES',
                 'DATA_STALE_MINUTES_ASIAN', 'MT5_RETRY_ATTEMPTS', 'MT5_RETRY_DELAY_SEC',
                  'BACKTEST_MODE', 'BACKTEST_CSV', 'BACKTEST_SPEED',
                 '_backtest_index', '_backtest_data',
                 'SA_OFFSET', 'initial_equity',
                 'sessions_traded_today',
                 'KILLZONES_WINTER', 'KILLZONES_SUMMER',
                 'INGWE_BLACKOUTS_WINTER', 'INGWE_BLACKOUTS_SUMMER',
                 'SB_WINDOWS_WINTER', 'SB_WINDOWS_SUMMER',
                 'SB_BLACKOUTS_WINTER', 'SB_BLACKOUTS_SUMMER',
                 'BTC_KILLZONES', 'ICT_M1_SESSIONS',
                 '_SYMBOL_MAP', 'CONFIG', 'MARKET_CIRCUIT',
                 'DB', 'DB_AVAILABLE', 'KRONOS_VETO_GATE', 'BUY_THRESHOLD',
                 'TICK_ENGINE_AVAILABLE', 'RUNNING_AS_PACKAGE',
                 'SA_OFFSET_SUMMER', 'SA_OFFSET_WINTER',
                 ):
        if _key in _g:
            setattr(s, _key, _g[_key])


_sync_state()


# =======================================================
#  SECTION 1 -- UTILITIES & TIMEZONE
# =======================================================

# Single logger, instantiated lazily once the real instance tag is known.
# The log() helper creates it on first use so importing this module for
# introspection never opens a logger with a placeholder tag.
logger = None


def log(msg: str, level: str = "INFO"):
    """Wrapper for UnifiedLogger to maintain compatibility with existing calls."""
    global logger
    if logger is None:
        logger = get_logger(_instance_tag or "Ingwe")
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
    v6.2: File writes offloaded to the TelemetryQueue worker thread.
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
        
        # 2. Update handover.json
        _mc_summary = MARKET_CIRCUIT.summary if hasattr(MARKET_CIRCUIT, 'summary') else {}
        handover = {
            "bot": "Ingwe",
            "instance": _instance_tag,
            "last_updated": now.isoformat(),
            "equity": equity,
            "daily_pnl": get_daily_pnl(),
            "active_sessions": list(sessions_traded_today),
            "status": "HEALTHY",
            "market_phase": _mc_summary.get("phase", "UNKNOWN"),
            "market_phase_confidence": _mc_summary.get("confidence", 0),
            "market_adx": _mc_summary.get("adx", 0),
            "market_trend": _mc_summary.get("trend", "NONE"),
            "market_bos": _mc_summary.get("bos", "NONE"),
            "market_bb_width": _mc_summary.get("bb_width", 0),
        }
        
        from vuka.utils.telemetry_queue import get_telemetry
        get_telemetry().submit("memory_state", {
            "state_data": state_data,
            "handover": handover,
            "handover_file": "handover.json",
        })
            
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
    for attempt in range(1, s.MT5_RETRY_ATTEMPTS + 1):
        result = fetch_fn(*args, **kwargs)
        if result is not None:
            return result
        error = mt5.last_error()
        log(f"MT5 fetch failed (attempt {attempt}/{s.MT5_RETRY_ATTEMPTS}). "
            f"Error: {error}. Waiting {s.MT5_RETRY_DELAY_SEC}s...", "WARN")
        time.sleep(s.MT5_RETRY_DELAY_SEC)
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
    v6.2: Offloaded to TelemetryQueue -- the scan loop never blocks on disk.
    """
    from vuka.utils.telemetry_queue import get_telemetry
    get_telemetry().submit("sessions", {
        "date":     datetime.now().strftime("%Y-%m-%d"),
        "symbol":   _arg_symbol,
        "strategy": STRATEGY,
        "sessions": list(sessions),
        "sessions_file": SESSIONS_FILE,
    })


# [REMOVED] Section 4 - now in vuka.risk.portfolio
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
    v6.x: Cache lives on the shared state, keyed by symbol, so hot reloads and
          multi-symbol processes never reset or cross-contaminate bias data.
    """
    if BACKTEST_MODE or s.BACKTEST_MODE:
        return None

    cache = s.htf_bias_cache.setdefault(_arg_symbol, {"bias": None, "timestamp": 0.0})

    now = time.monotonic()
    cache_age = now - cache["timestamp"]
    if cache_age < 3600 and cache["bias"] is not None:
        return cache["bias"]

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
        cache["bias"] = d1_bias
        cache["timestamp"] = now
        return d1_bias
    
    log(f"HTF bias split -- D1: {d1_bias}  H4: {h4_bias}. No HTF confirmation.")
    cache["bias"] = None
    cache["timestamp"] = now
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

        # Trailing window instead of copying rows 0..N on every scan.
        # Mirrors live mode's 200-candle fetch so strategy behaviour (df.iloc[-1]
        # == current candle, .tail(N) lookbacks) is identical, while bounding
        # per-cycle memory to 200 rows instead of N rows.
        start = max(0, _backtest_index - 199)
        return _backtest_data.iloc[start:_backtest_index + 1]
    
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
    global _backtest_index, _backtest_data
    if _backtest_data is None:
        return False
    
    candle_time = _backtest_index
    
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



# [REMOVED] Section 6 - now in vuka.market_structure.ict
# [REMOVED] Section 7 - now in vuka.risk.filters
# [REMOVED] Section 8 - now in vuka.risk.portfolio
# [REMOVED] Section 9 - now in vuka.core.config
# [REMOVED] Section 10 - now in vuka.execution.orders + position_manager

#  SECTION 11 -- DAILY RESET
# =======================================================

def reset_daily_sessions():
    global consecutive_losses
    local = now_sast()
    if local.hour == 0 and local.minute < 15 and s.sessions_traded_today:
        s.sessions_traded_today.clear()
        save_sessions(s.sessions_traded_today)
        # Invalidates the RAM loss cache -- a new day must reload from source.
        s.consecutive_losses = None
        s.last_counted_ticket = 0
        consecutive_losses = 0
        log("Midnight reset -- sessions and loss counter cleared.")


# =======================================================
#  SECTION 12 -- MAIN SCAN LOOP
# =======================================================

MARKET_CIRCUIT = None  # set in main() once the real instance tag is known


# MTF cache: M15/H1 refresh only on their candle boundaries -- M1 is the only
# frame fetched every scan cycle. Slashes MT5 copy_rates traffic by ~2/3.
_mtf_cache: dict = {}


def _fetch_mtf_data():
    """Fetch M1, M15, H1 data for market circuit detection.

    M15 is refetched only when a new M15 candle opens; H1 only on the hour.
    """
    now = datetime.now(timezone.utc)
    df_m1 = df_m15 = df_h1 = None
    try:
        rates = mt5_fetch_with_retry(mt5.copy_rates_from_pos, SYMBOL, mt5.TIMEFRAME_M1, 0, 100)
        if rates is not None:
            df_m1 = pd.DataFrame(rates)
            df_m1["time"] = pd.to_datetime(df_m1["time"], unit="s", utc=True)
    except Exception:
        pass

    m15_key = now.timestamp() // 900
    cached = _mtf_cache.get("m15")
    if cached is not None and cached["key"] == m15_key and cached["df"] is not None:
        df_m15 = cached["df"]
    else:
        try:
            rates = mt5_fetch_with_retry(mt5.copy_rates_from_pos, SYMBOL, mt5.TIMEFRAME_M15, 0, 100)
            if rates is not None:
                df_m15 = pd.DataFrame(rates)
                df_m15["time"] = pd.to_datetime(df_m15["time"], unit="s", utc=True)
                _mtf_cache["m15"] = {"key": m15_key, "df": df_m15}
        except Exception:
            pass

    h1_key = now.timestamp() // 3600
    cached = _mtf_cache.get("h1")
    if cached is not None and cached["key"] == h1_key and cached["df"] is not None:
        df_h1 = cached["df"]
    else:
        try:
            rates = mt5_fetch_with_retry(mt5.copy_rates_from_pos, SYMBOL, mt5.TIMEFRAME_H1, 0, 100)
            if rates is not None:
                df_h1 = pd.DataFrame(rates)
                df_h1["time"] = pd.to_datetime(df_h1["time"], unit="s", utc=True)
                _mtf_cache["h1"] = {"key": h1_key, "df": df_h1}
        except Exception:
            pass

    return df_m1, df_m15, df_h1


def _get_phase_adjustments(phase: str, confidence: int) -> dict:
    """
    Return threshold and confluence adjustments based on market phase.
    """
    adj = {"threshold_mod": 0, "score_bonus": 0, "direction_favor": "NONE"}
    if phase == "EXPANSION_BULLISH":
        adj["threshold_mod"] = -5
        adj["score_bonus"] = 10
        adj["direction_favor"] = "BUY"
    elif phase == "EXPANSION_BEARISH":
        adj["threshold_mod"] = -5
        adj["score_bonus"] = 10
        adj["direction_favor"] = "SELL"
    elif phase in ("BREAKOUT_BULLISH",):
        adj["threshold_mod"] = -10
        adj["score_bonus"] = 15
        adj["direction_favor"] = "BUY"
    elif phase in ("BREAKOUT_BEARISH",):
        adj["threshold_mod"] = -10
        adj["score_bonus"] = 15
        adj["direction_favor"] = "SELL"
    elif phase == "SQUEEZE":
        adj["threshold_mod"] = 10
        adj["score_bonus"] = 0
        adj["direction_favor"] = "NONE"
    elif phase == "CONSOLIDATION":
        adj["threshold_mod"] = 5
        adj["score_bonus"] = -5
        adj["direction_favor"] = "NONE"
    elif phase == "CHOP":
        adj["threshold_mod"] = 10
        adj["score_bonus"] = -10
        adj["direction_favor"] = "NONE"
    return adj


def run_agent():
    MARKET_CIRCUIT._scan_phase = "UNKNOWN"
    MARKET_CIRCUIT._scan_phase_conf = 0
    MARKET_CIRCUIT._scan_phase_adj = {"threshold_mod": 0, "score_bonus": 0, "direction_favor": "NONE"}

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

    # ── MARKET CIRCUIT ───────────────────────────────────
    try:
        df_m1, df_m15, df_h1 = _fetch_mtf_data()
        if df_m1 is not None and df_m15 is not None and df_h1 is not None:
            m15_bos = detect_m15_bos(df_m15)
            MARKET_CIRCUIT._scan_phase = MARKET_CIRCUIT.detect(df_m1, df_m15, df_h1, m15_bos or "NONE")
            MARKET_CIRCUIT._scan_phase_conf = MARKET_CIRCUIT.confidence
            MARKET_CIRCUIT._scan_phase_adj = _get_phase_adjustments(MARKET_CIRCUIT._scan_phase, MARKET_CIRCUIT._scan_phase_conf)
            log(f"MARKET CIRCUIT: {MARKET_CIRCUIT._scan_phase} (confidence={MARKET_CIRCUIT._scan_phase_conf}% | "
                f"BOS={m15_bos or 'NONE'} | "
                f"threshold_mod={MARKET_CIRCUIT._scan_phase_adj['threshold_mod']:+d} | "
                f"favor={MARKET_CIRCUIT._scan_phase_adj['direction_favor']})")
        else:
            log(f"MARKET CIRCUIT: insufficient multi-timeframe data", "WARN")
    except Exception as e:
        log(f"MARKET CIRCUIT error: {e}", "WARN")

    # P0-FULL: Market Circuit phase check
    
    phase_allowed, phase_reason = TRADING_GOVERNOR.check_market_phase(MARKET_CIRCUIT._scan_phase, _instance_tag)
    if not phase_allowed:
        log(f"Phase filter: {phase_reason}. Ingwe waits.", "GUARD")
        return

    # ── ACTIVE WINDOW (strategy-aware) ──────────────────
    if STRATEGY == "SILVER_BULLET":
        active = get_current_sb_window()
        if not active:
            log("No Silver Bullet window active. Ingwe watches...")
            return
        win_start, win_end = get_active_sb_windows()[active]
        log(f"SB WINDOW: {active} ({win_start:02d}:00-{win_end:02d}:00 SAST)")
    else:
        active = get_current_session()
        if not active:
            log("No killzone active. Ingwe watches...")
            return
        
        # P0-A / P0-C: Session filter gate - variant-aware
        allowed, reason = TRADING_GOVERNOR.check_session(active, _instance_tag, getattr(s, 'ATR_MULTIPLIER', 1.0))
        if not allowed:
            log(f"Session filtered: {active} ({reason}). Ingwe waits.", "GUARD")
            return
        
        win_start, win_end = get_active_killzones()[active]
        log(f"KILLZONE: {active} ({win_start:02d}:00-{win_end:02d}:00 SAST)")

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

    # Risk-fix: size against the projected stop (ATR x multiplier), matching what
    # every strategy actually places. Each strategy still resizes to its exact
    # final stop distance immediately before placing the order -- this value is
    # now only a pre-trade estimate for logging/display.
    lot_size = calculate_lot_size(atr * s.ATR_MULTIPLIER)

    # ── STRATEGY BRANCH ──────────────────────────────────
    if STRATEGY == "SILVER_BULLET":
        evaluate_silver_bullet(df, fvgs, sweep, sweep_level, price, atr,
                               lot_size, active, unicorn_zones,
                                market_phase=MARKET_CIRCUIT._scan_phase, phase_adj=MARKET_CIRCUIT._scan_phase_adj)
    elif STRATEGY == "LONDON_OPEN":
        evaluate_london_breakout(df, fvgs, sweep, sweep_level, price, atr,
                                 lot_size, active,
                                 market_phase=MARKET_CIRCUIT._scan_phase, phase_adj=MARKET_CIRCUIT._scan_phase_adj)
    elif STRATEGY == "ICT_M1":
        evaluate_ict_m1(df, fvgs, sweep, sweep_level, price, atr,
                        lot_size, active,
                        market_phase=MARKET_CIRCUIT._scan_phase, phase_adj=MARKET_CIRCUIT._scan_phase_adj)
    else:
        evaluate_ingwe(df, fvgs, sweep, sweep_level, price, atr, lot_size, active,
                       market_phase=MARKET_CIRCUIT._scan_phase, phase_adj=MARKET_CIRCUIT._scan_phase_adj)


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

def main():
    global _arg_symbol, _arg_strategy, _arg_check, _arg_backtest, _arg_fast, _arg_test
    global STRATEGY, SYMBOL, _instance_tag, _instance_short, _instance_magic
    global LOG_FILE, SESSIONS_FILE, logger, sessions_traded_today

    args = _build_parser().parse_args()
    _arg_symbol = args.symbol.upper()
    _arg_strategy = args.strategy.upper()
    _arg_check = args.check
    _arg_backtest = args.backtest
    _arg_fast = args.fast
    _arg_test = args.test

    STRATEGY = _arg_strategy
    SYMBOL = _SYMBOL_MAP[_arg_symbol]
    _instance_tag   = f"{_arg_symbol}_{STRATEGY}"
    _instance_short = f"{_arg_symbol[:3]}{'SB' if STRATEGY == 'SILVER_BULLET' else ('M1' if STRATEGY == 'ICT_M1' else ('LO' if STRATEGY == 'LONDON_OPEN' else 'IW'))}"
    _instance_magic = _derive_magic(_instance_tag)
    LOG_FILE        = f"trades_{_instance_tag}.json"
    SESSIONS_FILE   = f"sessions_{_instance_tag}.json"

    _apply_strategy_config()
    _sync_state()

    # Single logger, created only now that the instance tag is final.
    logger = get_logger(_instance_tag)

    # Market circuit instance -- tagged by symbol_strategy so each bot owns its
    # own state file (no cross-process writes to one shared market_circuit.json).
    global MARKET_CIRCUIT
    MARKET_CIRCUIT = get_circuit()
    _sync_state()

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
    print(f"   PROJECT VUKA -- AGENT INGWE  v{__version__}")
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
        for name, (hs, he) in get_active_sb_windows().items():
            ny_offset = -6 if summer else -7
            ny_s = (hs + ny_offset) % 24
            ny_e = (he + ny_offset) % 24
            print(f"     {name:<14} {hs:02d}:00-{he:02d}:00 SAST   ({ny_s:02d}:00-{ny_e:02d}:00 NY)")
    else:
        print("   ACTIVE KILLZONES (SAST):")
        for name, (hs, he) in get_active_killzones().items():
            print(f"     {name:<18} {hs:02d}:00-{he:02d}:00")
    print()
    import sys; sys.stdout.flush()

    if not mt5.initialize():
        log(f"MT5 FAILED. Error: {mt5.last_error()}", "ERROR")
        exit(1)

    log("MT5 connected.")
    mt5.symbol_select(SYMBOL, True)
    sessions_traded_today = load_sessions()
    s.sessions_traded_today = sessions_traded_today  # keep the singleton and the module global in lock-step
    get_initial_equity()
    load_consecutive_losses()  # prime the RAM cache -- no DB/JSON reads on the scan loop

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
        from vuka.utils.telemetry_queue import get_telemetry
        get_telemetry().flush()
        mt5.shutdown()
        log("MT5 disconnected. Until next sunrise.")


if __name__ == "__main__":
    main()