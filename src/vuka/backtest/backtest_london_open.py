"""
backtest_london_open.py — LONDON_OPEN Strategy Performance Backtest
Project Vuka | Replays ingwe.py LONDON_OPEN strategy on GBPUSD M15 historical CSV

Usage:
    python backtest_london_open.py [--csv data/sessions/GBPUSDc_M15_*.csv] [--balance 10000] [--rrr 3.0]
"""

import argparse
import json
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

BASE_DIR = Path(__file__).parent

# Project root, so this script works whether run standalone or as part of the package
_PROJECT_ROOT = BASE_DIR.resolve().parents[2]
if str(_PROJECT_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT / "src"))

from vuka.core.config import calculate_confluence_score, get_confluence_threshold, load_config
from vuka.market_structure.ict import calculate_adx_wilder

CSV_DIR = BASE_DIR / "data" / "sessions"
DEFAULT_CSV = CSV_DIR / "GBPUSDc_M15_202601012200_202605110000.csv"
RESULTS_FILE = BASE_DIR / "data" / "london_open_backtest_results.json"


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
    adx_at_entry: float = 25.0
    fvg_type: str = ""
    sweep_type: str = ""
    confluence_score: int = 0
    exit_time: str = ""
    bars_held: int = 0


@dataclass
class BacktestState:
    balance: float
    equity: float
    trades: list = field(default_factory=list)
    open_positions: list = field(default_factory=list)
    sessions_traded_today: set = field(default_factory=set)
    trailing_sl_moves: int = 0


