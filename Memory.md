# Agent Memory — Ingwe 🐆

> Persistent state file. Updated automatically after each session.
> Do NOT manually edit unless correcting a known error (log reason below).

---

## Current State

```yaml
last_updated       :  2026-05-26T16:02:01.526716+00:00
active_instances   :  ["EURUSD_INGWE", "GBPUSD_INGWE", "EURUSD_SILVER_BULLET", "GBPUSD_SILVER_BULLET"]
current_equity     :  4170.51
bot_status         :  RUNNING
environment        :  LIVE
```

---

## Today's Stats

```yaml
date               :  2026-05-26
daily_pnl          :  0
daily_pnl_pct      : 0.00
sessions_traded    :  []
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
kronos_threshold      : 0.40       # Maintained after audit; 0.70 too strict (missed winners)
```

---

## Recent Activity

```yaml
last_scan        :  2026-05-26 16:02:01
last_trade       : ""            # Timestamp of last executed trade
last_trade_id    : ""
last_report_run  : ""
errors           : []            # Format: ["YYYY-MM-DD HH:MM | error_message"]
manual_overrides : ["2026-05-24 | Keep Kronos threshold at 0.40 | Audit Result: Precision 50%, FP=9, TP=9"]
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
  - "[ ] ICT_M1 strategy optimization and live testing"
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
  - "[x] Live TUI dashboard with fleet status and command dispatch"
  - "[x] Bot consolidation — single ingwe.py with --backtest/--test flags"
  - "[x] Shared indicators module (indicators.py) eliminating ADX duplication"
  - "[x] Supervisor auto-launch from dashboard"
  - "[x] Dashboard flicker fix — msvcrt input, no screen clearing, ANSI-only overwrite"
  - "[x] Zombie cleanup — supervisor kills stale bots on startup"
  - "[x] DB trade symbol fix — 40 UNKNOWN records patched to EURUSDc/GBPUSDc"
  - "[x] Session reset — all session files restored to 2026-05-26"
  - "[x] .gitignore organized — backups, CSVs, data/, junk suppressed"
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
