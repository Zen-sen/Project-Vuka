# SKILL: run_bot

> Manages the live lifecycle of Agent Ingwe — start, stop, status, restart.

## Triggers
Use this skill when the user says:
- "Run the bot", "Start Ingwe", "Launch on EURUSD"
- "Stop trading", "Pause Ingwe", "Emergency stop"
- "What's the bot status?", "Is Ingwe running?"
- "Restart after circuit break"

## Description
This skill invokes `run_bot.py` to control bot instances on MetaTrader 5.
Always check session state and news calendar before starting.

## Pre-Flight Checklist (verify before invoking)
1. MT5 terminal open and logged into Exness
2. `data/sessions_today.json` → `circuit_break_triggered` is `false`
3. No red-folder news events within 30 minutes
4. `data/sessions_today.json` date matches today

## Commands

### Check Status
```bash
python skills/run_bot.py --status
```

### Start Bot
```bash
python skills/run_bot.py --start --symbol EURUSDc --strategy INGWE
python skills/run_bot.py --start --symbol EURUSDc --strategy SILVER_BULLET
python skills/run_bot.py --start --symbol BOTH --strategy BOTH
```

### Stop Bot (Graceful)
```bash
python skills/run_bot.py --stop
```

### Emergency Stop (immediate, preserves open trades)
```bash
python skills/run_bot.py --emergency-stop
```

### Dry Run (paper trade — no live orders)
```bash
python skills/run_bot.py --start --symbol EURUSDc --strategy INGWE --dry-run
```

### Reset After Circuit Break
```bash
python skills/run_bot.py --reset --confirm
```

## Output
Updates `data/bot_status.json` → `bot_status`, `active_instances`, `last_updated`

## Caution
- Never start with `--strategy BOTH` during high-impact news days
- Always run `--status` first before `--start`
- After `--emergency-stop`, manually review open positions in MT5
