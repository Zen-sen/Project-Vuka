# SKILL: kronos_diagnostics

> Audits the Kronos AI server, veto gate decisions, and port health. Designed to diagnose "no trades" periods and API instability.

## Triggers

Use this skill when:
- "Why are there no trades?"
- "Is Kronos running?"
- "Check the veto log"
- "Why is port 8000 blocked?"
- "What's the Kronos confidence trend?"
- "Run a full system diagnostic"

## Description

This skill runs `kronos_diagnostics.py` to inspect every layer of the Kronos veto system: API health, port binding, veto decision patterns, circuit breaker state, and configuration. Use it before restarting services or when diagnosing trade inactivity.

## Pre-Flight

- Python environment with `requests` installed
- Run from the project root (`Project Vuka/`)

## Commands

### Quick Summary (all checks at once)
```bash
python skills/kronos_diagnostics.py --all
```

### API Health
```bash
python skills/kronos_diagnostics.py --health
```

### Port 8000 Check
```bash
python skills/kronos_diagnostics.py --port
```

### Veto Log Analysis (last 7 days)
```bash
python skills/kronos_diagnostics.py --veto --days 7
```

### Circuit Breaker State
```bash
python skills/kronos_diagnostics.py --circuit
```

### Current Configuration
```bash
python skills/kronos_diagnostics.py --config
```

### Quick Go/No-Go Summary
```bash
python skills/kronos_diagnostics.py --summary
```

## Output

- **--health**: Returns HTTP status and response body from `/health`
- **--port**: Shows if port 8000 is LISTENING and which PID holds it
- **--veto**: Counts Allowed vs Vetoed vs Errors, with last 5 of each type and confidence/reason
- **--circuit**: Shows circuit breaker state, safety mode, current threshold
- **--config**: Dumps all veto_gate and heartbeat settings from config_v4.6.json
- **--summary**: 4-point checklist (API, Port, ALLOW_SAFE mode, Heartbeat)

## Typical Flow

1. User reports no trades
2. Run `--summary` — often reveals port conflict or Kronos unreachable
3. Run `--port` — confirms zombie process on :8000
4. Run `--veto --days 14` — shows whether trades were vetoed or API was down
5. Kill zombie / restart Kronos / confirm recovery with `--health`
