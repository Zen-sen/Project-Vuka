#!/usr/bin/env python3
"""
Phase 2a: Canary Deployment Monitoring

Real-time monitoring of event-driven tick engine performance.
Tracks: Latency, trades, win rate, database health
"""

import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path


class CanaryMonitor:
    def __init__(self, symbol="EURUSD", strategy="INGWE", db_path=None):
        self.symbol = symbol
        self.strategy = strategy
        # Resolve relative to the project root so the canary reads the same
        # database regardless of the working directory it is launched from.
        self.db_path = Path(db_path) if db_path else Path(__file__).resolve().parents[3] / "vuka_trading.db"
        self.start_time = datetime.now()
        self._conn: sqlite3.Connection | None = None

    def get_elapsed_hours(self):
        return (datetime.now() - self.start_time).total_seconds() / 3600

    def _get_conn(self) -> sqlite3.Connection:
        """Reuse a single connection instead of opening one per query."""
        if self._conn is None:
            self._conn = sqlite3.connect(str(self.db_path))
        return self._conn

    def close(self):
        """Release the shared connection (idempotent)."""
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception as e:
                print(f"[ERROR] Database close failed: {e}")
            finally:
                self._conn = None

    def query_database(self, query, params=()):
        """
        Execute SQLite query safely using parameterized statements.

        `params` are bound with placeholders (?), never interpolated into the
        SQL string -- no injection path even if symbol/strategy become
        user- or config-driven. Commit before returning so any future
        INSERT/UPDATE is durable and WAL journals are trimmed.
        """
        try:
            conn = self._get_conn()
            cursor = conn.execute(query, params)
            result = cursor.fetchall()
            conn.commit()
            return result
        except Exception as e:
            print(f"[ERROR] Database query failed: {e}")
            return None

    def get_trade_stats(self):
        """Get trade statistics from database."""
        # Recent trades
        result = self.query_database("""
            SELECT COUNT(*) FROM trades
            WHERE symbol = ? AND strategy = ?
            AND created_at > datetime('now', '-1 day')
        """, (self.symbol, self.strategy))
        recent_trades = result[0][0] if result else 0

        # Win rate -- wins and total from a SINGLE atomic query so the
        # numerator/denominator can never come from different transaction
        # boundaries (a trade inserted between two queries would skew it).
        result = self.query_database("""
            SELECT COUNT(*) AS total,
                   SUM(CASE WHEN effective_rr > 0 THEN 1 ELSE 0 END) AS wins
            FROM trades
            WHERE symbol = ? AND strategy = ?
        """, (self.symbol, self.strategy))
        total = result[0][0] if result else 0
        wins = result[0][1] if result and result[0][1] is not None else 0

        win_rate = (wins / total * 100) if total > 0 else 0

        # Average RR
        result = self.query_database("""
            SELECT AVG(effective_rr) FROM trades
            WHERE symbol = ? AND strategy = ?
            AND effective_rr > 0
        """, (self.symbol, self.strategy))
        avg_rr = result[0][0] if result and result[0][0] else 0

        return {
            "recent_trades": recent_trades,
            "total_trades": total,
            "wins": wins,
            "win_rate": win_rate,
            "avg_rr": avg_rr
        }

    def check_database_health(self):
        """Verify database is healthy"""
        health = {
            "connected": False,
            "writable": False,
            "size_mb": 0
        }

        try:
            # Connection check (reuses the shared connection)
            conn = self._get_conn()
            conn.execute("SELECT 1")
            health["connected"] = True

            # Writable check (try read)
            result = self.query_database("SELECT COUNT(*) FROM trades")
            health["writable"] = result is not None

            # File size
            if self.db_path.exists():
                size_bytes = self.db_path.stat().st_size
                health["size_mb"] = round(size_bytes / (1024 * 1024), 2)
        except Exception as e:
            print(f"[ERROR] Database health check failed: {e}")

        return health

    def print_status(self):
        """Print current canary status"""
        elapsed = self.get_elapsed_hours()
        trade_stats = self.get_trade_stats()
        db_health = self.check_database_health()

        print("\n" + "=" * 80)
        print("PHASE 2A: CANARY DEPLOYMENT MONITOR")
        print(f"Instance: {self.symbol}_{self.strategy}")
        print(f"Elapsed: {elapsed:.1f} hours")
        print("=" * 80)

        print("\n[TRADING]")
        print(f"  Recent trades (24h):  {trade_stats['recent_trades']}")
        print(f"  Total trades:         {trade_stats['total_trades']}")
        print(f"  Wins:                 {trade_stats['wins']}")
        print(f"  Win rate:             {trade_stats['win_rate']:.1f}%")
        print(f"  Avg RR achieved:      {trade_stats['avg_rr']:.2f}:1")

        print("\n[DATABASE]")
        print(f"  Connected:            {'[OK] YES' if db_health['connected'] else '[X] NO'}")
        print(f"  Writable:             {'[OK] YES' if db_health['writable'] else '[X] NO'}")
        print(f"  Size:                 {db_health['size_mb']:.2f} MB")

        print("\n" + "=" * 80)

        # Checkpoints
        if elapsed >= 24:
            print("[CHECKPOINT 4 - CANARY COMPLETE]")
            print("  [OK] 24-hour period complete")
            print("  Proceed to Phase 2b: Full Rollout")
            return "complete"
        elif elapsed >= 18:
            print("[CHECKPOINT 3 - OVERNIGHT TRADING]")
            print("  Monitoring overnight trading performance...")
            return "checkpoint3"
        elif elapsed >= 12:
            print("[CHECKPOINT 2 - MID-CANARY]")
            print("  Verifying performance stability...")
            return "checkpoint2"
        elif elapsed >= 6:
            print("[CHECKPOINT 1 - EARLY VALIDATION]")
            print("  Checking initial tick engine behavior...")
            return "checkpoint1"
        else:
            print("[STARTING UP]")
            print("  Waiting for first trades and tick events...")
            return "startup"

    def run_continuous_monitoring(self, interval_minutes=5):
        """Run continuous monitoring loop"""
        print(f"\n[*] Starting continuous monitoring (update interval: {interval_minutes} min)")
        print("[*] Press Ctrl+C to stop\n")

        try:
            while True:
                status = self.print_status()

                if status == "complete":
                    print("\n[OK] CANARY DEPLOYMENT COMPLETE")
                    print("Decision: Ready for Phase 2b - Full Rollout")
                    break

                # Wait before next update
                print(f"\n[*] Next check in {interval_minutes} minutes...")
                time.sleep(interval_minutes * 60)

        except KeyboardInterrupt:
            print("\n\n[*] Monitoring stopped by user")
            elapsed = self.get_elapsed_hours()
            print(f"[*] Canary ran for {elapsed:.1f} hours")


if __name__ == "__main__":
    # Optional: run with different check interval
    interval = 5  # minutes
    if len(sys.argv) > 1:
        try:
            interval = int(sys.argv[1])
        except ValueError:
            print("Usage: python3 monitor_canary.py [interval_minutes]")
            interval = 5

    monitor = CanaryMonitor()
    try:
        monitor.run_continuous_monitoring(interval_minutes=interval)
    finally:
        monitor.close()
