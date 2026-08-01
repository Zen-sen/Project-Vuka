"""Shared mutable state for all vuka modules.

All names accessed via `s.STRATEGY`, `s.SYMBOL`, etc.
Bot.py sets values at startup; all modules see updates because
they share the same State instance.
"""

from __future__ import annotations

from typing import Any


class State:
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
        self.DB: Any = None
        self.DB_AVAILABLE: bool = False

        # Kronos
        self.KRONOS_VETO_GATE: Any = None
        self.BUY_THRESHOLD: float = 0.35

        # Trading governor (P0-FULL filter system)
        self.TRADING_GOVERNOR: Any = None

        # Market circuit
        self.MARKET_CIRCUIT: Any = None


s = State()
