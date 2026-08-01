"""Execution tests — inject missing bot.py names before testing."""
import sys
import types
from unittest.mock import ANY, MagicMock, patch

import pytest

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
        from datetime import datetime, timedelta

        import vuka.execution.orders as mod
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


def _call_log_trade(confidence):
    """Run log_trade through the deal/position_id path; return (tele, entry)."""
    import vuka.execution.orders as mod
    s.active_trails.clear()
    s.BACKTEST_MODE = False
    s.SYMBOL = "EURUSD"
    s.STRATEGY = "INGWE"
    s._instance_magic = 555

    result = MagicMock(deal=1, price=1.1000)
    pos = MagicMock()
    pos.magic = 555
    pos.ticket = 777

    tele = MagicMock()
    ct = MagicMock()
    ct.get_confidence_score.return_value = confidence
    fake_bot = types.ModuleType("vuka.core.bot")
    fake_bot.TRADING_GOVERNOR = MagicMock()

    with patch.object(mod, "mt5") as mock_mt5, \
         patch.object(mod, "is_eu_summer", return_value=True), \
         patch.object(mod, "get_spread", return_value=0.0001), \
         patch("vuka.utils.telemetry_queue.get_telemetry", return_value=tele), \
         patch("skills.concept_tracker.ConceptTracker", return_value=ct), \
         patch.dict(sys.modules, {"vuka.core.bot": fake_bot}):
        mock_mt5.positions_get.return_value = [pos]
        mod.log_trade(
            "BUY", 1.1000, 1.0980, 1.1050, result, 0.10, "London Open",
            context={
                "fvg_type": "BULLISH",
                "sweep": "SWEEP_HIGH",
                "setup_type": "LONDON_OPEN",
                "session": "London Open",
                "market_phase": "ACCUMULATION",
                "trend": "BULLISH",
            },
        )
    return tele, tele.submit.call_args[0][1]


class TestActiveTrails:
    def test_log_trade_injects_high_confidence_trail_into_ram(self):
        tele, payload = _call_log_trade(confidence=0.8)
        assert s.active_trails[777] == {
            "trail_be_at": 2.0,
            "concept_confidence": 0.8,
        }
        assert payload["trade_entry"]["trail_be_at"] == 2.0
        assert payload["trade_entry"]["concept_confidence"] == 0.8

    def test_log_trade_injects_low_confidence_trail_into_ram(self):
        tele, payload = _call_log_trade(confidence=0.3)
        assert s.active_trails[777] == {
            "trail_be_at": 1.0,
            "concept_confidence": 0.3,
        }
        assert payload["trade_entry"]["trail_be_at"] == 1.0

    def test_log_trade_submits_trade_payload(self):
        tele, payload = _call_log_trade(confidence=0.5)
        tele.submit.assert_called_once_with("trade", ANY)
        assert payload["trade_id"] == "777"
        assert payload["direction"] == "BUY"


class TestManagePositionsRAM:
    """v6.2: trail config comes from s.active_trails (RAM), never the disk."""

    def _pos(self, price_current):
        pos = MagicMock()
        pos.magic = 555
        pos.identifier = 777
        pos.type = 0  # BUY
        pos.price_open = 1.1000
        pos.sl = 1.0950
        pos.price_current = price_current
        return pos

    @staticmethod
    def _run(pm, pos, assert_no_disk_read=False):
        with patch.object(pm, "mt5") as mock_mt5, \
             patch.object(pm, "_modify_sl") as modify, \
             patch("builtins.open") as mo:
            mock_mt5.ORDER_TYPE_BUY = 0
            mock_mt5.ORDER_TYPE_SELL = 1
            mock_mt5.positions_get.return_value = [pos]
            pm.manage_open_positions()
        if assert_no_disk_read:
            assert not mo.called
        return modify

    def test_standard_trail_moves_sl_to_1r_at_2r(self):
        import vuka.execution.position_manager as pm
        s.active_trails.clear()
        s.SYMBOL = "EURUSD"
        s._instance_magic = 555
        s.LOG_FILE = "trades_test.json"
        s.BACKTEST_MODE = True
        s.active_trails[777] = {"trail_be_at": 1.0, "concept_confidence": 0.25}
        pos = self._pos(1.1102)  # just past 2R above entry
        modify = self._run(pm, pos, assert_no_disk_read=True)
        modify.assert_called_once()
        _, new_sl, label = modify.call_args[0]
        assert new_sl == round(1.1050, 5)
        assert label == "1:2 -> SL to 1:1"

    def test_high_confidence_trails_be_at_2r(self):
        import vuka.execution.position_manager as pm
        s.active_trails.clear()
        s.SYMBOL = "EURUSD"
        s._instance_magic = 555
        s.BACKTEST_MODE = True
        s.active_trails[777] = {"trail_be_at": 2.0, "concept_confidence": 0.8}
        pos = self._pos(1.1102)  # just past the 2R BE target
        modify = self._run(pm, pos)
        _, new_sl, label = modify.call_args[0]
        assert new_sl == round(1.1000, 5)
        assert label == "BE@2.0R -> SL to BE"

    def test_defaults_when_no_ram_entry(self):
        """Position not in active_trails falls back to the standard 1:1 trail."""
        import vuka.execution.position_manager as pm
        s.active_trails.clear()
        s.SYMBOL = "EURUSD"
        s._instance_magic = 555
        s.BACKTEST_MODE = True
        pos = self._pos(1.1102)
        modify = self._run(pm, pos)
        _, new_sl, label = modify.call_args[0]
        assert new_sl == round(1.1050, 5)
        assert label == "1:2 -> SL to 1:1"
