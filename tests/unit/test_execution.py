"""Execution tests — inject missing bot.py names before testing."""
import pytest
from unittest.mock import patch, MagicMock, ANY
from vuka.core.state import s


@pytest.fixture(autouse=True)
def inject_missing_names():
    """Inject names that the extracted module expects from bot.py."""
    import vuka.execution.orders as ord_mod
    import vuka.execution.position_manager as pm_mod

    for mod in (ord_mod, pm_mod):
        if not hasattr(mod, "log"):
            mod.log = MagicMock()
        if not hasattr(mod, "DB"):
            mod.DB = MagicMock()
    for mod in (ord_mod,):
        if not hasattr(mod, "_server_now"):
            mod._server_now = MagicMock(return_value=MagicMock())
        if not hasattr(mod, "get_spread"):
            mod.get_spread = MagicMock(return_value=0.0001)
        if not hasattr(mod, "is_eu_summer"):
            mod.is_eu_summer = MagicMock(return_value=True)
        if not hasattr(mod, "record_concept_trade"):
            mod.record_concept_trade = MagicMock()
        if not hasattr(mod, "TRADING_GOVERNOR"):
            mod.TRADING_GOVERNOR = MagicMock()
        if not hasattr(mod, "check_backtest_limit_fill"):
            mod.check_backtest_limit_fill = MagicMock(return_value=True)


class TestPlaceTrade:
    def test_backtest_mode_returns_mock(self):
        s.BACKTEST_MODE = True
        from vuka.execution.orders import place_trade
        result = place_trade("BUY", 1.1000, 1.0980, 1.1050, 0.10)
        assert result.retcode == 10009
        assert result.comment == "BACKTEST FILLED"

    def test_buy_market_order(self):
        s.BACKTEST_MODE = False
        s.SYMBOL = "EURUSD"
        s._instance_tag = "test_instance"
        s._instance_short = "TEST"
        s._instance_magic = 100
        import vuka.execution.orders as mod
        mod.check_backtest_limit_fill = MagicMock(return_value=True)
        mod._server_now = MagicMock(return_value=MagicMock())

        with patch.object(mod, "mt5") as mock_mt5, \
             patch.object(mod, "has_open_position", return_value=False), \
             patch.object(mod, "log"), \
             patch.object(mod, "get_spread", return_value=0.0001):

            mock_result = MagicMock()
            mock_result.retcode = 10009
            mock_result.comment = "FILLED"
            mock_mt5.order_send.return_value = mock_result
            result = mod.place_trade("BUY", 1.1000, 1.0980, 1.1050, 0.10)
            assert result is not None

    def test_sell_market_order(self):
        s.BACKTEST_MODE = False
        s.SYMBOL = "EURUSD"
        s._instance_tag = "test"
        s._instance_short = "TST"
        s._instance_magic = 100
        import vuka.execution.orders as mod
        mod.check_backtest_limit_fill = MagicMock(return_value=True)
        mod._server_now = MagicMock(return_value=MagicMock())

        with patch.object(mod, "mt5") as mock_mt5, \
             patch.object(mod, "has_open_position", return_value=False), \
             patch.object(mod, "log"), \
             patch.object(mod, "get_spread", return_value=0.0001):

            mock_result = MagicMock()
            mock_result.retcode = 10009
            mock_mt5.order_send.return_value = mock_result
            result = mod.place_trade("SELL", 1.1000, 1.1020, 1.0950, 0.10)
            assert result is not None


class TestLimitOrder:
    def test_live_limit_order(self):
        s.BACKTEST_MODE = False
        s.SYMBOL = "EURUSD"
        s._instance_tag = "test"
        s._instance_short = "TST"
        s._instance_magic = 100
        s.LIMIT_ORDER_EXPIRY_CANDLES = 4
        s.SCAN_INTERVAL_SEC = 900
        import vuka.execution.orders as mod
        from datetime import timedelta, datetime
        mod.check_backtest_limit_fill = MagicMock(return_value=True)
        mod._server_now = MagicMock(
            return_value=datetime(2026, 1, 1, 12, 0, 0)
        )
        mod.timedelta = timedelta
        with patch.object(mod, "mt5") as mock_mt5, patch.object(mod, "log"):
            mock_mt5.order_send.return_value = MagicMock(
                retcode=10009, comment="LIMIT PLACED"
            )
            result = mod.place_limit_order("BUY", 1.0980, 1.0960, 1.1050, 0.10)
            assert result.retcode == 10009


class TestModifySL:
    def test_modify_sl(self):
        s.SYMBOL = "EURUSD"
        import vuka.execution.orders as mod
        with patch.object(mod, "mt5") as mock_mt5, \
             patch.object(mod, "log"), \
             patch.object(mod, "log_sl_move"):
            mock_mt5.order_send.return_value = MagicMock(retcode=10009)
            pos = MagicMock()
            pos.ticket = 123
            pos.price_open = 1.1000
            pos.sl = 1.0980
            pos.tp = 1.1050
            mod._modify_sl(pos, 1.0990, "TEST")
            mock_mt5.order_send.assert_called_once()


class TestPositionQueries:
    def test_no_open_position(self):
        s.BACKTEST_MODE = False
        s.SYMBOL = "EURUSD"
        import vuka.execution.orders as mod
        with patch.object(mod, "mt5") as mock_mt5:
            mock_mt5.positions_get.return_value = None
            assert mod.has_open_position() is False

    def test_has_open_position(self):
        s.BACKTEST_MODE = False
        s.SYMBOL = "EURUSD"
        s._instance_magic = 100
        import vuka.execution.orders as mod
        with patch.object(mod, "mt5") as mock_mt5:
            pos = MagicMock()
            pos.magic = 100
            mock_mt5.positions_get.return_value = [pos]
            assert mod.has_open_position() is True

    def test_no_pending_order(self):
        s.BACKTEST_MODE = False
        s.SYMBOL = "EURUSD"
        import vuka.execution.orders as mod
        with patch.object(mod, "mt5") as mock_mt5:
            mock_mt5.orders_get.return_value = None
            assert mod.has_pending_order() is False

    def test_has_pending_order(self):
        s.BACKTEST_MODE = False
        s.SYMBOL = "EURUSD"
        s._instance_magic = 100
        import vuka.execution.orders as mod
        with patch.object(mod, "mt5") as mock_mt5:
            order = MagicMock()
            order.magic = 100
            mock_mt5.orders_get.return_value = [order]
            assert mod.has_pending_order() is True


class TestRoundToTick:
    def test_round_to_tick(self):
        s.SYMBOL = "EURUSD"
        import vuka.execution.orders as mod
        with patch.object(mod, "mt5") as mock_mt5:
            mock_mt5.symbol_info.return_value = MagicMock(
                trade_tick_size=0.00001, digits=5
            )
            result = mod.round_to_tick(1.10007, "EURUSD")
            assert result == 1.10007


class TestManagePositions:
    def test_manage_positions_no_positions(self):
        s.SYMBOL = "EURUSD"
        import vuka.execution.position_manager as mod
        with patch.object(mod, "mt5") as mock_mt5, patch.object(mod, "log"):
            mock_mt5.positions_get.return_value = None
            result = mod.manage_open_positions()
            assert result is None