class LondonOpenBacktester:
    KILLZONES_WINTER = {"London Open": (10, 13)}
    KILLZONES_SUMMER = {"London Open": (9, 12)}

    def __init__(self, csv_path: Path, balance: float = 10000.0, rrr: float = 3.0,
                 risk_pct: float = 1.0, atr_mult: float = 3.0, adx_min: int = 25,
                 min_spread: float = 0.0002, hard_lot_cap: float = 0.20):
        self.csv_path = csv_path
        self.state = BacktestState(balance, balance)
        self.config = {
            "initial_balance": balance,
            "rrr": rrr,
            "risk_pct": risk_pct,
            "atr_mult": atr_mult,
            "adx_min": adx_min,
            "min_spread": min_spread,
            "hard_lot_cap": hard_lot_cap,
        }
        self.df = None
        self.current_idx = 0
        load_config("GBPUSD", "LONDON_OPEN", "GBPUSD_LONDON_OPEN", "GBPUSD")
        self.atr = 0.0005
        self.adx = None
        self.spread = min_spread
        self.adx_values = []
        self.ema10 = None
        self.ema30 = None
        self.h1_bullish = None
        self.start_time = None

    def load_csv(self):
        if not self.csv_path.exists():
            alt = sorted(CSV_DIR.glob("GBPUSDc_M15_*.csv"))
            if alt:
                self.csv_path = alt[-1]
                print(f"[INFO] Using most recent CSV: {self.csv_path.name}")
            else:
                print(f"[ERROR] No GBPUSD M15 CSV found in {CSV_DIR}")
                sys.exit(1)

        self.df = pd.read_csv(self.csv_path, sep="\t")
        self.df.columns = [c.strip("<>").lower() for c in self.df.columns]
        self.df["time"] = pd.to_datetime(self.df["date"] + " " + self.df["time"])
        self.df = self.df.sort_values("time").reset_index(drop=True)
        print(f"[OK] Loaded {len(self.df)} candles from {self.csv_path.name}")

    def is_eu_summer(self) -> bool:
        ref = self.df.iloc[self.current_idx]["time"] if self.current_idx < len(self.df) else datetime.now()
        if isinstance(ref, str):
            ref = pd.to_datetime(ref)
        march = datetime(ref.year, 3, 31 - (ref.year % 4), tzinfo=timezone.utc)
        sunday = march - timedelta(days=march.weekday() + 1 - 6) if march.weekday() != 6 else march
        oct_ = datetime(ref.year, 10, 31 - (ref.year % 4), tzinfo=timezone.utc)
        last_sun = oct_ - timedelta(days=oct_.weekday() + 1 - 6) if oct_.weekday() != 6 else oct_
        return sunday <= ref.replace(tzinfo=timezone.utc) < last_sun

    def get_killzones(self):
        return self.KILLZONES_SUMMER if self.is_eu_summer() else self.KILLZONES_WINTER

    def get_session(self) -> str | None:
        if self.current_idx >= len(self.df):
            return None
        hour = self.df.iloc[self.current_idx]["time"].hour
        kz = self.get_killzones()
        for name, (start, end) in kz.items():
            if start <= hour < end:
                return name
        return None

    def calculate_atr(self, period: int = 14):
        if len(self.df) < period + 1:
            return 0.0005
        high = self.df["high"].values
        low = self.df["low"].values
        close = self.df["close"].values
        tr = [max(high[i] - low[i], abs(high[i] - close[i-1]), abs(low[i] - close[i-1])) for i in range(1, min(period + 1, len(high)))]
        self.atr = sum(tr) / len(tr) if tr else 0.0005
        return self.atr

    def calculate_ema(self, period: int = 20) -> float:
        if self.current_idx < period:
            return self.df.iloc[self.current_idx]["close"]
        close = self.df["close"].values[:self.current_idx]
        ema = close[0]
        mul = 2 / (period + 1)
        for price in close[1:]:
            ema = (price - ema) * mul + ema
        return ema

    def compute_indicators(self):
        self.ema10 = self.calculate_ema(10)
        self.ema30 = self.calculate_ema(30)
        self.h1_bullish = self.ema10 > self.ema30 if (self.ema10 is not None and self.ema30 is not None) else None
        window = self.df.iloc[max(0, self.current_idx - 60):self.current_idx + 1]
        adx, _plus_di, _minus_di = calculate_adx_wilder(window)
        self.adx = adx

    def detect_sweep(self):
        if self.current_idx < 4:
            return None, None
        start = max(0, self.current_idx - 4)
        recent = self.df.iloc[start:self.current_idx + 1]
        if len(recent) < 5:
            return None, None
        prev_high = recent["high"].iloc[:-1].max()
        prev_low = recent["low"].iloc[:-1].min()
        last = recent.iloc[-1]
        if last["high"] > prev_high * 1.0001:
            return "SWEEP_HIGH", prev_high
        if last["low"] < prev_low * 0.9999:
            return "SWEEP_LOW", prev_low
        return None, None

    def detect_fvg(self, max_age: int = 20):
        fvgs = []
        start = max(3, self.current_idx - max_age)
        end = min(self.current_idx + 1, len(self.df))
        for i in range(start, end):
            if i < 2:
                continue
            c1 = self.df.iloc[i - 2]
            c2 = self.df.iloc[i - 1]
            c3 = self.df.iloc[i]
            if c1["high"] < c3["low"]:
                gap = c3["low"] - c1["high"]
                fvgs.append(("BULLISH_FVG", c1["high"], c3["low"], gap, i))
            if c3["high"] < c1["low"]:
                gap = c1["low"] - c3["high"]
                fvgs.append(("BEARISH_FVG", c3["high"], c1["low"], gap, i))
        return fvgs[-3:] if fvgs else []

    def check_premium_discount_zone(self, price: float, direction: str) -> bool:
        start = max(0, self.current_idx - 20)
        recent = self.df.iloc[start:self.current_idx + 1]
        hi, lo = recent["high"].max(), recent["low"].min()
        mid = lo + (hi - lo) * 0.5
        if direction == "BUY":
            return price <= mid
        return price >= mid

    def calculate_lot(self) -> float:
        risk_dollars = self.state.balance * (self.config["risk_pct"] / 100)
        stop_pips = self.atr * self.config["atr_mult"]
        lot = risk_dollars / (stop_pips * 100000) if stop_pips > 0 else 0.01
        return min(max(round(lot, 2), 0.01), self.config["hard_lot_cap"])

    def place_trade(self, direction: str, entry: float, sl: float, tp: float,
                    session: str, fvg_type: str, sweep_type: str, score: int):
        lot = self.calculate_lot()
        self.state.open_positions.append({
            "direction": direction, "entry": entry, "sl": sl, "tp": tp,
            "lot": lot, "session": session, "fvg_type": fvg_type,
            "sweep_type": sweep_type, "score": score,
            "entry_time": str(self.df.iloc[self.current_idx]["time"]),
            "entry_idx": self.current_idx,
            "trailing_sl_level": None, "sl_moved_to_be": False, "sl_moved_to_1r": False,
            "adx_at_entry": self.adx if self.adx is not None else 25.0
        })
        self.state.sessions_traded_today.add(session)

        label = "B" if direction == "BUY" else "S"
        ts = self.df.iloc[self.current_idx]["time"]
        print(f"  [{ts}] {label} @ {entry:.5f} SL={sl:.5f} TP={tp:.5f} "
              f"Lot={lot} Score={score} {fvg_type} {sweep_type}")

    def manage_positions(self):
        still_open = []
        for pos in self.state.open_positions:
            direction = pos["direction"]
            entry = pos["entry"]
            current_sl = pos["sl"]
            tp = pos["tp"]
            lot = pos["lot"]
            current_price = self.df.iloc[self.current_idx]["close"]
            sl_dist = abs(entry - current_sl)
            if sl_dist == 0:
                still_open.append(pos)
                continue

            profit_r = abs(current_price - entry) / sl_dist

            if direction == "BUY":
                if profit_r >= 2 and current_sl < entry + sl_dist and not pos["sl_moved_to_1r"]:
                    pos["sl"] = round(entry + sl_dist, 5)
                    pos["sl_moved_to_1r"] = True
                    self.state.trailing_sl_moves += 1
                elif profit_r >= 1 and current_sl < entry and not pos["sl_moved_to_be"]:
                    pos["sl"] = round(entry, 5)
                    pos["sl_moved_to_be"] = True
                    self.state.trailing_sl_moves += 1

                if current_price >= tp:
                    pnl = (tp - entry) * lot * 100000
                    exit_idx = self.current_idx
                    self.state.trades.append(Trade(
                        entry_time=pos["entry_time"], direction="BUY",
                        entry=entry, sl=pos["sl"], tp=tp, lot=lot,
                        outcome="WIN", pnl=pnl, session=pos["session"],
                        fvg_type=pos["fvg_type"], sweep_type=pos["sweep_type"],
                        confluence_score=pos["score"],
                        adx_at_entry=pos.get("adx_at_entry", 25.0),
                        exit_time=str(self.df.iloc[exit_idx]["time"]),
                        bars_held=exit_idx - pos["entry_idx"]))
                    self.state.balance += pnl
                    ts = self.df.iloc[exit_idx]["time"]
                    print(f"  [{ts}] WIN {direction} +${pnl:.2f} ({pos['fvg_type']} {pos['sweep_type']})")
                    continue
                elif current_price <= pos["sl"]:
                    pnl = (pos["sl"] - entry) * lot * 100000
                    exit_idx = self.current_idx
                    outcome = "WIN" if pnl > 0 else ("BE" if pnl == 0 else "LOSS")
                    self.state.trades.append(Trade(
                        entry_time=pos["entry_time"], direction="BUY",
                        entry=entry, sl=pos["sl"], tp=tp, lot=lot,
                        outcome=outcome, pnl=pnl, session=pos["session"],
                        fvg_type=pos["fvg_type"], sweep_type=pos["sweep_type"],
                        confluence_score=pos["score"],
                        adx_at_entry=pos.get("adx_at_entry", 25.0),
                        exit_time=str(self.df.iloc[exit_idx]["time"]),
                        bars_held=exit_idx - pos["entry_idx"]))
                    self.state.balance += pnl
                    ts = self.df.iloc[exit_idx]["time"]
                    print(f"  [{ts}] {outcome} {direction} {pnl:+.2f} ({pos['fvg_type']} {pos['sweep_type']})")
                    continue
            else:
                if profit_r >= 2 and current_sl > entry - sl_dist and not pos["sl_moved_to_1r"]:
                    pos["sl"] = round(entry - sl_dist, 5)
                    pos["sl_moved_to_1r"] = True
                    self.state.trailing_sl_moves += 1
                elif profit_r >= 1 and current_sl > entry and not pos["sl_moved_to_be"]:
                    pos["sl"] = round(entry, 5)
                    pos["sl_moved_to_be"] = True
                    self.state.trailing_sl_moves += 1

                if current_price <= tp:
                    pnl = (entry - tp) * lot * 100000
                    exit_idx = self.current_idx
                    self.state.trades.append(Trade(
                        entry_time=pos["entry_time"], direction="SELL",
                        entry=entry, sl=pos["sl"], tp=tp, lot=lot,
                        outcome="WIN", pnl=pnl, session=pos["session"],
                        fvg_type=pos["fvg_type"], sweep_type=pos["sweep_type"],
                        confluence_score=pos["score"],
                        adx_at_entry=pos.get("adx_at_entry", 25.0),
                        exit_time=str(self.df.iloc[exit_idx]["time"]),
                        bars_held=exit_idx - pos["entry_idx"]))
                    self.state.balance += pnl
                    ts = self.df.iloc[exit_idx]["time"]
                    print(f"  [{ts}] WIN {direction} +${pnl:.2f} ({pos['fvg_type']} {pos['sweep_type']})")
                    continue
                elif current_price >= pos["sl"]:
                    pnl = (entry - pos["sl"]) * lot * 100000
                    exit_idx = self.current_idx
                    outcome = "WIN" if pnl > 0 else ("BE" if pnl == 0 else "LOSS")
                    self.state.trades.append(Trade(
                        entry_time=pos["entry_time"], direction="SELL",
                        entry=entry, sl=pos["sl"], tp=tp, lot=lot,
                        outcome=outcome, pnl=pnl, session=pos["session"],
                        fvg_type=pos["fvg_type"], sweep_type=pos["sweep_type"],
                        confluence_score=pos["score"],
                        adx_at_entry=pos.get("adx_at_entry", 25.0),
                        exit_time=str(self.df.iloc[exit_idx]["time"]),
                        bars_held=exit_idx - pos["entry_idx"]))
                    self.state.balance += pnl
                    ts = self.df.iloc[exit_idx]["time"]
                    print(f"  [{ts}] {outcome} {direction} {pnl:+.2f} ({pos['fvg_type']} {pos['sweep_type']})")
                    continue

            still_open.append(pos)
        self.state.open_positions = still_open

    def run(self):
        self.start_time = time.time()
        print(f"\n{'='*65}")
        print(f"  LONDON OPEN STRATEGY BACKTEST")
        print(f"{'='*65}")
        self.load_csv()
        self.calculate_atr()
        print(f"  Initial Balance : ${self.config['initial_balance']:,.2f}")
        print(f"  Risk/Trade      : {self.config['risk_pct']}%")
        print(f"  RRR             : {self.config['rrr']}")
        print(f"  ATR Multiplier  : {self.config['atr_mult']}")
        print(f"  ATR             : {self.atr:.5f}")
        print(f"  Scan Interval   : M15 (each candle = 1 scan)")
        print(f"  Session Filter  : London Open only")
        print(f"{'='*65}\n")

        n = len(self.df)
        _last_date = None
        while self.current_idx < n:
            current_time = self.df.iloc[self.current_idx]["time"]
            current_date = current_time.date()
            if _last_date is not None and current_date != _last_date:
                self.state.sessions_traded_today.clear()
            _last_date = current_date

            session = self.get_session()
            self.manage_positions()

            if session and session not in self.state.sessions_traded_today:
                self.compute_indicators()
                sweep, sweep_level = self.detect_sweep()
                fvgs = self.detect_fvg(max_age=20)

                if sweep and fvgs:
                    price = self.df.iloc[self.current_idx]["close"]
                    spread_ok = True
                    level_sweep = False
                    htf_ok = self.h1_bullish is not None
                    bos = False

                    for fvg_type, fvg_low, fvg_high, fvg_gap, fvg_idx in fvgs:
                        if sweep == "SWEEP_LOW" and fvg_type == "BULLISH_FVG":
                            if not self.check_premium_discount_zone(price, "BUY"):
                                continue
                            trend = "BULLISH" if self.h1_bullish else "UNKNOWN"
                            score = calculate_confluence_score(
                                trend, True, True, spread_ok, True,
                                level_sweep, bos, htf_ok, session, "BUY"
                            )
                            threshold = max(40, min(90, get_confluence_threshold(self.adx, session, "BUY")))
                            if score >= threshold:
                                stop = self.atr * self.config["atr_mult"]
                                entry = price
                                sl = entry - stop
                                tp = entry + stop * self.config["rrr"]
                                self.place_trade("BUY", entry, sl, tp, session, fvg_type, sweep, score)
                                break

                        elif sweep == "SWEEP_HIGH" and fvg_type == "BEARISH_FVG":
                            if not self.check_premium_discount_zone(price, "SELL"):
                                continue
                            trend = "BEARISH" if not self.h1_bullish else "UNKNOWN"
                            score = calculate_confluence_score(
                                trend, True, True, spread_ok, True,
                                level_sweep, bos, htf_ok, session, "SELL"
                            )
                            threshold = max(40, min(90, get_confluence_threshold(self.adx, session, "SELL")))
                            if score >= threshold:
                                stop = self.atr * self.config["atr_mult"]
                                entry = price
                                sl = entry + stop
                                tp = entry - stop * self.config["rrr"]
                                self.place_trade("SELL", entry, sl, tp, session, fvg_type, sweep, score)
                                break

            self.current_idx += 1

            if self.current_idx % 2000 == 0:
                elapsed = time.time() - self.start_time
                print(f"  Progress: {self.current_idx}/{n} candles | "
                      f"Balance: ${self.state.balance:,.2f} | "
                      f"Trades: {len(self.state.trades)} | "
                      f"Elapsed: {elapsed:.0f}s")

        self.current_idx = n - 1
        for _ in range(5):
            self.manage_positions()

        self.print_results()
        self.save_results()

    def compute_metrics(self):
        wins = [t for t in self.state.trades if t.outcome == "WIN"]
        losses = [t for t in self.state.trades if t.outcome == "LOSS"]
        be = [t for t in self.state.trades if t.outcome == "BE"]
        total = len(self.state.trades)
        decided = len(wins) + len(losses)
        win_rate = (len(wins) / decided * 100) if decided > 0 else 0
        net_pnl = sum(t.pnl for t in self.state.trades)

        gross_profit = sum(t.pnl for t in wins)
        gross_loss = abs(sum(t.pnl for t in losses))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf')

        peak = self.config["initial_balance"]
        drawdown = 0
        max_dd = 0
        running = self.config["initial_balance"]
        for t in self.state.trades:
            running += t.pnl
            if running > peak:
                peak = running
            dd = (peak - running) / peak * 100 if peak > 0 else 0
            if dd > max_dd:
                max_dd = dd
            drawdown = dd

        avg_rr = 0
        if losses:
            avg_win = gross_profit / len(wins) if wins else 0
            avg_loss = gross_loss / len(losses) if losses else 1
            avg_rr = avg_win / avg_loss if avg_loss > 0 else 0

        returns = [t.pnl / self.config["initial_balance"] * 100 for t in self.state.trades]
        avg_return = np.mean(returns) if returns else 0
        std_return = np.std(returns) if returns and len(returns) > 1 else 1
        sharpe = (avg_return / std_return * np.sqrt(252)) if std_return > 0 else 0

        return {
            "total": total, "wins": len(wins), "losses": len(losses),
            "be": len(be),
            "win_rate": round(win_rate, 1),
            "net_pnl": round(net_pnl, 2),
            "final_balance": round(self.state.balance, 2),
            "return_pct": round((self.state.balance - self.config["initial_balance"]) / self.config["initial_balance"] * 100, 2),
            "profit_factor": round(profit_factor, 2),
            "max_drawdown_pct": round(max_dd, 2),
            "avg_rr": round(avg_rr, 2),
            "sharpe_ratio": round(sharpe, 2),
            "trailing_sl_moves": self.state.trailing_sl_moves,
            "open_positions_end": len(self.state.open_positions),
        }

    def print_results(self):
        m = self.compute_metrics()
        dt = time.time() - self.start_time
        print(f"\n{'='*65}")
        print(f"  LONDON OPEN BACKTEST RESULTS")
        print(f"{'='*65}")
        print(f"  CSV File        : {self.csv_path.name}")
        print(f"  Candles Scanned : {len(self.df)}")
        print(f"  London Sessions : {sum(1 for i in range(len(self.df)) if self.get_session_for_idx(i) == 'London Open')}")
        print(f"  Duration        : {dt:.0f}s")
        print(f"  ")
        print(f"  TRADES")
        print(f"  Total Trades    : {m['total']}")
        print(f"  Wins / Losses   : {m['wins']} / {m['losses']}")
        print(f"  Breakeven       : {m['be']}")
        print(f"  Win Rate        : {m['win_rate']}%")
        print(f"  Avg RR          : {m['avg_rr']}:1")
        print(f"  ")
        print(f"  P&L")
        print(f"  Net P&L         : ${m['net_pnl']:+,.2f}")
        print(f"  Final Balance   : ${m['final_balance']:,.2f}")
        print(f"  Return          : {m['return_pct']:+.2f}%")
        print(f"  Profit Factor   : {m['profit_factor']}")
        print(f"  ")
        print(f"  RISK")
        print(f"  Max Drawdown    : {m['max_drawdown_pct']:.2f}%")
        print(f"  Sharpe Ratio    : {m['sharpe_ratio']}")
        print(f"  Open Positions  : {m['open_positions_end']}")
        print(f"  Trailing SL Moves : {m['trailing_sl_moves']}")
        print(f"{'='*65}\n")

    def get_session_for_idx(self, idx: int):
        if idx >= len(self.df): return None
        hour = self.df.iloc[idx]["time"].hour
        kz = self.get_killzones()
        for name, (start, end) in kz.items():
            if start <= hour < end:
                return name
        return None

    def save_results(self):
        m = self.computed_metrics if hasattr(self, 'computed_metrics') else self.compute_metrics()
        trades_data = []
        for t in self.state.trades:
            trades_data.append({
                "entry_time": t.entry_time, "exit_time": t.exit_time,
                "direction": t.direction, "entry": t.entry, "sl": t.sl, "tp": t.tp,
                "lot": t.lot, "outcome": t.outcome, "pnl": round(t.pnl, 2),
                "session": t.session, "fvg_type": t.fvg_type, "sweep_type": t.sweep_type,
                "adx_at_entry": round(t.adx_at_entry, 2) if t.adx_at_entry is not None else None,
                "confluence_score": t.confluence_score, "bars_held": t.bars_held
            })

        results = {
            "run_date": datetime.now(timezone.utc).isoformat(),
            "strategy": "LONDON_OPEN",
            "symbol": "GBPUSD",
            "timeframe": "M15",
            "csv_file": str(self.csv_path.name),
            "config": self.config,
            "metrics": m,
            "trades": trades_data,
            "open_positions": len(self.state.open_positions),
        }

        RESULTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(RESULTS_FILE, "w") as f:
            json.dump(results, f, indent=2)
        print(f"[OK] Results saved to {RESULTS_FILE}")


def main():
    parser = argparse.ArgumentParser(description="LONDON_OPEN Strategy Backtest")
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV, help="GBPUSD M15 CSV path")
    parser.add_argument("--balance", type=float, default=10000.0, help="Initial balance")
    parser.add_argument("--rrr", type=float, default=3.0, help="Risk:Reward ratio")
    parser.add_argument("--risk", type=float, default=1.0, help="Risk percentage per trade")
    parser.add_argument("--atr-mult", type=float, default=3.0, help="ATR multiplier for SL")
    parser.add_argument("--adx-min", type=int, default=25, help="Minimum ADX threshold")

    args = parser.parse_args()
    bt = LondonOpenBacktester(args.csv, args.balance, args.rrr, args.risk, args.atr_mult, args.adx_min)
    bt.run()


if __name__ == "__main__":
    main()
