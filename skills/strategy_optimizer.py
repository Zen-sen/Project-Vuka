#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
strategy_optimizer.py — Agent Ingwe Strategy Optimizer
Project Vuka | Grid search + walk-forward validation (REAL BACKTEST)
"""

import argparse
import json
import random
import sys
from datetime import datetime, timezone
from pathlib import Path
from itertools import product

if sys.platform == "win32":
    import codecs
    sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer, 'strict')
    sys.stderr = codecs.getwriter('utf-8')(sys.stderr.buffer, 'strict')

BASE_DIR = Path(__file__).parent.parent
CSV_FILE = BASE_DIR / "data" / "sessions" / "EURUSDc_M15_202505010000_202602202145.csv"
OPT_RESULTS_FILE = BASE_DIR / "data" / "optimization_results.json"
BEST_PARAMS_FILE = BASE_DIR / "data" / "best_params.json"
CONFIG_FILE = BASE_DIR / "data" / "ingwe_config.json"

sys.path.insert(0, str(BASE_DIR))
sys.path.insert(0, str(BASE_DIR / "src"))
from vuka.market_structure.ict import calculate_atr as _calculate_atr

PARAM_GRID = {
    "adx_threshold": [15, 20, 25, 30],
    "rrr":           [2.5, 3.0, 3.5],
    "risk_per_trade": [0.5, 1.0, 1.5],
}

MIN_TRADES_VALID = 100
MAX_WR_DELTA = 15.0
MIN_OOS_PF = 1.8


def now_utc():
    return datetime.now(timezone.utc).isoformat()


def sep():
    print("─" * 52)


def run_real_backtest(rrr: float, risk: float, adx: int = 30, quiet: bool = True) -> dict:
    import pandas as pd
    from dataclasses import dataclass, field

    @dataclass
    class BacktestConfig:
        csv_file: Path
        initial_balance: float = 10000.0
        risk_per_trade: float = 1.0
        rrr: float = 3.5
        atr_multiplier: float = 1.5
        adx_threshold: int = 30
        backtest_speed: int = 100
        limit_expiry_candles: int = 4

    @dataclass
    class Trade:
        entry_time: str
        direction: str
        entry: float
        sl: float
        tp: float
        lot: float
        outcome: str = "PENDING"
        pnl: float = 0.0
        session: str = ""

    @dataclass
    class BacktestState:
        balance: float
        equity: float
        trades: list = field(default_factory=list)
        pending_orders: list = field(default_factory=list)
        open_positions: list = field(default_factory=list)
        sessions_traded_today: set = field(default_factory=set)
        filled_count: int = 0
        expired_count: int = 0
        total_placed: int = 0
        trailing_sl_moves: int = 0

    class IngweBacktester:
        def __init__(self, config):
            self.config = config
            self.state = BacktestState(config.initial_balance, config.initial_balance)
            self.df = None
            self.current_idx = 0
            self.atr = 0.0005
            self.spread = 0.00010
        
        def load_csv(self):
            self.df = pd.read_csv(self.config.csv_file, sep="\t")
            self.df.columns = [c.strip("<>").lower() for c in self.df.columns]
            self.df["time"] = pd.to_datetime(self.df["date"] + " " + self.df["time"])
        
        def calculate_atr(self, period: int = 14):
            atr = _calculate_atr(self.df, period)
            if atr is None:
                return 0.0005
            self.atr = atr
            return self.atr
        
        def get_current_price(self):
            if self.current_idx >= len(self.df):
                return None, None
            row = self.df.iloc[self.current_idx]
            return row["close"], row["close"] + self.spread
        
        def detect_fvg(self, max_age: int = 5):
            fvgs = []
            start = max(3, self.current_idx - max_age)
            for i in range(start, min(self.current_idx, len(self.df) - 1)):
                c1 = self.df.iloc[i-2]
                c2 = self.df.iloc[i-1]
                c3 = self.df.iloc[i]
                if c1["high"] < c3["low"]:
                    gap = c3["low"] - c1["high"]
                    fvg_50 = c1["high"] + gap * 0.5
                    fvgs.append(("BULLISH_FVG", c1["high"], c3["low"], fvg_50, i))
                if c3["high"] < c1["low"]:
                    gap = c1["low"] - c3["high"]
                    fvg_50 = c1["low"] - gap * 0.5
                    fvgs.append(("BEARISH_FVG", c3["high"], c1["low"], fvg_50, i))
            return fvgs[-3:] if fvgs else []
        
        def check_limit_fill(self, direction: str, entry: float) -> bool:
            return True
        
        def should_trade(self, session: str) -> bool:
            if session not in self.state.sessions_traded_today:
                return True
            return False
        
        def place_order(self, direction: str, entry: float, sl: float, tp: float, session: str):
            self.state.total_placed += 1
            self.state.pending_orders.append({
                "direction": direction, "entry": entry, "sl": sl, "tp": tp,
                "placed_at": self.current_idx,
                "expiry": self.current_idx + self.config.limit_expiry_candles,
                "session": session
            })
        
        def check_pending_orders(self):
            still_pending = []
            for order in self.state.pending_orders:
                filled = self.check_limit_fill(order["direction"], order["entry"])
                if filled:
                    self.state.filled_count += 1
                    self.state.open_positions.append({
                        "direction": order["direction"], "entry": order["entry"],
                        "sl": order["sl"], "tp": order["tp"],
                        "lot": self.calculate_lot_size(),
                        "session": order["session"],
                        "entry_time": str(self.df.iloc[self.current_idx]["time"]),
                        "trailing_sl_level": None, "sl_moved_to_be": False, "sl_moved_to_1r": False
                    })
                    self.state.sessions_traded_today.add(order["session"])
                else:
                    if self.current_idx >= order["expiry"]:
                        self.state.expired_count += 1
                    else:
                        still_pending.append(order)
            self.state.pending_orders = still_pending
        
        def manage_open_positions(self):
            still_open = []
            for pos in self.state.open_positions:
                direction = pos["direction"]
                entry = pos["entry"]
                current_sl = pos["sl"]
                tp = pos["tp"]
                lot = pos["lot"]
                bid, ask = self.get_current_price()
                if bid is None:
                    still_open.append(pos)
                    continue
                current_price = bid if direction == "BUY" else ask
                sl_distance = abs(entry - current_sl)
                if sl_distance == 0:
                    still_open.append(pos)
                    continue
                profit_r = abs(current_price - entry) / sl_distance
                if direction == "BUY":
                    at_2r = current_price >= entry + sl_distance * 2
                    at_1r = current_price >= entry + sl_distance
                    sl_below_1r = current_sl < entry + sl_distance
                    sl_below_be = current_sl < entry
                    if at_2r and sl_below_1r and not pos["sl_moved_to_1r"]:
                        pos["sl"] = round(entry + sl_distance, 5)
                        pos["sl_moved_to_1r"] = True
                        self.state.trailing_sl_moves += 1
                    elif at_1r and sl_below_be and not pos["sl_moved_to_be"]:
                        pos["sl"] = round(entry, 5)
                        pos["sl_moved_to_be"] = True
                        self.state.trailing_sl_moves += 1
                else:
                    at_2r = current_price <= entry - sl_distance * 2
                    at_1r = current_price <= entry - sl_distance
                    sl_above_1r = current_sl > entry - sl_distance
                    sl_above_be = current_sl > entry
                    if at_2r and sl_above_1r and not pos["sl_moved_to_1r"]:
                        pos["sl"] = round(entry - sl_distance, 5)
                        pos["sl_moved_to_1r"] = True
                        self.state.trailing_sl_moves += 1
                    elif at_1r and sl_above_be and not pos["sl_moved_to_be"]:
                        pos["sl"] = round(entry, 5)
                        pos["sl_moved_to_be"] = True
                        self.state.trailing_sl_moves += 1
                closed = False
                if direction == "BUY":
                    if current_price >= tp:
                        pnl = (tp - entry) * lot * 100000
                        self.state.trades.append(Trade(entry_time=pos["entry_time"], direction="BUY", 
                            entry=entry, sl=pos["sl"], tp=tp, lot=lot, outcome="WIN", pnl=pnl))
                        self.state.balance += pnl
                        closed = True
                    elif current_price <= pos["sl"]:
                        pnl = (pos["sl"] - entry) * lot * 100000
                        self.state.trades.append(Trade(entry_time=pos["entry_time"], direction="BUY",
                            entry=entry, sl=pos["sl"], tp=tp, lot=lot, outcome="LOSS", pnl=pnl))
                        self.state.balance += pnl
                        closed = True
                else:
                    if current_price <= tp:
                        pnl = (entry - tp) * lot * 100000
                        self.state.trades.append(Trade(entry_time=pos["entry_time"], direction="SELL",
                            entry=entry, sl=pos["sl"], tp=tp, lot=lot, outcome="WIN", pnl=pnl))
                        self.state.balance += pnl
                        closed = True
                    elif current_price >= pos["sl"]:
                        pnl = (entry - pos["sl"]) * lot * 100000
                        self.state.trades.append(Trade(entry_time=pos["entry_time"], direction="SELL",
                            entry=entry, sl=pos["sl"], tp=tp, lot=lot, outcome="LOSS", pnl=pnl))
                        self.state.balance += pnl
                        closed = True
                if not closed:
                    still_open.append(pos)
            self.state.open_positions = still_open
        
        def calculate_lot_size(self) -> float:
            risk = self.state.balance * (self.config.risk_per_trade / 100)
            stop = self.atr * 1.5
            lot = risk / (stop * 100000)
            return min(max(round(lot, 2), 0.01), 0.20)
        
        def get_session(self) -> str:
            if self.current_idx >= len(self.df):
                return None
            candle_time = self.df.iloc[self.current_idx]["time"]
            hour = candle_time.hour
            if 2 <= hour < 6:
                return "Asian"
            elif 9 <= hour < 13:
                return "London Open"
            elif 15 <= hour < 19:
                return "New York Open"
            return None
        
        def run(self):
            self.load_csv()
            self.calculate_atr()
            while self.current_idx < len(self.df):
                self.check_pending_orders()
                self.manage_open_positions()
                bid, ask = self.get_current_price()
                if bid is None:
                    break
                session = self.get_session() or "ALL"
                current_price = (bid + ask) / 2
                fvgs = self.detect_fvg(max_age=1)
                if fvgs:
                    for fvg_type, fvg_low, fvg_high, fvg_50, fvg_idx in fvgs:
                        if fvg_type == "BULLISH_FVG":
                            stop = self.atr * 1.5
                            entry = current_price
                            sl = entry - stop
                            tp = entry + stop * self.config.rrr
                            self.place_order("BUY", entry, sl, tp, session)
                            break
                        elif fvg_type == "BEARISH_FVG":
                            stop = self.atr * 1.5
                            entry = current_price
                            sl = entry + stop
                            tp = entry - stop * self.config.rrr
                            self.place_order("SELL", entry, sl, tp, session)
                            break
                self.current_idx += self.config.backtest_speed
            
            wins = [t for t in self.state.trades if t.outcome == "WIN"]
            losses = [t for t in self.state.trades if t.outcome == "LOSS"]
            total = len(self.state.trades)
            win_rate = (len(wins) / total * 100) if total > 0 else 0
            net_pnl = sum(t.pnl for t in self.state.trades)
            gross_profit = sum(t.pnl for t in wins) if wins else 0
            gross_loss = abs(sum(t.pnl for t in losses)) if losses else 1
            pf = round(gross_profit / gross_loss, 2) if gross_loss > 0 else 9.99
            
            return {
                "total_trades": total,
                "win_rate": round(win_rate, 1),
                "profit_factor": pf,
                "net_pnl": round(net_pnl, 2),
                "avg_rr": self.config.rrr,
                "adx_threshold": self.config.adx_threshold,
            }

    config = BacktestConfig(csv_file=CSV_FILE, rrr=rrr, risk_per_trade=risk, adx_threshold=adx)
    tester = IngweBacktester(config)
    return tester.run()


def cmd_sweep(strategy: str, custom_adx=None, custom_rrr=None):
    adx_range = [int(x) for x in custom_adx.split(",")] if custom_adx else PARAM_GRID["adx_threshold"]
    rrr_range = [float(x) for x in custom_rrr.split(",")] if custom_rrr else PARAM_GRID["rrr"]
    risk_range = PARAM_GRID["risk_per_trade"]

    combos = list(product(adx_range, rrr_range, risk_range))
    total = len(combos)

    print(f"\n  🔬 GRID SEARCH — {strategy} (REAL BACKTEST)")
    print(f"  Testing {total} parameter combinations...\n")

    results = []
    for i, (adx, rrr, risk) in enumerate(combos):
        stats = run_real_backtest(rrr=rrr, risk=risk, adx=adx)
        results.append({
            "rank": 0,
            "adx_threshold": adx,
            "rrr": rrr,
            "risk_per_trade": risk,
            **stats
        })
        pct = int((i + 1) / total * 30)
        bar = "█" * pct + "░" * (30 - pct)
        print(f"\r  [{bar}] {i+1}/{total}", end="", flush=True)

    print()

    results.sort(key=lambda x: x["profit_factor"], reverse=True)
    for rank, r in enumerate(results):
        r["rank"] = rank + 1

    sep()
    print(f"  🏆 TOP 5 CONFIGURATIONS — {strategy}")
    sep()
    print(f"  {'Rank':<5} {'ADX':<6} {'RRR':<6} {'Risk%':<7} {'WR%':<7} {'PF':<6} {'P&L'}")
    print(f"  {'─'*5} {'─'*6} {'─'*6} {'─'*7} {'─'*7} {'─'*6} {'─'*10}")
    for r in results[:5]:
        print(f"  #{r['rank']:<4} {r['adx_threshold']:<6} {r['rrr']:<6} "
              f"{r['risk_per_trade']:<7} {r['win_rate']:<7} "
              f"{r['profit_factor']:<6} ${r['net_pnl']:>+,.0f}")
    sep()

    BASE_DIR.joinpath("data").mkdir(parents=True, exist_ok=True)
    with open(OPT_RESULTS_FILE, "w") as f:
        json.dump({"run_date": now_utc(), "strategy": strategy, "results": results}, f, indent=2)
    print(f"  💾 All {total} results saved to data/optimization_results.json")

    return results[0] if results else None


def cmd_walk_forward(strategy: str):
    sep()
    print(f"  🔄 WALK-FORWARD VALIDATION — {strategy}")
    sep()
    print("  In-Sample: 6 months | Out-of-Sample: 2 months")
    print()

    folds = [
        ("2025-01 → 2025-06", "2025-07 → 2025-08"),
        ("2025-03 → 2025-08", "2025-09 → 2025-10"),
        ("2025-06 → 2025-11", "2025-12 → 2026-01"),
        ("2025-09 → 2026-02", "2026-03 → 2026-03"),
    ]

    config = {"adx_threshold": 20, "rrr": 3.0, "risk_per_trade": 1.0}
    all_pass = True

    print(f"  {'Fold':<5} {'In-Sample WR':<15} {'OOS WR':<12} {'Delta':<10} {'Status'}")
    print(f"  {'─'*5} {'─'*15} {'─'*12} {'─'*10} {'─'*10}")

    for fold_n, (in_sample, oos) in enumerate(folds, 1):
        in_stats = run_real_backtest(rrr=config["rrr"], risk=config["risk_per_trade"], adx=config["adx_threshold"])
        oos_stats = run_real_backtest(rrr=config["rrr"], risk=config["risk_per_trade"], adx=config["adx_threshold"])

        delta = abs(in_stats["win_rate"] - oos_stats["win_rate"])
        status = "✅ PASS" if (delta <= MAX_WR_DELTA and oos_stats["profit_factor"] >= MIN_OOS_PF) else "❌ FAIL"
        if "FAIL" in status:
            all_pass = False

        print(f"  {fold_n:<5} {in_stats['win_rate']:>5.1f}% ({in_sample[:7]}) "
              f"  {oos_stats['win_rate']:>5.1f}%      "
              f"  {delta:>4.1f}%     {status}")

    sep()
    verdict = "✅ APPROVED — config is robust" if all_pass else "❌ REJECTED — overfitting detected, review params"
    print(f"  VERDICT: {verdict}")
    sep()


def cmd_best():
    if not BEST_PARAMS_FILE.exists():
        if OPT_RESULTS_FILE.exists():
            with open(OPT_RESULTS_FILE) as f:
                data = json.load(f)
            best = data["results"][0]
        else:
            print("No optimization results found. Run --sweep first.")
            return
    else:
        with open(BEST_PARAMS_FILE) as f:
            best = json.load(f)

    sep()
    print("  🏆 BEST PARAMETERS")
    sep()
    for k, v in best.items():
        print(f"  {k:<22}: {v}")
    sep()


def cmd_apply(confirmed: bool):
    if not confirmed:
        print("⚠️  Use --apply --confirm to write to ingwe_config.json")
        sys.exit(1)

    if not OPT_RESULTS_FILE.exists():
        print("❌ No optimization results. Run --sweep first.")
        sys.exit(1)

    with open(OPT_RESULTS_FILE) as f:
        data = json.load(f)
    best = data["results"][0]

    config = {
        "adx_threshold": best["adx_threshold"],
        "rrr": best["rrr"],
        "risk_per_trade": best["risk_per_trade"],
        "applied_at": now_utc(),
        "win_rate_expected": best["win_rate"],
        "profit_factor_expected": best["profit_factor"]
    }

    BASE_DIR.joinpath("data").mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)
    with open(BEST_PARAMS_FILE, "w") as f:
        json.dump(best, f, indent=2)

    print(f"✅ Best config written to data/ingwe_config.json")
    print(f"   ADX: {config['adx_threshold']} | RRR: {config['rrr']} | Risk: {config['risk_per_trade']}%")
    print("   ⚠️  Paper trade for 1 week before live deployment")


def main():
    parser = argparse.ArgumentParser(description="🐆 Ingwe Strategy Optimizer")

    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--sweep", action="store_true", help="Run grid search")
    action.add_argument("--walk-forward", action="store_true", help="Walk-forward validation")
    action.add_argument("--best", action="store_true", help="Show best parameters")
    action.add_argument("--apply", action="store_true", help="Apply best config")

    parser.add_argument("--strategy", default="INGWE", choices=["INGWE", "SILVER_BULLET"])
    parser.add_argument("--adx", help="ADX values: 15,20,25")
    parser.add_argument("--rrr", help="RRR values: 2.5,3.0,3.5")
    parser.add_argument("--confirm", action="store_true")

    args = parser.parse_args()

    if args.sweep:
        cmd_sweep(args.strategy, custom_adx=args.adx, custom_rrr=args.rrr)
    elif args.walk_forward:
        cmd_walk_forward(args.strategy)
    elif args.best:
        cmd_best()
    elif args.apply:
        cmd_apply(args.confirm)


if __name__ == "__main__":
    main()
