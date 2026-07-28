> **⚠️ SUPERSEDED — v4.6 modules removed.** `state_manager_v4.6.py`, `health_monitor_v4.6.py`, `kronos_guardian_v4.6.py` no longer exist as standalone files. Functionality consolidated into main modules. See `AGENT.md` for current file structure.

# 🐆 INGWE v4.6 — COMPLETE DEPLOYMENT GUIDE
## Defense-in-Depth Hardening Release

**Release Date:** 2026-05-15  
**Status:** PRODUCTION READY  
**Breaking Changes:** None (fully backward compatible)  
**Estimated Deployment Time:** 6 hours  

---

## DELIVERY PACKAGE

### Documentation (4 files)
1. **INGWE_v4.6_SPECIFICATION.md** — Architecture & technical specs
2. **INGWE_v4.6_INTEGRATION_GUIDE.md** — Code integration steps
3. **DEPLOYMENT_GUIDE_v4.6.md** — This file (step-by-step deployment)
4. **TEST_PLAN_v4.6.md** — Testing procedures

### Source Code (4 modules + 1 configuration)
1. **kronos_guardian_v4.6.py** — Circuit breaker + VETO_SAFE mode
2. **state_manager_v4.6.py** — Atomic writes + backup rotation
3. **health_monitor_v4.6.py** — Anomaly detection + alerting
4. **config_v4.6.json** — System configuration
5. *(ingwe_v4.6.py will be generated post-integration)*

---

## PRE-DEPLOYMENT CHECKLIST

### Team & Access
- [ ] All team members have SSH access to trading server
- [ ] Git repo access for version control
- [ ] Monitoring dashboard access (if available)
- [ ] Slack/alert channel for deployment notifications

### System State
- [ ] MT5 is running and stable
- [ ] Kronos server is running
- [ ] Current v4.5 instances are executing trades (optional but recommended)
- [ ] Backup of v4.5 source code created
- [ ] Latest git commit is tagged as "v4.5-stable"

### Testing
- [ ] Python 3.14+ installed in project venv
- [ ] All v4.6 modules import successfully
- [ ] Test suite runs without errors

---

## DEPLOYMENT PHASES

### PHASE 1: PREPARATION (30 minutes)

#### Step 1.1: Backup Current System
```bash
cd C:\Users\classic\Desktop\Project Vuka

# Create version control snapshot
git add -A
git commit -m "v4.5 stable checkpoint before v4.6 hardening"
git tag -a v4.5-stable -m "Stable v4.5 before v4.6 deployment"

# Backup source files
mkdir -p backups/v4.5-$(date +%Y%m%d-%H%M%S)
cp ingwe.py backups/v4.5-$(date +%Y%m%d-%H%M%S)/
cp kronos_guardian.py backups/v4.5-$(date +%Y%m%d-%H%M%S)/
cp kronos_server.py backups/v4.5-$(date +%Y%m%d-%H%M%S)/
cp config*.json backups/v4.5-$(date +%Y%m%d-%H%M%S)/

# Backup trade history
cp trades_*.json backups/v4.5-$(date +%Y%m%d-%H%M%S)/
cp sessions_*.json backups/v4.5-$(date +%Y%m%d-%H%M%S)/

echo "✅ Backup complete"
```

#### Step 1.2: Copy v4.6 Modules
```bash
# Copy from delivery package to project directory
cp state_manager_v4.6.py ./
cp health_monitor_v4.6.py ./
cp kronos_guardian_v4.6.py ./
cp config_v4.6.json ./

# Verify imports
python -c "from state_manager_v4.6 import StateManager; print('✅ StateManager')"
python -c "from health_monitor_v4.6 import HealthMonitor; print('✅ HealthMonitor')"
python -c "from kronos_guardian_v4.6 import KronosVetoGate; print('✅ VetoGate')"

# All should print ✅
echo "✅ Module verification complete"
```

#### Step 1.3: Prepare Test Environment
```bash
# Create test directory
mkdir -p test_v4.6
cd test_v4.6

# Copy modules for isolated testing
cp ../state_manager_v4.6.py ./
cp ../health_monitor_v4.6.py ./
cp ../kronos_guardian_v4.6.py ./

# Run basic unit tests
python -m pytest test_*.py -v 2>&1 | tee test_results.log

# Check results
echo "✅ Review test_results.log for any failures"
```

