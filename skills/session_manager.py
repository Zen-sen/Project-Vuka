#!/usr/bin/env python3
"""
session_manager.py — Agent Ingwe Session Manager
Project Vuka | Manages sessions_today.json lifecycle
"""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
SESSION_FILE = BASE_DIR / "data" / "sessions_today.json"
SESSION_HISTORY = BASE_DIR / "data" / "sessions" / "session_history.json"

VALID_SESSIONS = ["asian", "london", "ny"]


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def today_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def load() -> dict:
    if not SESSION_FILE.exists():
        return {}
    with open(SESSION_FILE) as f:
        return json.load(f)


def save(data: dict):
    SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(SESSION_FILE, "w") as f:
        json.dump(data, f, indent=2)


def archive(session: dict):
    SESSION_HISTORY.parent.mkdir(parents=True, exist_ok=True)
    history = []
    if SESSION_HISTORY.exists():
        with open(SESSION_HISTORY) as f:
            history = json.load(f)
    history.append(session)
    with open(SESSION_HISTORY, "w") as f:
        json.dump(history, f, indent=2)


def sep():
    print("─" * 52)


def cmd_read():
    s = load()
    if not s:
        print("⚠️  sessions_today.json not found or empty.")
        return

    sep()
    print("  📅 SESSION STATE")
    sep()
    print(f"  Date             : {s.get('date', '?')}")
    print(f"  Session Locked   : {s.get('session_locked', False)}")
    print(f"  Circuit Break    : {s.get('circuit_break_triggered', False)}")
    print(f"  Trades Today     : {s.get('trades_today', 0)}")
    print(f"  Daily P&L        : ${s.get('daily_pnl', 0.0):+.2f}")
    print(f"  Daily DD %       : {s.get('daily_drawdown_pct', 0.0):.2f}%")
    print(f"  Sessions Done    : {s.get('sessions_completed', [])}")
    print(f"  Sessions Pending : {s.get('sessions_pending', [])}")
    print(f"  Open Trades      : {s.get('open_trade_ids', [])}")
    print(f"  Last Trade       : {s.get('last_trade_time', 'None')}")
    print(f"  Notes            : {s.get('notes', '')}")
    sep()


def cmd_daily_reset():
    old = load()
    if old:
        archive(old)
        print(f"  📦 Archived session for {old.get('date', 'unknown date')}")

    today = today_str()
    fresh = {
        "date": today,
        "session_locked": False,
        "circuit_break_triggered": False,
        "trades_today": 0,
        "wins_today": 0,
        "losses_today": 0,
        "daily_pnl": 0.0,
        "daily_drawdown_pct": 0.0,
        "sessions_completed": [],
        "sessions_pending": ["asian", "london", "ny"],
        "last_trade_id": None,
        "last_trade_time": None,
        "open_trade_ids": [],
        "notes": f"Daily reset at {now_utc()}"
    }
    save(fresh)
    sep()
    print(f"  🌅 DAILY RESET COMPLETE — {today}")
    sep()
    print("  ✅ session_locked     → false")
    print("  ✅ circuit_break      → false")
    print("  ✅ daily_pnl          → 0.00")
    print("  ✅ trades_today       → 0")
    print("  ✅ sessions_pending   → ['asian', 'london', 'ny']")
    sep()


def cmd_lock(reason: str):
    s = load()
    if not s:
        print("❌ No session file found. Run --daily-reset first.")
        return
    s["session_locked"] = True
    s["notes"] = f"LOCKED at {now_utc()} — {reason}"
    save(s)
    print(f"🔒 Session locked. Reason: {reason}")


def cmd_unlock(authorized_by: str):
    s = load()
    if not s:
        print("❌ No session file found.")
        return
    s["session_locked"] = False
    s["circuit_break_triggered"] = False
    s["notes"] = f"UNLOCKED at {now_utc()} by {authorized_by}"
    save(s)
    print(f"🔓 Session unlocked. Authorized by: {authorized_by}")


def cmd_validate():
    s = load()
    sep()
    print("  🔍 SESSION VALIDATION")
    sep()

    issues = []

    if s.get("date") != today_str():
        issues.append(f"⚠️  Session date ({s.get('date')}) ≠ today ({today_str()}) — run --daily-reset")
    else:
        print("  ✅ Date matches today")

    open_trades = s.get("open_trade_ids", [])
    if s.get("session_locked") and open_trades:
        issues.append(f"⚠️  Session locked but {len(open_trades)} open trade(s) present")
    else:
        print("  ✅ Open trade state consistent")

    dd = s.get("daily_drawdown_pct", 0.0)
    pnl = s.get("daily_pnl", 0.0)
    if pnl < 0 and dd == 0.0:
        issues.append("⚠️  Negative P&L but drawdown is 0 — possible sync issue")
    else:
        print("  ✅ P&L / drawdown consistent")

    if s.get("circuit_break_triggered") and not s.get("session_locked"):
        issues.append("⚠️  Circuit break triggered but session not locked — auto-correcting")
        s["session_locked"] = True
        save(s)

    if issues:
        print()
        for i in issues:
            print(f"  {i}")
    else:
        print()
        print("  ✅ All checks passed — session state is healthy")
    sep()


def cmd_complete_session(session_name: str):
    s = load()
    if not s:
        print("❌ No session file. Run --daily-reset first.")
        return
    completed = s.get("sessions_completed", [])
    pending = s.get("sessions_pending", [])
    if session_name not in completed:
        completed.append(session_name)
    if session_name in pending:
        pending.remove(session_name)
    s["sessions_completed"] = completed
    s["sessions_pending"] = pending
    save(s)
    print(f"✅ Session marked complete: {session_name}")
    print(f"   Remaining: {pending}")


def main():
    parser = argparse.ArgumentParser(
        description="🐆 Ingwe Session Manager",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python skills/session_manager.py --read
  python skills/session_manager.py --daily-reset
  python skills/session_manager.py --validate
  python skills/session_manager.py --lock --reason "manual pause"
  python skills/session_manager.py --unlock --authorized-by Rox
  python skills/session_manager.py --complete-session london
        """
    )

    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--read", action="store_true", help="Read current session state")
    action.add_argument("--daily-reset", action="store_true", help="Reset for new trading day")
    action.add_argument("--validate", action="store_true", help="Validate session integrity")
    action.add_argument("--lock", action="store_true", help="Lock session")
    action.add_argument("--unlock", action="store_true", help="Unlock session")
    action.add_argument("--complete-session", metavar="SESSION", help="Mark session as completed")

    parser.add_argument("--reason", default="manual", help="Reason for lock")
    parser.add_argument("--authorized-by", default="unknown", help="Who authorized unlock")

    args = parser.parse_args()

    if args.read:
        cmd_read()
    elif args.daily_reset:
        cmd_daily_reset()
    elif args.validate:
        cmd_validate()
    elif args.lock:
        cmd_lock(args.reason)
    elif args.unlock:
        cmd_unlock(args.authorized_by)
    elif args.complete_session:
        if args.complete_session not in VALID_SESSIONS:
            print(f"❌ Invalid session. Choose from: {VALID_SESSIONS}")
        else:
            cmd_complete_session(args.complete_session)


if __name__ == "__main__":
    main()
