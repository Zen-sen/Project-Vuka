"""Trading governor tests — inject missing bot.py names before testing."""
import pytest
from unittest.mock import patch, MagicMock
from vuka.core.state import s


@pytest.fixture(autouse=True)
def inject_filter_names():
    """Replace stub functions in vuka.risk.filters with mocks for testing."""
    import vuka.risk.filters as mod
    mod.now_sast = MagicMock()
    mod.is_eu_summer = MagicMock(return_value=True)
    mod.get_active_killzones = MagicMock()
    mod.get_active_sb_windows = MagicMock()
    mod.get_active_blackouts = MagicMock()
    mod.get_spread = MagicMock()
    mod.log = MagicMock()


class TestSessionFilters:
    def test_london_session(self):
        import vuka.risk.filters as mod
        mod.now_sast.return_value.hour = 10
        mod.get_active_killzones.return_value = {
            "Asian": (2, 6), "London Open": (9, 12),
            "New York Open": (15, 18), "London Close": (18, 21),
        }
        assert mod.get_current_session() == "London Open"

    def test_asian_session(self):
        import vuka.risk.filters as mod
        mod.now_sast.return_value.hour = 4
        mod.get_active_killzones.return_value = {
            "Asian": (2, 6), "London Open": (9, 12),
            "New York Open": (15, 18), "London Close": (18, 21),
        }
        assert mod.get_current_session() == "Asian"

    def test_ny_session(self):
        import vuka.risk.filters as mod
        mod.now_sast.return_value.hour = 16
        mod.get_active_killzones.return_value = {
            "Asian": (2, 6), "London Open": (9, 12),
            "New York Open": (15, 18), "London Close": (18, 21),
        }
        assert mod.get_current_session() == "New York Open"

    def test_no_session(self):
        import vuka.risk.filters as mod
        mod.now_sast.return_value.hour = 23
        mod.get_active_killzones.return_value = {
            "Asian": (2, 6), "London Open": (9, 12),
            "New York Open": (15, 18), "London Close": (18, 21),
        }
        assert mod.get_current_session() is None


class TestDeadZone:
    def test_in_summer_dead_zone(self):
        import vuka.risk.filters as mod
        mod.now_sast.return_value.hour = 14
        mod.is_eu_summer.return_value = True
        assert mod.is_in_dead_zone() is True

    def test_outside_dead_zone(self):
        import vuka.risk.filters as mod
        mod.now_sast.return_value.hour = 10
        mod.is_eu_summer.return_value = True
        assert mod.is_in_dead_zone() is False


class TestSBWindows:
    def test_in_sb_window(self):
        import vuka.risk.filters as mod
        mod.now_sast.return_value.hour = 9
        mod.get_active_sb_windows.return_value = {"SB_Window1": (9, 10)}
        assert mod.get_current_sb_window() == "SB_Window1"

    def test_outside_sb_window(self):
        import vuka.risk.filters as mod
        mod.now_sast.return_value.hour = 11
        mod.get_active_sb_windows.return_value = {"SB_Window1": (9, 10)}
        assert mod.get_current_sb_window() is None


class TestNewsBlackout:
    def test_in_blackout(self):
        import vuka.risk.filters as mod
        mod.now_sast.return_value.hour = 8
        mod.now_sast.return_value.minute = 50
        mod.get_active_blackouts.return_value = [(8, 45, 9, 0)]
        assert mod.is_in_news_blackout() is True

    def test_outside_blackout(self):
        import vuka.risk.filters as mod
        mod.now_sast.return_value.hour = 10
        mod.now_sast.return_value.minute = 0
        mod.get_active_blackouts.return_value = [(8, 45, 9, 0)]
        assert mod.is_in_news_blackout() is False


class TestPanicCandle:
    def test_panic_detected(self, ohlcv):
        df = ohlcv.copy()
        df.loc[df.index[-1], "high"] = df.loc[df.index[-1], "low"] + 0.01
        import vuka.risk.filters as mod
        assert mod.check_panic_candle(df, 0.001) is True

    def test_no_panic(self, ohlcv):
        import vuka.risk.filters as mod
        assert mod.check_panic_candle(ohlcv, 0.01) is False


class TestPremiumDiscount:
    def test_buy_in_discount(self, ohlcv):
        price = ohlcv["low"].min() + (ohlcv["high"].max() - ohlcv["low"].min()) * 0.25
        import vuka.risk.filters as mod
        assert bool(mod.check_premium_discount_zone(ohlcv, price, "BUY")) is True

    def test_buy_in_premium(self, ohlcv):
        price = ohlcv["high"].max()
        import vuka.risk.filters as mod
        assert bool(mod.check_premium_discount_zone(ohlcv, price, "BUY")) is False


class TestSpreadFilter:
    def test_spread_ok(self):
        import vuka.risk.filters as mod
        mod.get_spread.return_value = 0.00010
        s.MIN_SPREAD_PIPS = 0.0002
        s._arg_symbol = "EURUSD"
        assert mod.check_pre_trade_spread(atr=0.001) is True

    def test_spread_too_wide(self):
        import vuka.risk.filters as mod
        mod.get_spread.return_value = 0.0010
        s.MIN_SPREAD_PIPS = 0.0002
        s._arg_symbol = "EURUSD"
        assert mod.check_pre_trade_spread(atr=0.001) is False


class TestGovernorSessionWhitelist:
    """P0-C session whitelist behavior.

    Regression: with config_v4.6.json missing the governor fell back to
    defaults that excluded "New York Open" (the only killzone the bots
    never traded). The restored full config must whitelist it.
    """

    def test_full_config_whitelists_ny_open(self):
        from skills.trading_governor import TradingGovernor
        cfg = {
            "enabled": True,
            "allowed_sessions": ["Asian", "London Close", "London Open", "New York Open"],
            "blocked_sessions": [],
        }
        gov = TradingGovernor(cfg)
        allowed, reason = gov.check_session("New York Open", "TEST")
        assert allowed is True

    def test_default_config_excludes_ny_open(self):
        from skills.trading_governor import TradingGovernor
        gov = TradingGovernor({"enabled": True})
        allowed, reason = gov.check_session("New York Open", "TEST")
        assert allowed is False
        assert "WHITELIST" in reason
