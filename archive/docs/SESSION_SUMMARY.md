# Project Vuka - Session Summary
**Date:** 2026-04-08  
**Bot Version:** v4.4  
**Status:** Active (v4.4 Deployed)

---

## Account Overview

| Metric | Value |
|--------|-------|
| Account | 161470960 |
| Broker | Exness MT5 (USC Cent Account) |
| Balance | $4,250.87 |
| Net P&L (All Time) | +$625.87 |
| Total Closed Trades | 42 |

---

## Performance by Strategy

| Strategy | Trades | Wins | Losses | Win Rate | Net P&L | Profit Factor |
|----------|--------|------|--------|----------|---------|---------------|
| EURUSD_INGWE | 22 | 10 | 11 | 45.5% | +$526.47 | 2.85 |
| GBPUSD_INGWE | 14 | 4 | 10 | 28.6% | +$108.20 | 1.32 |
| GBPUSD_SILVER_BULLET | 3 | 1 | 1 | 33.3% | +$48.80 | 2.38 |
| EURUSD_SILVER_BULLET | 3 | 0 | 2 | 0.0% | -$57.60 | 0.00 |
| **TOTAL** | **42** | **15** | **24** | **35.7%** | **+$625.87** | **1.87** |

---

## v4.4 Changes Implemented (2026-04-08)

### FIX-1: Duplicate Entry Prevention
- Added `has_open_position()` function
- Checks for existing positions by magic number before placing trades
- Prevents over-leveraged duplicate entries

### FIX-2: Persistent Loss Tracking
- Loss counter now persists across days via sessions file
- Bot pauses after 2 consecutive losses
- Resets counter on any win

### FIX-3: GBPUSD London Open Disable
- London Open session disabled for GBPUSD_INGWE
- Historical win rate: 0% (0W/5L)
- Reduces exposure to underperforming session

### FIX-4: SL Movement Tracking
- All trailing SL moves logged to `sl_moves_{symbol}_{strategy}.json`
- Enables analysis of trailing SL effectiveness

---

## Active Killzones (SAST = UTC+2)

| Session | Time | GBPUSD_INGWE | EURUSD_INGWE |
|---------|------|--------------|--------------|
| Asian | 02:00-06:00 | Active | Active |
| London Open | 09:00-12:00 | **Disabled** | Active |
| New York Open | 15:00-18:00 | Active | Active |

---

## Risk Parameters

| Parameter | Value |
|-----------|-------|
| Risk per Trade | 1.0% |
| Hard Lot Cap | 0.2 lots |
| Daily Loss Limit | $40.00 |
| Trailing SL | 1:1 to BE, 1:2 to 1:1 |

---

## Pending Tasks

- [ ] EURUSD_SILVER_BULLET investigation (sample size too small)
- [ ] Backtest validation of v4.4 changes
- [ ] Monitor GBPUSD_INGWE improvement after London Open disable
- [ ] Document performance benchmarks

---

## Next Steps

1. Monitor performance for 1-2 weeks with v4.4
2. Compare GBPUSD_INGWE performance (before vs after London Open disable)
3. Re-evaluate EURUSD_SILVER_BULLET after more trades close
4. Run backtest to validate changes before further optimization

---

*Generated: 2026-04-08 23:35 SAST*
