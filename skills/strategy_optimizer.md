# SKILL: strategy_optimizer

> Grid search + walk-forward validation for parameter optimization.

## Triggers
Use this skill when the user says:
- "Optimize the strategy", "Run parameter sweep"
- "Find best ADX threshold", "Tune RRR"
- "Walk-forward validation", "Is the config robust?"
- "Apply best params", "Update config"

## Description
Runs grid search over parameter combinations (ADX, RRR, Risk%)
and validates robustness via walk-forward analysis.
Saves best config to `data/best_params.json`.

## Commands

### Run Grid Search
```bash
python skills/strategy_optimizer.py --sweep --strategy INGWE
```

### Custom Parameter Range
```bash
python skills/strategy_optimizer.py --sweep --strategy INGWE --adx 15,20,25 --rrr 2.5,3.0,3.5
```

### Walk-Forward Validation
```bash
python skills/strategy_optimizer.py --walk-forward --strategy INGWE
```

### Show Best Parameters
```bash
python skills/strategy_optimizer.py --best
```

### Apply Best Config
```bash
python skills/strategy_optimizer.py --apply --confirm
```

## Parameter Grid

| Parameter | Range | Default |
|-----------|-------|---------|
| adx_threshold | 15, 20, 25, 30 | 20 |
| rrr | 2.5, 3.0, 3.5 | 3.0 |
| risk_per_trade | 0.5, 1.0, 1.5 | 1.0 |

## Anti-Overfitting Rules
- Max 15% win rate delta between in-sample and out-of-sample
- Minimum 1.8 profit factor on out-of-sample
- Require 100+ trades per configuration
- Walk-forward uses 6 months in-sample, 2 months out-of-sample
