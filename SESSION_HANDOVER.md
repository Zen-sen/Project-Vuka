# Project Vuka — Session Handover
## Date: Aug 6-7, 2026

---

## Session Outcome (IMPORTANT — corrects prior handover)

Prior handover claimed *"Kronos doesn't use its AI model / fake heuristics / confidence always 90%"*. **This is now FALSE and outdated.** Live testing this session proved the modern codebase is healthy:

- `kronos_server.py` runs a **real transformer** forward pass via `run_inference()` (`kronos_server.py:216-344`), returns actual softmax probabilities.
- Live `/v1/predict-ict` returned **real, discriminating confidence (0.40-0.55)** and directionally coherent `agree` values (SELL in a down market, rejected BUY). Verified end-to-end.
- Kronos was **never the blocker** — it simply was never reached (0 Kronos rows in DB).

---

## ROOT CAUSE OF "NO TRADES" (verified, still current)
**0 trades ever.** The blocker was a stacked funnel, now mostly fixed:

| Blocker | Status | Fix applied (this session) |
|---|---|---|
| `P0_PHASE_BLOCKED:CHOP` (570×) | ✅ fixed | `config_v4.6.json`: `block_phases:["CHOP"]→[]`, CHOP → `caution_phases`, `adx_trend_min 25→18` |
| NY Open session whitelist missing | ✅ fixed earlier | commit `298243e` |
| M15-ADX 25 gate blocked EXPANSION | ✅ fixed | `ingwe.py:60-73` — bypass when circuit trending |
| `ADX_MIN_THRESHOLD` 25 | ✅ | `config.py:212` → 20 |
| HTF-bias hard-block | ✅ | `ingwe.py:194-200` → flag to Kronos instead of `continue` |
| Sweep only on live candle | ✅ | `ict.py:33-51` — check last 2 candles |
| **No FVG after sweep** | ⛔ **CURRENT BLOCKER** | market-driven; no code fix |

**Current gate position (08-07 02:17 SAST):** gates 1-11 (phase/session/ADX/HTF/sweep) ALL pass. **Block at gate 12 `detect_fvg()` → `No FVG within 5hr lookback. Ingwe waits...`** — requires a displacement+FVG gap *after* the sweep that the market hasn't produced yet. Kronos (gate 13) healthy and ready. **0 TRADE rows ever.**

---

## CRITICAL DEBUGGING GROUND TRUTHS (do not skip)
1. **Live DB = `src/vuka/core/vuka_trading.db`** (NOT root `vuka_trading.db`). Bots run `cwd=src/vuka/core` (`supervisor.py:23,95`). WAL files `.db-wal/.db-shm` prove live writes.
2. **Authoritative event source = `system_logs` table** (`database_manager.py:618` INSERT). Do NOT rely on `.log` files (stdout only).
3. **Gate order in `run_agent()` (`bot.py:894`)** is fixed — see trace table.
4. **Kronos is healthy** (`GET /health` → 200 `{status:ok,model_loaded:true}`). Endpoint `POST :8000/v1/predict-ict`.
5. **Report the FIRST GUARD line that stops each scan** + gate # + `bot.py` line.

---

## Improved Debug Prompts (calibrated to real architecture)

