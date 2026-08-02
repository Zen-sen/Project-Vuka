import contextlib
import json
import logging
import os
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    import msvcrt  # Windows advisory locking
except ImportError:
    msvcrt = None

try:
    import fcntl  # POSIX advisory locking
except ImportError:
    fcntl = None

logger = logging.getLogger(__name__)

LOCK_TIMEOUT_SECONDS = 5.0


class StateLockError(Exception):
    """Raised when the session lock cannot be acquired in time."""


class StateManager:
    """
    Manages session persistence with atomic writes and backup rotation.

    Features:
    - Atomic writes (temp file + move) prevent corruption
    - Backup rotation keeps last N versions
    - Corruption detection with automatic recovery
    - JSON schema validation
    """

    def __init__(self, session_file: str = "sessions_today.json", max_backups: int = 10):
        self.session_file = Path(session_file)
        self.backup_dir = self.session_file.parent / "state_backups"
        self.backup_dir.mkdir(exist_ok=True)
        self.max_backups = max_backups
        self.lock_file = self.session_file.with_suffix(".lock")

    @contextlib.contextmanager
    def _locked(self):
        """
        Advisory lock guarding concurrent writers (bot + monitor + dashboard).

        Uses an OS advisory lock (msvcrt on Windows, fcntl on POSIX) on the
        .lock file with a bounded wait. If no locking primitive is available
        it degrades to a no-op so existing single-process flows still work.
        """
        if msvcrt is None and fcntl is None:
            yield
            return

        fd = os.open(self.lock_file, os.O_CREAT | os.O_RDWR)
        acquired = False
        try:
            # msvcrt.locking requires at least one byte at the lock offset.
            if os.fstat(fd).st_size == 0:
                os.write(fd, b"\x00")

            deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS
            while True:
                try:
                    if msvcrt is not None:
                        os.lseek(fd, 0, os.SEEK_SET)
                        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                    else:
                        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise StateLockError(f"Could not acquire lock: {self.lock_file}") from None
                    time.sleep(0.05)

            yield
        finally:
            if acquired:
                try:
                    if msvcrt is not None:
                        os.lseek(fd, 0, os.SEEK_SET)
                        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                    else:
                        fcntl.flock(fd, fcntl.LOCK_UN)
                except OSError:
                    pass
            os.close(fd)

    def _atomic_write(self, data: dict) -> bool:
        """
        Write data atomically using temp file + os.replace.

        os.replace is atomic on POSIX and Windows: a reader can never observe
        a partial file, and a crash mid-write leaves the previous good copy
        untouched. There is no delete-main-first step to race against.
        """
        temp_file = self.session_file.with_suffix(".tmp")

        try:
            with open(temp_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2)
                f.flush()
                os.fsync(f.fileno())

            os.replace(temp_file, self.session_file)
            logger.info(f"Atomic write successful: {self.session_file}")
            return True

        except Exception as e:
            logger.error(f"Atomic write failed: {str(e)}")
            if temp_file.exists():
                with contextlib.suppress(OSError):
                    temp_file.unlink()
            return False

    def _rotate_backups(self):
        """Keep only max_backups versioned backups, delete oldest."""
        try:
            backups = sorted(self.backup_dir.glob("session_*.json"))

            if len(backups) > self.max_backups:
                for old_backup in backups[:-self.max_backups]:
                    old_backup.unlink()
                    logger.info(f"Deleted old backup: {old_backup.name}")

        except Exception as e:
            logger.warning(f"Backup rotation failed: {str(e)}")

    def save_session(self, trades: list, metadata: dict) -> bool:
        """
        Save session state with atomic write and backup creation.

        Args:
            trades: List of executed trades
            metadata: Session metadata (PnL, timestamps, etc.)

        Returns:
            True if save successful, False otherwise
        """
        data = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "trades": trades,
            "metadata": metadata,
            "version": "v4.6",
            "backup_count": len(list(self.backup_dir.glob("session_*.json")))
        }

        # Locked so a concurrent writer (bot + monitor + dashboard) cannot
        # interleave its backup copy with ours mid-write.
        success = False
        try:
            with self._locked():
                # Step 1: Timestamped backup before overwriting (rolling
                # scheme -- each save keeps a named copy for recovery).
                if self.session_file.exists():
                    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                    backup_file = self.backup_dir / f"session_{timestamp}.json"
                    # copyfile: content only, no ownership/ACL metadata that
                    # could block a monitor reading as another user.
                    shutil.copyfile(self.session_file, backup_file)
                    logger.debug(f"Backup created: {backup_file.name}")

                # Step 2: Atomic write
                success = self._atomic_write(data)
        except StateLockError as e:
            logger.error(f"Session save aborted: {e}")
            return False

        if success:
            self._rotate_backups()
            logger.info(f"Session saved: {len(trades)} trades")

        return success

    def load_session(self) -> dict | None:
        """
        Load session state with corruption detection.

        Returns:
            Session dict if valid, None if corrupted or missing
            Automatically attempts recovery from backups on corruption
        """
        if not self.session_file.exists():
            logger.info("No session file found")
            return None

        try:
            with open(self.session_file, encoding='utf-8') as f:
                data = json.load(f)

            # Validate schema
            self._validate_schema(data)

            logger.info(f"Session loaded: {len(data.get('trades', []))} trades")
            return data

        except json.JSONDecodeError as e:
            logger.error(f"Session file corrupted (JSON): {str(e)}")
            return self._restore_from_backup()

        except ValueError as e:
            logger.error(f"Session file invalid: {str(e)}")
            return self._restore_from_backup()

        except Exception as e:
            logger.error(f"Session load failed: {str(e)}")
            return None

    def _validate_schema(self, data: dict):
        """
        Validate only the fields the loader actually reads.

        Unknown/extra fields (a human-added column, a new strategy field) are
        intentionally ignored -- over-strict validation would reject a
        legitimate session and trigger a needless backup restore.
        """
        required = ["timestamp", "trades", "metadata", "version"]

        if not isinstance(data, dict):
            raise ValueError("Session must be a dict")

        for field in required:
            if field not in data:
                raise ValueError(f"Missing required field: {field}")

        if not isinstance(data["trades"], list):
            raise ValueError("Trades must be a list")

        for i, trade in enumerate(data["trades"]):
            if not isinstance(trade, dict):
                raise ValueError(f"Trade {i} is not a dict")

    def _restore_from_backup(self) -> dict | None:
        """Attempt to restore from most recent valid backup."""
        backups = sorted(self.backup_dir.glob("session_*.json"), reverse=True)

        logger.info(f"Attempting recovery from {len(backups)} backups...")

        for backup in backups:
            try:
                with open(backup, encoding='utf-8') as f:
                    data = json.load(f)

                self._validate_schema(data)

                logger.info(f"Restored from backup: {backup.name}")

                # Restore to main session file (content only, no metadata).
                with self._locked():
                    shutil.copyfile(backup, self.session_file)

                return data

            except Exception as e:
                logger.warning(f"Backup {backup.name} unusable: {str(e)}")
                continue

        logger.error("No valid backups available")
        return None

    def get_backup_count(self) -> int:
        """Return number of backup files."""
        return len(list(self.backup_dir.glob("session_*.json")))

    def clear_backups(self):
        """Remove all backups (use with caution)."""
        try:
            for backup in self.backup_dir.glob("session_*.json"):
                backup.unlink()
            logger.info("Backups cleared")
        except Exception as e:
            logger.error(f"Backup cleanup failed: {str(e)}")


if __name__ == "__main__":
    # Quick test
    logging.basicConfig(level=logging.INFO)

    mgr = StateManager()

    # Test save
    test_trades = [
        {"time": "2026-05-15 10:00", "direction": "BUY", "entry": 1.0, "sl": 0.99, "tp": 1.03}
    ]
    test_meta = {"daily_pnl": 50, "instance": "EURUSD_INGWE"}

    mgr.save_session(test_trades, test_meta)

    # Test load
    loaded = mgr.load_session()
    print(f"[OK] Loaded: {len(loaded['trades'])} trades")
    print(f"[OK] Backups: {mgr.get_backup_count()}")

