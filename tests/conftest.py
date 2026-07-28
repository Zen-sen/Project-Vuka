import sys
import pytest
import numpy as np
import pandas as pd
from unittest.mock import MagicMock, patch

# ── Mock MetaTrader5 before any vuka import ─────────────
_mt5 = MagicMock()
_mt5.TIMEFRAME_M1 = 1
_mt5.TIMEFRAME_M5 = 5
_mt5.TIMEFRAME_M15 = 15
_mt5.TIMEFRAME_H1 = 60
_mt5.TIMEFRAME_D1 = 1440
_mt5.ORDER_TYPE_BUY = 0
_mt5.ORDER_TYPE_SELL = 1
_mt5.ORDER_TYPE_BUY_LIMIT = 2
_mt5.ORDER_TYPE_SELL_LIMIT = 3
_mt5.TRADE_ACTION_DEAL = 1
_mt5.TRADE_ACTION_PENDING = 5
_mt5.TRADE_ACTION_SLTP = 6
_mt5.TRADE_RETCODE_DONE = 10009
_mt5.TRADE_RETCODE_ERROR = 10010
_mt5.ORDER_FILLING_RETURN = 0
_mt5.ORDER_FILLING_IOC = 1
_mt5.ORDER_FILLING_FOK = 2
_mt5.ORDER_TIME_GTC = 0
_mt5.ORDER_TIME_SPECIFIED = 1
_mt5.ORDER_TIME_SPECIFIED_DAY = 2

class MockAccountInfo:
    equity = 10000.0
    balance = 10000.0
    profit = 0.0
    margin = 0.0
    margin_free = 10000.0
    currency = "USD"
    leverage = 100
    login = 12345
    server = "Exness-Mock"
    name = "Test Account"

class MockSymbolInfo:
    trade_tick_value = 1.0
    trade_tick_size = 0.00001
    volume_min = 0.01
    volume_max = 100.0
    volume_step = 0.01
    digits = 5
    spread = 10
    bid = 1.10000
    ask = 1.10010

class MockTick:
    bid = 1.10000
    ask = 1.10010

_mt5.account_info.return_value = MockAccountInfo()
_mt5.symbol_info.return_value = MockSymbolInfo()
_mt5.symbol_info_tick.return_value = MockTick()
_mt5.last_error.return_value = (0, "No error")

sys.modules["MetaTrader5"] = _mt5

# ── Synthetic OHLCV fixture ────────────────────────────
@pytest.fixture
def ohlcv():
    n = 200
    np.random.seed(42)
    close = 1.10000 + np.cumsum(np.random.randn(n) * 0.0005)
    high = close + np.abs(np.random.randn(n) * 0.0003)
    low = close - np.abs(np.random.randn(n) * 0.0003)
    df = pd.DataFrame({
        "open": close - 0.0001 + np.random.randn(n) * 0.0001,
        "high": high,
        "low": low,
        "close": close,
        "volume": np.random.randint(100, 1000, n),
        "time": pd.date_range("2026-01-01", periods=n, freq="15min"),
    })
    df["open"] = df["open"].clip(lower=df["low"], upper=df["high"])
    return df

@pytest.fixture(autouse=True)
def reset_state():
    """Reset shared state between tests."""
    from vuka.core.state import s
    s.STRATEGY = ""
    s.SYMBOL = ""
    s._arg_symbol = ""
    s._instance_tag = ""
    s._instance_short = ""
    s._instance_magic = 0
    s.LOG_FILE = ""
    s.SESSIONS_FILE = ""
    s.initial_equity = None
    s.sessions_traded_today = set()
    s.BACKTEST_MODE = False
    s.BACKTEST_CSV = ""
    s.TIMEFRAME = 15
    s.RISK_PERCENT = 1.0
    s.RISK_REWARD_RATIO = 2.0
    s.ATR_PERIOD = 14
    s.ATR_MULTIPLIER = 1.5
    s.MIN_SL_ATR_MULTIPLIER = 0.5
    s.LIMIT_ORDER_EXPIRY_CANDLES = 4
    s.ADX_PERIOD = 14
    s.ADX_MIN_THRESHOLD = 25
    s.MIN_SPREAD_PIPS = 0.0002
    s.MAX_DAILY_LOSS = 50.0
    s.MAX_DRAWDOWN_PCT = 10.0
    s.HARD_LOT_CAP = 0.20
    s.SCAN_INTERVAL_SEC = 900
    s.DB_AVAILABLE = False
    s.KRONOS_VETO_GATE = None
    s.BUY_THRESHOLD = 0.35
    s.MARKET_CIRCUIT = None
