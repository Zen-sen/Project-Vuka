# Project Vuka - Session Handover
## Date: July 21, 2026

---

## Changes Made This Session

### Fix 1: Volume column safeguard (kronos_guardian.py)
**File:** `kronos_guardian.py:269-275`
**What:** Added fallback for missing `volume` column in `_prepare_ohlcv_payload()` — tries `tick_volume`, then `real_volume`, then defaults to `0.0`. Matches the fix already in `ingwe.py:get_candles()`.

### Fix 2: Restored Kronos confidence range (kronos_server.py)
**File:** `kronos_server.py:run_inference()`
**What:** Reverted three changes from v6.2 "Phase 4a" that widened the confidence range:
| Setting | v6.2 (broken) | Restored (June 18 behavior) |
|---------|---------------|----------------------------|
| Formula | `0.25 + (score * 0.65)` → 0.25–0.90 | `0.5 + (score * 0.2)` → 0.5–0.7 |
| n<2 default | `0.40` | `0.5` |
| Error default | `0.45` | `0.60` |

The wider range was causing Kronos to produce confidence scores (0.25–0.33) below the config threshold (0.40), blocking all trades.

---

## Current State

### Running Processes (8 bot instances + Kronos + Supervisor + Dashboard)
- EURUSD INGWE (2 instances, different Python versions)
- GBPUSD INGWE (2 instances)
- EURUSD LONDON_OPEN (2 instances)
- GBPUSD LONDON_OPEN (2 instances)
- kronos_server.py on port 8000
- supervisor.py
- dashboard.py

### Account
- Equity: $4,248.51 (42,485.09 USC cent account)
- Daily P&L: $0.00
- Active sessions: none

### Config (config_v4.6.json)
- Veto Gate: `enabled`, `enforced` mode, `VETO_SAFE` safety
- Threshold: `0.40` (BUY: `0.35`)
- Allowed sessions: Asian, London Close, London Open
- Daily loss limit: $50.00
- Weekly trade cap: 10

### Uncommitted Changes
- `ingwe.py` — volume fallback in `get_candles()` (lines 998–1004, added but not committed)
- `kronos_guardian.py` — volume fallback in `_prepare_ohlcv_payload()` (lines 269–275)
- `kronos_server.py` — confidence formula reverted to 0.5–0.7 range

---

## What to Watch

1. **Kronos confidence after restart** — Should be back to 0.47–0.67 range after the formula revert. Trades should flow again if confidence > 0.40 threshold.

2. **Duplicate Python versions** — Two different Python interpreters are running the same bots. This could cause MT5 connection conflicts. Consider killing all and restarting with one Python.

3. **Concept tracker penalizer** — Still active in `kronos_server.py:predict_ict()` (line ~768). If recent pattern win rates are <40%, it multiplies confidence by 0.75. This is fine as long as base confidence starts at 0.5+.

---

## Quick Commands

```powershell
# View running processes
Get-Process -Name python* | Select-Object Id, @{N='Cmd';E={$_.CommandLine.Substring(0, [Math]::Min(120, $_.CommandLine.Length))}}

# Restart Vuka (from Project Vuka directory)
.\vuka.bat restart

# Check Kronos health
curl http://127.0.0.1:8000/health

# View live vetos
Get-Content logs\kronos_veto.log -Tail 10

# View trade log
Get-Content trades_EURUSD_INGWE.json -Tail 5
```

---

*Last updated: 2026-07-21*
