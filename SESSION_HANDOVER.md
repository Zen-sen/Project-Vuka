# Project Vuka — Session Handover
## Date: Aug 7, 2026

---

## ROOT CAUSE OF "NO TRADES" FOUND & FIXED (this session)

**0 trades ever — and it was a silent crash, not a filter.** This session found the real, final blocker that the prior handover missed:

| Blocker | What it was | Status / Fix (this session) |
|---|---|---|
| `NameError: name 'MT5_RETRY_ATTEMPTS' is not defined` | `bot.py:439` in `mt5_fetch_with_retry()`, reached via `get_h1_trend()` (`bot.py:550`) ← `evaluate_ingwe()` (`ingwe.py:99`). Every qualifying setup crashed here. | ✅ **FIXED** — commit `438c054`. Now reads `s.MT5_RETRY_ATTEMPTS` / `s.MT5_RETRY_DELAY_SEC` off the shared singleton `s` (pattern already proven in `risk/portfolio.py:42-52`). Bare-global refs were never bound at call time. |
| Why it was invisible | `TickEngine` swallows callback exceptions to stderr (`tick_engine_v5.py:325`) — **never written to `system_logs`**. | ⚠️ The prior handover said *"Do NOT rely on `.log` files — `system_logs` is authoritative."* **That guidance is what hid this crash.** Both matter: `system_logs` for gates, `src/vuka/core/logs/*.log` (captured by supervisor stdout redirection) for TickEngine-stderr tracebacks. |
| Earlier `NameError: calculate_adx_wilder is not defined` | same crash site, earlier in the run | ✅ resolved earlier via import at `ingwe.py:12` |
| **No FVG after sweep** | the previous "current blocker" claim (`02:17 SAST`) | ❌ was real but **transient** — sweeps DID pass at 16:15/16:17 SAST and the setups then hit the NameError. Now that the crash is fixed this gate can actually be reached. |

### Prior-session fixes (still valid, still in place)
`block_phases:["CHOP"]→[]` + CHOP→`caution_phases` + `adx_trend_min 25→18` (`config_v4.6.json`, commit `a27425d`); NY Open whitelist (`298243e`); M15-ADX bypass (`ingwe.py:60-73`); `ADX_MIN_THRESHOLD`→20 (`config.py`); HTF bias soft-flag (`ingwe.py:194-200`); sweep last-2-candles (`ict.py:33-51`).

---

## LIVE STATE (verified after restart — 08-07 23:24 SAST)
- **All 4 bots restarted** via supervisor command queue (root DB) with fresh code, new PIDs: EURUSD-INGWE `3136`, GBPUSD-INGWE `34084`, GBPUSD-SILVER_BULLET `35816`, EURUSD-SILVER_BULLET `40380`. Supervisor `10216`, Kronos `21260`, dashboard `6668` all alive.
- Post-restart: **0 errors / 0 warnings** in `system_logs`; `MT5 connected`; both strategies in event loop. `NameError` gone from the crash path.
- `TRADES` still 0 — but the execution path can now survive `get_h1_trend()` and reach `place_trade()` for the first time. Next real confirmation = a qualifying sweep+FVG setup closing a row in `TRADES`.
- Note: `system_logs.timestamp` is **UTC** (21:24 = 23:24 SAST).

---

## CRITICAL DEBUGGING GROUND TRUTHS (do not skip)
1. **Live DB = `src/vuka/core/vuka_trading.db`** (NOT root `vuka_trading.db`). Bots run `cwd=src/vuka/core` (`supervisor.py:23,75`). WAL files `.db-wal/.db-shm` prove live writes. ⚠️ **Supervisor's OWN DB is the ROOT `vuka_trading.db`** (its cwd is the project root) — supervisor command queue lives THERE, not in the live DB.
2. **Gates/events = `system_logs` table** (`database_manager.py:618` INSERT). **BUT uncaught TickEngine/stderr tracebacks = `src/vuka/core/logs/*.log` only** (supervisor captures stdout+stderr). **Check BOTH.** This is exactly how the `MT5_RETRY_ATTEMPTS` NameError stayed hidden for a day.
3. **Gate order in `run_agent()` (`bot.py:894`)** is fixed — see trace table.
4. **Kronos is healthy** (`GET /health` → 200 `{status:ok,model_loaded:true}`). Endpoint `POST :8000/v1/predict-ict`.
5. **Report the FIRST GUARD line that stops each scan** + gate # + `bot.py` line.
6. **`system_logs.timestamp` is UTC** (SAST = UTC+2).

