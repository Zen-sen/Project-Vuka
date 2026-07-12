#!/usr/bin/env python3
"""
compile_journal.py — Consolidated Trade Journal + Improvement Ledger
Project Vuka | Produces a single Markdown report with summary tables,
ICT scorecard, improvement timeline, and all enriched trade entries.
"""
import json
import math
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
TRADE_LOG = BASE_DIR / "data" / "trade_log.json"
IMPROVEMENT_LOG = BASE_DIR / "data" / "improvement_log.json"
JOURNAL_DIR = BASE_DIR / "data" / "journal"
OUTPUT = JOURNAL_DIR / "compiled.md"

PIP_VALUES = {"EURUSD": 0.0001, "GBPUSD": 0.0001}


def load_json(path):
    if not path.exists():
        return []
    with open(path) as f:
        try:
            data = json.load(f)
            return data if isinstance(data, list) else []
        except json.JSONDecodeError:
            return []


def pip_value(symbol):
    return PIP_VALUES.get(symbol, 0.0001)


def outcome_emoji(out):
    return {"WIN": "🟢", "LOSS": "🔴", "BREAKEVEN": "⚪"}.get(out, "⚪")


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


def build_trade_entries(trades):
    lines = []
    for t in trades:
        emoji = outcome_emoji(t.get("outcome", ""))
        tid = t.get("trade_id", "UNKNOWN")
        sym = t.get("symbol", "UNKNOWN")
        strat = t.get("strategy", "N/A")
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

        lines.append(f"""### {emoji} {tid}
**{sym} | {strat}** | {entry_time} → {exit_time} | {session_tag} | {direction} {volume}L

| Metric | Value | Execution | Value |
|:---|---:|:---|---:|
| **P&L** | ${pnl:+.2f} | **Spread** | {spread} pips |
| **Commission** | ${commission:.2f} | **Slippage** | {slippage} pips |
| **Swap** | ${swap:.2f} | **Exit** | {exit_reason} |

| Validation | Value | System | Value |
|:---|---:|:---|---:|
| **HTF** | {htf_bias} | **Kronos** | {kronos_decision} ({kronos_conf}%) |
| **FVG** | {fvg} | **OB** | {ob} |
| **Retrace** | {retracement}% | **Circuit** | {cb} / {latency}ms |

---
""")
    return "\n".join(lines)


def fmt_pnl(val):
    return f"${val:+.2f}" if val >= 0 else f"${val:.2f}"


