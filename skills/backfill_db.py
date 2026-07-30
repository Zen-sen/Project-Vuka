"""
Backfill the SQLite database with trade data from trade_log.json.
Migrates historical trades to include the new enrichment columns
(htf_bias, kronos_decision, confluence_score, etc.) added in v5.5.

Usage:
    python -m skills.backfill_db
    python -m skills.backfill_db --dry-run
"""

import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "src"))

TRADE_LOG = BASE / "data" / "trade_log.json"


def main(dry_run: bool = False):
    from vuka.data.database_manager import get_db

    if not TRADE_LOG.exists():
        print(f"Trade log not found: {TRADE_LOG}")
        return

    with open(TRADE_LOG) as f:
        trades = json.load(f)

    db = get_db()
    conn = db._get_connection()

    inserted = 0
    updated = 0
    skipped = 0

    for t in trades:
        key = (t.get("symbol"), t.get("strategy"), t.get("entry_time"), t.get("direction"))
        if not all(key):
            skipped += 1
            continue

        # Extract sl/tp from exit_reason like "[sl 1.17607]" or "[tp 1.33408]"
        exit_reason = t.get("exit_reason") or ""
        sl_val = _extract_price(exit_reason, "sl")
        tp_val = _extract_price(exit_reason, "tp")

        trade_entry = {
            "symbol": t.get("symbol"),
            "strategy": t.get("strategy"),
            "time": t.get("entry_time"),
            "direction": t.get("direction"),
            "entry_req": t.get("entry"),
            "entry_fill": t.get("entry"),
            "sl": sl_val or 0,
            "tp": tp_val or 0,
            "lot_size": t.get("volume", 0),
            "effective_rr": None,
            "retcode": None,
            "comment": exit_reason,
            "session": None,
            "market_mode": None,
            "slippage": 0,
            "position_id": None,
            "pnl_usd": t.get("pnl_usd"),
            "htf_bias": "SPLIT",
            "kronos_decision": "ALLOW",
            "kronos_confidence": 0.0,
            "circuit_breaker": "CLOSED",
            "api_latency_ms": 0.0,
            "spread_at_entry": None,
            "fvg_confirmed": False,
            "ob_present": False,
            "confluence_score": 0,
            "setup_type": "",
            "concept_confidence": 0.0,
            "trail_be_at": 1.0,
        }

        if dry_run:
            print(f"  Would insert: {trade_entry['time']} {trade_entry['symbol']} {trade_entry['direction']}")
            continue

        try:
            row_id = db.insert_trade(trade_entry)
            if row_id > 0:
                inserted += 1
            else:
                updated += 1
        except Exception as e:
            print(f"  Error inserting trade {key}: {e}")
            skipped += 1
            continue

        # Update PnL and exit info if the trade is closed
        exit_time = t.get("exit_time") or ""
        pnl = t.get("pnl_usd")
        exit_reason = t.get("exit_reason") or ""
        if pnl is not None or exit_time or exit_reason:
            db.update_trade_pnl(
                symbol=trade_entry["symbol"],
                strategy=trade_entry["strategy"],
                time_str=trade_entry["time"],
                direction=trade_entry["direction"],
                pnl_usd=pnl if pnl is not None else 0,
                exit_price=None,
                exit_reason=exit_reason or None,
                exit_time=exit_time or None,
            )

    print(f"\nBackfill complete.")
    print(f"  Inserted: {inserted}")
    print(f"  Updated:  {updated}")
    print(f"  Skipped:  {skipped}")


def _extract_price(reason: str, tag: str) -> float | None:
    """Extract price from exit_reason like '[sl 1.17607]' or '[tp 1.33408]'."""
    import re
    m = re.search(rf"\[{tag}\s+([\d.]+)\]", reason)
    if m:
        return float(m.group(1))
    return None


if __name__ == "__main__":
    dry = "--dry-run" in sys.argv
    main(dry_run=dry)
