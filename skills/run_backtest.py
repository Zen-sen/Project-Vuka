#!/usr/bin/env python3
"""
run_backtest.py — Agent Ingwe CSV Backtest Runner
Project Vuka | Replays ingwe.py strategy logic on historical CSV data
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

BASE_DIR = Path(__file__).parent.parent
CSV_DEFAULT = BASE_DIR / "eurusd_m15_template.csv"
RESULTS_FILE = BASE_DIR / "data" / "backtest_results.json"


@dataclass
class BacktestConfig:
    csv_file: Path
    initial_balance: float = 10000.0
    risk_per_trade: float = 1.0
    rrr: float = 3.5
    atr_multiplier: float = 1.5
    adx_threshold: int = 30
    backtest_speed: int = 1
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
    def __init__(self, config: BacktestConfig):
        self.config = config
        self.state = BacktestState(config.initial_balance, config.initial_balance)
        self.df = None
        self.current_idx = 0
        self.atr = 0.0005
        self.spread = 0.00010
    
    def load_csv(self):
        if not self.config.csv_file.exists():
            print(f"[ERROR] CSV not found: {self.config.csv_file}")
            sys.exit(1)
        
        import pandas as pd
        self.df = pd.read_csv(self.config.csv_file, sep="\t")
        
        self.df.columns = [c.strip("<>").lower() for c in self.df.columns]
        self.df["time"] = pd.to_datetime(self.df["date"] + " " + self.df["time"])
        print(f"[OK] Loaded {len(self.df)} candles from {self.config.csv_file.name}")
    
    def calculate_atr(self, period: int = 14):
        if len(self.df) < period + 1:
            return 0.0005
        
        high = self.df["high"].values
        low = self.df["low"].values
        close = self.df["close"].values
        
        tr = [max(high[i] - low[i], 
                  abs(high[i] - close[i-1]), 
                  abs(low[i] - close[i-1])) 
              for i in range(1, len(high))]
        
        self.atr = sum(tr[:period]) / period
        return self.atr
    
    def calculate_ema(self, period: int = 20) -> float:
        if len(self.df) < period:
            return self.df.iloc[-1]["close"]
        close = self.df["close"].values
        ema = close[0]
        multiplier = 2 / (period + 1)
        for price in close[1:]:
            ema = (price - ema) * multiplier + ema
        return ema
    
    def calculate_adx(self, period: int = 14) -> float:
        if len(self.df) < period + 2:
            return 25
        
        high = self.df["high"].values
        low = self.df["low"].values
        close = self.df["close"].values
        
        tr = []
        plus_dm = []
        minus_dm = []
        
        for i in range(1, min(period + 1, len(high))):
            tr.append(max(high[i] - low[i], abs(high[i] - close[i-1]), abs(low[i] - close[i-1])))
            hd = high[i] - high[i-1]
            ld = low[i-1] - low[i]
            if hd > ld and hd > 0:
                plus_dm.append(hd)
            else:
                plus_dm.append(0)
            if ld > hd and ld > 0:
                minus_dm.append(ld)
            else:
                minus_dm.append(0)
        
        if not tr or sum(tr) == 0:
            return 25
        
        smoothed_tr = sum(tr)
        smoothed_plus_dm = sum(plus_dm)
        smoothed_minus_dm = sum(minus_dm)
        
        plus_di = (smoothed_plus_dm / smoothed_tr) * 100 if smoothed_tr > 0 else 0
        minus_di = (smoothed_minus_dm / smoothed_tr) * 100 if smoothed_tr > 0 else 0
        
        if plus_di + minus_di == 0:
            return 25
        
        dx = abs(plus_di - minus_di) / (plus_di + minus_di) * 100
        return dx
    
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
    
    def detect_sweep(self):
        if self.current_idx < 5:
            return None, None
        
        recent = self.df.iloc[self.current_idx-5:self.current_idx]
        prev_high = recent["high"].iloc[:-1].max()
        prev_low = recent["low"].iloc[:-1].min()
        last = self.df.iloc[self.current_idx - 1]
        
        if last["high"] > prev_high:
            return "SWEEP_HIGH", prev_high
        if last["low"] < prev_low:
            return "SWEEP_LOW", prev_low
        return None, None
    
    def check_limit_fill(self, direction: str, entry: float) -> bool:
        return True
    
    def should_trade(self, session: str) -> bool:
        if session not in self.state.sessions_traded_today:
            return True
        return False
    
    def place_order(self, direction: str, entry: float, sl: float, tp: float, session: str):
        self.state.total_placed += 1
        self.state.pending_orders.append({
            "direction": direction,
            "entry": entry,
            "sl": sl,
            "tp": tp,
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
                    "direction": order["direction"],
                    "entry": order["entry"],
                    "sl": order["sl"],
                    "tp": order["tp"],
                    "lot": self.calculate_lot_size(),
                    "session": order["session"],
                    "entry_time": str(self.df.iloc[self.current_idx]["time"]),
                    "trailing_sl_level": None,
                    "sl_moved_to_be": False,
                    "sl_moved_to_1r": False
                })
                
                self.state.sessions_traded_today.add(order["session"])
            else:
                if self.current_idx >= order["expiry"]:
                    self.state.expired_count += 1
                else:
                    still_pending.append(order)
        
        self.state.pending_orders = still_pending
    
    def manage_open_positions(self):
        """Trailing SL manager - v3.9.5 logic:
        - At 1:1 profit -> SL moves to breakeven (entry)
        - At 1:2 profit -> SL moves to 1:1 level
        """
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
                    new_sl = round(entry + sl_distance, 5)
                    pos["sl"] = new_sl
                    pos["sl_moved_to_1r"] = True
                    self.state.trailing_sl_moves += 1
                    print(f"    -> TRAIL 1:2->1:1 | SL moved to {new_sl:.5f}")
                elif at_1r and sl_below_be and not pos["sl_moved_to_be"]:
                    new_sl = round(entry, 5)
                    pos["sl"] = new_sl
                    pos["sl_moved_to_be"] = True
                    self.state.trailing_sl_moves += 1
                    print(f"    -> TRAIL 1:1->BE | SL moved to {new_sl:.5f}")
            
            else:
                at_2r = current_price <= entry - sl_distance * 2
                at_1r = current_price <= entry - sl_distance
                sl_above_1r = current_sl > entry - sl_distance
                sl_above_be = current_sl > entry
                
                if at_2r and sl_above_1r and not pos["sl_moved_to_1r"]:
                    new_sl = round(entry - sl_distance, 5)
                    pos["sl"] = new_sl
                    pos["sl_moved_to_1r"] = True
                    self.state.trailing_sl_moves += 1
                    print(f"    -> TRAIL 1:2->1:1 | SL moved to {new_sl:.5f}")
                elif at_1r and sl_above_be and not pos["sl_moved_to_be"]:
                    new_sl = round(entry, 5)
                    pos["sl"] = new_sl
                    pos["sl_moved_to_be"] = True
                    self.state.trailing_sl_moves += 1
                    print(f"    -> TRAIL 1:1->BE | SL moved to {new_sl:.5f}")
            
            closed = False
            if direction == "BUY":
                if current_price >= tp:
                    pnl = (tp - entry) * lot * 100000
                    trade = Trade(entry_time=pos["entry_time"], direction="BUY", 
                                 entry=entry, sl=pos["sl"], tp=tp, lot=lot, 
                                 outcome="WIN", pnl=pnl, session=pos["session"])
                    self.state.trades.append(trade)
                    self.state.balance += pnl
                    closed = True
                elif current_price <= pos["sl"]:
                    pnl = (pos["sl"] - entry) * lot * 100000
                    trade = Trade(entry_time=pos["entry_time"], direction="BUY",
                                 entry=entry, sl=pos["sl"], tp=tp, lot=lot,
                                 outcome="LOSS", pnl=pnl, session=pos["session"])
                    self.state.trades.append(trade)
                    self.state.balance += pnl
                    closed = True
            else:
                if current_price <= tp:
                    pnl = (entry - tp) * lot * 100000
                    trade = Trade(entry_time=pos["entry_time"], direction="SELL",
                                 entry=entry, sl=pos["sl"], tp=tp, lot=lot,
                                 outcome="WIN", pnl=pnl, session=pos["session"])
                    self.state.trades.append(trade)
                    self.state.balance += pnl
                    closed = True
                elif current_price >= pos["sl"]:
                    pnl = (entry - pos["sl"]) * lot * 100000
                    trade = Trade(entry_time=pos["entry_time"], direction="SELL",
                                 entry=entry, sl=pos["sl"], tp=tp, lot=lot,
                                 outcome="LOSS", pnl=pnl, session=pos["session"])
                    self.state.trades.append(trade)
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
        print(f"\n{'='*60}")
        print(f"  INGWE BACKTEST")
        print(f"{'='*60}")
        
        self.load_csv()
        self.calculate_atr()
        
        print(f"  Initial Balance: ${self.config.initial_balance:,.2f}")
        print(f"  Risk/Trade: {self.config.risk_per_trade}%")
        print(f"  RRR: {self.config.rrr}")
        print(f"  ATR: {self.atr:.5f}")
        print(f"{'='*60}\n")
        
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
                        print(f"  [{self.df.iloc[self.current_idx]['time']}] "
                              f"BUY @ {entry:.5f} | SL: {sl:.5f} | TP: {tp:.5f}")
                        break
                    
                    elif fvg_type == "BEARISH_FVG":
                        stop = self.atr * 1.5
                        entry = current_price
                        sl = entry + stop
                        tp = entry - stop * self.config.rrr
                        self.place_order("SELL", entry, sl, tp, session)
                        print(f"  [{self.df.iloc[self.current_idx]['time']}] "
                              f"SELL @ {entry:.5f} | SL: {sl:.5f} | TP: {tp:.5f}")
                        break
            
            self.current_idx += self.config.backtest_speed
            
            if self.current_idx % 100 == 0:
                print(f"  Progress: {self.current_idx}/{len(self.df)} candles | "
                      f"Balance: ${self.state.balance:,.2f}")
        
        self.print_results()
        self.save_results()
    
    def print_results(self):
        wins = [t for t in self.state.trades if t.outcome == "WIN"]
        losses = [t for t in self.state.trades if t.outcome == "LOSS"]
        
        total = len(self.state.trades)
        win_rate = (len(wins) / total * 100) if total > 0 else 0
        net_pnl = sum(t.pnl for t in self.state.trades)
        
        fill_rate = (self.state.filled_count / self.state.total_placed * 100) if self.state.total_placed > 0 else 0
        
        print(f"\n{'='*60}")
        print(f"  BACKTEST RESULTS")
        print(f"{'='*60}")
        print(f"  Total Candles    : {len(self.df)}")
        print(f"  Orders Placed   : {self.state.total_placed}")
        print(f"  Orders Filled   : {self.state.filled_count}")
        print(f"  Orders Expired  : {self.state.expired_count}")
        print(f"  Fill Rate       : {fill_rate:.1f}%")
        print(f"  ")
        print(f"  TRAILING SL")
        print(f"  SL Moves Total  : {self.state.trailing_sl_moves}")
        print(f"  Open Positions  : {len(self.state.open_positions)}")
        print(f"  ")
        print(f"  Trades Executed : {total}")
        print(f"  Wins / Losses   : {len(wins)} / {len(losses)}")
        print(f"  Win Rate        : {win_rate:.1f}%")
        print(f"  Net P&L         : ${net_pnl:+,.2f}")
        print(f"  Final Balance   : ${self.state.balance:,.2f}")
        print(f"  Return %        : {((self.state.balance - self.config.initial_balance) / self.config.initial_balance * 100):+.2f}%")
        print(f"{'='*60}\n")
    
    def save_results(self):
        import pandas as pd
        
        BASE_DIR.joinpath("data").mkdir(parents=True, exist_ok=True)
        
        trades_data = []
        for t in self.state.trades:
            trades_data.append({
                "entry_time": t.entry_time,
                "direction": t.direction,
                "entry": t.entry,
                "sl": t.sl,
                "tp": t.tp,
                "lot": t.lot,
                "outcome": t.outcome,
                "pnl": round(t.pnl, 2),
                "session": t.session
            })
        
        results = {
            "run_date": datetime.now(timezone.utc).isoformat(),
            "csv_file": str(self.config.csv_file.name),
            "initial_balance": self.config.initial_balance,
            "final_balance": round(self.state.balance, 2),
            "total_orders_placed": self.state.total_placed,
            "orders_filled": self.state.filled_count,
            "orders_expired": self.state.expired_count,
            "fill_rate_pct": round((self.state.filled_count / self.state.total_placed * 100) if self.state.total_placed > 0 else 0, 1),
            "trailing_sl_moves": self.state.trailing_sl_moves,
            "open_positions_at_end": len(self.state.open_positions),
            "total_trades": len(self.state.trades),
            "wins": len(wins := [t for t in self.state.trades if t.outcome == "WIN"]),
            "losses": len(losses := [t for t in self.state.trades if t.outcome == "LOSS"]),
            "win_rate_pct": round(len(wins) / len(self.state.trades) * 100, 1) if self.state.trades else 0,
            "net_pnl": round(net_pnl := sum(t.pnl for t in self.state.trades), 2),
            "trades": trades_data
        }
        
        with open(RESULTS_FILE, "w") as f:
            json.dump(results, f, indent=2)
        
        print(f"[OK] Results saved to {RESULTS_FILE}")


def main():
    parser = argparse.ArgumentParser(description="Ingwe Backtest Runner")
    parser.add_argument("--csv", type=Path, default=CSV_DEFAULT, help="CSV file path")
    parser.add_argument("--balance", type=float, default=10000.0, help="Initial balance")
    parser.add_argument("--risk", type=float, default=1.0, help="Risk %% per trade")
    parser.add_argument("--rrr", type=float, default=2.0, help="Risk:Reward ratio")
    parser.add_argument("--speed", type=int, default=1, help="Backtest speed (1=slow, 100=fast)")
    
    args = parser.parse_args()
    
    config = BacktestConfig(
        csv_file=args.csv,
        initial_balance=args.balance,
        risk_per_trade=args.risk,
        rrr=args.rrr,
        backtest_speed=args.speed
    )
    
    tester = IngweBacktester(config)
    tester.run()


if __name__ == "__main__":
    main()
