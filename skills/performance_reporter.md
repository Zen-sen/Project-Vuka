# SKILL: performance_reporter

> Generates daily, weekly, and monthly P&L reports with benchmark comparisons.

## Triggers
Use this skill when the user says:
- "Generate weekly report", "Monthly P&L summary"
- "How's Ingwe performing?", "Show the equity curve"
- "Compare this week vs last week"
- "Am I hitting my targets?", "Print the stats"
- "Export performance report"

## Description
Reads `data/trade_log.json` and produces structured reports across timeframes.
Flags performance against baselines stored in `Memory.md`.

## Commands

### Daily Report
```bash
python skills/performance_reporter.py --daily
python skills/performance_reporter.py --daily --date 2026-03-29
```

### Weekly Report
```bash
python skills/performance_reporter.py --weekly
```

### Monthly Report
```bash
python skills/performance_reporter.py --monthly
python skills/performance_reporter.py --monthly --month 2026-03
```

### Compare Two Periods
```bash
python skills/performance_reporter.py --compare --period-a 2026-02 --period-b 2026-03
```

### Export Equity Curve CSV
```bash
python skills/performance_reporter.py --equity-curve --export
```

## Output Files
Reports saved to: `data/reports/`
- `daily_YYYYMMDD.json`
- `weekly_WYYYY_WW.json`
- `monthly_YYYYMM.json`
- `equity_curve.csv`
