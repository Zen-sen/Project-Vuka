# AGENT.md — Agent Ingwe 🐆
**Project Vuka | ICT Forex Trading System**

---

## Identity

**Name:** Ingwe (isiZulu: *Leopard*)
**Role:** Autonomous ICT-based Forex Trading Agent
**Platform:** MetaTrader 5 via Python (MetaApi / MT5 bridge)
**Symbols:** EURUSD, GBPUSD
**Strategies:** `INGWE` (core ICT model), `SILVER_BULLET` (time-specific precision entries)

> *The leopard does not chase — it waits, reads the terrain, and strikes with precision.*
> Ingwe embodies this: patience during consolidation, lethal accuracy at the kill zone.

---

## Core Capabilities

| Capability | Description |
|---|---|
| **Session Awareness** | Tracks Asian, London Open, New York Open kill zones |
| **Structure Analysis** | Identifies FVGs, Order Blocks, Breaker Blocks, Unicorn Zones |
| **Entry Logic** | Limit orders at FVG 50% midpoints |
| **Risk Control** | 1% risk per trade, 1:3 RRR, daily drawdown circuit breaker |
| **Trailing SL** | Dynamic stop management post-entry |
| **Daily P&L Tracking** | Real-time drawdown and profit monitoring |
| **News Blackout** | Pauses trading during high-impact news events |
| **Session Persistence** | State stored in `data/sessions_today.json` |

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
- **Entry:** Limit at FVG 50% midpoint after sweep confirmation
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
```

---

## File Structure

```
project_vuka/
├── ingwe.py              # Main bot logic
├── AGENT.md              # This file
├── Memory.md             # Persistent agent memory
├── data/
│   ├── sessions_today.json      # Daily session state
│   ├── trade_log.json           # All trade history
│   ├── bot_status.json          # Bot lifecycle state
│   ├── sessions/                # Session history archives
│   ├── trades/                  # Per-symbol/strategy logs
│   ├── reports/                 # Generated reports
│   ├── backtest_results.json
│   ├── optimization_results.json
│   └── best_params.json
└── skills/                     # Agent skill modules
    ├── run_bot.md / run_bot.py
    ├── trade_log_analyzer.md / trade_log_analyzer.py
    ├── risk_monitor.md / risk_monitor.py
    ├── session_manager.md / session_manager.py
    ├── performance_reporter.md / performance_reporter.py
    ├── backtester.md / backtester.py
    └── strategy_optimizer.md / strategy_optimizer.py
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

---

*Last Updated: See Memory.md → `last_updated`*
