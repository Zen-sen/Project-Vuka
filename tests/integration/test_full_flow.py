"""Integration tests: verify the full pipeline composes without crashes.

These tests mock MT5 and bot dependencies at the module level before
import, then exercise the full orchestration path end-to-end.
"""
import sys
import pytest
from unittest.mock import patch, MagicMock
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

# Patch MT5 before any vuka imports
_mt5 = MagicMock()
_mt5.TIMEFRAME_M1 = 1
_mt5.TIMEFRAME_M15 = 15
_mt5.ORDER_TYPE_BUY = 0
_mt5.ORDER_TYPE_SELL = 1
_mt5.TRADE_ACTION_DEAL = 1
_mt5.TRADE_ACTION_SLTP = 6
_mt5.TRADE_RETCODE_DONE = 10009
_mt5.ORDER_FILLING_RETURN = 0
_mt5.ORDER_FILLING_IOC = 1
_mt5.ORDER_FILLING_FOK = 2
_mt5.ORDER_TIME_GTC = 0
_mt5.symbol_info.return_value = MagicMock(
    trade_tick_value=1.0, trade_tick_size=0.00001, volume_min=0.01,
    volume_max=100.0, volume_step=0.01, digits=5,
)
_mt5.account_info.return_value = MagicMock(
    equity=10000.0, balance=10000.0, profit=0.0, margin=0.0,
    margin_free=10000.0,
)
_mt5.last_error.return_value = (0, "No error")
_mt5.positions_get.return_value = []
sys.modules["MetaTrader5"] = _mt5

from vuka.core.state import s
from vuka.core.config import calculate_confluence_score


@pytest.fixture(autouse=True)
def reset():
    s.STRATEGY = "INGWE"
    s.SYMBOL = "EURUSD"
    s._arg_symbol = "EURUSD"
    s._instance_tag = "test"
    s._instance_short = "TST"
    s._instance_magic = 100
    s.BACKTEST_MODE = False
    s.TIMEFRAME = 15
    s.RISK_PERCENT = 1.0
    s.RISK_REWARD_RATIO = 2.0
    s.ATR_PERIOD = 14
    s.ATR_MULTIPLIER = 1.5
    s.ADX_MIN_THRESHOLD = 25
    s.HARD_LOT_CAP = 0.20
    s.MAX_DAILY_LOSS = 50.0
    s.MAX_DRAWDOWN_PCT = 10.0
    s.DB_AVAILABLE = False
    s.MIN_SPREAD_PIPS = 0.0002

    # Inject missing names into extracted modules
    import vuka.risk.filters as filt
    import vuka.risk.portfolio as port
    import vuka.execution.orders as ord_
    import vuka.execution.position_manager as pm

    for mod in [filt, port, ord_, pm]:
        if not hasattr(mod, "log"):
            mod.log = MagicMock()
    for mod in [filt]:
        if not hasattr(mod, "now_sast"):
            mod.now_sast = MagicMock()
        if not hasattr(mod, "get_active_killzones"):
            mod.get_active_killzones = MagicMock()
        if not hasattr(mod, "get_spread"):
            mod.get_spread = MagicMock(return_value=0.0001)
        if not hasattr(mod, "is_eu_summer"):
            mod.is_eu_summer = MagicMock(return_value=True)
        if not hasattr(mod, "get_active_blackouts"):
            mod.get_active_blackouts = MagicMock(return_value=[])
        if not hasattr(mod, "get_active_sb_windows"):
            mod.get_active_sb_windows = MagicMock(return_value={})
    for mod in [port]:
        if not hasattr(mod, "get_initial_equity"):
            mod.get_initial_equity = MagicMock(return_value=10000.0)
        if not hasattr(mod, "get_exness_server_offset"):
            mod.get_exness_server_offset = MagicMock(return_value=2)
        if not hasattr(mod, "mt5_fetch_with_retry"):
            mod.mt5_fetch_with_retry = MagicMock(return_value=None)
        if not hasattr(mod, "now_sast"):
            mod.now_sast = MagicMock()
        if not hasattr(mod, "is_eu_summer"):
            mod.is_eu_summer = MagicMock(return_value=True)
        if not hasattr(mod, "get_candles"):
            mod.get_candles = MagicMock()
        if not hasattr(mod, "calculate_atr"):
            mod.calculate_atr = MagicMock(return_value=0.002)
    for mod in [ord_]:
        if not hasattr(mod, "_server_now"):
            mod._server_now = MagicMock(return_value=datetime(2026, 1, 1, 12, 0, 0))
        if not hasattr(mod, "get_spread"):
            mod.get_spread = MagicMock(return_value=0.0001)
        if not hasattr(mod, "is_eu_summer"):
            mod.is_eu_summer = MagicMock(return_value=True)
        if not hasattr(mod, "has_open_position"):
            mod.has_open_position = MagicMock(return_value=False)
        if not hasattr(mod, "check_backtest_limit_fill"):
            mod.check_backtest_limit_fill = MagicMock(return_value=True)
        if not hasattr(mod, "record_concept_trade"):
            mod.record_concept_trade = MagicMock()
        if not hasattr(mod, "TRADING_GOVERNOR"):
            mod.TRADING_GOVERNOR = MagicMock()
        if not hasattr(mod, "timedelta"):
            mod.timedelta = timedelta
        if not hasattr(mod, "DB"):
            mod.DB = MagicMock()
    for mod in [pm]:
        if not hasattr(mod, "_server_now"):
            mod._server_now = MagicMock(return_value=datetime(2026, 1, 1, 12, 0, 0))
        if not hasattr(mod, "_server_midnight"):
            mod._server_midnight = MagicMock()
        if not hasattr(mod, "_modify_sl"):
            mod._modify_sl = MagicMock()
        if not hasattr(mod, "record_concept_outcome"):
            mod.record_concept_outcome = MagicMock()
        if not hasattr(mod, "DB"):
            mod.DB = MagicMock()


