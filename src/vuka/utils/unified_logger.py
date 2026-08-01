"""
Unified Logger for Project Vuka
Centralizes all system events into the SQLite database for correlation and monitoring.

Fail-safe by design: logging must never crash the bot.
  * The DB connection is resolved lazily on first flush, never at import.
  * An unreachable DB degrades to a no-op sink (NullDB) instead of raising.
  * Every log() call buffers in memory and returns immediately; a single
    daemon writer thread drains the buffer (TelemetryQueue pattern), so disk
    I/O never blocks the trade path.
"""
import contextlib
import sys
import threading
import time
import uuid
from typing import Any

from vuka.data.database_manager import get_db

# ── Buffered writer ────────────────────────────────────────────────────────
_BUFFER: list[dict] = []
_BUFFER_LOCK = threading.Lock()
_WRITER: threading.Thread | None = None
_FLUSH_INTERVAL_SEC = 5.0
_FLUSH_THRESHOLD = 64


class _NullDB:
    """No-op sink used when the real database cannot be reached."""

    def log_event(self, *args, **kwargs):
        pass


def _enqueue(entry: dict) -> None:
    """Buffer a log entry and opportunistically flush on a full buffer."""
    global _WRITER
    with _BUFFER_LOCK:
        _BUFFER.append(entry)
        at_threshold = len(_BUFFER) >= _FLUSH_THRESHOLD
        if _WRITER is None or not _WRITER.is_alive():
            _WRITER = threading.Thread(
                target=_writer_loop, name="UnifiedLoggerWriter", daemon=True
            )
            _WRITER.start()
    if at_threshold:
        _flush()


def _writer_loop() -> None:
    while True:
        time.sleep(_FLUSH_INTERVAL_SEC)
        with contextlib.suppress(Exception):
            _flush()


def _flush() -> None:
    """Drain the buffer to the database. Failures are swallowed per-entry."""
    global _BUFFER
    with _BUFFER_LOCK:
        batch = _BUFFER
        _BUFFER = []
    for entry in batch:
        _write(entry)


_stderr_lock = threading.Lock()


def _write(entry: dict) -> None:
    try:
        db = get_db()
    except Exception:
        db = _NullDB()
    try:
        metadata = dict(entry.get("metadata") or {})
        metadata.setdefault("session_id", entry.get("session_id", ""))
        db.log_event(
            level=entry["level"],
            component=entry["component"],
            message=entry["message"],
            symbol=entry.get("symbol"),
            strategy=entry.get("strategy"),
            trace_id=entry.get("trace_id"),
            metadata=metadata,
        )
    except Exception as e:
        # Logging must be fail-safe: drop the entry rather than re-queue it
        # (an error echo loop would flood the channel it just broke).
        with _stderr_lock:
            print(
                f"[UnifiedLogger] dropped {entry['level']} "
                f"{entry['component']}: {e}",
                file=sys.stderr,
            )


class UnifiedLogger:
    def __init__(self, component: str):
        self.component = component
        # Each instance of a bot gets a session_id to track its current run.
        # It is attached to every event's metadata so it is queryable.
        self.session_id = str(uuid.uuid4())[:8]

    def log(self, level: str, message: str,
            symbol: str | None = None,
            strategy: str | None = None,
            trace_id: str | None = None,
            metadata: dict[str, Any] | None = None):
        """
        Log an event to the unified system log (buffered, non-blocking).
        """
        _enqueue({
            "level": level.upper(),
            "component": self.component,
            "message": message,
            "symbol": symbol,
            "strategy": strategy,
            "trace_id": trace_id,
            "metadata": metadata,
            "session_id": self.session_id,
        })

    def info(self, message: str, **kwargs):
        self.log("INFO", message, **kwargs)

    def warn(self, message: str, **kwargs):
        self.log("WARN", message, **kwargs)

    def warning(self, message: str, **kwargs):
        self.log("WARN", message, **kwargs)

    def error(self, message: str, **kwargs):
        self.log("ERROR", message, **kwargs)

    def trade(self, message: str, **kwargs):
        self.log("TRADE", message, **kwargs)

    def guard(self, message: str, **kwargs):
        self.log("GUARD", message, **kwargs)

    def create_trace(self) -> str:
        """Generate a unique ID to track a specific trade setup flow."""
        return str(uuid.uuid4())[:8]


# Helper to create loggers for different components (cached per component)
_logger_cache: dict[str, UnifiedLogger] = {}


def get_logger(component: str) -> UnifiedLogger:
    """Return the cached UnifiedLogger for a component, creating it on first use."""
    if component not in _logger_cache:
        _logger_cache[component] = UnifiedLogger(component)
    return _logger_cache[component]
