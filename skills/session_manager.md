# SKILL: session_manager

> Manages sessions_today.json — the daily heartbeat file of Ingwe.

## Triggers
Use this skill when the user says:
- "Reset today's session", "Show session state"
- "Is the session locked?", "Clear daily counters"
- "New trading day", "Unlock the bot"
- "Why isn't Ingwe trading?", "Check session file"
- "Schedule the daily reset"

## Description
Reads, writes, validates, and resets `data/sessions_today.json`.
Run `--validate` before each trading day to catch stale state.
The `--daily-reset` should be triggered at 00:00 UTC automatically.

## Commands

### Read Current State
```bash
python skills/session_manager.py --read
```

### Daily Reset (new trading day)
```bash
python skills/session_manager.py --daily-reset
```

### Lock Session Manually
```bash
python skills/session_manager.py --lock --reason "manual pause"
```

### Unlock Session
```bash
python skills/session_manager.py --unlock --authorized-by "Rox"
```

### Validate Integrity
```bash
python skills/session_manager.py --validate
```

### Mark Session Complete
```bash
python skills/session_manager.py --complete-session london
```

## Kill Zone Reference (SAST = UTC+2)

| Session       | UTC        | SAST       |
|---------------|------------|------------|
| Asian Open    | 00:00–03:00| 02:00–05:00|
| London Open   | 07:00–10:00| 09:00–12:00|
| NY Open       | 12:00–15:00| 14:00–17:00|
| SB Window 1   | 03:00–04:00| 05:00–06:00|
| SB Window 2   | 10:00–11:00| 12:00–13:00|
| SB Window 3   | 14:00–15:00| 16:00–17:00|
