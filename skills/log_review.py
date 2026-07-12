#!/usr/bin/env python3
"""
log_review.py — System Improvement Ledger CLI
Project Vuka | Track reviews, improvements, and outcomes with full timestamps.
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).parent.parent
IMPROVEMENT_LOG = BASE_DIR / "data" / "improvement_log.json"
TRADE_LOG = BASE_DIR / "data" / "trade_log.json"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def load_log() -> list[dict]:
    if not IMPROVEMENT_LOG.exists():
        return []
    with open(IMPROVEMENT_LOG) as f:
        try:
            data = json.load(f)
            return data if isinstance(data, list) else []
        except json.JSONDecodeError:
            return []


def save_log(entries: list[dict]):
    IMPROVEMENT_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(IMPROVEMENT_LOG, "w") as f:
        json.dump(entries, f, indent=2)


def next_id(entries: list[dict]) -> int:
    return max((e.get("id", 0) for e in entries), default=0) + 1


def resolve_trade_range(trade_ids: list[str]) -> list[str]:
    if not trade_ids:
        return []
    try:
        with open(TRADE_LOG) as f:
            trades = json.load(f)
    except Exception:
        return []
    timestamps = [
        t.get("entry_time", "")[:10]
        for t in trades
        if t.get("trade_id") in trade_ids and t.get("entry_time")
    ]
    if not timestamps:
        return []
    return [min(timestamps), max(timestamps)]


def cmd_add_review(args, entries: list[dict]) -> int:
    trade_ids = args.trades.split(",") if args.trades else []
    trade_range = resolve_trade_range(trade_ids)
    entry = {
        "id": next_id(entries),
        "type": "review",
        "created_at": now_iso(),
        "updated_at": None,
        "trade_ids": trade_ids,
        "trade_range": trade_range,
        "observations": args.notes or "",
        "sessions_reviewed": [],
        "symbols_reviewed": [],
    }
    if trade_ids:
        try:
            with open(TRADE_LOG) as f:
                trades = json.load(f)
            matched = [t for t in trades if t.get("trade_id") in trade_ids]
            entry["sessions_reviewed"] = list(set(
                t.get("session", "UNKNOWN") for t in matched if t.get("session")
            ))
            entry["symbols_reviewed"] = list(set(
                t.get("symbol", "UNKNOWN") for t in matched if t.get("symbol")
            ))
        except Exception:
            pass
    entries.append(entry)
    save_log(entries)
    print(f"Review #{entry['id']} logged at {entry['created_at']}")
    return entry["id"]


def cmd_add_improvement(args, entries: list[dict]) -> int:
    eid = next_id(entries)
    entry = {
        "id": eid,
        "type": "improvement",
        "created_at": now_iso(),
        "updated_at": None,
        "review_id": args.review_id,
        "summary": args.summary or "",
        "scope": args.scope or "",
        "expected_impact": args.expected or "",
        "status": "pending",
    }
    entries.append(entry)
    save_log(entries)
    print(f"Improvement #{eid} logged at {entry['created_at']} (linked to review #{args.review_id})")
    return eid


def cmd_set_outcome(args, entries: list[dict]):
    for e in entries:
        if e.get("id") == args.improvement_id and e.get("type") == "improvement":
            outcome_entry = {
                "id": next_id(entries),
                "type": "outcome",
                "created_at": now_iso(),
                "improvement_id": args.improvement_id,
                "measured_impact": args.impact or "",
                "pnl_pre": args.pnl_pre,
                "pnl_post": args.pnl_post,
                "decision": args.decision or "pending",
            }
            entries.append(outcome_entry)
            e["status"] = args.decision or "pending"
            e["updated_at"] = now_iso()
            save_log(entries)
            print(f"Outcome for improvement #{args.improvement_id} recorded: {args.decision}")
            return
    print(f"Improvement #{args.improvement_id} not found.")


def cmd_history(args, entries: list[dict]):
    filtered = entries
    if args.improvement_id is not None:
        filtered = [e for e in entries if e.get("improvement_id") == args.improvement_id or e.get("id") == args.improvement_id]
    if args.type:
        filtered = [e for e in filtered if e.get("type") == args.type]
    if args.limit:
        filtered = filtered[-args.limit:]

    if not filtered:
        print("No entries found.")
        return

    print(f"\n{'ID':<4} {'Type':<14} {'Created':<22} {'Summary'}")
    print("-" * 80)
    for e in filtered:
        eid = e.get("id", "?")
        etype = e.get("type", "?")
        ts = e.get("created_at", "?")[:19]
        summary = (
            e.get("observations", e.get("summary", e.get("measured_impact", "")))[:50]
        )
        print(f"{eid:<4} {etype:<14} {ts:<22} {summary}")


def main():
    parser = argparse.ArgumentParser(
        description="System Improvement Ledger — track reviews, improvements, and outcomes"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_review = sub.add_parser("add-review", help="Log a trade review session")
    p_review.add_argument("--trades", help="Comma-separated trade IDs")
    p_review.add_argument("--notes", help="Observations from the review")

    p_impr = sub.add_parser("add-improvement", help="Log an improvement decision")
    p_impr.add_argument("--review-id", type=int, required=True, help="Linked review ID")
    p_impr.add_argument("--summary", required=True, help="What was changed")
    p_impr.add_argument("--scope", help="Scope of the change (e.g. ingwe.py entry logic)")
    p_impr.add_argument("--expected", help="Expected impact")

    p_out = sub.add_parser("set-outcome", help="Record the measured outcome of an improvement")
    p_out.add_argument("--improvement-id", type=int, required=True)
    p_out.add_argument("--impact", help="Measured impact description")
    p_out.add_argument("--pnl-pre", type=float, help="P&L before improvement")
    p_out.add_argument("--pnl-post", type=float, help="P&L after improvement")
    p_out.add_argument("--decision", choices=["active", "reverted", "superseded", "pending"], default="pending")

    p_hist = sub.add_parser("history", help="View the ledger")
    p_hist.add_argument("--improvement-id", type=int, help="Filter by improvement ID")
    p_hist.add_argument("--type", choices=["review", "improvement", "outcome"])
    p_hist.add_argument("--limit", type=int, default=20, help="Max entries to show")

    args = parser.parse_args()
    entries = load_log()

    if args.command == "add-review":
        cmd_add_review(args, entries)
    elif args.command == "add-improvement":
        cmd_add_improvement(args, entries)
    elif args.command == "set-outcome":
        cmd_set_outcome(args, entries)
    elif args.command == "history":
        cmd_history(args, entries)


if __name__ == "__main__":
    main()