---

## Improved Debug Prompts (calibrated to real architecture)

### Prompt 0 — "Why no trades?" (full trace)
1. **Process health** — list every running `python` process by argv: `vuka.ai.kronos_server` (port 8000), `supervisor.py`, `dashboard.py`, each `vuka.core.bot <SYM> <STRAT>`. Expected bots (supervisor.py:30-33): EURUSD-INGWE, GBPUSD-INGWE, EURUSD-SILVER_BULLET, GBPUSD-SILVER_BULLET. Flag missing. Report PID, uptime, CPU.
2. **Bot activity** — query **live** DB `src/vuka/core/vuka_trading.db`: `SELECT timestamp,level,component,message FROM system_logs WHERE component='<TAG>' ORDER BY timestamp DESC LIMIT 20;` (TAG e.g. `EURUSD_INGWE`).
2b. **Silent crash check (P0)** — grep `src/vuka/core/logs/*.log` for `NameError` / `Traceback` / `Error in callback`. The TickEngine swallows exceptions to stderr; only these files show them. Zero errors there + no trades = filters/logic; errors there = code bug.
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
- **Before blaming confluence/FVG, run the silent-crash check (Prompt 0 step 2b)** — the `MT5_RETRY_ATTEMPTS` NameError (fixed in `438c054`) was the real reason no execution happened; swept+setup scans were dying at `get_h1_trend()`. Expected lookbacks: sweep on last 2 candles, FVG within 5hr. If those pass but no trade, the remaining gap is confluence/premium-discount/Kronos — NOT a crash.

### Prompts 3-6 — data / cache / sessions / losses
- `validate_candles()` (`bot.py:451-495`), freshness thresholds, MT5 timestamps. HTF bias cache in `state.py:55` + `bot.py:58-88`, keyed by symbol, 1h TTL. Sessions in `get_current_session()`. Consecutive-loss / daily reset in `bot.py:87`, `portfolio.py`. — All refactor to live DB paths above and the shared singleton `s`.

### Prompts 7, 15-19 — circuit/health/anomalies/backtest
- Governor: `skills/trading_governor.py`. Anomalies: `health_monitor.py:138-283` (throttle thresholds, small scan history → false positives). Backtest vs live: `bot.py:648-768` replay. SL/position mgmt: `vuka/execution/orders.py`, `position_manager.py`. Error classification: `health_monitor.py:123-136`.

---

## Files-to-Check Cheat Sheet (avoid the root trap)
| Concern | Correct location |
|---|---|
| Live DB | `src/vuka/core/vuka_trading.db` (+WAL) — NOT root `vuka_trading.db`. **Supervisor queue live DB = root `vuka_trading.db`** |
| Bot logs | `src/vuka/core/logs/{eurusd_ingwe,...}.log` (⚠️ **CRITICAL for TickEngine-swallowed tracebacks**, NOT just secondary) — NOT root `logs/` |
| Events | `system_logs` table in live DB |
| Config | `config_v4.6.json` (root) — governor reads via `BASE_DIR/config_v4.6.json` |
| Veto | `kronos_guardian.py`, `kronos_server.py` |
| Risk | `skills/trading_governor.py`, `risk/filters.py`, `risk/portfolio.py` |
| Strategy | `strategies/ingwe.py`, `silver_bullet.py`, `london_open.py`, `ict_m1.py` |
| MTF/market | `market_structure/ict.py`, `core/monitor.py` |
| State | `core/state.py` (`s`) | ✅