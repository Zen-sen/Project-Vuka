# Memory.md — Persistent Agent Memory

---

## Current State
- **Version:** v6.1
- **Account Equity:** $4,248.51
- **Total Known PnL:** +$224.60 (from 6 known outcomes out of 55 trades)
- **Session Date:** 2026-07-19 (all sessions reset)

## Active Instances
- (none — all killed for fresh start)

## Known Issues & Backlog

### HIGH PRIORITY
- [ ] **89% of trades have null PnL** — 49/55 trades have `pnl_usd: null`. PnL backfill now has fallback matching for `position_id=0` (v6.1 fix), but historical trades won't retroactively backfill since the MT5 position history may no longer be available.
- [ ] **Kronos confidence range (0.36–0.51)** — Below the 0.40 threshold on some trades. BUY threshold of 0.35 explains some, but SELL trades at 0.36 should be blocked. Monitor.
- [ ] **Backlog of 49 trades** needs PnL manually fetched from MT5 history.

### MEDIUM PRIORITY
- [ ] **SILVER_BULLET and ICT_M1 strategies** — Near-zero activity (4 trades combined). Consider retiring or investigating why no signals fire.
- [ ] **Performance reporting tools** — `skills/performance_reporter.py` and `skills/trade_log_analyzer.py` now have `data/trade_log.json` available.
- [ ] **Daily PnL tracking** — `get_daily_pnl()` works per-instance but doesn't write back to trade logs.

### LOW PRIORITY
- [ ] **Memory.md should be updated after each session** — Currently manual.
- [ ] **Add `--backfill-pnl` CLI flag** to scan MT5 history for all trades with null PnL and backfill them.

## Backtest Performance (v5.5, Jan–May 2026, from AGENT.md)

| Metric | EURUSD M15 | GBPUSD M15 |
|--------|-----------|-----------|
| Win Rate | 56.0% | 50.0% |
| Net P&L | +$305.83 | +$337.15 |
| Return | +3.06% | +3.37% |

## Config (config_v4.6.json)
- Veto Threshold: 0.40 (BUY: 0.35)
- Mode: enforced
- Safety Mode: ALLOW_SAFE
- Daily Loss Limit: $50.00
- Weekly Trade Cap: 10
- Allowed Sessions: Asian, London Close, London Open

---

*Last updated: 2026-07-19*
