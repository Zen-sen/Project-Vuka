#!/usr/bin/env python3
"""
PROJECT VUKA -- MONITOR
Runs all bots in check mode and displays consolidated status.
"""

import sys
import os
import time
import json
import subprocess
import argparse
from datetime import datetime, timedelta
from pathlib import Path

SAST_OFFSET = 2

SESSION_ENDS = [6, 12, 18]

SYMBOLS = ["EURUSD", "GBPUSD"]
STRATEGIES = ["INGWE", "SILVER_BULLET"]

LOG_DIR = Path(".")

def now_sast():
    return datetime.now() + timedelta(hours=SAST_OFFSET)

def get_next_session_end():
    now = now_sast()
    current_hour = now.hour
    
    for end_hour in SESSION_ENDS:
        if current_hour < end_hour:
            next_end = now.replace(hour=end_hour, minute=0, second=0, microsecond=0)
            return next_end
    
    next_end = now.replace(hour=6, minute=0, second=0, microsecond=0) + timedelta(days=1)
    return next_end

def get_today_sessions():
    now = now_sast()
    sessions = []
    
    asian_end = now.replace(hour=6, minute=0, second=0, microsecond=0)
    london_end = now.replace(hour=12, minute=0, second=0, microsecond=0)
    ny_end = now.replace(hour=18, minute=0, second=0, microsecond=0)
    
    if asian_end <= now:
        sessions.append("Asian (ended)" if now > asian_end else "Asian (active)")
    if london_end <= now:
        sessions.append("London Open (ended)" if now > london_end else "London Open (active)")
    if ny_end <= now:
        sessions.append("New York Open (ended)" if now > ny_end else "New York Open (active)")
    
    if not sessions:
        if now < now.replace(hour=2, minute=0):
            sessions.append("Pre-Asian")
        elif now < now.replace(hour=9, minute=0):
            sessions.append("Asian (active)")
        elif now < now.replace(hour=15, minute=0):
            sessions.append("London Open (active)")
        else:
            sessions.append("New York Open (active)")
    
    return sessions

def get_daily_pnl_from_log(symbol, strategy):
    log_file = LOG_DIR / f"trades_{symbol}_{strategy}.json"
    if not log_file.exists():
        return 0.0
    
    try:
        with open(log_file, "r") as f:
            trades = json.load(f)
        
        if not trades:
            return 0.0
        
        today = now_sast().strftime("%Y-%m-%d")
        today_trades = [t for t in trades if t.get("time", "").startswith(today)]
        
        if not today_trades:
            return 0.0
        
        pnl = 0.0
        for trade in today_trades:
            if "pnl" in trade:
                pnl += trade["pnl"]
            elif "retcode" in trade and trade["retcode"] == 10009:
                pnl += 0.0
        
        return pnl
    except Exception:
        return 0.0

def run_check(symbol, strategy):
    cmd = [sys.executable, "ingwe.py", symbol, strategy, "--check"]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60,
            cwd=os.getcwd()
        )
        return result.stdout + result.stderr
    except subprocess.TimeoutExpired:
        return "[TIMEOUT]"
    except Exception as e:
        return f"[ERROR: {e}]"

def parse_bot_output(output, strategy):
    result = {
        "adx": "---",
        "trend": "---",
        "pnl": "---",
        "signal": "---",
        "notes": "---"
    }
    
    lines = output.split("\n")
    
    for line in lines:
        if "ADX:" in line and ("+" in line or "-" in line):
            parts = line.split("ADX:")[1].strip().split()
            if parts:
                result["adx"] = parts[0]
        
        if "Trend:" in line or "H1 Trend:" in line:
            if "H1 Trend:" in line:
                trend = line.split("H1 Trend:")[1].strip().split()[0]
            else:
                trend = line.split("Trend:")[1].strip().split()[0]
            result["trend"] = trend
        
        if "Daily P&L [" in line:
            try:
                pnl_str = line.split("Daily P&L [")[1].split("]:")[1].strip().split()[0]
                result["pnl"] = pnl_str
            except:
                pass
        
        if "SWEEP:" in line:
            result["signal"] = "SWEEP DETECTED"
        elif "FVG READY" in line:
            result["signal"] = "FVG READY"
        elif "No FVG" in line or "Ingwe waits" in line:
            result["signal"] = "WAIT - NO FVG"
        elif "News blackout" in line:
            result["signal"] = "NEWS BLACKOUT"
        elif "Check complete" in line or "stands down" in line:
            result["signal"] = "NO SIGNAL"
        elif "HUNTING" in line or "Entry placed" in line:
            result["signal"] = "ENTRY PLACED"
        
        if "KILLZONE:" in line:
            try:
                killzone = line.split("KILLZONE:")[1].strip().split("(")[0].strip()
                result["notes"] = killzone
            except:
                pass
        elif "SWEEP:" in line:
            try:
                sweep = line.split("SWEEP:")[1].strip().split("at")[0].strip()
                if result["notes"] == "---":
                    result["notes"] = sweep
                else:
                    result["notes"] += f" | {sweep}"
            except:
                pass
        elif "News blackout" in line:
            result["notes"] = "News blackout"
        elif "Ingwe conditions not aligned" in line:
            result["notes"] = "Conditions not met"
        elif "Panic candle" in line:
            result["notes"] = "Panic - no chase"
        elif "No Silver Bullet window" in line:
            result["notes"] = "Outside SB window"
    
    return result

