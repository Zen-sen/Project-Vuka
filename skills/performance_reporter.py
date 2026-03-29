#!/usr/bin/env python3
"""
performance_reporter.py — Agent Ingwe Performance Reporter
Project Vuka | Generates structured P&L reports
"""

import argparse
import json
import csv
from datetime import datetime, timezone, timedelta
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
TRADE_LOG = BASE_DIR / "data" / "trade_log.json"
REPORTS_DIR = BASE_DIR / "data" / "reports"

BASELINE_WIN_RATE = 73.0
BASELINE_PF = 2.4
TARGET_MONTHLY_RETURN = 15.0
MAX_DD_TARGET = 10.0


def load_trades() -> list:
    if not TRADE_LOG.exists():
        print(f"❌ trade_log.json not found at {TRADE_LOG}")
        return []
    with open(TRADE_LOG) as f:
        data = json.load(f)
    return data if isinstance(data, list) else data.get("trades", [])


def filter_by_period(trades, start: str, end: str) -> list:
    return [t for t in trades
            if start <= t.get("entry_time", "")[:10] <= end]


def calc_stats(trades: list) -> dict:
    if not trades:
        return {"total": 0}
    wins = [t for t in trades if t.get("outcome") == "WIN"]
    losses = [t for t in trades if t.get("outcome") == "LOSS"]
    total = len(trades)
    gross_profit = sum(t.get("pnl_usd", 0) for t in wins)
    gross_loss = abs(sum(t.get("pnl_usd", 0) for t in losses))
    net_pnl = sum(t.get("pnl_usd", 0) for t in trades)
    pf = round(gross_profit / gross_loss, 2) if gross_loss > 0 else float("inf")
    avg_rr = round(sum(t.get("rr_achieved", 0) for t in trades) / total, 2)
    win_rate = round(len(wins) / total * 100, 1)

    return {
        "total": total, "wins": len(wins), "losses": len(losses),
        "win_rate": win_rate, "net_pnl": round(net_pnl, 2),
        "gross_profit": round(gross_profit, 2), "gross_loss": round(gross_loss, 2),
        "profit_factor": pf, "avg_rr": avg_rr
    }


def flag(value, target, higher_is_better=True):
    ok = value >= target if higher_is_better else value <= target
    return "✅" if ok else "❌"


def sep():
    print("─" * 52)


def cmd_daily(date_str: str = None):
    date = date_str or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    trades = filter_by_period(load_trades(), date, date)
    s = calc_stats(trades)

    sep()
    print(f"  📅 DAILY REPORT — {date}")
    sep()
    if s["total"] == 0:
        print("  No trades found for this date.")
        sep()
        return

    print(f"  Trades         : {s['total']}  ({s['wins']}W / {s['losses']}L)")
    print(f"  Win Rate       : {s['win_rate']}%  {flag(s['win_rate'], 65)}")
    print(f"  Net P&L        : ${s['net_pnl']:+.2f}")
    print(f"  Profit Factor  : {s['profit_factor']}  {flag(s['profit_factor'], 2.0)}")
    print(f"  Avg RR         : {s['avg_rr']}  {flag(s['avg_rr'], 2.5)}")
    sep()

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    out = REPORTS_DIR / f"daily_{date.replace('-','')}.json"
    with open(out, "w") as f:
        json.dump({"date": date, **s}, f, indent=2)
    print(f"  💾 Saved to {out.name}")
    sep()


def cmd_weekly():
    today = datetime.now(timezone.utc)
    start = today - timedelta(days=today.weekday())
    end = today
    start_str = start.strftime("%Y-%m-%d")
    end_str = end.strftime("%Y-%m-%d")
    week_num = today.isocalendar()[1]

    trades = filter_by_period(load_trades(), start_str, end_str)
    s = calc_stats(trades)

    sep()
    print(f"  📊 WEEKLY REPORT — W{week_num} ({start_str} → {end_str})")
    sep()
    if s["total"] == 0:
        print("  No trades found this week.")
        sep()
        return

    print(f"  Trades         : {s['total']}  ({s['wins']}W / {s['losses']}L)")
    print(f"  Win Rate       : {s['win_rate']}%  {flag(s['win_rate'], BASELINE_WIN_RATE)} (baseline {BASELINE_WIN_RATE}%)")
    print(f"  Net P&L        : ${s['net_pnl']:+.2f}")
    print(f"  Profit Factor  : {s['profit_factor']}  {flag(s['profit_factor'], BASELINE_PF)} (baseline {BASELINE_PF})")
    print(f"  Avg RR         : {s['avg_rr']}")
    sep()


