"""Shared mutable state for all vuka modules.

All names accessed via `s.STRATEGY`, `s.SYMBOL`, etc.
Bot.py sets values at startup; all modules see updates because
they share the same State instance.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from vuka.data.database_manager import DatabaseManager
    from skills.trading_governor import TradingGovernor


class State:
    # Fixed attribute set -- prevents accidental attribute creation and
    # trims per-instance memory (no per-instance __dict__).
    __slots__ = (
        # Instance identity
        "STRATEGY", "SYMBOL", "_arg_symbol", "_instance_tag",
        "_instance_short", "_instance_magic", "LOG_FILE", "SESSIONS_FILE",
        # Runtime state
        "initial_equity", "sessions_traded_today", "active_trails",
        "consecutive_losses", "last_counted_ticket",
        "BACKTEST_MODE", "BACKTEST_CSV", "BACKTEST_SPEED",
        "_backtest_index", "_backtest_data",
        # Strategy config
        "TIMEFRAME", "RISK_PERCENT", "RISK_REWARD_RATIO",
        "ATR_PERIOD", "ATR_MULTIPLIER", "MIN_SL_ATR_MULTIPLIER",
        "LIMIT_ORDER_EXPIRY_CANDLES", "ADX_PERIOD", "ADX_MIN_THRESHOLD",
        "MIN_SPREAD_PIPS", "MAX_DAILY_LOSS", "MAX_DRAWDOWN_PCT",
        "HARD_LOT_CAP", "SCAN_INTERVAL_SEC", "DATA_STALE_MINUTES",
        "DATA_STALE_MINUTES_ASIAN", "MT5_RETRY_ATTEMPTS", "MT5_RETRY_DELAY_SEC",
        # DB
        "DB", "DB_AVAILABLE",
        # Kronos
        "KRONOS_VETO_GATE", "BUY_THRESHOLD",
        # Trading governor (P0-FULL filter system)
        "TRADING_GOVERNOR",
        # Market circuit
        "MARKET_CIRCUIT",
        # Mirrored from bot.py module globals via the state-sync loop
        "SA_OFFSET", "KILLZONES_WINTER", "KILLZONES_SUMMER",
        "INGWE_BLACKOUTS_WINTER", "INGWE_BLACKOUTS_SUMMER",
        "SB_WINDOWS_WINTER", "SB_WINDOWS_SUMMER",
        "SB_BLACKOUTS_WINTER", "SB_BLACKOUTS_SUMMER",
        "BTC_KILLZONES", "ICT_M1_SESSIONS",
        "_SYMBOL_MAP", "CONFIG",
        "TICK_ENGINE_AVAILABLE", "RUNNING_AS_PACKAGE",
        "SA_OFFSET_SUMMER", "SA_OFFSET_WINTER",
        # Per-symbol HTF bias cache (hourly refresh)
        "htf_bias_cache",
    )

    def __init__(self):
        # Instance identity
        self.STRATEGY: str = ""
        self.SYMBOL: str = ""
        self._arg_symbol: str = ""
        self._instance_tag: str = ""
        self._instance_short: str = ""
        self._instance_magic: int = 0
        self.LOG_FILE: str = ""
        self.SESSIONS_FILE: str = ""

        # Runtime state
        self.initial_equity: float | None = None
        self.sessions_traded_today: set[str] = set()
        self.active_trails: dict[int, dict] = {}
        # RAM cache for consecutive-loss tracking (loaded once, written on change)
        self.consecutive_losses: int | None = None
        self.last_counted_ticket: int = 0
        self.BACKTEST_MODE: bool = False
        self.BACKTEST_CSV: str = ""
        self.BACKTEST_SPEED: int = 1
        self._backtest_index: int = 0
        self._backtest_data: Any = None

        # Strategy config
        self.TIMEFRAME: int = 15
        self.RISK_PERCENT: float = 1.0
        self.RISK_REWARD_RATIO: float = 2.0
        self.ATR_PERIOD: int = 14
        self.ATR_MULTIPLIER: float = 1.5
        self.MIN_SL_ATR_MULTIPLIER: float = 0.5
        self.LIMIT_ORDER_EXPIRY_CANDLES: int = 4
        self.ADX_PERIOD: int = 14
        self.ADX_MIN_THRESHOLD: float = 25
        self.MIN_SPREAD_PIPS: float = 0.0001
        self.MAX_DAILY_LOSS: float = 50.0
        self.MAX_DRAWDOWN_PCT: float = 10.0
        self.HARD_LOT_CAP: float = 0.20
        self.SCAN_INTERVAL_SEC: int = 900
        self.DATA_STALE_MINUTES: int = 30
        self.DATA_STALE_MINUTES_ASIAN: int = 90
        self.MT5_RETRY_ATTEMPTS: int = 3
        self.MT5_RETRY_DELAY_SEC: int = 30

        # DB
        self.DB: DatabaseManager | None = None
        self.DB_AVAILABLE: bool = False

        # Kronos
        self.KRONOS_VETO_GATE: Any = None
        self.BUY_THRESHOLD: float = 0.35

        # Trading governor (P0-FULL filter system)
        self.TRADING_GOVERNOR: TradingGovernor | None = None

        # Market circuit
        self.MARKET_CIRCUIT: Any = None

        # Per-symbol HTF bias cache, keyed by arg_symbol
        # e.g. {"EURUSD": {"bias": "BULLISH", "timestamp": 123.0}}
        self.htf_bias_cache: dict = {}


s = State()