def check_consecutive_losses(symbol, strategy):
    return "N/A"

def print_header():
    now = now_sast()
    print("=" * 70)
    print(f"   PROJECT VUKA -- MONITOR")
    print(f"   {now.strftime('%Y-%m-%d %H:%M')} SAST")
    print("=" * 70)
    print()

def print_table(robot_status):
    print(f"{'Symbol/Strategy':<22} {'ADX':>5} {'Trend':>9} {'P&L':>10} {'Signal':<14} {'Notes'}")
    print("-" * 70)
    
    for (symbol, strategy), status in robot_status.items():
        row = f"{symbol}_{strategy:<10} {status['adx']:>5} {status['trend']:>9} {status['pnl']:>10} {status['signal']:<14} {status['notes']}"
        print(row)
    
    print()

def print_summary(robot_status):
    sessions = get_today_sessions()
    print("=== SUMMARY ===")
    print(f"Sessions today:   {', '.join(sessions)}")
    
    total_pnl = 0.0
    for (symbol, strategy), status in robot_status.items():
        pnl_str = status["pnl"]
        if pnl_str != "---":
            try:
                total_pnl += float(pnl_str)
            except:
                pass
    
    print(f"Total P&L:       {total_pnl:.2f} USC")
    
    print("\nConsecutive losses:")
    for (symbol, strategy), status in robot_status.items():
        print(f"  {symbol}_{strategy}: N/A (log only records entries)")
    
    print()

def check_alerts(robot_status):
    alerts = []
    
    for (symbol, strategy), status in robot_status.items():
        signal = status["signal"]
        
        if signal == "FVG READY":
            alerts.append(f"ALERT: {symbol}_{strategy} - FVG signal detected!")
    
    if alerts:
        print("\a")
        print("!!! ALERTS !!!")
        for alert in alerts:
            print(f"  {alert}")
        print()

def wait_for_session_end():
    next_end = get_next_session_end()
    now = now_sast()
    wait_seconds = (next_end - now).total_seconds()
    
    if wait_seconds <= 0:
        return
    
    print(f"Next session end: {next_end.strftime('%H:%M')} SAST")
    print(f"Waiting {wait_seconds/60:.0f} minutes...")
    print()
    
    time.sleep(wait_seconds)

def main():
    parser = argparse.ArgumentParser(description="Project Vuka Monitor")
    parser.add_argument("--watch", action="store_true", help="Wait for next session end, then run")
    parser.add_argument("--symbols", nargs="+", default=SYMBOLS, help="Symbols to monitor")
    parser.add_argument("--strategies", nargs="+", default=STRATEGIES, help="Strategies to monitor")
    args = parser.parse_args()
    
    if args.watch:
        wait_for_session_end()
    
    print_header()
    
    robot_status = {}
    
    for symbol in args.symbols:
        for strategy in args.strategies:
            print(f"Checking {symbol}_{strategy}...")
            output = run_check(symbol, strategy)
            status = parse_bot_output(output, strategy)
            status["pnl"] = str(get_daily_pnl_from_log(symbol, strategy))
            robot_status[(symbol, strategy)] = status
    
    print()
    print_table(robot_status)
    print_summary(robot_status)
    check_alerts(robot_status)
    
    print("Monitor complete.")

if __name__ == "__main__":
    main()
