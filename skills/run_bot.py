#!/usr/bin/env python3
"""
run_bot.py — Agent Ingwe Bot Lifecycle Manager
Project Vuka | Controls start/stop/status of the MT5 trading bot
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
MEMORY_FILE = BASE_DIR / "Memory.md"
SESSION_FILE = BASE_DIR / "data" / "sessions_today.json"
STATUS_FILE = BASE_DIR / "data" / "bot_status.json"


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_session() -> dict:
    if not SESSION_FILE.exists():
        return {}
    with open(SESSION_FILE) as f:
        return json.load(f)


def save_session(data: dict):
    SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(SESSION_FILE, "w") as f:
        json.dump(data, f, indent=2)


def load_status() -> dict:
    if not STATUS_FILE.exists():
        return {
            "bot_status": "IDLE",
            "active_instances": [],
            "last_updated": None
        }
    with open(STATUS_FILE) as f:
        return json.load(f)


def save_status(data: dict):
    STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
    data["last_updated"] = now_utc()
    with open(STATUS_FILE, "w") as f:
        json.dump(data, f, indent=2)


def print_separator():
    print("─" * 52)


def cmd_status():
    session = load_session()
    status = load_status()

    print_separator()
    print("  🐆 INGWE STATUS")
    print_separator()
    print(f"  Bot Status      : {status.get('bot_status', 'UNKNOWN')}")
    print(f"  Active Instances: {status.get('active_instances', [])}")
    print(f"  Session Locked  : {session.get('session_locked', False)}")
    print(f"  Circuit Break   : {session.get('circuit_break_triggered', False)}")
    print(f"  Trades Today    : {session.get('trades_today', 0)}")
    print(f"  Daily P&L       : {session.get('daily_pnl', 0.0):.2f}")
    print(f"  Daily DD %      : {session.get('daily_drawdown_pct', 0.0):.2f}%")
    print(f"  Last Trade      : {session.get('last_trade_time', 'None')}")
    print(f"  Last Updated    : {status.get('last_updated', 'Never')}")
    print_separator()


def cmd_start(symbol: str, strategy: str, dry_run: bool):
    session = load_session()
    status = load_status()

    if session.get("circuit_break_triggered", False):
        print("❌ BLOCKED — Circuit break is active. Run --reset --confirm first.")
        sys.exit(1)

    if session.get("session_locked", False):
        print("❌ BLOCKED — Session is locked. Use --reset --confirm to unlock.")
        sys.exit(1)

    instance_key = f"{symbol}-{strategy}"
    if instance_key in status.get("active_instances", []):
        print(f"⚠️  Instance {instance_key} is already running.")
        sys.exit(0)

    mode = "DRY-RUN" if dry_run else "LIVE"
    print_separator()
    print(f"  🐆 STARTING INGWE [{mode}]")
    print_separator()
    print(f"  Symbol   : {symbol}")
    print(f"  Strategy : {strategy}")
    print(f"  Mode     : {mode}")
    print(f"  Time     : {now_utc()}")
    print_separator()

    instances = status.get("active_instances", [])
    instances.append(instance_key)
    status["bot_status"] = "RUNNING"
    status["active_instances"] = instances
    save_status(status)

    if not dry_run:
        print(f"  ✅ Bot started. Instance registered: {instance_key}")
        print("  📡 Connect your ingwe.py main loop here.")
    else:
        print(f"  ✅ Dry-run mode active. No live orders will be placed.")


def cmd_stop(emergency: bool = False):
    status = load_status()

    if not status.get("active_instances"):
        print("ℹ️  No active instances running.")
        return

    stop_type = "EMERGENCY STOP" if emergency else "GRACEFUL STOP"
    print_separator()
    print(f"  🛑 {stop_type}")
    print_separator()
    print(f"  Stopping instances: {status['active_instances']}")

    if emergency:
        print("  ⚠️  Emergency stop — open trades preserved. Review in MT5.")
    else:
        print("  ✅ Graceful stop — session state preserved.")

    status["bot_status"] = "IDLE"
    status["active_instances"] = []
    save_status(status)
    print(f"  Stopped at: {now_utc()}")
    print_separator()


def cmd_reset(confirmed: bool):
    if not confirmed:
        print("⚠️  Use --reset --confirm to acknowledge the reset.")
        sys.exit(1)

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    fresh_session = {
        "date": today,
        "session_locked": False,
        "circuit_break_triggered": False,
        "trades_today": 0,
        "daily_pnl": 0.0,
        "daily_drawdown_pct": 0.0,
        "sessions_completed": [],
        "sessions_pending": ["asian", "london", "ny"],
        "last_trade_id": None,
        "last_trade_time": None,
        "open_trade_ids": [],
        "notes": f"Reset by run_bot.py at {now_utc()}"
    }
    save_session(fresh_session)

    status = load_status()
    status["bot_status"] = "IDLE"
    status["active_instances"] = []
    save_status(status)

    print("✅ Session reset complete.")
    print(f"   Date: {today}")
    print("   circuit_break → false, session_locked → false")


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except AttributeError:
        pass

    parser = argparse.ArgumentParser(
        description="🐆 Ingwe Bot Lifecycle Manager",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python skills/run_bot.py --status
  python skills/run_bot.py --start --symbol EURUSDc --strategy INGWE
  python skills/run_bot.py --start --symbol EURUSDc --strategy SILVER_BULLET --dry-run
  python skills/run_bot.py --stop
  python skills/run_bot.py --emergency-stop
  python skills/run_bot.py --reset --confirm
        """
    )

    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--status", action="store_true", help="Show current bot status")
    action.add_argument("--start", action="store_true", help="Start bot instance")
    action.add_argument("--stop", action="store_true", help="Graceful stop")
    action.add_argument("--emergency-stop", action="store_true", help="Emergency stop (preserves open trades)")
    action.add_argument("--reset", action="store_true", help="Reset session state")

    parser.add_argument("--symbol", choices=["EURUSDc", "GBPUSDc", "BOTH"], default="EURUSDc")
    parser.add_argument("--strategy", choices=["INGWE", "SILVER_BULLET", "BOTH"], default="INGWE")
    parser.add_argument("--dry-run", action="store_true", help="Paper trade mode — no live orders")
    parser.add_argument("--confirm", action="store_true", help="Confirm destructive actions")

    args = parser.parse_args()

    if args.status:
        cmd_status()
    elif args.start:
        cmd_start(args.symbol, args.strategy, args.dry_run)
    elif args.stop:
        cmd_stop(emergency=False)
    elif args.emergency_stop:
        cmd_stop(emergency=True)
    elif args.reset:
        cmd_reset(confirmed=args.confirm)


if __name__ == "__main__":
    main()
