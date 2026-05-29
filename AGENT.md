# AGENT.md — Agent Ingwe
**Project Vuka | ICT Forex Trading System**

---

## Identity

**Name:** Ingwe (isiZulu: *Leopard*)
**Role:** Autonomous ICT-based Forex Trading Agent
**Platform:** MetaTrader 5 via Python (MT5 bridge)
**Symbols:** EURUSD (`EURUSDc`), GBPUSD (`GBPUSDc`)
**Strategies:** `INGWE` (core ICT model), `SILVER_BULLET` (time-specific precision entries)

> *The leopard does not chase — it waits, reads the terrain, and strikes with precision.*

---

## Core Capabilities

| Capability | Description |
|---|---|
| **Session Awareness** | Tracks Asian, London Open, New York Open kill zones |
| **Structure Analysis** | Identifies FVGs, Order Blocks, Breaker Blocks |
| **Entry Logic** | Market orders at FVG sweep with confluence stack |
| **Risk Control** | 1% risk per trade, 1:3 RRR, daily drawdown circuit breaker |
| **Trailing SL** | Dynamic stop management (0.5:1 → BE, 1:2 → 1:1) |
| **Daily P&L Tracking** | Real-time drawdown and profit monitoring via SQLite |
| **News Blackout** | Pauses trading during high-impact news events |
| **Kronos AI Veto** | Transformer-based trade validation gate |
| **Live Dashboard** | TUI dashboard with fleet status, logs, and command dispatch |

---

## Strategies

### INGWE Strategy
- **Timeframes:** HTF bias (H4/D1), Entry (M15/M5)
- **Concepts:** FVG + MSS confluence, OTE zone entries, ADX trend filter
- **Sessions:** London Open, New York Open
- **Indicators:** EMA (20/50/200), ADX, ATR

### SILVER_BULLET Strategy
- **Windows:** 03:00–04:00 UTC, 10:00–11:00 UTC, 14:00–15:00 UTC
- **Concepts:** FVG sweeps, liquidity grabs, precision reversals
- **Entry:** Market order after sweep confirmation at FVG 50% midpoint
- **Session:** All three Silver Bullet windows

---

## Guardian Risk Layer

```
Max Risk Per Trade : 1% of account
Risk:Reward Ratio  : 1:3 minimum
Daily Loss Limit   : 3% circuit breaker → session lock
Panic Candle Guard : Detects abnormal volatility, halts entry
News Blackout      : ±30 min around red-folder events
Session Lock       : Prevents re-entry after daily limit hit
Kronos Veto Gate   : AI-powered trade validation (threshold 0.40, mode: enforced)
Pattern Sequence   : ICT sequential wave detection (sweep→displacement→retracement)
Killzone Timing    : Pattern quality scored by session alignment
```

---

## File Structure

```
project_vuka/
├── ingwe.py              # Main bot (supports --backtest/--test/--check)
├── supervisor.py         # Always-on watchdog — auto-restarts crashed bots
├── dashboard.py          # TUI dashboard (rich-based fleet monitor)
├── kronos_server.py      # Kronos AI inference server (FastAPI)
├── kronos_guardian.py    # Veto gate + circuit breaker
├── database_manager.py   # SQLite persistence layer
├── unified_logger.py     # Centralized logging to SQLite
├── state_manager.py      # Session state persistence
├── health_monitor.py     # Anomaly detection + alerting
├── memory_manager.py     # Memory.md state manager
├── indicators.py         # Shared technical indicators (ADX, ATR, etc.)
├── tick_engine_v5.py     # Event-driven tick engine (replaces polling)
├── tick_backtest_v5.py   # Tick-level backtest engine
├── config_v4.6.json      # System configuration
├── migration_to_sqlite.py# JSON→SQLite data migration
├── AGENT.md              # This file
├── Memory.md             # Persistent agent memory
├── data/
│   ├── sessions_today.json      # Daily session state
│   ├── trades_{tag}.json        # Per-instance trade logs
│   ├── sessions_{tag}.json      # Per-instance session state
│   ├── sl_moves_{tag}.json      # Trailing SL movement log
│   └── sessions/                # Historical CSV data
├── skills/                     # Agent skill modules
│   ├── __init__.py
│   ├── backtester.py / .md
│   ├── run_bot.py / .md
│   ├── trade_log_analyzer.py / .md
│   ├── risk_monitor.py / .md
│   ├── session_manager.py / .md
│   ├── performance_reporter.py / .md
│   ├── strategy_optimizer.py / .md
│   ├── concept_tracker.py
│   ├── decision_synthesizer.py
│   ├── ict_retriever.py / ict_parser.py
│   └── ...
├── Kronos/                    # Kronos transformer model
│   ├── model/kronos.py
│   ├── finetune/
│   └── webui/
└── archive/                   # Deprecated/backup files
```

