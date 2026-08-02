import json
from datetime import datetime
from unittest.mock import ANY, MagicMock, patch

import pytest

from vuka.core.state import s


@pytest.fixture(autouse=True)
def inject_portfolio_names():
    """Inject names that portfolio.py expects from bot.py."""
    import vuka.risk.portfolio as mod
    if not hasattr(mod, "log"):
        mod.log = MagicMock()
    if not hasattr(mod, "mt5_fetch_with_retry"):
        mod.mt5_fetch_with_retry = MagicMock()
    if not hasattr(mod, "DB"):
        mod.DB = MagicMock()
    if not hasattr(mod, "get_candles"):
        mod.get_candles = MagicMock()
    if not hasattr(mod, "calculate_atr"):
        mod.calculate_atr = MagicMock()
    if not hasattr(mod, "get_exness_server_offset"):
        mod.get_exness_server_offset = MagicMock(return_value=2)
    if not hasattr(mod, "now_sast"):
        mod.now_sast = MagicMock()
    if not hasattr(mod, "is_eu_summer"):
        mod.is_eu_summer = MagicMock(return_value=True)


class TestLotSize:
    def test_lot_size_standard(self):
        s.RISK_PERCENT = 1.0
        s.HARD_LOT_CAP = 0.20
        s.SYMBOL = "EURUSD"
        import vuka.risk.portfolio as mod
        mod.get_candles = MagicMock()
        mod.calculate_atr = MagicMock(return_value=0.002)
        with patch.object(mod, "mt5") as mock_mt5:
            mock_mt5.account_info.return_value.equity = 10000.0
            mock_mt5.symbol_info.return_value = MagicMock(
                trade_tick_value=1.0,
                trade_tick_size=0.00001,
                volume_min=0.01,
                volume_max=100.0,
                volume_step=0.01,
            )
            lot = mod.calculate_lot_size(sl_distance=0.0020)
            assert 0.01 <= lot <= 0.20

    def test_lot_size_hard_cap(self):
        s.RISK_PERCENT = 100.0
        s.HARD_LOT_CAP = 0.20
        import vuka.risk.portfolio as mod
        mod.get_candles = MagicMock()
        mod.calculate_atr = MagicMock(return_value=0.002)
        with patch.object(mod, "mt5") as mock_mt5:
            mock_mt5.account_info.return_value.equity = 100000.0
            mock_mt5.symbol_info.return_value = MagicMock(
                trade_tick_value=0.5,
                trade_tick_size=0.00001,
                volume_min=0.01,
                volume_max=100.0,
                volume_step=0.01,
            )
            lot = mod.calculate_lot_size(sl_distance=0.0010)
            assert lot <= 0.20

    def test_lot_size_broker_min(self):
        s.RISK_PERCENT = 0.01
        s.HARD_LOT_CAP = 0.20
        import vuka.risk.portfolio as mod
        mod.get_candles = MagicMock()
        mod.calculate_atr = MagicMock(return_value=0.002)
        with patch.object(mod, "mt5") as mock_mt5:
            mock_mt5.account_info.return_value.equity = 100.0
            mock_mt5.symbol_info.return_value = MagicMock(
                trade_tick_value=1.0,
                trade_tick_size=0.00001,
                volume_min=0.01,
                volume_max=100.0,
                volume_step=0.01,
            )
            lot = mod.calculate_lot_size(sl_distance=0.0020)
            assert lot >= 0.01

    def test_lot_size_zero_sl_fallback(self):
        s.RISK_PERCENT = 1.0
        s.HARD_LOT_CAP = 0.20
        import vuka.risk.portfolio as mod
        mod.get_candles = MagicMock()
        mod.calculate_atr = MagicMock(return_value=0.002)
        with patch.object(mod, "mt5") as mock_mt5:
            mock_mt5.account_info.return_value.equity = 10000.0
            mock_mt5.symbol_info.return_value = MagicMock(
                trade_tick_value=0, trade_tick_size=0,
                volume_min=0.01, volume_max=100.0, volume_step=0.01,
            )
            lot = mod.calculate_lot_size(sl_distance=0.0)
            assert 0.0 < lot <= 0.20

    def test_lot_rounding_reclamped_to_min(self):
        """Rounding to lot_step must not push volume below broker min_lot."""
        s.RISK_PERCENT = 1.0
        s.HARD_LOT_CAP = 1.0
        import vuka.risk.portfolio as mod
        with patch.object(mod, "mt5") as mock_mt5:
            mock_mt5.account_info.return_value.equity = 1000.0
            mock_mt5.symbol_info.return_value = MagicMock(
                trade_tick_value=1.0,
                trade_tick_size=0.00001,
                volume_min=0.025,
                volume_max=100.0,
                volume_step=0.02,
            )
            lot = mod.calculate_lot_size(sl_distance=0.0035)
            assert lot == 0.025


