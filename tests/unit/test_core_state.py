import pytest
from vuka.core.state import State


class TestState:
    def test_import_and_create(self):
        s = State()
        assert s.STRATEGY == ""

    def test_get_set_string(self):
        s = State()
        s.STRATEGY = "INGWE"
        assert s.STRATEGY == "INGWE"

    def test_get_set_dict(self):
        s = State()
        s.sessions_traded_today.add("London")
        assert "London" in s.sessions_traded_today

    def test_missing_key_returns_attribute_error(self):
        s = State()
        with pytest.raises(AttributeError):
            _ = s.NONEXISTENT

    def test_overwrite(self):
        s = State()
        s.SYMBOL = "EURUSD"
        s.SYMBOL = "GBPUSD"
        assert s.SYMBOL == "GBPUSD"

    def test_reset(self):
        s = State()
        s.STRATEGY = "INGWE"
        s.SYMBOL = "EURUSD"
        s.initial_equity = 12345.0

        s.STRATEGY = ""
        s.SYMBOL = ""
        s.initial_equity = None
        assert s.STRATEGY == ""
        assert s.SYMBOL == ""
        assert s.initial_equity is None

    def test_isolation_between_instances(self):
        a = State()
        b = State()
        a.STRATEGY = "INGWE"
        b.STRATEGY = "SILVER_BULLET"
        assert a.STRATEGY == "INGWE"
        assert b.STRATEGY == "SILVER_BULLET"

    def test_numeric_types(self):
        s = State()
        s.RISK_PERCENT = 1.0
        s.ATR_PERIOD = 14
        s.HARD_LOT_CAP = 0.20
        s.BUY_THRESHOLD = 0.35
        assert isinstance(s.RISK_PERCENT, float)
        assert isinstance(s.ATR_PERIOD, int)
        assert isinstance(s.HARD_LOT_CAP, float)
        assert isinstance(s.BUY_THRESHOLD, float)
