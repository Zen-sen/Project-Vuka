import pytest

from vuka.core.config import (
    calculate_confluence_score,
    get_session_multiplier,
    get_confluence_threshold,
    ADX_MIN_THRESHOLD,
)


@pytest.mark.parametrize("strategy,expected", [
    ("ICT_M1", 1),
    ("INGWE", 15),
    ("SILVER_BULLET", 15),
    ("LONDON_OPEN", 15),
])
def test_tf_by_strategy(strategy, expected):
    import MetaTrader5 as mt5
    from vuka.core.config import load_config
    config = load_config("EURUSD", strategy, "test", "EURUSD")
    tf_map = {
        "ICT_M1": mt5.TIMEFRAME_M1,
        "INGWE": mt5.TIMEFRAME_M15,
        "SILVER_BULLET": mt5.TIMEFRAME_M15,
        "LONDON_OPEN": mt5.TIMEFRAME_M15,
    }
    assert config["TIMEFRAME"] == tf_map.get(strategy, mt5.TIMEFRAME_M15)


def test_unknown_strategy_raises(monkeypatch):
    from vuka.core.config import load_config
    config = load_config("EURUSD", "UNKNOWN_STRAT", "test", "EURUSD")
    assert config["TIMEFRAME"] is not None
    assert config["RISK_REWARD_RATIO"] == 3.0


def test_btc_config():
    from vuka.core.config import load_config
    config = load_config("BTCUSD", "INGWE", "test", "BTCUSD")
    assert config["TIMEFRAME"] == 1
    assert config["SCAN_INTERVAL_SEC"] == 60


class TestConfluenceScoring:
    def test_full_score_bullish(self):
        score = calculate_confluence_score(
            "BULLISH", True, True, True, True,
            level_sweep=True, bos_aligned=True, htf_bias_ok=True,
            session="Asian", direction="sell"
        )
        assert score > 0
        assert score == 120

    def test_full_score_bearish(self):
        score = calculate_confluence_score(
            "BEARISH", True, True, True, True,
            level_sweep=True, bos_aligned=True, htf_bias_ok=True,
            session="New York", direction="sell"
        )
        assert score == 120

    def test_partial_score_no_trend(self):
        score = calculate_confluence_score(
            "RANGING", True, True, True, True,
            level_sweep=False, bos_aligned=False, htf_bias_ok=False
        )
        assert score == 55

    def test_no_confluence(self):
        score = calculate_confluence_score(
            "RANGING", False, False, False, False
        )
        assert score == 0

    def test_minimum_threshold_not_met(self):
        score = calculate_confluence_score(
            "BULLISH", True, False, False, True
        )
        assert score < 80

    def test_session_asymmetry_bonus(self):
        score_with = calculate_confluence_score(
            "BULLISH", True, True, True, True, session="Asian", direction="sell"
        )
        score_without = calculate_confluence_score(
            "BULLISH", True, True, True, True, session="London", direction="buy"
        )
        assert score_with > score_without


class TestSessionMultiplier:
    def test_high_winrate_returns_low_multiplier(self):
        m = get_session_multiplier("Asian", "sell")
        assert m == 0.9

    def test_low_winrate_returns_high_multiplier(self):
        m = get_session_multiplier("London", "sell")
        assert m == 1.30

    def test_unknown_session_returns_default(self):
        m = get_session_multiplier("Sydney", "buy")
        assert m == 1.0


class TestConfluenceThreshold:
    def test_adx_below_min_returns_80(self):
        t = get_confluence_threshold(15.0)
        assert t == 80

    def test_adx_above_40_returns_60(self):
        t = get_confluence_threshold(45.0, session="Asian", direction="sell")
        assert t == 54

    def test_adx_normal_with_session_multiplier(self):
        t = get_confluence_threshold(30.0)
        assert t == 70
