#!/usr/bin/env python3
"""
risk_monitor.py — Agent Ingwe Risk Monitor
Project Vuka | Guardian layer validation and exposure tracking
"""

import argparse
import json
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
SESSION_FILE = BASE_DIR / "data" / "sessions_today.json"
TRADE_LOG = BASE_DIR / "data" / "trade_log.json"

MAX_RISK_PCT = 1.0
DAILY_LOSS_LIMIT = 3.0
WARNING_THRESHOLD = 1.5
CRITICAL_THRESHOLD = 2.5
STREAK_ALERT = 3
PIP_VALUE_PER_LOT = 10.0


def load_session() -> dict:
    if not SESSION_FILE.exists():
        return {}
    with open(SESSION_FILE) as f:
        return json.load(f)


def load_trades() -> list:
    if not TRADE_LOG.exists():
        return []
    with open(TRADE_LOG) as f:
        data = json.load(f)
    return data if isinstance(data, list) else data.get("trades", [])


def sep():
    print("-" * 52)


def calc_lot_size(balance: float, sl_pips: float, risk_pct: float = MAX_RISK_PCT) -> dict:
    risk_amount = balance * (risk_pct / 100)
    lot = risk_amount / (sl_pips * PIP_VALUE_PER_LOT)
    lot = round(lot, 2)

    return {
        "balance": balance,
        "risk_pct": risk_pct,
        "risk_amount": round(risk_amount, 2),
        "sl_pips": sl_pips,
        "recommended_lot": max(0.01, lot),
        "pip_value": PIP_VALUE_PER_LOT,
        "valid": lot >= 0.01
    }


def print_lot_calc(result: dict):
    sep()
    print("  [LOT] LOT SIZE CALCULATOR")
    sep()
    print(f"  Account Balance : ${result['balance']:,.2f}")
    print(f"  Risk %          : {result['risk_pct']}%")
    print(f"  Risk Amount     : ${result['risk_amount']:.2f}")
    print(f"  SL Distance     : {result['sl_pips']} pips")
    print(f"  Recommended Lot : {result['recommended_lot']}")
    status = "[OK] VALID" if result['valid'] else "[X] TOO SMALL - min 0.01 lot"
    print(f"  Status          : {status}")
    sep()


def check_drawdown() -> dict:
    session = load_session()
    dd = session.get("daily_drawdown_pct", 0.0)
    locked = session.get("session_locked", False)
    circuit = session.get("circuit_break_triggered", False)

    if circuit or dd >= DAILY_LOSS_LIMIT:
        level, icon = "CIRCUIT_BREAK", "[!]"
    elif dd >= CRITICAL_THRESHOLD:
        level, icon = "CRITICAL", "[X]"
    elif dd >= WARNING_THRESHOLD:
        level, icon = "WARNING", "[!]"
    else:
        level, icon = "OK", "[OK]"

    remaining = max(0.0, DAILY_LOSS_LIMIT - dd)

    return {
        "daily_dd": dd,
        "daily_pnl": session.get("daily_pnl", 0.0),
        "level": level,
        "icon": icon,
        "session_locked": locked,
        "circuit_break": circuit,
        "remaining_budget_pct": round(remaining, 2)
    }


def print_drawdown(d: dict):
    sep()
    print("  [DD] DRAWDOWN STATUS")
    sep()
    print(f"  Daily Drawdown  : {d['daily_dd']:.2f}%  {d['icon']} {d['level']}")
    print(f"  Daily P&L       : ${d['daily_pnl']:+.2f}")
    print(f"  Remaining Budget: {d['remaining_budget_pct']:.2f}%")
    print(f"  Session Locked  : {d['session_locked']}")
    print(f"  Circuit Break   : {d['circuit_break']}")
    sep()


def check_streak() -> dict:
    trades = load_trades()
    if not trades:
        return {"streak": 0, "type": "NONE", "alert": False}

    recent = list(reversed(trades[-20:]))
    streak = 0
    streak_type = recent[0].get("outcome", "NONE") if recent else "NONE"

    for t in recent:
        if t.get("outcome") == streak_type:
            streak += 1
        else:
            break

    alert = streak_type == "LOSS" and streak >= STREAK_ALERT
    return {"streak": streak, "type": streak_type, "alert": alert}


