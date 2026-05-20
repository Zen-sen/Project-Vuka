"""
Integration test for database manager and ingwe.py
Verifies that trades, sessions, and SL moves are properly stored in SQLite
"""

import sqlite3
import sys
from pathlib import Path
from datetime import datetime
from database_manager_v5 import DatabaseManager


def test_trades_table():
    """Verify trades table has expected data."""
    db = DatabaseManager()
    conn = db._get_connection()
    
    # Get trades
    cursor = conn.execute("SELECT COUNT(*) as count FROM trades")
    result = cursor.fetchone()
    trade_count = result[0]
    
    print(f"✓ Trades table: {trade_count} records")
    
    # Check specific columns
    cursor = conn.execute("PRAGMA table_info(trades)")
    columns = {row[1] for row in cursor.fetchall()}
    expected = {"id", "symbol", "strategy", "time", "direction", "entry_req", "entry_fill", "sl", "tp", "lot_size"}
    
    if expected.issubset(columns):
        print(f"✓ Trades table schema valid ({len(columns)} columns)")
    else:
        print(f"✗ Missing columns: {expected - columns}")
        return False
    
    return True


def test_sessions_table():
    """Verify sessions table exists and has proper schema."""
    db = DatabaseManager()
    conn = db._get_connection()
    
    # Get session count
    cursor = conn.execute("SELECT COUNT(*) as count FROM sessions")
    result = cursor.fetchone()
    session_count = result[0]
    
    print(f"✓ Sessions table: {session_count} records")
    
    # Check schema
    cursor = conn.execute("PRAGMA table_info(sessions)")
    columns = {row[1] for row in cursor.fetchall()}
    expected = {"id", "date", "symbol", "strategy", "session_name", "traded"}
    
    if expected.issubset(columns):
        print(f"✓ Sessions table schema valid ({len(columns)} columns)")
    else:
        print(f"✗ Missing columns: {expected - columns}")
        return False
    
    return True


def test_loss_tracking_table():
    """Verify loss tracking table."""
    db = DatabaseManager()
    conn = db._get_connection()
    
    # Get loss tracking records
    cursor = conn.execute("SELECT COUNT(*) as count FROM loss_tracking")
    result = cursor.fetchone()
    loss_count = result[0]
    
    print(f"✓ Loss tracking table: {loss_count} records")
    
    # Check schema
    cursor = conn.execute("PRAGMA table_info(loss_tracking)")
    columns = {row[1] for row in cursor.fetchall()}
    expected = {"id", "date", "symbol", "strategy", "consecutive_losses"}
    
    if expected.issubset(columns):
        print(f"✓ Loss tracking schema valid ({len(columns)} columns)")
    else:
        print(f"✗ Missing columns: {expected - columns}")
        return False
    
    return True


def test_sl_movements_table():
    """Verify SL movements table."""
    db = DatabaseManager()
    conn = db._get_connection()
    
    # Get SL movement count
    cursor = conn.execute("SELECT COUNT(*) as count FROM sl_movements")
    result = cursor.fetchone()
    sl_count = result[0]
    
    print(f"✓ SL movements table: {sl_count} records")
    
    # Check schema
    cursor = conn.execute("PRAGMA table_info(sl_movements)")
    columns = {row[1] for row in cursor.fetchall()}
    expected = {"id", "time", "ticket", "symbol", "strategy", "old_sl", "new_sl", "movement", "label"}
    
    if expected.issubset(columns):
        print(f"✓ SL movements schema valid ({len(columns)} columns)")
    else:
        print(f"✗ Missing columns: {expected - columns}")
        return False
    
    return True


def test_instance_locks_table():
    """Verify instance locks table."""
    db = DatabaseManager()
    conn = db._get_connection()
    
    # Check schema
    cursor = conn.execute("PRAGMA table_info(instance_locks)")
    columns = {row[1] for row in cursor.fetchall()}
    expected = {"instance_tag", "locked_at", "expires_at"}
    
    if expected.issubset(columns):
        print(f"✓ Instance locks schema valid ({len(columns)} columns)")
    else:
        print(f"✗ Missing columns: {expected - columns}")
        return False
    
    return True


def test_lock_acquire_release():
    """Test lock acquisition and release."""
    db = DatabaseManager()
    
    # Test acquire lock
    instance_tag = "TEST_EURUSD_INGWE"
    success = db.acquire_lock(instance_tag, timeout_seconds=10)
    
    if success:
        print(f"✓ Lock acquired: {instance_tag}")
    else:
        print(f"✗ Failed to acquire lock: {instance_tag}")
        return False
    
    # Verify lock exists
    conn = db._get_connection()
    cursor = conn.execute("SELECT instance_tag FROM instance_locks WHERE instance_tag = ?", (instance_tag,))
    if cursor.fetchone():
        print(f"✓ Lock verified in database")
    else:
        print(f"✗ Lock not found in database")
        return False
    
    # Test release lock
    db.release_lock(instance_tag)
    cursor = conn.execute("SELECT instance_tag FROM instance_locks WHERE instance_tag = ?", (instance_tag,))
    if not cursor.fetchone():
        print(f"✓ Lock released successfully")
    else:
        print(f"✗ Lock still exists after release")
        return False
    
    return True


def test_insert_and_query():
    """Test inserting and querying trades."""
    db = DatabaseManager()
    
    # Insert test trade
    test_trade = {
        "symbol": "EURUSDc",
        "strategy": "TEST",
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "direction": "BUY",
        "entry_req": 1.1500,
        "entry_fill": 1.1501,
        "sl": 1.1450,
        "tp": 1.1550,
        "lot_size": 0.1,
        "effective_rr": 2.0,
        "retcode": 10009,
        "comment": "TEST_TRADE",
        "session": "London",
        "market_mode": "summer",
        "slippage": 0.1
    }
    
    try:
        result = db.insert_trade(test_trade)
        if result > 0:
            print(f"✓ Test trade inserted (ID: {result})")
        else:
            print(f"✗ Failed to insert test trade")
            return False
    except Exception as e:
        print(f"✗ Error inserting trade: {e}")
        return False
    
    # Query trades
    try:
        trades = db.get_trades(limit=5)
        if len(trades) > 0:
            print(f"✓ Query successful: {len(trades)} trades retrieved")
        else:
            print(f"✗ No trades returned from query")
            return False
    except Exception as e:
        print(f"✗ Error querying trades: {e}")
        return False
    
    return True


def main():
    """Run all tests."""
    print("\n" + "="*60)
    print("DATABASE INTEGRATION TESTS")
    print("="*60 + "\n")
    
    tests = [
        ("Trades Table", test_trades_table),
        ("Sessions Table", test_sessions_table),
        ("Loss Tracking Table", test_loss_tracking_table),
        ("SL Movements Table", test_sl_movements_table),
        ("Instance Locks Table", test_instance_locks_table),
        ("Lock Acquire/Release", test_lock_acquire_release),
        ("Insert & Query", test_insert_and_query),
    ]
    
    passed = 0
    failed = 0
    
    for test_name, test_func in tests:
        print(f"\n{test_name}:")
        print("-" * 40)
        try:
            if test_func():
                passed += 1
            else:
                failed += 1
        except Exception as e:
            print(f"✗ Test failed with exception: {e}")
            failed += 1
    
    print("\n" + "="*60)
    print(f"RESULTS: {passed} passed, {failed} failed")
    print("="*60 + "\n")
    
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