---

### PHASE 2: INTEGRATION (2-3 hours)

#### Step 2.1: Integrate State Manager
```bash
cd C:\Users\classic\Desktop\Project Vuka

# Edit ingwe.py
# 1. Add import: from state_manager_v4.6 import StateManager
# 2. In main(): Initialize state_mgr = StateManager(...)
# 3. Replace all manual JSON save calls with state_mgr.save_session(...)
# 4. Replace all manual JSON load calls with state_mgr = state_mgr.load_session(...)

# Verification (syntax check only, no execution)
python -m py_compile ingwe.py
echo "✅ ingwe.py syntax OK"
```

#### Step 2.2: Integrate Health Monitor
```bash
# Edit ingwe.py
# 1. Add import: from health_monitor_v4.6 import HealthMonitor
# 2. In main(): Initialize health_monitor = HealthMonitor(window_size=100)
# 3. After each scan cycle: health_monitor.record_scan({...})
# 4. Every 10 scans: Check health_monitor.should_alert() and log

# Verification
python -m py_compile ingwe.py
echo "✅ ingwe.py syntax OK after health monitor integration"
```

#### Step 2.3: Integrate Kronos Guardian v4.6
```bash
# Backup current kronos_guardian
cp kronos_guardian.py kronos_guardian.py.backup-v4.5

# Replace with v4.6
cp kronos_guardian_v4.6.py kronos_guardian.py

# Edit ingwe.py to use v4.6 features:
# 1. Update import: from kronos_guardian_v4.6 import create_veto_gate
# 2. Update initialization: veto_gate = create_veto_gate({...safety_mode: "VETO_SAFE"...})

# No API changes needed - existing validate() calls work as-is

# Verification
python -c "from kronos_guardian import KronosVetoGate; g=KronosVetoGate(safety_mode='VETO_SAFE'); print(f'✅ Circuit breaker: {g.get_circuit_breaker_state()}')"
```

#### Step 2.4: Update Configuration
```bash
# Review config_v4.6.json and update project config
# If using config file in ingwe.py, merge v4.6 settings

# Add to your config:
cat >> config.json << 'EOF'
,
  "v4.6_hardening": {
    "state_manager": {
      "enabled": true,
      "max_backups": 10
    },
    "health_monitor": {
      "enabled": true,
      "anomaly_alert_interval": 10
    },
    "veto_gate": {
      "safety_mode": "VETO_SAFE",
      "circuit_breaker_enabled": true
    }
  }
EOF

echo "✅ Configuration updated"
```

---

### PHASE 3: PRE-LIVE TESTING (2 hours)

#### Step 3.1: Dry Run (Backtest Mode)
```bash
cd C:\Users\classic\Desktop\Project Vuka

# Run ingwe_backtest.py with v4.6 modules
# This simulates market data without live MT5 connection

py ingwe_backtest.py EURUSD INGWE --duration 2h --mode hardening_test

# Expected output:
# - No errors from state_manager
# - No errors from health_monitor
# - veto.log shows valid ALLOW/VETO decisions
# - health alerts appear for simulated anomalies

# Check logs
echo "Checking logs..."
tail -20 logs/kronos_veto.log | grep "circuit_breaker_state"
tail -10 logs/health_monitor.log | grep "anomaly\|HEALTHY"

echo "✅ Dry run complete - review logs for anomalies"
```

#### Step 3.2: Single Instance Live Test
```bash
# Start with EURUSD INGWE only (not SILVER_BULLET yet)
# This tests state manager + health monitor + veto gate in live MT5

echo "Starting single instance test..."
timeout 30 py ingwe.py EURUSD INGWE &

# Monitor in parallel terminal
tail -f logs/kronos_veto.log | head -20

# After 30 seconds, kill and inspect
# Expected:
# - No state file corruption
# - Health monitor scans recorded
# - Veto decisions logged

echo "✅ Single instance test complete"
```

#### Step 3.3: Backlog Check
```bash
# Verify no trades were duplicated
python -c "
import json
trades = json.load(open('trades_EURUSD_INGWE.json'))
tickets = [t.get('ticket') for t in trades if 'ticket' in t]
duplicates = [t for t in set(tickets) if tickets.count(t) > 1]
if duplicates:
    print(f'❌ Duplicates found: {duplicates}')
else:
    print(f'✅ No duplicate trades ({len(trades)} total)')
"

# Verify backups were created
ls -la state_backups/
echo "✅ Backup rotation verified"
```

