# Trade History Analysis - Account 161470960
## Period: 2026.02.27 - 2026.03.12 (13 days)

---

## SUMMARY

| Metric | Value |
|--------|-------|
| **Total Trades** | 18 |
| **Wins** | 12 |
| **Losses** | 6 |
| **Win Rate** | 66.7% |
| **Net P&L** | +$834.43 USC |
| **Starting Balance** | $646.00 |
| **Final Balance** | $4,480.43 |
| **Return** | +593.6% |

---

## ANSWERS TO YOUR 3 QUESTIONS

### 1. Trade Frequency (75 trades/30 days?)

| Period | Actual Trades |
|--------|---------------|
| 13 days | 18 trades |
| 30 days (projected) | ~41 trades |

**Reality Check:** 
- Your projection of 75 trades/month is **too optimistic**
- Actual: ~41 trades/month (based on 13-day sample)
- Your v4.0 has strict confluence filters + session limits that prevent high frequency

---

### 2. Limit Orders at FVG 50% vs Market Entries

| Order Type | Count | Fill Rate |
|------------|-------|-----------|
| Market Orders | 18 | 100% (immediate) |
| Limit Orders | 0 | N/A |

**v4.0 Issue:** 
- Your trades show **all market orders** (comments: "Ingwe v3.1 EURUS", "ICT Killzone Age")
- This is from BEFORE v4.0 limit order upgrade
- **No limit order fill rate data available yet** - need to run v4.0 live

---

### 3. Trailing SL on Volatile Days

| Trade | Date | Outcome | Trailing SL Triggered? |
|-------|------|---------|------------------------|
| 6 x EURUSD SELL | Mar 2-3 | 4W, 2L | NO |
| 4 x EURUSD SELL | Mar 3 | 4W | NO |
| 2 x EURUSD BUY | Mar 10 | 1W, 1L | NO |
| 2 x GBPUSD | Mar 10-11 | 1W, 1L | NO |

**Analysis:**
- **No trailing SL was used** in these trades
- Your comments show `[sl X.XXXXX]` or `[tp X.XXXXX]` - direct SL/TP hits
- Trailing SL feature added in v3.9.5, but not triggered in this sample
- Capital protection: SL at original levels, no adjustment to BE

---

## DETAILED TRADE LIST

| # | Date | Symbol | Direction | Volume | Entry | SL | TP | Result | P&L |
|---|------|--------|-----------|--------|-------|-----|-----|--------|-----|
| 1 | Feb 27 | EURUSDc | SELL | 0.08 | 1.18058 | 1.18147 | 1.17793 | SL | -7.12 |
| 2 | Mar 2 | EURUSDc | SELL | 0.03 | 1.17864 | 1.18080 | 1.17217 | TP | +19.41 |
| 3 | Mar 2 | EURUSDc | SELL | 0.05 | 1.17169 | 1.17346 | 1.16638 | TP | +26.55 |
| 4 | Mar 2 | EURUSDc | SELL | 0.05 | 1.17182 | 1.17359 | 1.16651 | TP | +26.55 |
| 5 | Mar 2 | EURUSDc | SELL | 0.31 | 1.16929 | 1.17047 | 1.16575 | SL | -36.58 |
| 6 | Mar 2 | EURUSDc | SELL | 0.31 | 1.16933 | 1.17050 | 1.16578 | SL | -36.27 |
| 7 | Mar 3 | EURUSDc | SELL | 0.35 | 1.16696 | 1.16799 | 1.16388 | TP | +107.80 |
| 8 | Mar 3 | EURUSDc | SELL | 0.35 | 1.16695 | 1.16799 | 1.16388 | TP | +107.45 |
| 9 | Mar 3 | EURUSDc | SELL | 0.25 | 1.16015 | 1.16198 | 1.15465 | TP | +137.50 |
| 10 | Mar 3 | EURUSDc | SELL | 0.24 | 1.16077 | 1.16269 | 1.15506 | TP | +137.04 |
| 11 | Mar 3 | EURUSDc | SELL | 0.48 | 1.16186 | 1.16262 | 1.15921 | TP | +127.20 |
| 12 | Mar 5 | EURUSDc | SELL | 0.10 | 1.16229 | 1.16383 | 1.15768 | TP | +46.10 |
| 13 | Mar 10 | EURUSDc | BUY | 0.20 | 1.16139 | 1.16004 | 1.16519 | TP | +76.00 |
| 14 | Mar 10 | GBPUSDc | BUY | 0.20 | 1.34205 | 1.34051 | 1.34628 | TP | +84.60 |
| 15 | Mar 10 | EURUSDc | BUY | 0.20 | 1.16293 | 1.16124 | 1.16767 | SL | -33.80 |
| 16 | Mar 10 | GBPUSDc | BUY | 0.20 | 1.34482 | 1.34274 | 1.35066 | SL | -41.60 |
| 17 | Mar 11 | EURUSDc | BUY | 0.20 | 1.16162 | 1.16039 | 1.16500 | SL | -24.60 |
| 18 | Mar 11 | GBPUSDc | SELL | 0.20 | 1.34299 | 1.34520 | 1.33603 | TP | +139.20 |

---

## CONCLUSIONS

| Question | Answer |
|----------|--------|
| **75 trades/month?** | NO - actual ~41/month |
| **Limit order fill rate?** | NO DATA - need v4.0 live run |
| **Trailing SL protects capital?** | NOT TESTED - not used in this period |
