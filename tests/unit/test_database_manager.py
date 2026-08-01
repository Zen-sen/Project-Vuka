"""DatabaseManager dedup-lock tests.

v6.2: dedup_check_and_lock must FAIL CLOSED -- if the lock cannot be secured
(connection error, transaction error), the bot must not trade.
"""
from unittest.mock import patch

import pytest

from vuka.data.database_manager import DatabaseManager


@pytest.fixture
def db():
    return DatabaseManager(db_path=":memory:")


class TestDedupLock:
    def test_lock_acquired_when_free(self, db):
        assert db.dedup_check_and_lock("EURUSD", "London Open", "INGWE") is True

    def test_lock_denied_when_held(self, db):
        assert db.dedup_check_and_lock("EURUSD", "London Open", "INGWE") is True
        assert db.dedup_check_and_lock("EURUSD", "London Open", "INGWE") is False

    def test_lock_is_per_symbol_session(self, db):
        """The lock prevents ANY strategy from re-trading a symbol+session."""
        assert db.dedup_check_and_lock("EURUSD", "London Open", "INGWE") is True
        assert db.dedup_check_and_lock("EURUSD", "London Open", "SILVER_BULLET") is False
        assert db.dedup_check_and_lock("EURUSD", "NY", "INGWE") is True

    def test_fails_closed_on_transaction_error(self, db):
        """If the lock query itself raises, return False -- never trade."""
        with patch.object(db, "_transaction", side_effect=RuntimeError("disk full")):
            assert db.dedup_check_and_lock("EURUSD", "London Open", "INGWE") is False