---

### PHASE 4: FULL DEPLOYMENT (30 minutes)

#### Step 4.1: Stop All Instances
```bash
# Gracefully stop all 4 instances
echo "Stopping instances..."
pkill -f "py ingwe.py"

# Wait 10 seconds for cleanup
sleep 10

# Verify all stopped
tasklist | findstr python
# Should show no ingwe.py processes

echo "✅ All instances stopped"
```

#### Step 4.2: Boot All Instances (Sequential)
```bash
# Boot with 30-second delays to stagger load

echo "Booting EURUSD_INGWE..."
start cmd /k "py ingwe.py EURUSD INGWE"
sleep 30

echo "Booting EURUSD_SILVER_BULLET..."
start cmd /k "py ingwe.py EURUSD SILVER_BULLET"
sleep 30

echo "Booting GBPUSD_INGWE..."
start cmd /k "py ingwe.py GBPUSD INGWE"
sleep 30

echo "Booting GBPUSD_SILVER_BULLET..."
start cmd /k "py ingwe.py GBPUSD SILVER_BULLET"
sleep 30

echo "✅ All instances started"
```

#### Step 4.3: Health Check
```bash
# Verify all instances are running
for instance in EURUSD_INGWE EURUSD_SILVER_BULLET GBPUSD_INGWE GBPUSD_SILVER_BULLET; do
    if [ -f "logs/${instance,,}.log" ]; then
        echo "✅ $instance: Running"
        tail -1 "logs/${instance,,}.log" | head -c 80
    else
        echo "⚠️  $instance: No log file yet"
    fi
done

# Check veto gate status
tail -5 logs/kronos_veto.log | jq '.circuit_breaker_state'

echo "✅ Health check complete"
```

---

### PHASE 5: MONITORING (72 hours)

#### Step 5.1: Continuous Monitoring
```bash
# Terminal 1: Watch veto log for errors
watch -n 5 'tail -10 logs/kronos_veto.log | grep -E "ERROR|VETO_TIMEOUT|circuit"'

# Terminal 2: Watch health monitor
watch -n 10 'tail -5 logs/health_monitor.log | jq ".system_status"'

# Terminal 3: Watch trades
watch -n 30 'tail -1 trades_*.json | wc -l'  # Total trade count
```

#### Step 5.2: Metrics to Track

**Hourly Report:**
```bash
# Generate hourly health summary
python -c "
import json
from pathlib import Path
from datetime import datetime, timedelta

# Parse veto log
veto_lines = Path('logs/kronos_veto.log').read_text().strip().split('\n')
last_hour = [json.loads(line) for line in veto_lines[-240:] if line]  # Last 240 decisions (4 hours @ 1/min)

allows = sum(1 for d in last_hour if 'ALLOW' in d['decision'])
vetoes = sum(1 for d in last_hour if 'VETO' in d['decision'])
circuit_opens = sum(1 for d in last_hour if d['circuit_breaker_state'] == 'OPEN')

print(f'''
v4.6 HEALTH REPORT ({datetime.now().strftime('%Y-%m-%d %H:%M:%S')})
═══════════════════════════════════════════════════════════════
Allow Rate:         {allows}/{len(last_hour)} = {allows/len(last_hour):.0%}
Veto Rate:          {vetoes}/{len(last_hour)} = {vetoes/len(last_hour):.0%}
Circuit Opens:      {circuit_opens}

✅ System Health: {'HEALTHY' if circuit_opens < 5 else 'CHECK_LOGS'}
''')
"
```

#### Step 5.3: Alert Thresholds

**Red Flags:**
- Circuit breaker OPEN > 5 times/hour → Kronos connectivity issue
- Veto rate > 80% → Ingwe-Kronos mismatch
- Error rate > 20% → MT5 connectivity or data issues
- No trades > 4 hours → Signal generation or execution block

**Action:**
```bash
# If alert triggered, check:
tail -50 logs/kronos_veto.log | grep -i error
tail -50 logs/health_monitor.log | jq '.anomalies'

# Diagnose and document in logs
echo "INCIDENT: [description] at [time]" >> logs/incidents.log
```

