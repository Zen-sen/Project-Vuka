# Fix Plan — Position Sizing & Risk Model Corrections

## Steps

- [x] **1. bot.py**: Size lot against `atr * ATR_MULTIPLIER` (pre-trade estimate) instead of raw ATR
- [x] **2. bot.py**: Fix veto-gate default mode `"warn"` -> `"enforced"` (name matches behavior)
- [x] **3. bot.py**: Canonical veto threshold fallback `0.30` -> `0.40`
- [x] **4. portfolio.py**: Harden `update_consecutive_losses()` — process all new deals in ticket order
- [x] **5. kronos_guardian.py**: `create_veto_gate()` threshold default `0.75` -> `0.40` (defer to `KronosVetoGate` canonical default)
- [x] **6. kronos_guardian.py**: Remove dead `half_open_calls` state (keep `current_half_open_calls`)
- [x] **7. strategies/ingwe.py**: Import `calculate_lot_size`; resize to actual `stop` before all 4 `place_trade()` calls (re-apply overlap multiplier)
- [x] **8. strategies/ingwe.py**: Paths C/D — pass `zone_ok=False` (honest score; no free +15)
- [x] **9. strategies/london_open.py**: Import `calculate_lot_size`; resize to final `abs(entry - sl)` distance
- [x] **10. strategies/ict_m1.py**: Import `calculate_lot_size`; resize to `stop` distance
- [x] **11. strategies/silver_bullet.py**: Import `calculate_lot_size`; resize to actual `sl_dist` (breaker/sweep anchored)
- [x] **12. Run tests** (`pytest` on touched areas) — 25/25 passed (strategies + risk); full unit suite 216/216 clean

