# SKILL: trade_log_analyzer

> Parses trade_log.json to surface insights about entry quality, session patterns, and ICT condition correlation.

## Triggers
Use this skill when the user says:
- "Analyze my trades", "Show me today's trades"
- "Which FVG setups worked best?"
- "Win rate by session", "Best performing day/hour"
- "Why did this trade lose?", "Show losing trades"
- "ICT condition correlation", "FVG vs OB performance"

## Description
Reads `data/trade_log.json` and slices performance data by session, strategy,
ICT confluence conditions, date range, and outcome. Surfaces actionable insights.

## Commands

### Summary (default)
```bash
python skills/trade_log_analyzer.py --summary
```

### Filter by Date Range
```bash
python skills/trade_log_analyzer.py --from 2026-03-01 --to 2026-03-29
```

### Filter by Session
```bash
python skills/trade_log_analyzer.py --session london
python skills/trade_log_analyzer.py --session ny
```

### Filter by Strategy
```bash
python skills/trade_log_analyzer.py --strategy INGWE
python skills/trade_log_analyzer.py --strategy SILVER_BULLET
```

### ICT Condition Breakdown
```bash
python skills/trade_log_analyzer.py --by-condition
```

### Show Only Losses (for review)
```bash
python skills/trade_log_analyzer.py --losses-only
```

### Export to CSV
```bash
python skills/trade_log_analyzer.py --summary --export csv
```

## Output
Prints a structured report to stdout. Optionally exports to
`data/reports/analysis_YYYYMMDD.csv`
