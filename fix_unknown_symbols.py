"""
One-time fix: patch remaining UNKNOWN symbols in trades table.
Fixes:
1. JSON-imported trades: symbol from comment field (EURUS/GBPUS) or price heuristic
2. Bot-placed trades with missing symbol: price heuristic
3. Maps old JSON 'entry' field to entry_req/entry_fill
"""
import sqlite3
from database_manager import get_db

DB_FILE = "vuka_trading.db"


def patch_remaining():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row

    # First, fix by comment field
    cur = conn.execute(
        "SELECT id, comment, time, direction FROM trades WHERE symbol='UNKNOWN' AND comment IS NOT NULL"
    )
    fixed_comment = 0
    for row in cur.fetchall():
        comment = (row["comment"] or "").upper()
        if "EUR" in comment:
            symbol = "EURUSDc"
        elif "GBP" in comment:
            symbol = "GBPUSDc"
        else:
            continue
        conn.execute("UPDATE trades SET symbol=? WHERE id=?", (symbol, row["id"]))
        fixed_comment += 1
    print(f"Fixed by comment field: {fixed_comment}")

    # Second, map JSON 'entry' -> entry_req + entry_fill for remaining UNKNOWN
    for json_file, file_symbol in [
        ("trades_EURUSD_INGWE.json", "EURUSDc"),
        ("trades_GBPUSD_INGWE.json", "GBPUSDc"),
    ]:
        import json
        from pathlib import Path

        path = Path(json_file)
        if not path.exists():
            continue
        with open(path) as f:
            trades = json.load(f)
        for t in trades:
            ts = t.get("time")
            direction = t.get("direction")
            entry = t.get("entry")
            if not ts or not direction:
                continue
            # Find the DB record with matching time+direction+strategy
            cur = conn.execute(
                "SELECT id FROM trades WHERE symbol='UNKNOWN' AND strategy=? AND time=? AND direction=?",
                ("INGWE", ts, direction),
            )
            row = cur.fetchone()
            if row is None:
                continue
            conn.execute(
                "UPDATE trades SET symbol=?, entry_req=?, entry_fill=? WHERE id=?",
                (file_symbol, entry, entry, row["id"]),
            )
    print(f"Fixed by JSON re-map: (included above)")

    # Third, price heuristic for any remaining UNKNOWN trades
    cur = conn.execute(
        "SELECT id, entry_fill, entry_req FROM trades WHERE symbol='UNKNOWN'"
    )
    fixed_price = 0
    for row in cur.fetchall():
        price = row["entry_fill"] or row["entry_req"]
        if price is None:
            continue
        symbol = "GBPUSDc" if price > 1.25 else "EURUSDc"
        conn.execute("UPDATE trades SET symbol=? WHERE id=?", (symbol, row["id"]))
        fixed_price += 1
    print(f"Fixed by price heuristic: {fixed_price}")

    conn.commit()

    # Report
    rows = conn.execute(
        "SELECT symbol, strategy, COUNT(*) as cnt FROM trades GROUP BY symbol, strategy"
    ).fetchall()
    print("\nTrades by symbol/strategy after full fix:")
    for r in rows:
        print(f"  {r['symbol']:12s} {r['strategy']:15s} count={r['cnt']}")
    unknown = conn.execute(
        "SELECT COUNT(*) FROM trades WHERE symbol='UNKNOWN'"
    ).fetchone()
    print(f"\nRemaining UNKNOWN: {unknown[0]}")
    conn.close()


if __name__ == "__main__":
    patch_remaining()
