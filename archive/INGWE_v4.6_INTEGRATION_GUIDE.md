> **⚠️ SUPERSEDED — v4.6 modules removed.** `state_manager_v4.6.py`, `health_monitor_v4.6.py`, and `kronos_guardian_v4.6.py` no longer exist as standalone files. Their functionality was incorporated into `state_manager.py`, `health_monitor.py`, and `kronos_guardian.py`. See `AGENT.md` for current file structure.

# 🐆 INGWE v4.6 — INTEGRATION GUIDE
## Hardening v4.5 Ingwe with Defense-in-Depth Modules

**Scope:** Shows exact code changes to integrate v4.6 modules into existing ingwe.py  
**Effort:** ~2 hours  
**Risk:** Low (backward compatible)  

---

## MODULE INTEGRATION CHECKLIST

### Step 1: Import New Modules

**Location:** ingwe.py, after existing imports (top of file)

```python
# Existing imports...
import mt5
import pandas as pd
from datetime import datetime, timezone, timedelta
# ... other imports ...

# NEW: v4.6 Hardening modules
from state_manager_v4.6 import StateManager
from health_monitor_v4.6 import HealthMonitor
from kronos_guardian_v4.6 import KronosVetoGate, create_veto_gate

# (Keep kronos_guardian import that exists, replace with v4.6 version)
```

**Verification:**
```bash
python -c "from state_manager_v4.6 import StateManager; print('✅ StateManager OK')"
python -c "from health_monitor_v4.6 import HealthMonitor; print('✅ HealthMonitor OK')"
python -c "from kronos_guardian_v4.6 import KronosVetoGate; print('✅ VetoGate OK')"
```

---

### Step 2: Initialize Managers at Startup

**Location:** ingwe.py, `main()` function, after MT5 connection

**Find This:**
```python
def main():
    # ... MT5 setup ...
    mt5.initialize()
    
    # ... existing initialization ...
    session_data = json.load(open("sessions_today.json")) if Path("sessions_today.json").exists() else {"trades": []}
```

**Replace With:**
```python
def main():
    # ... MT5 setup ...
    mt5.initialize()
    
    # v4.6: Initialize hardening managers
    state_mgr = StateManager(
        session_file=f"sessions_{SYMBOL}_{STRATEGY}.json",
        max_backups=10
    )
    
    health_monitor = HealthMonitor(window_size=100)
    
    veto_gate_config = {
        "endpoint": "http://127.0.0.1:8000/v1/predict-ict",
        "threshold": 0.40,
        "enabled": True,
        "mode": "enforced",
        "safety_mode": "VETO_SAFE"  # v4.6: Conservative by default
    }
    veto_gate = create_veto_gate(veto_gate_config)
    
    # Load session state with automatic corruption recovery
    session_data = state_mgr.load_session()
    if session_data is None:
        session_data = {"trades": [], "metadata": {}}
    
    print(f"✅ v4.6 Hardening: StateManager, HealthMonitor, VetoGate initialized")
```

---

### Step 3: Replace Veto Gate Calls

**Location:** ingwe.py, where Kronos validation is called

**Find This (typically in evaluate_ingwe or execute_trade):**
```python
allowed, reason = veto_gate.validate(
    context=setup_context,
    df=df,
    symbol=SYMBOL
)
```

**Keep As-Is:** The v4.6 VetoGate is backward compatible, but now includes:
- Circuit breaker handling
- VETO_SAFE mode (blocks trades on Kronos errors)
- Fallback confidence scoring

No code changes needed here, just behavior improvements!

---

### Step 4: Integrate Health Monitoring

**Location:** ingwe.py, inside main scan loop (every 15 minutes)

**Add This After Each Scan:**
```python
# At end of scan_cycle():
health_monitor.record_scan({
    "timestamp": current_time.isoformat(),
    "session": current_session,
    "sweep_detected": bool(sweep_level),
    "sweep_level": float(sweep_level) if sweep_level else None,
    "fvg_detected": len(fvgs) > 0,
    "fvg_type": fvgs[0]["type"] if fvgs else None,
    "confluence_score": confluence_score,
    "htf_bias_ok": htf_bias_ok,
    "m15_bos": m15_bos,
    "adx": float(adx),
    "adx_ok": adx >= ADX_MIN_THRESHOLD,
    "signal_direction": direction,
    "kronos_agree": allowed if allowed is not None else None,
    "kronos_confidence": confidence if "confidence" in reason else None,
    "trade_executed": trade_executed,
    "error": error_msg if error_msg else None
})

# Check for critical alerts every 10 scans
if len(health_monitor.scan_history) % 10 == 0:
    if health_monitor.should_alert():
        alert_report = health_monitor.get_health_report()
        print(f"⚠️  SYSTEM ANOMALY DETECTED:")
        for anomaly in alert_report["recent_anomalies"]:
            print(f"   - {anomaly['type']}: {anomaly['message']}")
        health_monitor.log_alert(alert_report)
```

