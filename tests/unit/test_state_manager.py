"""StateManager tests — atomic write, lock timeout, backup restore (audit XXIV)."""
import json
from unittest.mock import MagicMock, patch

import pytest

from vuka.core import state_manager as sm
from vuka.core.state_manager import StateLockError, StateManager


@pytest.fixture
def manager(tmp_path):
    return StateManager(str(tmp_path / "sessions_today.json"), max_backups=3)


class TestAtomicWrite:
    def test_no_tmp_left_behind_and_content_persisted(self, manager):
        assert manager.save_session([{"time": "x"}], {"daily_pnl": 50})
        assert manager.session_file.exists()
        assert not manager.session_file.with_suffix(".tmp").exists()
        data = json.loads(manager.session_file.read_text(encoding="utf-8"))
        assert data["metadata"]["daily_pnl"] == 50
        assert len(data["trades"]) == 1

    def test_overwrite_keeps_valid_json(self, manager):
        manager.save_session([], {"a": 1})
        manager.save_session([{"t": 1}], {"a": 2})
        data = json.loads(manager.session_file.read_text(encoding="utf-8"))
        assert data["metadata"]["a"] == 2
        assert len(data["trades"]) == 1

    def test_save_acquires_lock(self, manager):
        fake = MagicMock()
        fake.__enter__.return_value = None
        fake.__exit__.return_value = False
        with patch.object(manager, "_locked", return_value=fake):
            assert manager.save_session([], {})
        fake.__enter__.assert_called_once()
        fake.__exit__.assert_called_once()


class TestLock:
    def test_timeout_raises_state_lock_error(self, manager, monkeypatch):
        class BusyMsvcrt:
            LK_NBLCK = 0
            LK_UNLCK = 1

            @staticmethod
            def locking(*args, **kwargs):
                raise OSError(13, "Permission denied")

        monkeypatch.setattr(sm, "msvcrt", BusyMsvcrt())
        monkeypatch.setattr(sm, "LOCK_TIMEOUT_SECONDS", 0.1)
        with pytest.raises(StateLockError), manager._locked():
            pass

    def test_lock_acquire_and_release(self, manager):
        with manager._locked():
            pass
        # Second acquisition proves the first was released.
        with manager._locked():
            pass


class TestLoadRestore:
    def test_missing_file_returns_none(self, manager):
        assert manager.load_session() is None

    def test_corrupt_file_restores_from_backup(self, manager):
        manager.save_session([{"time": "x"}], {"pnl": 1})
        manager.save_session([{"time": "x"}], {"pnl": 1})
        assert manager.get_backup_count() >= 1
        manager.session_file.write_text("{not valid json", encoding="utf-8")
        loaded = manager.load_session()
        assert loaded is not None
        assert loaded["metadata"]["pnl"] == 1
        restored = json.loads(manager.session_file.read_text(encoding="utf-8"))
        assert restored["metadata"]["pnl"] == 1

    def test_relaxed_schema_accepts_extra_fields(self, manager):
        data = {
            "timestamp": "t",
            "trades": [{"anything": 1}],
            "metadata": {},
            "version": "v4.6",
            "extra": "ignored",
        }
        manager.session_file.write_text(json.dumps(data), encoding="utf-8")
        assert manager.load_session() is not None

    def test_missing_required_field_fails_without_backup(self, manager):
        manager.session_file.write_text(json.dumps({"trades": []}), encoding="utf-8")
        assert manager.load_session() is None


class TestBackupRotation:
    def test_keeps_at_most_max_backups(self, manager):
        for i in range(6):
            assert manager.save_session([], {"i": i})
        assert manager.get_backup_count() <= manager.max_backups

    def test_clear_backups(self, manager):
        manager.save_session([], {"i": 1})
        manager.save_session([], {"i": 1})
        assert manager.get_backup_count() >= 1
        manager.clear_backups()
        assert manager.get_backup_count() == 0
