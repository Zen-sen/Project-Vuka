#!/usr/bin/env python3
"""
trade_log_analyzer.py — Agent Ingwe Trade Log Analyzer
Project Vuka | Parses trade_log.json for performance insights
"""

import argparse
import json
import csv
from collections import defaultdict
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
TRADE_LOG = BASE_DIR / "data" / "trade_log.json"
REPORTS_DIR = BASE_DIR / "data" / "reports"


def load_trades(from_date=None, to_date=None, session=None, strategy=None, losses_only=False):
    if not TRADE_LOG.exists():
        print(f"❌ trade_log.json not found at {TRADE_LOG}")
        return []

    with open(TRADE_LOG) as f:
        trades = json.load(f)

    if isinstance(trades, dict):
        trades = trades.get("trades", [])

    if from_date:
        trades = [t for t in trades if t.get("entry_time", "") >= from_date]
    if to_date:
        trades = [t for t in trades if t.get("entry_time", "") <= to_date + "T23:59:59"]
    if session:
        trades = [t for t in trades if t.get("session") == session]
    if strategy:
        trades = [t for t in trades if t.get("strategy") == strategy]
    if losses_only:
        trades = [t for t in trades if t.get("outcome") == "LOSS"]

    return trades


def calc_stats(trades: list) -> dict:
    if not trades:
        return {}

    wins = [t for t in trades if t.get("outcome") == "WIN"]
    losses = [t for t in trades if t.get("outcome") == "LOSS"]
    total = len(trades)

    gross_profit = sum(t.get("pnl_usd", 0) for t in wins)
    gross_loss = abs(sum(t.get("pnl_usd", 0) for t in losses))
    net_pnl = sum(t.get("pnl_usd", 0) for t in trades)
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else float("inf")
    avg_rr = sum(t.get("rr_achieved", 0) for t in trades) / total if total else 0

    return {
        "total": total,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / total * 100, 1) if total else 0,
        "net_pnl": round(net_pnl, 2),
        "gross_profit": round(gross_profit, 2),
        "gross_loss": round(gross_loss, 2),
        "profit_factor": round(profit_factor, 2),
        "avg_rr": round(avg_rr, 2),
    }


def sep():
    print("─" * 52)


def print_summary(trades, label="All Trades"):
    s = calc_stats(trades)
    if not s:
        print("No trades found for the given filters.")
        return
    sep()
    print(f"  📊 TRADE ANALYSIS — {label}")
    sep()
    print(f"  Total Trades   : {s['total']}")
    print(f"  Won / Lost     : {s['wins']}W / {s['losses']}L")
    print(f"  Win Rate       : {s['win_rate']}%")
    print(f"  Net P&L        : ${s['net_pnl']:+.2f}")
    print(f"  Profit Factor  : {s['profit_factor']}")
    print(f"  Avg RR Achieved: {s['avg_rr']}")
    sep()


def print_by_group(trades, key, label):
    groups = defaultdict(list)
    for t in trades:
        groups[t.get(key, "unknown")].append(t)

    sep()
    print(f"  📊 BREAKDOWN BY {label.upper()}")
    sep()
    for group, group_trades in sorted(groups.items()):
        s = calc_stats(group_trades)
        print(f"  {group:<18} | {s['total']:>3} trades | "
              f"WR {s['win_rate']:>5.1f}% | "
              f"PF {s['profit_factor']:>4.2f} | "
              f"P&L ${s['net_pnl']:>+8.2f}")
    sep()


def print_by_condition(trades):
    conditions = ["fvg_confirmed", "ob_present"]
    sep()
    print("  📊 ICT CONDITION CORRELATION")
    sep()
    for cond in conditions:
        yes = [t for t in trades if t.get(cond) is True]
        no = [t for t in trades if t.get(cond) is False]
        s_yes = calc_stats(yes)
        s_no = calc_stats(no)
        print(f"  {cond}:")
        if s_yes:
            print(f"    TRUE  → {s_yes['total']} trades | WR {s_yes['win_rate']}% | PF {s_yes['profit_factor']}")
        if s_no:
            print(f"    FALSE → {s_no['total']} trades | WR {s_no['win_rate']}% | PF {s_no['profit_factor']}")
    sep()


def print_losses(trades):
    sep()
    print("  📋 LOSS REVIEW")
    sep()
    for t in trades:
        print(f"  [{t.get('entry_time','?')[:10]}] {t.get('symbol','?')} "
              f"{t.get('direction','?')} | Session: {t.get('session','?')} | "
              f"P&L: ${t.get('pnl_usd', 0):+.2f} | "
              f"ADX: {t.get('adx_at_entry','?')} | "
              f"FVG: {t.get('fvg_confirmed','?')} OB: {t.get('ob_present','?')}")
    sep()


def export_csv(trades, filename):
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORTS_DIR / filename
    if not trades:
        print("No trades to export.")
        return
    keys = trades[0].keys()
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=keys)
        writer.writeheader()
        writer.writerows(trades)
    print(f"✅ Exported {len(trades)} trades to {path}")


def main():
    parser = argparse.ArgumentParser(
        description="🐆 Ingwe Trade Log Analyzer",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python skills/trade_log_analyzer.py --summary
  python skills/trade_log_analyzer.py --from 2026-03-01 --to 2026-03-29
  python skills/trade_log_analyzer.py --session london
  python skills/trade_log_analyzer.py --strategy INGWE --by-condition
  python skills/trade_log_analyzer.py --losses-only
  python skills/trade_log_analyzer.py --summary --export csv
        """
    )

    parser.add_argument("--summary", action="store_true", default=True, help="Print summary stats")
    parser.add_argument("--from", dest="from_date", help="Start date YYYY-MM-DD")
    parser.add_argument("--to", dest="to_date", help="End date YYYY-MM-DD")
    parser.add_argument("--session", choices=["asian", "london", "ny"], help="Filter by session")
    parser.add_argument("--strategy", choices=["INGWE", "SILVER_BULLET"], help="Filter by strategy")
    parser.add_argument("--by-session", action="store_true", help="Breakdown by session")
    parser.add_argument("--by-strategy", action="store_true", help="Breakdown by strategy")
    parser.add_argument("--by-condition", action="store_true", help="ICT condition correlation")
    parser.add_argument("--losses-only", action="store_true", help="Show only losing trades")
    parser.add_argument("--export", choices=["csv"], help="Export results")

    args = parser.parse_args()

    trades = load_trades(
        from_date=args.from_date,
        to_date=args.to_date,
        session=args.session,
        strategy=args.strategy,
        losses_only=args.losses_only
    )

    label = " | ".join(filter(None, [
        args.session, args.strategy,
        f"{args.from_date}→{args.to_date}" if args.from_date else None
    ])) or "All Trades"

    if args.losses_only:
        print_losses(trades)
    elif args.by_condition:
        print_by_condition(trades)
    elif args.by_session:
        print_by_group(trades, "session", "Session")
    elif args.by_strategy:
        print_by_group(trades, "strategy", "Strategy")
    else:
        print_summary(trades, label)

    if args.export == "csv":
        fname = f"analysis_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        export_csv(trades, fname)


if __name__ == "__main__":
    main()
