"""UnifiedLogger tests — construction is DB-free and logging is fail-safe.

The DB connection is resolved lazily on flush, never at import/construction,
and a broken DB degrades to a no-op sink instead of crashing the caller.
"""
from unittest.mock import MagicMock, patch

import pytest

import vuka.utils.unified_logger as ul


@pytest.fixture(autouse=True)
def reset_buffer():
    ul._BUFFER.clear()
    yield
    ul._BUFFER.clear()


class TestConstruction:
    def test_constructor_does_not_touch_db(self):
        with patch.object(ul, "get_db", side_effect=AssertionError("db touched at init")):
            logger = ul.UnifiedLogger("X")
            assert logger.component == "X"
            assert len(logger.session_id) == 8

    def test_get_logger_caches_by_component(self):
        a1 = ul.get_logger("CacheTest")
        a2 = ul.get_logger("CacheTest")
        b = ul.get_logger("CacheOther")
        assert a1 is a2
        assert a1 is not b


class TestBufferedWrite:
    def test_log_is_non_blocking_no_db_on_call(self):
        with patch.object(ul, "get_db", side_effect=AssertionError("db touched on log()")):
            ul.get_logger("ASYNC").info("x")

    def test_log_buffers_and_flushes_with_session_metadata(self):
        db = MagicMock()
        with patch.object(ul, "get_db", return_value=db):
            logger = ul.get_logger("TEST")
            logger.info("hello", symbol="EURUSD", strategy="INGWE")
            assert not db.log_event.called
            ul._flush()
        db.log_event.assert_called_once()
        kwargs = db.log_event.call_args.kwargs
        assert kwargs["level"] == "INFO"
        assert kwargs["component"] == "TEST"
        assert kwargs["message"] == "hello"
        assert kwargs["symbol"] == "EURUSD"
        assert kwargs["strategy"] == "INGWE"
        assert kwargs["metadata"]["session_id"] == logger.session_id

    def test_warn_error_guard_levels_pass_through(self):
        db = MagicMock()
        with patch.object(ul, "get_db", return_value=db):
            logger = ul.get_logger("LVL")
            logger.warn("w")
            logger.warning("w2")
            logger.error("e")
            logger.guard("g")
            logger.trade("t")
            ul._flush()
        levels = [c.kwargs["level"] for c in db.log_event.call_args_list]
        assert levels == ["WARN", "WARN", "ERROR", "GUARD", "TRADE"]


class TestFailSafe:
    def test_log_failsafe_when_db_unreachable(self):
        with patch.object(ul, "get_db", side_effect=RuntimeError("locked")):
            ul.get_logger("CRASH").error("boom")
            ul._flush()  # must not raise

    def test_log_failsafe_when_log_event_raises(self, capsys):
        db = MagicMock()
        db.log_event.side_effect = RuntimeError("disk full")
        with patch.object(ul, "get_db", return_value=db):
            ul.get_logger("CRASH").error("boom")
            ul._flush()
        err = capsys.readouterr().err
        assert "dropped ERROR CRASH" in err
