> **⚠️ HISTORICAL — March 29, 2026.** This reflects an older state of the project. See `AGENT.md` and `Memory.md` for current structure and performance.

# Project Vuka - Session Summary

## Current State (Mar 29, 2026)

### What We've Done
1. Fixed backtester to handle MT5 CSV format
2. Updated strategy to generate more trades (FVG-based)
3. Added instant fill simulation
4. Tested with 200 Monte Carlo runs

### Latest Backtest Results (10 months data)
- **Orders Placed:** 46
- **Trades:** 43
- **Win Rate:** 51.2%
- **Net P&L:** +$495
- **Return:** +4.95%
- **Trailing SL Moves:** 28

### Goal Analysis
- **Starting Balance:** $42
- **Target:** $250 in 60 days
- **Reality:** ~$6-10 profit in 60 days with current strategy

### Key Files
- `skills/run_backtest.py` - Main backtester
- `skills/risk_monitor.py` - Risk monitoring
- `skills/run_monte_carlo.py` - Monte Carlo simulation
- `data/sessions/EURUSDc_M15_202505010000_202602202145.csv` - 10 months data

### Last Command Run
```bash
.venv/Scripts/python.exe skills/run_backtest.py --csv "data/sessions/EURUSDc_M15_202505010000_202602202145.csv" --speed 100
```

### Next Steps to Consider
1. Test with H1 timeframe for more trades
2. Adjust risk to 3% per trade
3. Add more aggressive entry signals
4. Accept smaller realistic returns

### Config
- Risk: 1.0%
- RRR: 2.0
- ATR Multiplier: 1.5