def cmd_monthly(month_str: str = None):
    month = month_str or datetime.now(timezone.utc).strftime("%Y-%m")
    start = f"{month}-01"
    end = f"{month}-31"

    trades = filter_by_period(load_trades(), start, end)
    s = calc_stats(trades)

    sep()
    print(f"  📈 MONTHLY REPORT — {month}")
    sep()
    if s["total"] == 0:
        print("  No trades found for this month.")
        sep()
        return

    print(f"  Trades         : {s['total']}  ({s['wins']}W / {s['losses']}L)")
    print(f"  Win Rate       : {s['win_rate']}%  {flag(s['win_rate'], BASELINE_WIN_RATE)}")
    print(f"  Net P&L        : ${s['net_pnl']:+.2f}")
    print(f"  Profit Factor  : {s['profit_factor']}  {flag(s['profit_factor'], 2.0)}")
    print(f"  Avg RR         : {s['avg_rr']}")
    sep()

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    out = REPORTS_DIR / f"monthly_{month.replace('-','')}.json"
    with open(out, "w") as f:
        json.dump({"month": month, **s}, f, indent=2)
    print(f"  💾 Saved to {out.name}")
    sep()


def cmd_compare(period_a: str, period_b: str):
    start_a, end_a = f"{period_a}-01", f"{period_a}-31"
    start_b, end_b = f"{period_b}-01", f"{period_b}-31"

    trades = load_trades()
    sa = calc_stats(filter_by_period(trades, start_a, end_a))
    sb = calc_stats(filter_by_period(trades, start_b, end_b))

    def diff(a, b):
        if a and b:
            d = round(b - a, 2)
            return f"({'+' if d >= 0 else ''}{d})"
        return ""

    sep()
    print(f"  ⚖️  COMPARISON: {period_a} vs {period_b}")
    sep()
    print(f"  {'Metric':<18} {'Period A':>10} {'Period B':>10} {'Change':>10}")
    print(f"  {'─'*18} {'─'*10} {'─'*10} {'─'*10}")

    metrics = [
        ("Trades", "total", ""),
        ("Win Rate %", "win_rate", "%"),
        ("Net P&L $", "net_pnl", "$"),
        ("Profit Factor", "profit_factor", ""),
        ("Avg RR", "avg_rr", ""),
    ]
    for label, key, unit in metrics:
        va = sa.get(key, 0)
        vb = sb.get(key, 0)
        d = diff(va, vb)
        print(f"  {label:<18} {str(va):>10} {str(vb):>10} {d:>10}")
    sep()


def cmd_equity_curve(export: bool):
    trades = load_trades()
    if not trades:
        print("No trades to chart.")
        return

    trades_sorted = sorted(trades, key=lambda t: t.get("entry_time", ""))
    balance = 10000.0
    rows = []
    for t in trades_sorted:
        balance += t.get("pnl_usd", 0)
        rows.append({
            "date": t.get("entry_time", "")[:10],
            "trade_id": t.get("trade_id", ""),
            "pnl": t.get("pnl_usd", 0),
            "balance": round(balance, 2),
            "outcome": t.get("outcome", "")
        })

    if export:
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        out = REPORTS_DIR / "equity_curve.csv"
        with open(out, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=rows[0].keys())
            writer.writeheader()
            writer.writerows(rows)
        print(f"✅ Equity curve exported to {out}")
    else:
        sep()
        print("  📉 EQUITY CURVE (last 10 trades)")
        sep()
        for row in rows[-10:]:
            bar = "▲" if row["pnl"] >= 0 else "▼"
            print(f"  {row['date']} | {bar} ${row['pnl']:>+8.2f} | Balance: ${row['balance']:,.2f}")
        sep()


def main():
    parser = argparse.ArgumentParser(description="🐆 Ingwe Performance Reporter")

    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--daily", action="store_true")
    action.add_argument("--weekly", action="store_true")
    action.add_argument("--monthly", action="store_true")
    action.add_argument("--compare", action="store_true")
    action.add_argument("--equity-curve", action="store_true")

    parser.add_argument("--date", help="Date for daily report (YYYY-MM-DD)")
    parser.add_argument("--month", help="Month for monthly report (YYYY-MM)")
    parser.add_argument("--period-a", help="Period A for comparison (YYYY-MM)")
    parser.add_argument("--period-b", help="Period B for comparison (YYYY-MM)")
    parser.add_argument("--export", action="store_true", help="Export results to file")

    args = parser.parse_args()

    if args.daily:
        cmd_daily(args.date)
    elif args.weekly:
        cmd_weekly()
    elif args.monthly:
        cmd_monthly(args.month)
    elif args.compare:
        if not args.period_a or not args.period_b:
            print("❌ --compare requires --period-a and --period-b")
        else:
            cmd_compare(args.period_a, args.period_b)
    elif args.equity_curve:
        cmd_equity_curve(args.export)


if __name__ == "__main__":
    main()
