"""CanaryMonitor tests — parameterized queries, shared conn, atomic win/total (audit XXIII)."""
import sqlite3
from pathlib import Path

from vuka.core.monitor_canary import CanaryMonitor


def _make_db(tmp_path) -> Path:
    db = tmp_path / "vuka_trading.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE trades (symbol TEXT, strategy TEXT, created_at TEXT, effective_rr REAL)"
    )
    conn.commit()
    conn.close()
    return db


def _insert(db: Path, rows):
    conn = sqlite3.connect(db)
    conn.executemany(
        "INSERT INTO trades VALUES (?,?,?,?)",
        [(r[0], r[1], "2026-01-01 10:00:00", r[2]) for r in rows],
    )
    conn.commit()
    conn.close()


class TestQueryDatabase:
    def test_parameterized_query(self, tmp_path):
        db = _make_db(tmp_path)
        _insert(db, [("EURUSD", "INGWE", 2.0), ("GBPUSD", "INGWE", 1.0)])
        monitor = CanaryMonitor(db_path=db)
        rows = monitor.query_database(
            "SELECT symbol FROM trades WHERE symbol = ? AND strategy = ?",
            ("EURUSD", "INGWE"),
        )
        assert rows == [("EURUSD",)]
        monitor.close()

    def test_shared_connection_reused(self, tmp_path):
        db = _make_db(tmp_path)
        monitor = CanaryMonitor(db_path=db)
        assert monitor._get_conn() is monitor._get_conn()
        monitor.close()
        assert monitor._conn is None

    def test_insert_is_committed(self, tmp_path):
        db = _make_db(tmp_path)
        monitor = CanaryMonitor(db_path=db)
        monitor.query_database(
            "INSERT INTO trades VALUES (?,?,?,?)",
            ("EURUSD", "INGWE", "2026-01-01 10:00:00", 1.5),
        )
        conn = sqlite3.connect(db)
        count = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
        conn.close()
        assert count == 1
        monitor.close()

    def test_bad_query_returns_none_without_raising(self, tmp_path):
        db = _make_db(tmp_path)
        monitor = CanaryMonitor(db_path=db)
        assert monitor.query_database("SELECT * FROM does_not_exist") is None
        monitor.close()


class TestGetTradeStats:
    def test_wins_and_total_from_atomic_query(self, tmp_path):
        db = _make_db(tmp_path)
        _insert(db, [("EURUSD", "INGWE", rr) for rr in (2.0, -1.0, 0.5, -2.0, 3.0)])
        monitor = CanaryMonitor(db_path=db)
        stats = monitor.get_trade_stats()
        assert stats["total_trades"] == 5
        assert stats["wins"] == 3
        assert stats["win_rate"] == 60.0
        monitor.close()

    def test_unknown_symbol_returns_zeros(self, tmp_path):
        db = _make_db(tmp_path)
        _insert(db, [("EURUSD", "INGWE", 2.0)])
        monitor = CanaryMonitor(db_path=db, symbol="GBPUSD")
        stats = monitor.get_trade_stats()
        assert stats["total_trades"] == 0
        assert stats["wins"] == 0
        assert stats["win_rate"] == 0.0
        monitor.close()

    def test_db_health_reports_connected_and_writable(self, tmp_path):
        db = _make_db(tmp_path)
        monitor = CanaryMonitor(db_path=db)
        health = monitor.check_database_health()
        assert health["connected"] is True
        assert health["writable"] is True
        assert health["size_mb"] >= 0
        monitor.close()