---

### Step 5: Integrate Session State Management

**Location:** ingwe.py, after each trade execution

**Find This:**
```python
# Old way: Save to JSON manually
trades_list.append(trade_dict)
with open(f"trades_{SYMBOL}_{STRATEGY}.json", 'w') as f:
    json.dump(trades_list, f)
```

**Replace With:**
```python
# New way: Atomic writes with backup rotation
session_data["trades"].append(trade_dict)
session_data["metadata"]["last_trade"] = current_time.isoformat()
session_data["metadata"]["daily_pnl"] = calculate_daily_pnl()  # Your P&L calc

state_mgr.save_session(
    trades=session_data["trades"],
    metadata=session_data["metadata"]
)

# Verify backups exist (info only)
backup_count = state_mgr.get_backup_count()
logger.info(f"Session saved ({backup_count} backups in rotation)")
```

---

### Step 6: Update Veto Gate Initialization

**Location:** ingwe.py, where veto_gate is first created

**Old (v4.5):**
```python
from kronos_guardian import KronosVetoGate

veto_gate = KronosVetoGate(
    enabled=True,
    mode="enforced"
)
```

**New (v4.6):**
```python
from kronos_guardian_v4.6 import create_veto_gate

veto_gate = create_veto_gate({
    "enabled": True,
    "mode": "enforced",
    "safety_mode": "VETO_SAFE"  # NEW: Conservative error handling
})
```

---

### Step 7: Add Graceful Shutdown Handler

**Location:** ingwe.py, add to main() or exit handler

```python
def on_exit():
    """Graceful shutdown with state persistence"""
    print("\n🐆 Ingwe shutting down...")
    
    # Save final state
    state_mgr.save_session(
        trades=session_data["trades"],
        metadata={**session_data["metadata"], "shutdown": datetime.now(timezone.utc).isoformat()}
    )
    
    # Generate final health report
    health_report = health_monitor.get_health_report()
    print(f"\n📊 Session Report:")
    print(f"   Uptime: {health_report['uptime']}")
    print(f"   Trades: {health_report['metrics']['total_trades']}")
    print(f"   Errors: {health_report['metrics']['total_errors']}")
    print(f"   Status: {health_report['system_status']}")
    
    # Log final state
    logger.info(json.dumps(health_report))
    
    print("✅ Graceful shutdown complete")

# Register cleanup
import atexit
atexit.register(on_exit)
```

---

## CONFIGURATION CHANGES

### v4.6 Config Block (config_v4.6.json)

Create this file in the project directory:

```json
{
  "system": {
    "version": "4.6",
    "hardening": "defense_in_depth"
  },
  "veto_gate": {
    "enabled": true,
    "mode": "enforced",
    "safety_mode": "VETO_SAFE",
    "endpoint": "http://127.0.0.1:8000/v1/predict-ict",
    "threshold": 0.40
  },
  "state_manager": {
    "session_file": "sessions_today.json",
    "backup_dir": "state_backups",
    "max_backups": 10,
    "atomic_write_enabled": true
  },
  "health_monitor": {
    "window_size": 100,
    "anomaly_check_interval": 10,
    "alert_on_error": true,
    "alert_on_veto_rate_gt": 0.7,
    "alert_on_no_sweeps": true
  }
}
```

---

## BACKWARD COMPATIBILITY NOTES

✅ **v4.6 is fully backward compatible with v4.5:**

