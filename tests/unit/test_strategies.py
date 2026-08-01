"""Strategy smoke tests.

The extracted strategy modules previously referenced dozens of bare names
(get_spread, place_trade, KRONOS_VETO_GATE, ...) that were never imported,
so every production scan raised NameError before a single trade could log.
These tests exercise the full signal path through a fake bot module (the
same pattern test_execution.py uses for orders) and assert that:

  * the module runs end-to-end without NameError,
  * ``market_phase`` reaches the trade context (log_trade),
  * the Unicorn failure branch falls through to the standard FVG path,
  * ``check_pre_trade_spread`` is computed once per signal path.
"""
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

from vuka.core.state import s


def _fake_bot(**names):
    bot = types.ModuleType("vuka.core.bot")
    bot.TRADING_GOVERNOR = MagicMock()
    for name, value in names.items():
        setattr(bot, name, value)
    return bot


@pytest.fixture(autouse=True)
def strategy_state():
    s.SYMBOL = "EURUSDc"
    s._instance_tag = "EURUSD_SILVER_BULLET"
    s.RISK_REWARD_RATIO = 2.0
    s.ATR_MULTIPLIER = 1.5
    s.MIN_SL_ATR_MULTIPLIER = 0.8
    s.MIN_SPREAD_PIPS = 0.0002
    s.KRONOS_VETO_GATE = None


class TestSilverBullet:
    def test_buy_injects_market_phase(self, ohlcv):
        import vuka.strategies.silver_bullet as sb
        fake_bot = _fake_bot(
            get_draw_on_liquidity=MagicMock(return_value=("PDH", 1.1100)),
            save_sessions=MagicMock(),
        )
        fvgs = [("BULLISH_FVG", 1.0950, 1.0970, 5, None, 1.0960)]
        with patch.dict(sys.modules, {"vuka.core.bot": fake_bot}), \
             patch.object(sb, "get_spread", return_value=0.0001), \
             patch.object(sb, "check_pre_trade_spread", return_value=True), \
             patch.object(sb, "place_trade", return_value=MagicMock(retcode=10009)), \
             patch.object(sb, "log_trade") as lt:
            sb.evaluate_silver_bullet(
                ohlcv, fvgs, "SWEEP_LOW", 1.0940, 1.0955, 0.0020, 0.10, "SB_Window1",
                market_phase="EXPANSION",
            )
        lt.assert_called_once()
        ctx = lt.call_args[1]["context"]
        assert ctx["market_phase"] == "EXPANSION"
        assert ctx["setup_type"] == "SILVER_BULLET"

    def test_unicorn_failure_falls_through_to_fvg(self, ohlcv):
        """The failed Unicorn trade must NOT kill the scan cycle."""
        import vuka.strategies.silver_bullet as sb
        fake_bot = _fake_bot(
            get_draw_on_liquidity=MagicMock(return_value=("PDH", 1.1100)),
            save_sessions=MagicMock(),
        )
        unicorns = [("BULLISH_UNICORN", 1.0940, 1.0960, 1.0950, 1.0930, 1.0970)]
        fvgs = [("BULLISH_FVG", 1.0950, 1.0970, 5, None, 1.0960)]
        with patch.dict(sys.modules, {"vuka.core.bot": fake_bot}), \
             patch.object(sb, "get_spread", return_value=0.0001), \
             patch.object(sb, "check_pre_trade_spread", return_value=True), \
             patch.object(sb, "place_trade", side_effect=[
                 MagicMock(retcode=10018),  # unicorn BUY rejected
                 MagicMock(retcode=10009),  # FVG BUY fills
             ]) as pt, \
             patch.object(sb, "log_trade") as lt:
            sb.evaluate_silver_bullet(
                ohlcv, fvgs, "SWEEP_LOW", 1.0940, 1.0955, 0.0020, 0.10, "SB_Window1",
                unicorn_zones=unicorns, market_phase="EXPANSION",
            )
        assert pt.call_count == 2
        lt.assert_called_once()

    def test_spread_check_computed_once(self, ohlcv):
        """check_pre_trade_spread must run once per signal path, not twice."""
        import vuka.strategies.silver_bullet as sb
        fake_bot = _fake_bot(
            get_draw_on_liquidity=MagicMock(return_value=("PDH", 1.1100)),
            save_sessions=MagicMock(),
        )
        fvgs = [("BULLISH_FVG", 1.0950, 1.0970, 5, None, 1.0960)]
        with patch.dict(sys.modules, {"vuka.core.bot": fake_bot}), \
             patch.object(sb, "get_spread", return_value=0.0001), \
             patch.object(sb, "place_trade", return_value=MagicMock(retcode=10009)), \
             patch.object(sb, "log_trade"), \
             patch.object(sb, "check_pre_trade_spread", return_value=True) as csp:
            sb.evaluate_silver_bullet(
                ohlcv, fvgs, "SWEEP_LOW", 1.0940, 1.0955, 0.0020, 0.10, "SB_Window1",
            )
        assert csp.call_count == 1


class TestIctM1:
    def test_buy_injects_market_phase(self, ohlcv):
        import vuka.strategies.ict_m1 as m1
        fake_bot = _fake_bot(save_sessions=MagicMock())
        fvgs = [("BULLISH_FVG", 1.0950, 1.0970, 5, None, 1.0960)]
        with patch.dict(sys.modules, {"vuka.core.bot": fake_bot}), \
             patch.object(m1, "get_spread", return_value=0.0001), \
             patch.object(m1, "check_pre_trade_spread", return_value=True), \
             patch.object(m1, "place_trade", return_value=MagicMock(retcode=10009)), \
             patch.object(m1, "log_trade") as lt:
            m1.evaluate_ict_m1(
                ohlcv, fvgs, "SWEEP_LOW", 1.0940, 1.0955, 0.0020, 0.10, "NY_Open",
                market_phase="ACCUMULATION",
            )
        lt.assert_called_once()
        ctx = lt.call_args[1]["context"]
        assert ctx["market_phase"] == "ACCUMULATION"
        assert ctx["setup_type"] == "ICT_M1"


class TestLondonOpen:
    def test_buy_injects_market_phase(self, ohlcv):
        import vuka.strategies.london_open as lo
        fake_bot = _fake_bot(
            get_pdh_pdl=MagicMock(return_value=(1.1020, 1.0980)),
            get_asian_range=MagicMock(return_value=(1.1000, 1.0960)),
            save_sessions=MagicMock(),
        )
        fvgs = [("BULLISH_FVG", 1.1010, 1.1020, 5, None, 1.1015)]
        with patch.dict(sys.modules, {"vuka.core.bot": fake_bot}), \
             patch.object(lo, "get_spread", return_value=0.0001), \
             patch.object(lo, "check_premium_discount_zone", return_value=True), \
             patch.object(lo, "check_pre_trade_spread", return_value=True), \
             patch.object(lo, "place_trade", return_value=MagicMock(retcode=10009)), \
             patch.object(lo, "log_trade") as lt:
            lo.evaluate_london_breakout(
                ohlcv, fvgs, "SWEEP_LOW", 1.0955, 1.0998, 0.0020, 0.10, "London Open",
                market_phase="BREAKOUT_BULLISH",
            )
        lt.assert_called_once()
        ctx = lt.call_args[1]["context"]
        assert ctx["market_phase"] == "BREAKOUT_BULLISH"
        assert ctx["setup_type"] == "LONDON_BREAKOUT"
