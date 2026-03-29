# SKILL: backtester

> Simulates INGWE and SILVER_BULLET against historical OHLCV data from MT5.

## Triggers
Use this skill when the user says:
- "Backtest INGWE on EURUSD", "Run a backtest"
- "How did this strategy perform last year?"
- "Test the ADX change historically"
- "What's the max drawdown on a bad month?"
- "Validate before going live"

## Description
Fetches historical data from MetaTrader 5, simulates entry/exit logic,
applies the Guardian risk layer, and outputs a full performance report.
Always backtest BEFORE deploying any parameter change to live trading.

## Commands

### Run Backtest (INGWE, EURUSD, last 12 months)
```bash
python skills/backtester.py --run --symbol EURUSD --strategy INGWE
```

### Custom Date Range
```bash
python skills/backtester.py --run --symbol EURUSD --strategy INGWE \
  --from 2025-01-01 --to 2026-01-01
```

### Test Both Strategies
```bash
python skills/backtester.py --run --symbol EURUSD --strategy BOTH
```

### Show Last Backtest Results
```bash
python skills/backtester.py --results
```

### Export Results to CSV
```bash
python skills/backtester.py --run --symbol EURUSD --strategy INGWE --export
```

## Anti-Overfitting Rules
1. Never optimize on the same period you'll trade live
2. Require minimum 100 trades for valid results
3. Out-of-sample win rate must be within 15% of in-sample
4. Prefer consistent performance across sessions over peak metrics

## Output Files
- `data/backtest_results.json` — Full trade-by-trade log
- `data/backtest_summary.json` — Aggregated metrics
