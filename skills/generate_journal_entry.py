#!/usr/bin/env python3
"""
generate_journal_entry.py — Automated trade journal entry generator
Project Vuka | Structures algorithmic trade logs into human-readable Markdown
Supports single trade targeting or weekly batch generation.
"""
import json
import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
TRADE_LOG = BASE_DIR / "data" / "trade_log.json"
JOURNAL_DIR = BASE_DIR / "data" / "journal"


def load_trades():
    if not TRADE_LOG.exists():
        print(f"trade_log.json not found at {TRADE_LOG}")
        return []
    with open(TRADE_LOG) as f:
        try:
            trades = json.load(f)
            return trades if isinstance(trades, list) else trades.get("trades", [])
        except json.JSONDecodeError:
            print("Error reading trade_log.json. Invalid JSON format.")
            return []


def compute_session(entry_time_str):
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


def outcome_emoji(outcome):
    return {
        "WIN": "🟢",
        "LOSS": "🔴",
        "BREAKEVEN": "⚪",
    }.get(outcome, "⚪")


def build_markdown(t):
    emoji = outcome_emoji(t.get("outcome", ""))
    symbol = t.get("symbol", "UNKNOWN")
    trade_id = t.get("trade_id", "UNKNOWN")
    strategy = t.get("strategy", "N/A")
    entry_time = t.get("entry_time", "N/A")
    exit_time = t.get("exit_time", "N/A")
    direction = t.get("direction", "N/A")
    volume = t.get("volume", "N/A")
    pnl = t.get("pnl_usd", 0)
    commission = t.get("commission", 0.0)
    swap = t.get("swap", 0.0)
    exit_reason = t.get("exit_reason", "N/A")
    spread = t.get("spread_at_entry", "N/A")
    slippage = t.get("slippage", "N/A")
    htf_bias = t.get("htf_bias", "SPLIT")
    kronos_decision = t.get("kronos_decision", "ALLOW")
    kronos_conf = t.get("kronos_confidence", "0.00")
    fvg = t.get("fvg_confirmed", False)
    ob = t.get("ob_present", False)
    retracement = t.get("retracement_depth", "N/A")
    cb = t.get("circuit_breaker", "CLOSED")
    latency = t.get("api_latency_ms", "N/A")
    session_tag = t.get("session", compute_session(entry_time))

    template = f"""### {emoji} TRADE ANALYSIS: {trade_id}
---
* **Strategy/Asset:** {symbol} | {strategy}
* **Timestamp:** {entry_time} (Exit: {exit_time})
* **Session:** {session_tag}
* **Direction:** {direction} | **Volume:** {volume} Lots

| Financial Metric | Value | Execution Architecture | Value |
| :--- | :--- | :--- | :--- |
| **Gross P&L** | ${pnl:+.2f} | **Spread at Entry** | {spread} pips |
| **Commission** | ${commission:.2f} | **Slippage** | {slippage} pips |
| **Swap** | ${swap:.2f} | **Exit Reason** | {exit_reason} |

#### Quantitative Validation & Confluence
* **HTF Alignment:** {htf_bias}
* **Kronos Brain Veto:** {kronos_decision} | **Model Confidence:** {kronos_conf}%
* **ICT Factors:** FVG: {fvg} | OB: {ob} | Retracement: {retracement}%
* **System State:** Circuit Breaker: {cb} | Latency: {latency}ms

#### Architectural & Behavioral Post-Mortem
> **1. The Intervention Check:** Did I manually tamper with the rules? What was the physiological/anxiety trigger?
>
> **2. Edge Degradation vs. Market Noise:** Was this standard cost-of-business loss distribution, or structural friction requiring an engineering adjustment?
>
> **3. Alignment Calibration:** Did the code, Kronos validation, and MT5 terminal metrics behave identically?
>

---
"""
    return template


def save_entry(trade):
    md_output = build_markdown(trade)
    JOURNAL_DIR.mkdir(parents=True, exist_ok=True)
    date_str = trade.get("entry_time", "unknown")[:10]
    trade_id = trade.get("trade_id", "unknown")
    filename = JOURNAL_DIR / f"{date_str}_{trade_id}.md"
    with open(filename, "w", encoding="utf-8") as f:
        f.write(md_output)
    return filename


def main():
    parser = argparse.ArgumentParser(
        description="Generate structured Markdown journal entries from trade_log.json"
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--trade_id", help="Single trade ID to parse")
    group.add_argument("--week", action="store_true", help="Batch generate all trades from the past 7 days")
    group.add_argument("--all", action="store_true", help="Generate journal entries for every trade in the log")
    args = parser.parse_args()

    trades = load_trades()
    if not trades:
        print("No trades available to parse.")
        return

    if args.trade_id:
        target = next((t for t in trades if t.get("trade_id") == args.trade_id), None)
        if not target:
            print(f"Trade {args.trade_id} not found.")
            return
        path = save_entry(target)
        print(f"Core telemetry extracted to {path}")

    elif args.week:
        cutoff = datetime.now(timezone.utc) - timedelta(days=7)
        def in_window(t):
            try:
                dt = datetime.strptime(t.get("entry_time", ""), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                return dt >= cutoff
            except (ValueError, TypeError):
                return False
        weekly = [t for t in trades if in_window(t)]
        if not weekly:
            print("No trades found within the last 7 days.")
            return
        print(f"Batch processing {len(weekly)} trades into your journal directory...")
        for t in weekly:
            save_entry(t)
        print(f"Generated {len(weekly)} journal templates in {JOURNAL_DIR}/")

    elif args.all:
        print(f"Batch processing all {len(trades)} trades into your journal directory...")
        for t in trades:
            save_entry(t)
        print(f"Generated {len(trades)} journal templates in {JOURNAL_DIR}/")


if __name__ == "__main__":
    main()
