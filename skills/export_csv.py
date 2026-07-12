#!/usr/bin/env python3
"""
export_csv.py — Export enriched trade data to CSVs for Excel/spreadsheets
Project Vuka | Generates trade_export.csv, improvement_export.csv, summary_tables.csv
"""
import csv
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
TRADE_LOG = BASE_DIR / "data" / "trade_log.json"
IMPROVEMENT_LOG = BASE_DIR / "data" / "improvement_log.json"
OUT_DIR = BASE_DIR / "data" / "journal"


def load_json(path):
    if not path.exists():
        return []
    with open(path) as f:
        try:
            data = json.load(f)
            return data if isinstance(data, list) else []
        except json.JSONDecodeError:
            return []


def compute_session(entry_time_str: str) -> str:
    try:
        dt = datetime.strptime(entry_time_str, "%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return "UNKNOWN"
    h = dt.hour
    m = dt.minute
    if h < 7 or (h == 7 and m == 0):
        return "ASIAN"
    elif h < 12 or (h == 12 and m == 0):
        return "LONDON_OPEN"
    elif h < 17 or (h == 17 and m == 0):
        return "LONDON_CLOSE"
    else:
        return "NY_OPEN"


TRADE_FIELDS = [
    "trade_id", "entry_time", "exit_time", "symbol", "strategy",
    "direction", "volume", "entry_price", "pnl_usd", "outcome",
    "commission", "swap", "exit_reason", "session", "market_mode",
    "htf_bias", "kronos_decision", "kronos_confidence",
    "circuit_breaker", "api_latency_ms", "spread_at_entry",
    "slippage", "fvg_confirmed", "ob_present", "retracement_depth",
    "confluence_score", "setup_type", "source", "week", "day_of_week",
]

IMPROVEMENT_FIELDS = [
    "id", "type", "created_at", "updated_at", "review_id",
    "improvement_id", "summary", "scope", "expected_impact",
    "status", "decision", "trade_ids", "sessions_reviewed",
    "symbols_reviewed", "pnl_pre", "pnl_post", "measured_impact",
]

SUMMARY_FIELDS = [
    "table", "group_key", "trades", "wins", "losses", "be",
    "win_rate", "pnl",
]


def flatten_trade(t):
    try:
        dt = datetime.strptime(t.get("entry_time", ""), "%Y-%m-%d %H:%M:%S")
        week = dt.strftime("%Y-W%V")
        day_of_week = dt.strftime("%A")
    except (ValueError, TypeError):
        week = ""
        day_of_week = ""

    return {
        "trade_id": t.get("trade_id", ""),
        "entry_time": t.get("entry_time", ""),
        "exit_time": t.get("exit_time", ""),
        "symbol": t.get("symbol", ""),
        "strategy": t.get("strategy", ""),
        "direction": t.get("direction", ""),
        "volume": t.get("volume", ""),
        "entry_price": t.get("entry", ""),
        "pnl_usd": t.get("pnl_usd", 0),
        "outcome": t.get("outcome", ""),
        "commission": t.get("commission", 0),
        "swap": t.get("swap", 0),
        "exit_reason": t.get("exit_reason", ""),
        "session": t.get("session", compute_session(t.get("entry_time", ""))),
        "market_mode": t.get("market_mode", ""),
        "htf_bias": t.get("htf_bias", ""),
        "kronos_decision": t.get("kronos_decision", ""),
        "kronos_confidence": t.get("kronos_confidence", ""),
        "circuit_breaker": t.get("circuit_breaker", ""),
        "api_latency_ms": t.get("api_latency_ms", ""),
        "spread_at_entry": t.get("spread_at_entry", ""),
        "slippage": t.get("slippage", ""),
        "fvg_confirmed": t.get("fvg_confirmed", ""),
        "ob_present": t.get("ob_present", ""),
        "retracement_depth": t.get("retracement_depth", ""),
        "confluence_score": t.get("confluence_score", ""),
        "setup_type": t.get("setup_type", ""),
        "source": t.get("source", ""),
        "week": week,
        "day_of_week": day_of_week,
    }


def flatten_improvement(e):
    return {
        "id": e.get("id", ""),
        "type": e.get("type", ""),
        "created_at": e.get("created_at", ""),
        "updated_at": e.get("updated_at", ""),
        "review_id": e.get("review_id", ""),
        "improvement_id": e.get("improvement_id", ""),
        "summary": e.get("observations", e.get("summary", e.get("measured_impact", ""))),
        "scope": e.get("scope", ""),
        "expected_impact": e.get("expected_impact", ""),
        "status": e.get("status", ""),
        "decision": e.get("decision", ""),
        "trade_ids": ", ".join(e.get("trade_ids", [])),
        "sessions_reviewed": ", ".join(e.get("sessions_reviewed", [])),
        "symbols_reviewed": ", ".join(e.get("symbols_reviewed", [])),
        "pnl_pre": e.get("pnl_pre", ""),
        "pnl_post": e.get("pnl_post", ""),
        "measured_impact": e.get("measured_impact", ""),
    }


def build_summaries(trades):
    rows = []

    groups = {
        "by_session": defaultdict(lambda: {"trades": [], "pnl": 0.0}),
        "by_symbol": defaultdict(lambda: {"trades": [], "pnl": 0.0}),
        "by_week": defaultdict(lambda: {"trades": [], "pnl": 0.0}),
        "by_outcome": defaultdict(lambda: {"trades": [], "pnl": 0.0}),
        "by_session_symbol": defaultdict(lambda: {"trades": [], "pnl": 0.0}),
    }

    for t in trades:
        session = t.get("session", compute_session(t.get("entry_time", "")))
        symbol = t.get("symbol", "UNKNOWN")
        outcome = t.get("outcome", "UNKNOWN")
        pnl = t.get("pnl_usd", 0)

        try:
            dt = datetime.strptime(t.get("entry_time", ""), "%Y-%m-%d %H:%M:%S")
            week = dt.strftime("%Y-W%V")
        except (ValueError, TypeError):
            week = "UNKNOWN"

        groups["by_session"][session]["trades"].append(outcome)
        groups["by_session"][session]["pnl"] += pnl
        groups["by_symbol"][symbol]["trades"].append(outcome)
        groups["by_symbol"][symbol]["pnl"] += pnl
        groups["by_week"][week]["trades"].append(outcome)
        groups["by_week"][week]["pnl"] += pnl
        groups["by_outcome"][outcome]["trades"].append(outcome)
        groups["by_outcome"][outcome]["pnl"] += pnl
        groups["by_session_symbol"][f"{session}|{symbol}"]["trades"].append(outcome)
        groups["by_session_symbol"][f"{session}|{symbol}"]["pnl"] += pnl

    for table_name, data in groups.items():
        for key, d in sorted(data.items()):
            outcomes = d["trades"]
            wins = sum(1 for o in outcomes if o == "WIN")
            losses = sum(1 for o in outcomes if o == "LOSS")
            bes = sum(1 for o in outcomes if o == "BREAKEVEN")
            total = len(outcomes)
            wr = wins / total * 100 if total else 0
            rows.append({
                "table": table_name,
                "group_key": key,
                "trades": total,
                "wins": wins,
                "losses": losses,
                "be": bes,
                "win_rate": round(wr, 1),
                "pnl": round(d["pnl"], 2),
            })

    return rows


def write_csv(path, fieldnames, rows):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"  {len(rows)} rows -> {path}")


def main():
    trades = load_json(TRADE_LOG)
    improvements = load_json(IMPROVEMENT_LOG)

    print("Exporting CSVs to data/journal/ ...")

    trade_rows = [flatten_trade(t) for t in trades]
    write_csv(OUT_DIR / "trade_export.csv", TRADE_FIELDS, trade_rows)

    impr_rows = [flatten_improvement(e) for e in improvements]
    write_csv(OUT_DIR / "improvement_export.csv", IMPROVEMENT_FIELDS, impr_rows)

    summary_rows = build_summaries(trades)
    write_csv(OUT_DIR / "summary_tables.csv", SUMMARY_FIELDS, summary_rows)

    total_pnl = sum(t.get("pnl_usd", 0) for t in trades)
    wins = sum(1 for t in trades if t.get("outcome") == "WIN")
    print(f"\nDone. {len(trades)} trades, ${total_pnl:+.2f}, {wins} wins.")


if __name__ == "__main__":
    main()
