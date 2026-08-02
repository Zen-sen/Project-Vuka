# Project Vuka - Session Handover
## Date: April 30, 2026

---

## Current State

### What's Working
- Ingwe trading bot generates FVG-based entry signals
- Kronos guard gate can veto trades
- Unicode issues fixed in logging
- Backtest runner with Kronos integration

### What's Broken
- **Kronos doesn't use its AI model** - uses fake numpy heuristics instead
- **Confidence always 90%+** - hardcoded bonuses never decrease
- **Override mode breaks everything** - flips ~50% of trades randomly
- **No learning** - model never improves from trade outcomes
- **Strategy loses money** - even with Kronos filtering, backtest shows -$172

---

## Problems Identified (Detailed)

### Problem 1: Fake Inference (CRITICAL)
**Location:** `kronos_server.py` lines 176-238

The code loads the real Kronos transformer model but never uses it. Instead it runs:
```python
# FAKE - not using model
first_half = valid_prices[:n//2]
second_half = valid_prices[n//2:]
overall_trend = second_mean - first_mean
conf = 0.5 + consistent * 0.2 + min(trend_pct * 3, 0.25) + ...
```

This produces fake 90%+ confidence on every trade.

### Problem 2: Override Mode Chaos
**Location:** `kronos_guardian.py` lines 163-193

Since confidence is always 90%+, the condition `confidence >= 0.70` is always true. Kronos flips ~50% of trades to opposite direction, causing massive losses.

### Problem 3: No Learning
**Location:** `skills/concept_tracker.py`

Records trades to SQLite but never updates model or uses historical data to improve decisions.

---

## Fixes Required

### Fix 1: Real Kronos Inference
Replace fake logic with actual model forward pass:
```python
logits = model(tokens)
probs = torch.softmax(logits[0, -1], dim=-1)
up_prob = probs[1].item()  # Real probability from transformer
```

### Fix 2: Realistic Confidence
Remove all arbitrary bonuses. Use actual model probabilities (typically 0.50-0.80).

### Fix 3: Remove Override Mode
Keep VETO only - override causes chaos.

### Fix 4: Add Pattern Learning
Track (symbol, session, setup_type) outcomes and use historical win rate as filter:
- Pattern with <40% win rate → AUTO-VETO
- Pattern with >60% win rate → AUTO-APPROVE

---

## Files to Modify

| File | Change |
|------|--------|
| `kronos_server.py` | Replace `run_inference()` with real model call |
| `kronos_guardian.py` | Remove override mode, keep veto only |
| `skills/concept_tracker.py` | Add pattern performance tracking |
| `skills/run_backtest.py` | Already has Kronos integration |

---

## Testing Plan

1. Run backtest with fixed Kronos
2. Compare: No Kronos vs VETO vs OVERRIDE
3. Check win rate per pattern improves over time
4. Verify confidence distribution is realistic (not always 90%+)

---

## Expected Outcome

After fixes:
- Kronos confidence should range 0.50-0.80 (realistic)
- VETO mode should block bad patterns based on history
- P&L should improve as system learns from trades

---

## Current Best Configuration

```python
# kronos_guardian.py
MODE = "veto"  # NOT "override"
KRONOS_THRESHOLD = 0.50

# run_backtest.py
KRONOS_ENABLED = True
KRONOS_THRESHOLD = 0.50
KRONOS_OVERRIDE_THRESHOLD = 0.70  # Won't trigger after fix
```