class TestSpread:
    def test_spread_backtest_mode(self):
        s.BACKTEST_MODE = True
        import vuka.risk.portfolio as mod
        spread = mod.get_spread()
        assert spread == 0.00010

    def test_spread_live_mode(self):
        s.BACKTEST_MODE = False
        s.SYMBOL = "EURUSD"
        import vuka.risk.portfolio as mod
        with patch.object(mod, "mt5") as mock_mt5:
            mock_tick = MagicMock()
            mock_tick.ask = 1.10010
            mock_tick.bid = 1.10000
            mock_mt5.symbol_info_tick.return_value = mock_tick
            spread = mod.get_spread()
            assert abs(spread - 0.0001) < 1e-8


class TestDrawdown:
    def test_drawdown_within_limit(self):
        s.MAX_DRAWDOWN_PCT = 10.0
        import vuka.risk.portfolio as mod
        with patch.object(mod, "mt5") as mock_mt5, \
             patch("vuka.risk.portfolio.get_initial_equity", return_value=10000.0):
            mock_mt5.account_info.return_value.equity = 9500.0
            assert mod.check_equity_drawdown() is False

    def test_drawdown_at_limit(self):
        s.MAX_DRAWDOWN_PCT = 10.0
        import vuka.risk.portfolio as mod
        with patch.object(mod, "mt5") as mock_mt5, \
             patch("vuka.risk.portfolio.get_initial_equity", return_value=10000.0):
            mock_mt5.account_info.return_value.equity = 9000.0
            assert mod.check_equity_drawdown() is False

    def test_drawdown_exceeds_limit(self):
        s.MAX_DRAWDOWN_PCT = 10.0
        import vuka.risk.portfolio as mod
        with patch.object(mod, "mt5") as mock_mt5, \
             patch("vuka.risk.portfolio.get_initial_equity", return_value=10000.0), \
             patch.object(mod, "log"):
            mock_mt5.account_info.return_value.equity = 8500.0
            assert mod.check_equity_drawdown() is True


class TestDailyPnL:
    def test_empty_deals_returns_zero(self):
        s._instance_magic = 100
        import vuka.risk.portfolio as mod
        mod.mt5_fetch_with_retry = MagicMock(return_value=None)
        pnl = mod.get_daily_pnl()
        assert pnl == 0.0

    def test_pnl_includes_swap_and_commission(self):
        s._instance_magic = 100
        import vuka.risk.portfolio as mod
        deals = [
            MagicMock(magic=100, profit=50.0, swap=-1.5, commission=-0.8),
            MagicMock(magic=100, profit=-25.0, swap=-0.6, commission=-0.4),
            MagicMock(magic=999, profit=999.0, swap=0.0, commission=0.0),
        ]
        mod.mt5_fetch_with_retry = MagicMock(return_value=deals)
        pnl = mod.get_daily_pnl()
        assert pnl == pytest.approx(21.7)