### Prompt 0 — "Why no trades?" (full trace)
1. **Process health** — list every running `python` process by argv: `vuka.ai.kronos_server` (port 8000), `supervisor.py`, `dashboard.py`, each `vuka.core.bot <SYM> <STRAT>`. Expected bots (supervisor.py:30-33): EURUSD-INGWE, GBPUSD-INGWE, EURUSD-SILVER_BULLET, GBPUSD-SILVER_BULLET. Flag missing. Report PID, uptime, CPU.
2. **Bot activity** — query **live** DB `src/vuka/core/vuka_trading.db`: `SELECT timestamp,level,component,message FROM system_logs WHERE component='<TAG>' ORDER BY timestamp DESC LIMIT 20;` (TAG e.g. `EURUSD_INGWE`).
3. **Blocking gate** — walk `bot.py:110-1000` gate order, quote the **exact GUARD line** + gate # + line. Gate order: (1) market open, (2) equity drawdown, (3) consec losses, (4) news/dead zone, (5) daily loss cap, (6) circuit breakers, (7) market phase, (8) session window+filter, (9) already-traded, (10) validate_candles, (11) sweep, (12) FVG, (13) Kronos veto.
4. **Market state** — read `data/market_circuit_{SYM}_{STRAT}.json` (phase/ADX/confidence/BOS). Cross-check SAST = UTC+2 (summer). Pull live MT5 bid/ask/spread. Determine if phase+killzone would allow a signal now.
5. **Confirm tradability** — `calculate_lot_size(atr * s.ATR_MULTIPLIER)` (`bot.py:1047`); confirm `s` is the shared singleton (`vuka/core/state.py:122`), not shadowed; confirm `KRONOS_VETO_GATE` enabled + `safety_mode` (VETO_SAFE blocks on any Kronos error).
6. **Conclusion** — expected vs found; the exact gate/setting; what must change.

### Prompt 1 — Kronos connectivity
- **Do NOT assume down.** Verify: `GET http://127.0.0.1:8000/health` → expect `{status:ok, model_loaded:true}`. Port `8000` owned by `vuka.ai.kronos_server` PID.
- Correct paths: `src/vuka/ai/kronos_guardian.py` (veto gate, circuit breaker; `heartbeat_interval` off by default), `kronos_server.py` (routes `/health`, `/v1/predict-ict`).
- **Common real cause of "offline":** Kronos reachable but `agree=false` (model opposes direction) → never an API/port issue when `/health` returns ok.
- Read circuiter state transitions (`CircuitBreaker) present; a timeout trips `VETO_SAFE`.

### Prompt 2 — Signals detected but `trade_executed:false`
- Focus downstream: sweep+FVG form → but stop. Check confluence threshold (`config.py:346-402`), `check_premium_discount_zone` (`risk/filters.py:82`), HTF bias flag path (`ingwe.py:194-200` now soft), DI filter (`+DI > -DI`), spread.
- The most common gap THIS build: sweep passes but **no displacement→FVG**, so execution branch never runs.

### Prompts 3-6 — data / cache / sessions / losses
- `validate_candles()` (`bot.py:451-495`), freshness thresholds, MT5 timestamps. HTF bias cache in `state.py:55` + `bot.py:58-88`, keyed by symbol, 1h TTL. Sessions in `get_current_session()`. Consecutive-loss / daily reset in `bot.py:87`, `portfolio.py`. — All refactor to live DB paths above and the shared singleton `s`.

### Prompts 7, 15-19 — circuit/health/anomalies/backtest
- Governor: `skills/trading_governor.py`. Anomalies: `health_monitor.py:138-283` (throttle thresholds, small scan history → false positives). Backtest vs live: `bot.py:648-768` replay. SL/position mgmt: `vuka/execution/orders.py`, `position_manager.py`. Error classification: `health_monitor.py:123-136`.

---

## Files-to-Check Cheat Sheet (avoid the root trap)
| Concern | Correct location |
|---|---|
| Live DB | `src/vuka/core/vuka_trading.db` (+WAL) — NOT root `vuka_trading.db` |
| Bot logs | `src/vuka/core/logs/{eurusd_ingwe,...}.log` (secondary) — NOT root `logs/` |
| Events | `system_logs` table in live DB |
| Config | `config_v4.6.json` (root) — governor reads via `BASE_DIR/config_v4.6.json` |
| Veto | `kronos_guardian.py`, `kronos_server.py` |
| Risk | `skills/trading_governor.py`, `risk/filters.py`, `risk/portfolio.py` |
| Strategy | `strategies/ingwe.py`, `silver_bullet.py`, `london_open.py`, `ict_m1.py` |
| MTF/market | `market_structure/ict.py`, `core/monitor.py` |
| State | `core/state.py` (`s`) | ✅