# SKILL: risk_monitor

> Real-time and retrospective risk exposure tracking. Guards the Guardian layer.

## Triggers
Use this skill when the user says:
- "Check my risk exposure", "What's the drawdown?"
- "Am I near the circuit breaker?", "Validate this trade size"
- "Calculate lot size for X pip SL"
- "Risk report for today", "Show open trade risk"
- "Check for loss streak"

## Description
Reads `data/sessions_today.json` and `data/trade_log.json` to evaluate current exposure,
validate lot sizes, and detect circuit breaker conditions.

## Commands

### Full Risk Report
```bash
python skills/risk_monitor.py --report
```

### Lot Size Calculator
```bash
python skills/risk_monitor.py --lot-size --balance 10000 --sl-pips 10
python skills/risk_monitor.py --lot-size --balance 10000 --sl-pips 15 --risk 0.5
```

### Drawdown Check
```bash
python skills/risk_monitor.py --drawdown
```

### Streak Check
```bash
python skills/risk_monitor.py --streak
```

### Full Validation (run before each trade)
```bash
python skills/risk_monitor.py --validate
```

## Output
Prints risk status with alert levels: ✅ OK | ⚠️ WARNING | 🔴 CRITICAL | 🚨 CIRCUIT BREAK
