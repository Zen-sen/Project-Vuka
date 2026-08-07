# Project Vuka — Calibrated Debug Prompts

> These prompts are calibrated to Vuka's real architecture (Aug 2026 session). Every prompt assumes the ground-truth rules below.

---

## Golden Rules (read first — they save hours)

1. **Live DB = `src/vuka/core/vuka_trading.db`, NOT root `vuka_trading.db`.** Bots run with `cwd=src/vuka/core` (`supervisor.py:23,95`). WAL files `.db-wal/.db-shm` prove live writes.
2. **Live logs = `src/vuka/core/logs/{eurusd_ingwe,...}.log`, NOT root `logs/`.** They are secondary (stdout only).
3. **`system_logs` table is the authoritative event source** — `UnifiedLogger` writes every event there (`database_manager.py:618`). Query it, don't grep files.
4. **Gate order in `run_agent()` (`bot.py:894`) is fixed** — report the FIRST GUARD line + gate # + `bot.py` line that stops each scan.
5. **Kronos is healthy** — `GET http://127.0.0.1:8000/health` → `{"status":"ok","model_loaded":true}`. A "Kronos offline" flag is usually `agree=false` (model opposes direction), not connectivity.
6. **SAST = UTC+2** in summer; Exness server is UTC+3.

---

## PROMPT 0 — "Why no trades?" (primary diagnostic, full trace)

**Task:** Trace why no trade has been placed. Do a full gate-by-gate diagnostic and report the first blocking gate.

1. **Process health.** List every running `python` process and identify its role by argv: `vuka.ai.kronos_server` (Kronos AI, port 8000), `supervisor.py`, `dashboard.py`, and each `vuka.core.bot <SYM> <STRAT>`. Expected bots (`supervisor.py:30-33`): **EURUSD-INGWE, GBPUSD-INGWE, EURUSD-SILVER_BULLET, GBPUSD-SILVER_BULLET**. Report symbol+strategy, PID, uptime, CPU%. Flag any bot NOT running.

2. **Bot activity.** Query the LIVE DB:
   ```sql
   SELECT timestamp, level, component, message FROM system_logs
   WHERE component = 'EURUSD_INGWE'
   ORDER BY timestamp DESC LIMIT 20;
   ```
   Repeat for GBPUSD_INGWE, EURUSD_SILVER_BULLET, GBPUSD_SILVER_BULLET. If the DB is empty but `src/vuka/core/logs/{tag}.log` has entries → the DB write path is broken; say so.

3. **Blocking gate.** Trace `run_agent()` (`bot.py:894`). Report the FIRST returning gate, quoting the exact GUARD log line:
   1. `is_market_open()` — weekend guard
   2. `check_equity_drawdown()` — drawdown breaker
   3. `check_consecutive_losses()` — 3-loss cooldown
   4. `is_in_news_blackout()` / `is_in_dead_zone()`
   5. `daily_pnl <= -MAX_DAILY_LOSS` — daily loss cap
   6. `check_circuit_breakers()` — weekly/risk caps
   7. `check_market_phase()` — phase filter (CHOP now = *Caution*, not Blocked)
   8. session window + `check_session()` — killzone/session filter
   9. `"Already traded {session} today"` — dedupe
   10. `validate_candles()` — stale/frozen data
   11. `detect_liquidity_sweep()` — `No sweep detected. Ingwe waits...`
   12. `detect_fvg()` / `detect_immediate_fvg()` — `No FVG within 5hr lookback. Ingwe waits...`
   13. **Kronos veto** — `KronosVetoGate.validate()` in strategy evaluator (`ingwe.py:249`)

4. **Market state.** Read `data/market_circuit_{SYM}_{STRAT}.json` for phase/ADX/confidence/BOS. Cross-check SAST time. Identify live killzone. Pull live EURUSD/GBPUSD bid/ask + spread from MT5. Report whether phase+killzone would allow a signal now. Warn "STALE" if ADX is old.

5. **Confirm tradability.** Verify `lot_size = calculate_lot_size(atr * s.ATR_MULTIPLIER)` (`bot.py:1047`); confirm `s` is the shared singleton (`vuka/core/state.py:122`), not shadowed; confirm `KRONOS_VETO_GATE` enabled + `safety_mode` (VETO_SAFE blocks on any Kronos error).

6. **Conclusion.** One paragraph: expected vs real bug; the exact gate/setting; what must change for a trade to fire.

---

## PROMPT 1 — Kronos connectivity / veto