- Veto gate defaults to `VETO_SAFE` mode but can be set to `ALLOW_SAFE` for v4.5 behavior
- State manager uses same JSON format as before, adds backup rotation
- Health monitor is non-blocking (runs parallel, doesn't affect trading)
- All imports are additive (no breaking changes)

**If you want v4.5 behavior temporarily:**
```python
veto_gate.set_safety_mode("ALLOW_SAFE")  # Reverts to allowing trades on errors
```

---

## TESTING INTEGRATION

### Unit Tests

```bash
# Test state manager
python -m pytest test_state_manager.py -v

# Test health monitor
python -m pytest test_health_monitor.py -v

# Test circuit breaker
python -m pytest test_circuit_breaker.py -v
```

### Dry Run (Backtest)

```bash
# Run with hardening modules (simulated market)
python ingwe_backtest.py EURUSD INGWE --mode hardening_test --duration 1h

# Check logs
tail -f logs/kronos_veto.log | grep -E '"safety_mode"|"circuit"'
```

### Live Test (Single Instance)

```bash
# Boot one instance with monitoring
py ingwe.py EURUSD INGWE &

# In another terminal, watch health
watch -n 5 'tail -20 logs/health_monitor.log'

# Check veto gate circuit breaker
tail -f logs/kronos_veto.log | grep "circuit_breaker_state"

# Simulate Kronos failure to test circuit breaker
# (Kill Kronos server and observe fallback behavior)
```

---

## EXPECTED BEHAVIOR CHANGES

### Pre-v4.6 (v4.5)
- Kronos timeout → Trade executes blindly (ALLOW_SAFE)
- Session corruption → Lost trades or duplicates
- No system anomaly detection
- Hard crash on unhandled error

### Post-v4.6
- Kronos timeout → Trade blocked, uses fallback confidence if high enough (VETO_SAFE)
- Session corruption → Auto-recovery from backup
- Anomalies logged and alerted in real-time
- Graceful degradation with fallback logic

### Log Output Examples

**v4.6 Normal Operation:**
```json
{"timestamp":"2026-05-15T10:00:00Z","signal":"BUY","decision":"ALLOW",
 "circuit_breaker_state":"CLOSED","safety_mode":"VETO_SAFE"}
```

**v4.6 Kronos Timeout (VETO_SAFE):**
```json
{"timestamp":"2026-05-15T10:00:00Z","signal":"BUY","decision":"VETO_TIMEOUT",
 "circuit_breaker_state":"OPEN","safety_mode":"VETO_SAFE",
 "reason":"Kronos API timeout (VETO_SAFE mode)"}
```

**v4.6 Fallback Mode:**
```json
{"timestamp":"2026-05-15T10:00:00Z","signal":"BUY","decision":"ALLOW_FALLBACK",
 "circuit_breaker_state":"HALF_OPEN","confidence":0.75,
 "reason":"Kronos offline, using Ingwe confluence (75%)"}
```

---

## MONITORING DASHBOARD

Create real-time health dashboard:

```python
# health_dashboard.py
import json
from pathlib import Path
from datetime import datetime

def main():
    veto_log = Path("logs/kronos_veto.log")
    health_log = Path("logs/health_alerts.log")
    
    # Parse recent decisions
    recent_decisions = [json.loads(line) for line in veto_log.tail(50)]
    
    # Calculate metrics
    allow_rate = sum(1 for d in recent_decisions if "ALLOW" in d["decision"]) / len(recent_decisions)
    veto_rate = 1 - allow_rate
    
    circuit_states = {}
    for d in recent_decisions:
        state = d["circuit_breaker_state"]
        circuit_states[state] = circuit_states.get(state, 0) + 1
    
    print(f"""
    🐆 INGWE v4.6 HEALTH DASHBOARD
    ══════════════════════════════════════════
    
    📊 Veto Gate Metrics:
       Allow Rate:     {allow_rate:.0%}
       Veto Rate:      {veto_rate:.0%}
    
    🔌 Circuit Breaker:
       CLOSED:   {circuit_states.get('CLOSED', 0)}
       OPEN:     {circuit_states.get('OPEN', 0)}
       HALF_OPEN: {circuit_states.get('HALF_OPEN', 0)}
    
    ⚠️  Safety Mode: VETO_SAFE (conservative error handling)
    
    Last Updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
    """)

if __name__ == "__main__":
    main()
```

---

## TROUBLESHOOTING

| Issue | Diagnosis | Fix |
|-------|-----------|-----|
| "StateManager: Session load failed" | Corrupted session file | Auto-recover from backup (check state_backups/) |
| "Circuit breaker: OPEN" | Kronos server down | Restart Kronos, circuit auto-recovers after 30s |
| "High veto rate" | Ingwe-Kronos mismatch | Check confidence calibration, may adjust threshold |
| "No sweeps detected" | No liquidity activity | Normal in dead zones, check during active killzones |

---

## ROLLBACK (If Needed)

```bash
# Revert to v4.5
cp kronos_guardian.py.backup-v4.5 kronos_guardian.py
rm state_manager_v4.6.py health_monitor_v4.6.py
rm -f kronos_guardian_v4.6.py

# In ingwe.py, remove v4.6 imports and revert to v4.5 initialization

# Restart
py ingwe.py EURUSD INGWE
```

---

**v4.6 integration is complete. System is now hardened against all identified single points of failure.**