def main():
    trades = load_json(TRADE_LOG)
    improvements = load_json(IMPROVEMENT_LOG)

    if not trades:
        print("No trades to compile.")
        return

    total = len(trades)
    wins = [t for t in trades if t.get("outcome") == "WIN"]
    losses = [t for t in trades if t.get("outcome") == "LOSS"]
    bes = [t for t in trades if t.get("outcome") == "BREAKEVEN"]
    n_wins = len(wins)
    n_losses = len(losses)
    n_be = len(bes)
    pnl = sum(t.get("pnl_usd", 0) for t in trades)
    win_rate = n_wins / total * 100 if total else 0
    avg_win = sum(t.get("pnl_usd", 0) for t in wins) / n_wins if n_wins else 0
    avg_loss = sum(t.get("pnl_usd", 0) for t in losses) / n_losses if n_losses else 0
    avg_rr = avg_win / abs(avg_loss) if avg_loss else 0

    dates = [t.get("entry_time", "")[:10] for t in trades if t.get("entry_time")]
    date_range = f"{dates[0]} to {dates[-1]}" if dates else "N/A"

    # Sessions
    session_data = defaultdict(lambda: {"trades": 0, "wins": 0, "pnl": 0.0})
    for t in trades:
        s = t.get("session", compute_session(t.get("entry_time", "")))
        session_data[s]["trades"] += 1
        if t.get("outcome") == "WIN":
            session_data[s]["wins"] += 1
        session_data[s]["pnl"] += t.get("pnl_usd", 0)

    # Symbols
    sym_data = defaultdict(lambda: {"trades": 0, "wins": 0, "pnl": 0.0})
    for t in trades:
        s = t.get("symbol", "UNKNOWN")
        sym_data[s]["trades"] += 1
        if t.get("outcome") == "WIN":
            sym_data[s]["wins"] += 1
        sym_data[s]["pnl"] += t.get("pnl_usd", 0)

    # Weeks
    week_data = defaultdict(lambda: {"trades": 0, "wins": 0, "pnl": 0.0})
    for t in trades:
        try:
            dt = datetime.strptime(t.get("entry_time", ""), "%Y-%m-%d %H:%M:%S")
            wk = dt.strftime("%Y-W%V")
        except (ValueError, TypeError):
            wk = "UNKNOWN"
        week_data[wk]["trades"] += 1
        if t.get("outcome") == "WIN":
            week_data[wk]["wins"] += 1
        week_data[wk]["pnl"] += t.get("pnl_usd", 0)

    # ICT scorecard
    n_ob = sum(1 for t in trades if t.get("ob_present") is True)
    n_fvg = sum(1 for t in trades if t.get("fvg_confirmed") is True)
    n_htf = sum(1 for t in trades if t.get("htf_bias") == "ALIGNED")
    retracements = [t.get("retracement_depth") for t in trades if isinstance(t.get("retracement_depth"), (int, float))]
    avg_retrace = sum(retracements) / len(retracements) if retracements else 0
    spreads = [float(t.get("spread_at_entry", 0)) for t in trades if t.get("spread_at_entry") not in ("N/A", None, "")]
    avg_spread = sum(spreads) / len(spreads) if spreads else 0

    # Build content
    lines = []
    lines.append(f"# Project Vuka — Compiled Trade Journal")
    lines.append(f"**Period:** {date_range} | **Trades:** {total} | **Strategy:** INGWE")
    lines.append("")

    # === Executive Summary ===
    lines.append("## Executive Summary")
    lines.append("")
    lines.append(f"| Metric | Value |")
    lines.append(f"|---|---:|")
    lines.append(f"| **Net P&L** | {fmt_pnl(pnl)} |")
    lines.append(f"| **Wins / Losses / BE** | {n_wins} / {n_losses} / {n_be} |")
    lines.append(f"| **Win Rate** | {win_rate:.1f}% |")
    lines.append(f"| **Avg Win** | {fmt_pnl(avg_win)} |")
    lines.append(f"| **Avg Loss** | {fmt_pnl(avg_loss)} |")
    lines.append(f"| **Avg R:R** | {avg_rr:.2f} |")
    gross_profit = sum(t.get('pnl_usd', 0) for t in wins)
    gross_loss = abs(sum(t.get('pnl_usd', 0) for t in losses))
    pf = f"{gross_profit / gross_loss:.2f}" if gross_loss else "N/A"
    lines.append(f"| **Profit Factor** | {pf} |")
    lines.append("")

    # === Performance Tables ===
    lines.append("## Performance by Session")
    lines.append("")
    lines.append("| Session | Trades | Wins | Win Rate | P&L |")
    lines.append("|---|---|---|---:|---:|")
    for s in ["ASIAN", "LONDON_OPEN", "LONDON_CLOSE", "NY_OPEN"]:
        d = session_data.get(s)
        if d:
            wr = d["wins"] / d["trades"] * 100 if d["trades"] else 0
            lines.append(f"| {s} | {d['trades']} | {d['wins']} | {wr:.0f}% | {fmt_pnl(d['pnl'])} |")
    lines.append("")

    lines.append("## Performance by Symbol")
    lines.append("")
    lines.append("| Symbol | Trades | Wins | Win Rate | P&L |")
    lines.append("|---|---|---|---:|---:|")
    for s in sorted(sym_data.keys()):
        d = sym_data[s]
        wr = d["wins"] / d["trades"] * 100 if d["trades"] else 0
        lines.append(f"| {s} | {d['trades']} | {d['wins']} | {wr:.0f}% | {fmt_pnl(d['pnl'])} |")
    lines.append("")

    lines.append("## Performance by Week")
    lines.append("")
    lines.append("| Week | Trades | Wins | Win Rate | P&L |")
    lines.append("|---|---|---|---:|---:|")
    for wk in sorted(week_data.keys()):
        d = week_data[wk]
        wr = d["wins"] / d["trades"] * 100 if d["trades"] else 0
        lines.append(f"| {wk} | {d['trades']} | {d['wins']} | {wr:.0f}% | {fmt_pnl(d['pnl'])} |")
    lines.append("")

    # === ICT Scorecard ===
    lines.append("## ICT & Execution Scorecard")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("|---|---:|")
    lines.append(f"| **Order Blocks** | {n_ob}/{total} ({n_ob/total*100:.1f}%) |")
    lines.append(f"| **FVG Confirmed** | {n_fvg}/{total} ({n_fvg/total*100:.1f}%) |")
    lines.append(f"| **HTF Aligned** | {n_htf}/{total} ({n_htf/total*100:.1f}%) |")
    lines.append(f"| **Avg Retracement** | {avg_retrace:.2f}% |")
    lines.append(f"| **Avg Spread** | {avg_spread:.1f} pips |")
    htf_win_rate = sum(1 for t in trades if t.get("htf_bias") == "ALIGNED" and t.get("outcome") == "WIN")
    htf_total = sum(1 for t in trades if t.get("htf_bias") == "ALIGNED")
    htf_label = f"{htf_win_rate}/{htf_total} ({htf_win_rate/htf_total*100:.0f}%)" if htf_total else "N/A"
    lines.append(f"| **HTF Aligned Win Rate** | {htf_label} |")
    ob_win_rate = sum(1 for t in trades if t.get("ob_present") is True and t.get("outcome") == "WIN")
    ob_label = f"{ob_win_rate}/{n_ob} ({ob_win_rate/n_ob*100:.0f}%)" if n_ob else "N/A"
    lines.append(f"| **OB Present Win Rate** | {ob_label} |")
    lines.append("")

    # === Improvement Timeline ===
    lines.append("## Improvement Log")
    lines.append("")
    if improvements:
        lines.append("| Date | Type | ID | Summary |")
        lines.append("|---|---|---|---|")
        for e in improvements:
            ts = e.get("created_at", "")[:10]
            etype = e.get("type", "?").title()
            eid = e.get("id", "?")
            summary = e.get("observations", e.get("summary", e.get("measured_impact", "")))[:60]
            lines.append(f"| {ts} | {etype} | #{eid} | {summary} |")
    else:
        lines.append("_No reviews or improvements logged yet. Use `python skills/log_review.py add-review` to begin._")
    lines.append("")

    # === Trade Ledger ===
    lines.append("---")
    lines.append("")
    lines.append("## Individual Trade Ledger")
    lines.append("")
    lines.append(build_trade_entries(trades))

    report = "\n".join(lines)

    JOURNAL_DIR.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"Compiled journal written to {OUTPUT}")
    print(f"  Trades: {total} | P&L: {fmt_pnl(pnl)} | Win rate: {win_rate:.1f}% | OB: {n_ob} | HTF aligned: {n_htf}")


if __name__ == "__main__":
    main()
