# Agent Memory — Ingwe 🐆

> Persistent state file. Updated automatically after each session.
> Do NOT manually edit unless correcting a known error (log reason below).

---

## Current State

```yaml
last_updated       : ""          # ISO timestamp — set by session_manager.py
active_instances   : []          # e.g. ["EURUSDc-INGWE", "GBPUSDc-SB"]
current_equity     : 0.00        # Live account equity (USD)
bot_status         : "IDLE"      # RUNNING | IDLE | PAUSED | CIRCUIT_BREAK
environment        : "LIVE"      # LIVE | PAPER | BACKTEST
```

---

## Today's Stats

```yaml
date               : ""
daily_pnl          : 0.00
daily_pnl_pct      : 0.00
sessions_traded    : []          # e.g. ["london", "ny"]
active_positions   : []          # list of open trade IDs
trades_today       : 0
wins_today         : 0
losses_today       : 0
session_locked     : false
circuit_break      : false
```

---

## Configuration

```yaml
default_risk_percent : 1.0       # % of equity per trade
max_daily_loss       : 3.0       # % circuit breaker threshold
default_strategy     : "INGWE"   # INGWE | SILVER_BULLET | BOTH
default_symbol       : "EURUSDc"
secondary_symbol     : "GBPUSDc"
rrr_minimum          : 3.0
trailing_sl          : true
adx_threshold        : 20
news_blackout_min    : 30
```

---

## Recent Activity

```yaml
last_scan        : ""            # Last time bot scanned for setups
last_trade       : ""            # Timestamp of last executed trade
last_trade_id    : ""
last_report_run  : ""
errors           : []            # Format: ["YYYY-MM-DD HH:MM | error_message"]
manual_overrides : []            # Format: ["YYYY-MM-DD | reason | authorized_by"]
```

---

## Performance Baselines

```yaml
# Updated weekly — source of truth for alert thresholds
avg_win_rate     : 73.0          # % — Week 1 benchmark
avg_rrr          : 3.1           # Average RR achieved
profit_factor    : 2.4
best_session     : "london"
worst_session    : "asian"
max_dd_observed  : 4.2           # % — highest drawdown seen in live trading
monthly_target   : 15.0          # % monthly return target
```

---

## Known Issues

```yaml
# Format: [id, description, status, resolution]
issues:
  - id: BUG-001
    desc: "Duplicate trade on restart — session_locked reset before position check"
    status: RESOLVED
    fix: "Read session_locked from file BEFORE opening any new positions"

  - id: BUG-002
    desc: "ADX filter blocking valid setups in low-volatility London sessions"
    status: MONITORING
    fix: "Reduced ADX threshold from 25 → 20, monitoring for 2 weeks"
```

---

## Backlog

```yaml
todo:
  - "[ ] Confluence scoring layer (multi-factor entry grade 1-10)"
  - "[ ] Live economic calendar API for news filter"
  - "[ ] Explainability log — reason string per trade decision"
  - "[ ] Telegram/Discord webhook alerts"
  - "[ ] GBPUSD performance split vs EURUSD analysis"
  - "[ ] Walk-forward optimizer integration"
  - "[ ] Stark Oracle dashboard MT5 live feed connection"
```

---

## Completed Milestones

```yaml
done:
  - "[x] Week 1 live run — 73% win rate on Exness cent account"
  - "[x] Guardian risk layer — 1% risk, 3% circuit breaker"
  - "[x] Session persistence via sessions_today.json"
  - "[x] Duplicate trade bug resolved (BUG-001)"
  - "[x] SILVER_BULLET strategy integrated"
  - "[x] EMA + ADX + ATR indicator stack"
  - "[x] Stark Oracle dashboard (JARVIS HUD)"
  - "[x] Claude API trade analysis integration"
```

---

## Handover Block

```
Project Vuka — Agent Ingwe (ICT Forex Bot)
Platform    : MetaTrader 5 / Exness / Python
Symbols     : EURUSDc, GBPUSDc
Strategies  : INGWE (ICT core), SILVER_BULLET (precision windows)
Risk Layer  : 1% per trade, 1:3 RRR, 3% daily circuit breaker
Live Status : Cent account, Week 1 = 73% WR
Key Bug     : BUG-001 duplicate trade — RESOLVED
Active Work : Confluence scoring, news filter, explainability log
Codebase    : C:/Users/classic/Desktop/Project Vuka/
Skills Dir  : C:/Users/classic/Desktop/Project Vuka/skills/
Memory File : C:/Users/classic/Desktop/Project Vuka/Memory.md
Data Dir    : C:/Users/classic/Desktop/Project Vuka/data/
```