class TestConsecutiveLosses:
    def test_no_losses(self):
        import vuka.risk.portfolio as mod
        with patch.object(mod, "load_consecutive_losses", return_value=(0, 0)):
            assert mod.check_consecutive_losses() is False

    def test_three_losses_triggers_pause(self):
        import vuka.risk.portfolio as mod
        with patch.object(mod, "load_consecutive_losses", return_value=(3, 12345)), \
             patch.object(mod, "log"):
            assert mod.check_consecutive_losses() is True

    def test_threshold_reads_config(self):
        s.MAX_CONSECUTIVE_LOSSES = 5
        import vuka.risk.portfolio as mod
        with patch.object(mod, "load_consecutive_losses", return_value=(4, 12345)), \
             patch.object(mod, "log"):
            assert mod.check_consecutive_losses() is False
        with patch.object(mod, "load_consecutive_losses", return_value=(5, 12345)), \
             patch.object(mod, "log"):
            assert mod.check_consecutive_losses() is True

    def test_streak_reset(self):
        import vuka.risk.portfolio as mod
        mock_deal = MagicMock()
        mock_deal.ticket = 999
        mock_deal.profit = 50.0
        mock_deal.magic = 100
        s._instance_magic = 100
        mod.mt5_fetch_with_retry = MagicMock(return_value=[mock_deal])
        with patch.object(mod, "save_consecutive_losses") as mock_save, \
             patch.object(mod, "load_consecutive_losses", return_value=(2, 888)), \
             patch.object(mod, "log"):
            mod.update_consecutive_losses()
            mock_save.assert_called_with(0, 999)

    def test_streak_increments(self):
        import vuka.risk.portfolio as mod
        mock_deal = MagicMock()
        mock_deal.ticket = 999
        mock_deal.profit = -25.0
        mock_deal.magic = 100
        s._instance_magic = 100
        mod.mt5_fetch_with_retry = MagicMock(return_value=[mock_deal])
        with patch.object(mod, "save_consecutive_losses") as mock_save, \
             patch.object(mod, "load_consecutive_losses", return_value=(1, 888)), \
             patch.object(mod, "log"):
            mod.update_consecutive_losses()
            mock_save.assert_called_with(2, 999)

    def test_multiple_new_deals_all_counted(self):
        """Every deal closed since last_ticket must be evaluated, not just the tail."""
        import vuka.risk.portfolio as mod
        s._instance_magic = 100
        loss1 = MagicMock(ticket=1000, profit=-10.0, magic=100)
        win   = MagicMock(ticket=1001, profit=20.0, magic=100)
        loss2 = MagicMock(ticket=1002, profit=-15.0, magic=100)
        mod.mt5_fetch_with_retry = MagicMock(return_value=[loss1, win, loss2])
        with patch.object(mod, "save_consecutive_losses") as mock_save, \
             patch.object(mod, "load_consecutive_losses", return_value=(0, 999)), \
             patch.object(mod, "log"):
            mod.update_consecutive_losses()
            mock_save.assert_called_with(1, 1002)


class TestConsecutiveLossesRAM:
    """v6.2: loss counter is cached in RAM -- the scan loop never re-reads disk."""

    def test_load_caches_once_then_serves_from_ram(self, tmp_path):
        import vuka.risk.portfolio as mod
        s.DB_AVAILABLE = False
        s.consecutive_losses = None
        today = datetime.now().strftime("%Y-%m-%d")
        f = tmp_path / "sessions.json"
        f.write_text(json.dumps({
            "date": today, "consecutive_losses": 2, "last_counted_ticket": 99
        }))
        s.SESSIONS_FILE = str(f)

        count, ticket = mod.load_consecutive_losses()
        assert (count, ticket) == (2, 99)
        assert s.consecutive_losses == 2
        assert s.last_counted_ticket == 99

        # Source removed -- a cache hit must not touch disk.
        f.unlink()
        count2, ticket2 = mod.load_consecutive_losses()
        assert (count2, ticket2) == (2, 99)

    def test_save_updates_ram_and_submits_to_telemetry(self):
        import vuka.risk.portfolio as mod
        tele = MagicMock()
        with patch("vuka.utils.telemetry_queue.get_telemetry", return_value=tele):
            mod.save_consecutive_losses(3, 555)
        assert s.consecutive_losses == 3
        assert s.last_counted_ticket == 555
        tele.submit.assert_called_once_with("loss_tracking", {
            "count": 3, "last_ticket": 555, "date": ANY,
        })