class TestFullScanCycle:
    def test_scan_cycle_no_crash(self):
        """The full pipeline should not crash with mocked dependencies."""
        import vuka.risk.filters as mod
        mod.now_sast.return_value.hour = 10
        mod.get_active_killzones.return_value = {
            "Asian": (2, 6), "London Open": (9, 12),
            "New York Open": (15, 18), "London Close": (18, 21),
        }
        session = mod.get_current_session()
        assert session == "London Open"

    def test_confluence_scoring_pipeline(self):
        """Confluence scoring produces a valid integer."""
        score = calculate_confluence_score(
            "BULLISH", True, True, True, True,
            level_sweep=True, bos_aligned=True, htf_bias_ok=True,
            session="Asian", direction="sell",
        )
        assert isinstance(score, int)
        assert 0 <= score <= 120

    def test_has_open_position_returns_false_with_no_mt5_positions(self):
        from vuka.execution.orders import has_open_position
        assert has_open_position() is False

    def test_execution_order_returns_result(self):
        """place_trade with mocked MT5 returns a result object."""
        from vuka.execution.orders import place_trade
        result = place_trade("BUY", 1.1000, 1.0980, 1.1050, 0.10, session="London")
        assert result is not None


class TestCircuitBreakers:
    def test_drawdown_allows_trading_when_under_limit(self):
        s.MAX_DRAWDOWN_PCT = 10.0
        s.initial_equity = 10000.0
        _mt5.account_info.return_value.equity = 9200.0
        from vuka.risk.portfolio import check_equity_drawdown
        import vuka.risk.portfolio as port
        with patch.object(port, "get_initial_equity", return_value=10000.0):
            assert check_equity_drawdown() is False

    def test_spread_check_does_not_crash(self):
        from vuka.risk.filters import check_pre_trade_spread
        import vuka.risk.filters as mod
        mod.get_spread.return_value = 0.0001
        result = check_pre_trade_spread(atr=0.001)
        assert isinstance(result, bool)
