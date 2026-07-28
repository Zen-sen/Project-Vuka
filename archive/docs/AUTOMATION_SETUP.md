# Project Vuka - Automation Setup

## Overview

Project Vuka now has an **always-on watchdog** that keeps all 4 bot instances running 24/7.

## Files Created

| File | Purpose |
|------|---------|
| `supervisor.py` | Main watchdog script - keeps bots running |
| `start_supervisor.bat` | Batch launcher for manual start |
| `setup_scheduler.bat` | Sets up Windows Task Scheduler (run as admin) |
| `logs/` | Directory for log files |

## Quick Start

### Option 1: Manual Start (for testing)
```bash
python supervisor.py
```

### Option 2: Auto-Start on Login (Recommended)

1. **Run as Administrator:** Right-click `setup_scheduler.bat` → "Run as administrator"

2. **That's it!** Supervisor will start automatically every time you log in.

### Option 3: Start Now Without Waiting
```bash
start_supervisor.bat
```

## What the Supervisor Does

1. **Starts all 4 bots:**
   - EURUSD_INGWE
   - GBPUSD_INGWE
   - EURUSD_SILVER_BULLET
   - GBPUSD_SILVER_BULLET

2. **Monitors health every 30 seconds**

3. **Restarts crashed bots within 60 seconds**

4. **Logs everything to:**
   - `logs/supervisor.log` - Supervisor activity
   - `logs/{symbol}_{strategy}.log` - Individual bot output

## Killzone Hours

The bots themselves handle timing. They only trade during:

| Session | Time (SAST) |
|---------|-------------|
| Asian | 02:00 - 06:00 |
| London Open | 09:00 - 12:00 |
| New York Open | 15:00 - 18:00 |

Outside these hours, bots wait in standby mode.

## Stopping the Supervisor

To stop all bots:
1. Find the Command Prompt running the supervisor
2. Press Ctrl+C
3. Or close the window

## Logs

Check `logs/supervisor.log` for:
- Bot startup/shutdown events
- Crash recovery events
- Errors

## Troubleshooting

### Bots not starting?
- Check `logs/supervisor.log` for errors
- Make sure MT5 is running and connected

### Supervisor not starting on login?
- Run `setup_scheduler.bat` as Administrator again
- Check Windows Task Scheduler for "ProjectVuka_Supervisor"

### Need to restart everything?
```bash
# Stop all bots
python -c "import os; [os.kill(int(p), 9) for p in os.popen('tasklist /FI \"IMAGENAME eq python.exe\" /FO CSV /NH').read().split() if 'python' in p]"

# Start supervisor
start_supervisor.bat
```

## Architecture

```
┌─────────────────────────────────────┐
│  Windows (starts on login)           │
│  Task Scheduler → start_supervisor   │
└─────────────────┬───────────────────┘
                  │
                  ▼
┌─────────────────────────────────────┐
│  supervisor.py (watchdog)           │
│  - Monitors 24/7                    │
│  - Restarts crashed bots             │
│  - Health check every 30s            │
└─────────────────┬───────────────────┘
                  │
      ┌───────────┼───────────┐
      ▼           ▼           ▼
┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐
│ EURUSD  │ │ GBPUSD  │ │ EURUSD  │ │ GBPUSD  │
│ INGWE   │ │ INGWE   │ │   SB    │ │   SB    │
└─────────┘ └─────────┘ └─────────┘ └─────────┘
```
