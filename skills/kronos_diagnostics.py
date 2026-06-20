#!/usr/bin/env python3
"""
kronos_diagnostics.py — Kronos Health & Veto Gate Auditor
Project Vuka | Diagnose no-trade periods, veto patterns, API health
"""

import argparse
import json
import sys
import os
import socket
import subprocess
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
LOG_DIR = BASE_DIR / "logs"
VETO_LOG = LOG_DIR / "kronos_veto.log"
CONFIG_PATH = BASE_DIR / "config_v4.6.json"

TIMEOUT = 5

def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()[:19]


def sep():
    print("-" * 60)


def load_config():
    if not CONFIG_PATH.exists():
        return {}
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def cmd_health():
    config = load_config()
    veto_cfg = config.get("veto_gate", {})
    endpoint = veto_cfg.get("endpoint", "http://127.0.0.1:8000/v1/predict-ict")
    base_url = endpoint.replace("/v1/predict-ict", "")
    health_url = f"{base_url}/health"

    sep()
    print("  KRONOS HEALTH CHECK")
    sep()

    try:
        import requests
        resp = requests.get(health_url, timeout=TIMEOUT)
        if resp.status_code == 200:
            print(f"  Status : HEALTHY (HTTP {resp.status_code})")
            data = resp.json()
            for k, v in data.items():
                print(f"    {k}: {v}")
        else:
            print(f"  Status : UNHEALTHY (HTTP {resp.status_code})")
    except ImportError:
        print("  Warning: requests not installed, using socket check.")
        try:
            host, port_str = base_url.split("://")[1].split(":")
            port = int(port_str)
            s = socket.socket()
            s.settimeout(TIMEOUT)
            s.connect((host, port))
            s.close()
            print(f"  Status : PORT OPEN ({host}:{port})")
        except Exception as e:
            print(f"  Status : UNREACHABLE ({e})")
    except Exception as e:
        print(f"  Status : UNREACHABLE ({e})")

    print(f"  Endpoint : {endpoint}")
    print(f"  Time     : {now_utc()}")
    sep()


def cmd_port():
    sep()
    print("  PORT 8000 CHECK")
    sep()
    try:
        result = subprocess.run(
            ["netstat", "-ano"], capture_output=True, text=True, timeout=TIMEOUT
        )
        lines = result.stdout.splitlines()
        listeners = [l for l in lines if "127.0.0.1:8000" in l and "LISTENING" in l]
        if listeners:
            for l in listeners:
                parts = l.strip().split()
                pid = parts[-1] if parts else "?"
                print(f"  LISTENING  PID={pid}")
            print("  Port 8000 is IN USE.")
        else:
            print("  Port 8000 is FREE.")
    except FileNotFoundError:
        print("  netstat not available on this system.")
    except Exception as e:
        print(f"  Error: {e}")
    sep()


def cmd_veto(days: int = 7):
    if not VETO_LOG.exists():
        print(f"No veto log found at {VETO_LOG}")
        return

    sep()
    print(f"  VETO LOG ANALYSIS (last {days} days)")
    sep()

    lines = VETO_LOG.read_text(encoding="utf-8", errors="replace").splitlines()
    cutoff = (datetime.now(timezone.utc).timestamp()) - (days * 86400)

    entries = []
    for l in lines:
        if not l.startswith("{"):
            continue
        try:
            d = json.loads(l)
            ts = d.get("timestamp", "")
            if ts:
                parsed = datetime.fromisoformat(ts).timestamp()
                if parsed >= cutoff:
                    entries.append(d)
        except Exception:
            continue

    if not entries:
        print("  No entries in the selected window.")
        return

    allowed = [e for e in entries if "ALLOW" in e.get("decision", "")]
    vetoed = [e for e in entries if "VETO" in e.get("decision", "")]
    errors = [e for e in entries if "ERROR" in e.get("decision", "")]

    total = len(entries)
    print(f"  Total decisions : {total}")
    print(f"  Allowed         : {len(allowed)} ({len(allowed)/total*100:.0f}%)")
    print(f"  Vetoed          : {len(vetoed)} ({len(vetoed)/total*100:.0f}%)")
    print(f"  Errors          : {len(errors)}")
    sep()

    if allowed:
        print("  RECENT ALLOWED:")
        for e in allowed[-5:]:
            ts = e.get("timestamp", "?")[:16]
            sym = e.get("symbol", "?")
            sig = e.get("signal", "?")
            conf = e.get("confidence", 0)
            rsn = e.get("reason", "")[:60]
            print(f"    {ts}  {sym:10s} {sig:5s}  conf={conf:.2f}  {rsn}")
        sep()

    if vetoed:
        print("  RECENT VETOED:")
        for e in vetoed[-5:]:
            ts = e.get("timestamp", "?")[:16]
            sym = e.get("symbol", "?")
            sig = e.get("signal", "?")
            conf = e.get("confidence", 0)
            rsn = e.get("reason", "")[:60]
            print(f"    {ts}  {sym:10s} {sig:5s}  conf={conf:.2f}  {rsn}")
        sep()