---

## How to Run

```bash
# Production (event-driven tick engine)
python ingwe.py EURUSD INGWE

# Backtest mode (polling loop)
python ingwe.py EURUSD INGWE --backtest
python ingwe.py GBPUSD SILVER_BULLET --backtest

# Single scan + exit
python ingwe.py EURUSD INGWE --check
python ingwe.py EURUSD INGWE --test

# Dashboard (auto-starts supervisor in background)
python dashboard.py

# Supervisor (standalone watchdog)
python supervisor.py
```

---

## Operating Principles

1. **Never trade against the trend** — HTF bias is law
2. **Structure first, indicator second** — Price action leads
3. **Session discipline** — Only trade during kill zones
4. **Risk is sacred** — No exceptions to the Guardian layer
5. **Log everything** — Every decision must be traceable

---

## Skill Index

| Skill | File | Purpose |
|---|---|---|
| RunBot | `skills/run_bot.py` | Start/stop/monitor bot instances |
| TradeLogAnalyzer | `skills/trade_log_analyzer.py` | Parse and analyze trade history |
| RiskMonitor | `skills/risk_monitor.py` | Real-time risk exposure tracking |
| Backtester | `skills/backtester.py` | Historical strategy simulation |
| StrategyOptimizer | `skills/strategy_optimizer.py` | Parameter tuning and optimization |
| PerformanceReporter | `skills/performance_reporter.py` | P&L reports, win rate, drawdown |
| SessionManager | `skills/session_manager.py` | Session state, daily resets |
| ConceptTracker | `skills/concept_tracker.py` | Trade concept outcome tracking |
| DecisionSynthesizer | `skills/decision_synthesizer.py` | Multi-factor entry scoring |

---

## Recent Structural Changes

- **v5.5 — Critical datetime fix** — `place_limit_order()` now uses `_server_now()` (naive server-time) instead of timezone-aware Unix timestamps. Expiry passes `datetime` object directly to MT5.
- **v5.5 — Scoring rebalance** — Trend weight 40→30 (no longer dominant alone), HTF bias 10→15, Zone 20→15. New `SESSION_ASYMMETRY_BONUS` (+10) encodes live SELL/BUY asymmetry (~77% vs ~40%).
- **v5.5 — ADX backtest decontaminated** — Removed `random.uniform(10,40)` fallback. Candle skipped when ADX unavailable. Proper Wilder cold-start seed in both backtest engines.
- **v5.5 — HTF backtest guard** — `get_htf_bias()` returns `None` in `BACKTEST_MODE`, preventing live MT5 calls during replay.
- **v5.5 — Kronos pattern training** — `detect_pattern_sequence()` identifies sweep→displacement→retracement wave. `get_killzone_quality()` scores timing alignment. Both modulate inference confidence.
- **v5.5 — Blacklist inversion fix** — Removed `("Asian", "SELL", "SWEEP_HIGH")` that was blocking a 100% win-rate pattern.
- **v5.5 — ADX threshold unified** — Single `ADX_MIN_THRESHOLD` replaces split `min_adx_hard_limit=10` magic number.
- **v5.5 — Kronos veto enforced** — Config changed from `"warn"` to `"enforced"`. Gate now blocks trades below 0.40 confidence.
- **v4.6** — Consolidated `ingwe_backtest.py` and `ingwe_v4.6_test.py` into `ingwe.py` with `--backtest`/`--test` flags
- **v4.6** — Added `dashboard.py`, `indicators.py`, `skills/__init__.py`
- **v4.6** — Removed old v4.6 modules, added zombie cleanup, patched UNKNOWN trade symbols

---

## Backtest Results (v5.5, Real ADX, Jan–May 2026)

| Metric | EURUSD M15 | GBPUSD M15 |
|---|---|---|
| Candles | 8,748 | 8,747 |
| Orders | 26 | 22 |
| Trades executed | 25 | 20 |
| Win rate | 56.0% | 50.0% |
| Net P&L | +$305.83 | +$337.15 |
| Return | +3.06% | +3.37% |
| SL moves | 15 | 13 |
| Fill rate | 100% | 100% |

---

*Last updated: 2026-05-29*