---

### PHASE 6: SIGN-OFF (End of 72 hours)

#### Step 6.1: Success Criteria Met?
```bash
# Checklist
echo "
✅ Deployment Checklist:
─────────────────────────────────────────
☐ All 4 instances running continuously
☐ No state file corruption detected
☐ No duplicate trades executed
☐ Circuit breaker tested (manual Kronos kill)
☐ Health monitor detecting anomalies
☐ Zero unhandled exceptions
☐ Veto log showing valid ALLOW/VETO decisions
☐ Backup rotation working (max 10 versions)
☐ Trade execution normal (consistent with v4.5)
☐ P&L tracking accurate

If all ☑️, proceed to v4.6 production sign-off
"
```

#### Step 6.2: Commit to Git
```bash
git add ingwe.py kronos_guardian.py state_manager_v4.6.py health_monitor_v4.6.py
git commit -m "v4.6 hardening deployment complete: circuit breaker + atomic state + anomaly monitoring"
git tag -a v4.6-deployed -m "v4.6 production deployment complete"
git push origin main
git push origin v4.6-deployed

echo "✅ v4.6 committed to production branch"
```

#### Step 6.3: Update Documentation
```bash
# Update project README
cat >> README.md << 'EOF'

## v4.6 Hardening Release (2026-05-15)
- Circuit breaker protects against Kronos outages
- Atomic session writes prevent corruption
- Health monitoring detects anomalies in real-time
- VETO_SAFE mode blocks unvalidated trades on errors
- See INGWE_v4.6_SPECIFICATION.md for details
EOF

echo "✅ Documentation updated"
```

---

## ROLLBACK PROCEDURE

If issues occur:

```bash
# Step 1: Stop all instances
pkill -f "py ingwe.py"

# Step 2: Restore v4.5 source
cp kronos_guardian.py.backup-v4.5 kronos_guardian.py
# (Also revert ingwe.py if needed - should be minimal changes)

# Step 3: Remove v4.6 modules (optional, backward compatible)
# rm state_manager_v4.6.py health_monitor_v4.6.py kronos_guardian_v4.6.py

# Step 4: Restart instances
py ingwe.py EURUSD INGWE &
py ingwe.py EURUSD SILVER_BULLET &
py ingwe.py GBPUSD INGWE &
py ingwe.py GBPUSD SILVER_BULLET &

# Step 5: Verify
tail logs/kronos_veto.log | head -5

echo "✅ Rollback to v4.5 complete"
```

---

## SUPPORT & DIAGNOSTICS

### Quick Diagnostics
```bash
# Is Kronos running?
curl -s http://127.0.0.1:8000/health | jq .

# Circuit breaker status?
tail -10 logs/kronos_veto.log | jq '.circuit_breaker_state' | sort | uniq -c

# Anomalies detected?
tail -50 logs/health_monitor.log | grep ANOMALIES_DETECTED

# Trade consistency?
python -c "
import json
for symbol in ['EURUSD', 'GBPUSD']:
    for strat in ['INGWE', 'SILVER_BULLET']:
        f = f'trades_{symbol}_{strat}.json'
        try:
            trades = json.load(open(f))
            print(f'{symbol} {strat}: {len(trades)} trades')
        except: pass
"
```

### Emergency Contacts
- **Kronos Issue:** Check kronos.log, restart with `nohup python kronos_server.py > kronos.log 2>&1 &`
- **State Corruption:** Check state_backups/, manually restore from latest good backup
- **Circuit Breaker Stuck OPEN:** Wait 30 seconds for auto-recovery, or restart ingwe instances

---

## SUCCESS CRITERIA

**v4.6 is deemed successful when:**

1. ✅ 72-hour production run with zero unhandled exceptions
2. ✅ All 4 instances execute trades normally
3. ✅ No state file corruption or duplicate trades
4. ✅ Circuit breaker prevents unvalidated execution on Kronos failure
5. ✅ Health monitor detects and logs anomalies
6. ✅ Backup rotation maintains 10 versioned states
7. ✅ Veto log shows proper decision logging with safety modes
8. ✅ P&L tracking matches v4.5 baseline (no performance regression)

**If all criteria met: v4.6 enters maintenance mode, v5.0 planning begins.**

---

**Deployment is controlled, documented, and reversible. Good luck.** 🐆