Do NOT assume Kronos is down.
1. `GET http://127.0.0.1:8000/health` → expect `{"status":"ok","model_loaded":true}`. Port 8000 must be owned by `vuka.ai.kronos_server`.
2. Endpoint used: `POST /v1/predict-ict` (`kronos_server.py:636`). Guardian derives health path via `.replace('/v1/predict-ict','/health')` (`kronos_guardian.py:40`).
3. Read circuit state (`kronos_guardian.py` CircuitBreaker: failure_threshold=3, recovery 30s). A timeout trips VETO_SAFE → blocks.
4. **If /health is ok but trades flagged "Kronos offline", the real cause is `agree=false`, not connectivity.** Verify by POSTing a realistic payload and reading `confidence`/`agree`.
5. `heartbeat_interval` is 0 (disabled) unless config `heartbeat.enabled=true`.

---

## PROMPT 2 — Signals detected but `trade_executed:false`

**Scope:** Sweep+FVG form but no trade. Check downstream of the strategy evaluators (`ingwe.py`, `silver_bullet.py`), order placement (`bot.py:1050-1065`), `vuka/execution/orders.py`.
- Confluence threshold (`config.py:346-402`): score must reach bar (`ingwe.py:93-94`). Weights: trend +30, FVG +30, zone +15, spread +10, level-sweep +5, BOS +5, HTF-bias +15, asymmetry +10; cap 120. Bar = `max(40, min(90, base × session_multiplier ± phase_mod))`; ADX<min forces 80.
- `check_premium_discount_zone` (`risk/filters.py:82`): BUY needs price ≤ mid of last 20 M15.
- HTF bias: `ingwe.py:194-200` now SOFT (flags Kronos, no hard block). DI filter: `+DI > -DI`.
- **Most common gap this build: sweep passes but no displacement→FVG, so execution never runs.**

---

## PROMPTS 3-20 — subsystem-specific

All must use the **live DB** (`src/vuka/core/vuka_trading.db`) + shared singleton `s`.

3. **Data validation** — `validate_candles()` (`bot.py:998`; freshness `bot.py:460`), MT5 retry, frozen price.
4. **HTF bias cache** — `state.py:55`, keyed by symbol, 1h TTL, invalidate on config change.
5. **Session persistence** — `get_current_session()`, save/load (`bot.py:500-540`), DB vs JSON fallback.
6. **Consecutive-loss tracking** — daily/reset logic (`bot.py:787-797`), `risk/portfolio.py`.
7. **Circuit-breaker triggers** — `skills/trading_governor.py`; validate P&L calc, thresholds.
8. **Dashboard perf** — `dashboard.py` refresh, `HealthMonitor.detect_anomalies()`.
9. **Pattern veto** — `skills/concept_tracker.py`; needs ≥15 samples, threshold 0.40.
10. **SQLite consolidation** — WAL mode, concurrency, `PRAGMA integrity_check`.
11. **Timeline sync** — `now_sast()`, SA_OFFSET, DST, Exness UTC+3 server time.
12. **Confluence discrepancies** — session performance JSON (`session_performance.json`: asian sell 1.00, london sell 0.00 — uncalibrated, 0 trades).
13. **Market circuit phase** — `core/monitor.py`, MTF data, BOS (`ict.py:190`).
14. **Supervisor mgmt** — process spawn, health check (30s), restart delay (60s), STOP/START via `push_command`.
15. **Health false-positives** — `health_monitor.py:138-283`, small scan history → false anomalies.
16. **Backtest vs live** — `bot.py:648-768` replay, CSV format, timing.
17. **Order modifications** — `execution/orders.py`, SL/trailing logic.
18. **Position management** — `execution/position_manager.py`, profit/SL movement.
19. **Error classification** — `health_monitor.py:123-136`, keyword matching.
20. **Skill/import integration** — circular imports, `__init__.py`, dependency resolution.

---

## Known Session Fixes (so far — do not re-fix)

| Blocker | Status | Fix |
|---|---|---|
| `P0_PHASE_BLOCKED:CHOP` (570×) | fixed | `config_v4.6.json`: `block_phases:["CHOP"]→[]`, CHOP→`caution_phases`, `adx_trend_min 25→18` |
| NY Open session whitelist | fixed | commit `298243e` |
| M15-ADX 25 gate blocked EXPANSION | fixed | `ingwe.py:60-73` bypass when circuit trending |
| `ADX_MIN_THRESHOLD` 25 | fixed | `config.py:212` → 20 |
| HTF-bias hard-block | fixed | `ingwe.py:194-200` → flag to Kronos |
| Sweep only on live candle | fixed | `ict.py:33-51` — check last 2 candles |
| **No FVG after sweep** | **CURRENT** | market-driven, no code fix |
