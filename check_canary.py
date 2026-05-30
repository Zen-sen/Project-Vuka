#!/usr/bin/env python3
"""Check canary deployment results from database"""

import sqlite3
from datetime import datetime, timedelta

conn = sqlite3.connect('vuka_trading.db')
cursor = conn.cursor()

print("=" * 80)
print("PHASE 2A CANARY DEPLOYMENT RESULTS")
print("=" * 80)

# Check recent trades (last 24 hours)
try:
    cursor.execute("""
        SELECT COUNT(*) as count FROM trades 
        WHERE created_at > datetime('now', '-1 day')
        AND symbol = 'EURUSD' AND strategy = 'INGWE'
    """)
    recent_count = cursor.fetchone()[0]
    print(f"\n[OK] Recent trades (last 24h): {recent_count}")
except Exception as e:
    print(f"[-] Error checking recent trades: {e}")
    recent_count = 0

# Check all EURUSD INGWE trades
try:
    cursor.execute("""
        SELECT COUNT(*) as count FROM trades 
        WHERE symbol = 'EURUSD' AND strategy = 'INGWE'
    """)
    total_count = cursor.fetchone()[0]
    print(f"[OK] Total EURUSD INGWE trades: {total_count}")
except Exception as e:
    print(f"[-] Error checking total trades: {e}")
    total_count = 0

# Calculate win rate
if total_count > 0:
    try:
        cursor.execute("""
            SELECT COUNT(*) as wins FROM trades
            WHERE symbol = 'EURUSD' AND strategy = 'INGWE'
            AND effective_rr > 0
        """)
        wins = cursor.fetchone()[0]
        win_rate = (wins / total_count) * 100
        print(f"[OK] Win rate: {win_rate:.1f}% ({wins}/{total_count})")
    except Exception as e:
        print(f"[-] Error calculating win rate: {e}")

# Check for errors in recent trades
try:
    cursor.execute("""
        SELECT retcode, COUNT(*) as count
        FROM trades
        WHERE created_at > datetime('now', '-1 day')
        AND symbol = 'EURUSD' AND strategy = 'INGWE'
        GROUP BY retcode
    """)
    results = cursor.fetchall()
    print(f"\n[OK] Trade execution status (last 24h):")
    for retcode, count in results:
        status = "SUCCESS" if retcode == 0 else f"ERROR {retcode}"
        print(f"  {status}: {count} trades")
except Exception as e:
    print(f"[-] Error checking retcodes: {e}")

# Get last 5 trades
print(f"\n[OK] Last 5 trades (EURUSD INGWE):")
try:
    cursor.execute("""
        SELECT created_at, direction, entry_fill, effective_rr, comment
        FROM trades
        WHERE symbol = 'EURUSD' AND strategy = 'INGWE'
        ORDER BY created_at DESC
        LIMIT 5
    """)
    for row in cursor.fetchall():
        created_at, direction, entry_fill, rr, comment = row
        print(f"  {created_at}: {direction} @ {entry_fill} (RR: {rr:.2f}:1)")
except Exception as e:
    print(f"[-] Error fetching last trades: {e}")

# Check tick engine activity (if available)
print("=" * 80)
print("[OK] Database query complete")

conn.close()