def print_streak(s: dict):
    icon = "[X]" if s["alert"] else ("[OK]" if s["type"] == "WIN" else "[-]")
    sep()
    print("  [STREAK] STREAK DETECTOR")
    sep()
    print(f"  Current Streak  : {s['streak']} consecutive {s['type']}s  {icon}")
    if s["alert"]:
        print(f"  [!] ALERT - {s['streak']} consecutive losses. Consider reducing lot size.")
    sep()


def cmd_validate():
    dd = check_drawdown()
    streak = check_streak()
    session = load_session()

    sep()
    print("  [GUARD]  PRE-TRADE VALIDATION")
    sep()

    issues = []

    if dd["circuit_break"] or dd["level"] == "CIRCUIT_BREAK":
        issues.append("[!] Circuit break active — NO TRADES")
    elif dd["level"] == "CRITICAL":
        issues.append("[X] Drawdown critical — reduce lot size")
    elif dd["level"] == "WARNING":
        issues.append("[!]  Drawdown warning — proceed with caution")
    else:
        print("  [OK] Drawdown OK")

    if session.get("session_locked", False):
        issues.append("[X] Session is locked")
    else:
        print("  [OK] Session not locked")

    if streak["alert"]:
        issues.append(f"[!]  Loss streak: {streak['streak']} consecutive losses")
    else:
        print(f"  [OK] Streak OK ({streak['streak']} {streak['type']}s)")

    if issues:
        print()
        for issue in issues:
            print(f"  {issue}")
        print()
        print("  [X] VERDICT: DO NOT TRADE - resolve issues above")
    else:
        print()
        print("  [OK] VERDICT: CONDITIONS MET - trade with normal sizing")
    sep()


def cmd_report():
    dd = check_drawdown()
    streak = check_streak()
    session = load_session()

    sep()
    print("  [REPORT] INGWE RISK REPORT")
    sep()
    print(f"  Date            : {session.get('date', 'Unknown')}")
    print(f"  Daily P&L       : ${dd['daily_pnl']:+.2f}")
    print(f"  Daily DD        : {dd['daily_dd']:.2f}%  {dd['icon']} {dd['level']}")
    print(f"  Remaining Budget: {dd['remaining_budget_pct']:.2f}% of daily limit")
    print(f"  Trades Today    : {session.get('trades_today', 0)}")
    print(f"  Session Locked  : {dd['session_locked']}")
    print(f"  Circuit Break   : {dd['circuit_break']}")
    streak_icon = "[!] " if streak["alert"] else "[OK]"
    print(f"  Loss Streak     : {streak['streak']} {streak['type']}s  {streak_icon}")
    sep()


def main():
    parser = argparse.ArgumentParser(
        description="🐆 Ingwe Risk Monitor",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python skills/risk_monitor.py --report
  python skills/risk_monitor.py --drawdown
  python skills/risk_monitor.py --streak
  python skills/risk_monitor.py --validate
  python skills/risk_monitor.py --lot-size --balance 10000 --sl-pips 10
  python skills/risk_monitor.py --lot-size --balance 10000 --sl-pips 15 --risk 0.5
        """
    )

    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--report", action="store_true", help="Full risk report")
    action.add_argument("--drawdown", action="store_true", help="Drawdown status")
    action.add_argument("--streak", action="store_true", help="Win/loss streak")
    action.add_argument("--validate", action="store_true", help="Pre-trade validation")
    action.add_argument("--lot-size", action="store_true", help="Calculate recommended lot size")

    parser.add_argument("--balance", type=float, help="Account balance for lot calculation")
    parser.add_argument("--sl-pips", type=float, help="Stop loss distance in pips")
    parser.add_argument("--risk", type=float, default=MAX_RISK_PCT, help="Risk percent (default: 1.0)")

    args = parser.parse_args()

    if args.report:
        cmd_report()
    elif args.drawdown:
        print_drawdown(check_drawdown())
    elif args.streak:
        print_streak(check_streak())
    elif args.validate:
        cmd_validate()
    elif args.lot_size:
        if not args.balance or not args.sl_pips:
            print("[X] --lot-size requires --balance and --sl-pips")
        else:
            print_lot_calc(calc_lot_size(args.balance, args.sl_pips, args.risk))


if __name__ == "__main__":
    main()