def cmd_circuit():
    sep()
    print("  CIRCUIT BREAKER STATE")
    sep()
    veto_log_lines = []
    if VETO_LOG.exists():
        veto_log_lines = VETO_LOG.read_text(
            encoding="utf-8", errors="replace"
        ).splitlines()

    cb_entries = [l for l in veto_log_lines if "circuit_breaker_state" in l]
    if cb_entries:
        last = json.loads(cb_entries[-1])
        state = last.get("circuit_breaker_state", "UNKNOWN")
        print(f"  Circuit Breaker : {state}")
        print(f"  Safety Mode     : {last.get('safety_mode', 'UNKNOWN')}")
        print(f"  Veto Mode       : {last.get('mode', 'UNKNOWN')}")
        print(f"  Threshold       : {last.get('threshold', 'UNKNOWN')}")
    else:
        print("  No circuit breaker entries found.")
    sep()


def cmd_config():
    config = load_config()
    veto = config.get("veto_gate", {})
    hb = config.get("heartbeat", {})
    sep()
    print("  CURRENT CONFIGURATION")
    sep()
    print(f"  Veto Gate Enabled    : {veto.get('enabled', 'N/A')}")
    print(f"  Mode                 : {veto.get('mode', 'N/A')}")
    print(f"  Safety Mode          : {veto.get('safety_mode', 'N/A')}")
    print(f"  Threshold            : {veto.get('threshold', 'N/A')}")
    print(f"  BUY Threshold        : {veto.get('buy_threshold', 'N/A')}")
    print(f"  Endpoint             : {veto.get('endpoint', 'N/A')}")
    print(f"  Heartbeat Enabled    : {hb.get('enabled', 'N/A')}")
    print(f"  Heartbeat Interval   : {hb.get('interval_seconds', 'N/A')}s")
    tel = config.get("notifications", {}).get("telegram", {})
    print(f"  Telegram Enabled     : {tel.get('enabled', 'N/A')}")
    if tel.get("bot_token") and tel.get("chat_id"):
        print(f"  Telegram Configured  : Yes")
    else:
        print(f"  Telegram Configured  : No (fill bot_token & chat_id)")
    sep()


def cmd_summary():
    sep()
    print("  QUICK SUMMARY — Is Everything OK?")
    sep()

    checks_passed = 0
    checks_total = 4

    config = load_config()
    veto_cfg = config.get("veto_gate", {})
    endpoint = veto_cfg.get("endpoint", "http://127.0.0.1:8000/v1/predict-ict")
    base_url = endpoint.replace("/v1/predict-ict", "")
    health_url = f"{base_url}/health"

    try:
        import requests
        resp = requests.get(health_url, timeout=TIMEOUT)
        if resp.status_code == 200:
            print("  [OK]  Kronos API is healthy")
            checks_passed += 1
        else:
            print("  [WARN] Kronos API returned HTTP {resp.status_code}")
    except Exception:
        print("  [FAIL] Kronos API is unreachable")

    try:
        result = subprocess.run(
            ["netstat", "-ano"], capture_output=True, text=True, timeout=TIMEOUT
        )
        listeners = [l for l in result.stdout.splitlines() if "127.0.0.1:8000" in l and "LISTENING" in l]
        if listeners:
            checks_passed += 1
            print("  [OK]  Port 8000 has a listener")
        else:
            print("  [FAIL] Port 8000 has no listener")
    except Exception:
        print("  [?]   Could not check port 8000")

    safety = veto_cfg.get("safety_mode", "VETO_SAFE")
    if safety == "ALLOW_SAFE":
        checks_passed += 1
        print("  [OK]  ALLOW_SAFE mode — Ingwe can trade when Kronos is down")
    else:
        print("  [WARN] VETO_SAFE mode — No trades when Kronos is down")

    hb_enabled = config.get("heartbeat", {}).get("enabled", False)
    if hb_enabled:
        checks_passed += 1
        print("  [OK]  Heartbeat monitor is active")
    else:
        print("  [WARN] Heartbeat monitor is disabled")

    sep()
    print(f"  Checks: {checks_passed}/{checks_total} passed")
    if checks_passed == checks_total:
        print("  STATUS: ALL GOOD")
    else:
        print("  STATUS: ISSUES DETECTED — review above")
    sep()


def main():
    parser = argparse.ArgumentParser(
        description="\U0001f40e Kronos Health & Veto Gate Auditor",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python skills/kronos_diagnostics.py --health
  python skills/kronos_diagnostics.py --veto --days 14
  python skills/kronos_diagnostics.py --port
  python skills/kronos_diagnostics.py --circuit
  python skills/kronos_diagnostics.py --config
  python skills/kronos_diagnostics.py --summary
  python skills/kronos_diagnostics.py --all
        """,
    )

    parser.add_argument("--health", action="store_true", help="Check Kronos API health")
    parser.add_argument("--veto", action="store_true", help="Analyze recent veto log")
    parser.add_argument("--port", action="store_true", help="Check port 8000")
    parser.add_argument("--circuit", action="store_true", help="Show circuit breaker state")
    parser.add_argument("--config", action="store_true", help="Show current configuration")
    parser.add_argument("--summary", action="store_true", help="Quick health summary")
    parser.add_argument("--all", action="store_true", help="Run all checks")
    parser.add_argument("--days", type=int, default=7, help="Days of veto history to analyze")

    args = parser.parse_args()

    any_action = args.health or args.veto or args.port or args.circuit or args.config or args.summary or args.all
    if not any_action:
        parser.print_help()
        sys.exit(1)

    if args.all or args.health:
        cmd_health()
    if args.all or args.port:
        cmd_port()
    if args.all or args.veto:
        cmd_veto(args.days)
    if args.all or args.circuit:
        cmd_circuit()
    if args.all or args.config:
        cmd_config()
    if args.all or args.summary:
        cmd_summary()


if __name__ == "__main__":
    main()
