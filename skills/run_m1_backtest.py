#!/usr/bin/env python3
"""
run_m1_backtest.py — ICT M1 Scalper Backtester
Generic M1 backtest with ICT strategy, sessions, and Kronos filter simulation
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional
import numpy as np
import pandas as pd

BASE_DIR = Path(__file__).parent.parent
RESULTS_FILE = BASE_DIR / "data" / "m1_backtest_results.json"

sys.path.insert(0, str(BASE_DIR))
sys.path.insert(0, str(BASE_DIR / "src"))
from vuka.market_structure.ict import calculate_atr as _calculate_atr

ICT_M1_SESSIONS = {
    "Asian": (2, 6),
    "London": (9, 12),
    "NY_Open": (15, 18),
    "NY_Session": (18, 22),
    "Late_NY": (22, 2),
}

KRONOS_THRESHOLD = 0.50
KRONOS_ENABLED = True


@dataclass
class BacktestConfig:
    csv_file: Path
    initial_balance: float = 10000.0
    risk_per_trade: float = 1.0
    rrr: float = 2.0
    atr_multiplier: float = 1.0
    fvg_lookback: int = 40
    scan_interval: int = 15
    use_kronos: bool = True
    kronos_threshold: float = 0.50


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
    kronos_confidence: float = 0.5


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
    kronos_vetoed: int = 0
    trailing_sl_moves: int = 0
    last_trade_idx: int = 0


class M1Backtester:
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
        
        try:
            self.df = pd.read_csv(self.config.csv_file, sep="\t")
            self.df.columns = [c.strip("<>").lower() for c in self.df.columns]
            self.df["time"] = pd.to_datetime(self.df["date"] + " " + self.df["time"])
        except:
            self.df = pd.read_csv(self.config.csv_file)
            self.df.columns = [c.strip("<>").lower() for c in self.df.columns]
            self.df["time"] = pd.to_datetime(self.df["time"])
        
        if "tick_volume" in self.df.columns:
            self.df["volume"] = self.df["tick_volume"]
        
        print(f"[OK] Loaded {len(self.df)} candles from {self.config.csv_file.name}")
    
    def calculate_atr(self, period: int = 14):
        atr = _calculate_atr(self.df, period)
        if atr is None:
            return 0.0005
        self.atr = atr
        return self.atr
    
    def get_session(self) -> str:
        if self.current_idx >= len(self.df):
            return None
        
        candle_time = self.df.iloc[self.current_idx]["time"]
        hour = candle_time.hour
        
        for session, (start, end) in ICT_M1_SESSIONS.items():
            if session == "Late_NY":
                if hour >= start or hour < end:
                    return session
            else:
                if start <= hour < end:
                    return session
        return None
    
    def is_session_active(self, session: str) -> bool:
        if self.current_idx >= len(self.df):
            return False
        return self.get_session() == session
    
    def detect_fvg(self, max_age: int = 40):
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
        
        return fvgs[-5:] if fvgs else []
    
    def detect_order_blocks(self, lookback: int = 30):
        obs = []
        start = max(10, self.current_idx - lookback)
        
        for i in range(start, self.current_idx - 2):
            c1 = self.df.iloc[i]
            c2 = self.df.iloc[i + 1]
            c3 = self.df.iloc[i + 2]
            
            if c1["close"] < c1["open"]:
                if c2["high"] > c1["high"] and c3["high"] > c1["high"]:
                    obs.append(("BULLISH_OB", c1["low"], c1["high"], i))
            
            if c1["close"] > c1["open"]:
                if c2["low"] < c1["low"] and c3["low"] < c1["low"]:
                    obs.append(("BEARISH_OB", c1["low"], c1["high"], i))
        
        return obs[-5:] if obs else []
    
    def detect_sweep(self):
        if self.current_idx < 10:
            return None, None
        
        lookback = min(20, self.current_idx - 1)
        recent = self.df.iloc[self.current_idx-lookback:self.current_idx]
        prev_high = recent["high"].iloc[:-1].max()
        prev_low = recent["low"].iloc[:-1].min()
        last = self.df.iloc[self.current_idx - 1]
        
        if last["high"] > prev_high:
            return "SWEEP_HIGH", prev_high
        if last["low"] < prev_low:
            return "SWEEP_LOW", prev_low
        return None, None
    
    def simulate_kronos(self, direction: str) -> tuple[bool, float]:
        if not self.config.use_kronos:
            return True, 0.75
        
        if self.current_idx < 50:
            return True, 0.75
        
        recent = self.df.iloc[max(0, self.current_idx-50):self.current_idx]
        close_prices = recent["close"].values
        
        if len(close_prices) < 10:
            return True, 0.75
        
        first_half = close_prices[:len(close_prices)//2]
        second_half = close_prices[len(close_prices)//2:]
        
        trend = np.mean(second_half) - np.mean(first_half)
        direction_bool = trend >= 0
        
        trend_strength = abs(trend) / np.mean(close_prices)
        confidence = min(0.95, 0.5 + trend_strength * 5)
        
        if direction == "BUY":
            agree = direction_bool
        else:
            agree = not direction_bool
        
        if confidence < self.config.kronos_threshold:
            agree = False
        
        return agree, confidence
    
    def get_current_price(self):
        if self.current_idx >= len(self.df):
            return None, None
        row = self.df.iloc[self.current_idx]
        return row["close"], row["close"] + self.spread
    
    def calculate_lot_size(self) -> float:
        risk = self.state.balance * (self.config.risk_per_trade / 100)
        stop = self.atr * self.config.atr_multiplier
        lot = risk / (stop * 100000)
        return min(max(round(lot, 2), 0.01), 0.50)
    
    def place_order(self, direction: str, entry: float, sl: float, tp: float, session: str):
        kronos_agree, kronos_conf = self.simulate_kronos(direction)
        
        if not kronos_agree:
            self.state.kronos_vetoed += 1
            return
        
        self.state.total_placed += 1
        self.state.pending_orders.append({
            "direction": direction,
            "entry": entry,
            "sl": sl,
            "tp": tp,
            "placed_at": self.current_idx,
            "expiry": self.current_idx + 4,
            "session": session,
            "kronos_confidence": kronos_conf
        })
    
    def check_pending_orders(self):
        still_pending = []
        
        for order in self.state.pending_orders:
            filled = True
            
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
                    "sl_moved_to_1r": False,
                    "kronos_confidence": order["kronos_confidence"]
                })
                
                # Removed session limiting for M1 scalping - more trades allowed
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
                    new_sl = round(entry + sl_distance, 5)
                    pos["sl"] = new_sl
                    pos["sl_moved_to_1r"] = True
                    self.state.trailing_sl_moves += 1
                elif at_1r and sl_below_be and not pos["sl_moved_to_be"]:
                    new_sl = round(entry, 5)
                    pos["sl"] = new_sl
                    pos["sl_moved_to_be"] = True
                    self.state.trailing_sl_moves += 1
            
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
                elif at_1r and sl_above_be and not pos["sl_moved_to_be"]:
                    new_sl = round(entry, 5)
                    pos["sl"] = new_sl
                    pos["sl_moved_to_be"] = True
                    self.state.trailing_sl_moves += 1
            
            closed = False
            kronos_conf = pos.get("kronos_confidence", 0.5)
            
            if direction == "BUY":
                if current_price >= tp:
                    pnl = (tp - entry) * lot * 100000
                    trade = Trade(entry_time=pos["entry_time"], direction="BUY", 
                                 entry=entry, sl=pos["sl"], tp=tp, lot=lot, 
                                 outcome="WIN", pnl=pnl, session=pos["session"],
                                 kronos_confidence=kronos_conf)
                    self.state.trades.append(trade)
                    self.state.balance += pnl
                    closed = True
                elif current_price <= pos["sl"]:
                    pnl = (pos["sl"] - entry) * lot * 100000
                    trade = Trade(entry_time=pos["entry_time"], direction="BUY",
                                 entry=entry, sl=pos["sl"], tp=tp, lot=lot,
                                 outcome="LOSS", pnl=pnl, session=pos["session"],
                                 kronos_confidence=kronos_conf)
                    self.state.trades.append(trade)
                    self.state.balance += pnl
                    closed = True
            else:
                if current_price <= tp:
                    pnl = (entry - tp) * lot * 100000
                    trade = Trade(entry_time=pos["entry_time"], direction="SELL",
                                 entry=entry, sl=pos["sl"], tp=tp, lot=lot,
                                 outcome="WIN", pnl=pnl, session=pos["session"],
                                 kronos_confidence=kronos_conf)
                    self.state.trades.append(trade)
                    self.state.balance += pnl
                    closed = True
                elif current_price >= pos["sl"]:
                    pnl = (entry - pos["sl"]) * lot * 100000
                    trade = Trade(entry_time=pos["entry_time"], direction="SELL",
                                 entry=entry, sl=pos["sl"], tp=tp, lot=lot,
                                 outcome="LOSS", pnl=pnl, session=pos["session"],
                                 kronos_confidence=kronos_conf)
                    self.state.trades.append(trade)
                    self.state.balance += pnl
                    closed = True
            
            if not closed:
                still_open.append(pos)
        
        self.state.open_positions = still_open
    
    def run(self):
        print(f"\n{'='*60}")
        print(f"  ICT M1 BACKTEST")
        print(f"{'='*60}")
        
        self.load_csv()
        self.calculate_atr()
        
        print(f"  Initial Balance: ${self.config.initial_balance:,.2f}")
        print(f"  Risk/Trade: {self.config.risk_per_trade}%")
        print(f"  RRR: {self.config.rrr}")
        print(f"  ATR: {self.atr:.5f}")
        print(f"  FVG Lookback: {self.config.fvg_lookback}")
        print(f"  Kronos: {'ON' if self.config.use_kronos else 'OFF'} (threshold: {self.config.kronos_threshold})")
        print(f"{'='*60}\n")
        
        scan_counter = 0
        
        while self.current_idx < len(self.df):
            self.check_pending_orders()
            self.manage_open_positions()
            
            scan_counter += 1
            
            if scan_counter % self.config.scan_interval != 0:
                self.current_idx += 1
                continue
            
            bid, ask = self.get_current_price()
            if bid is None:
                break
            
            session = self.get_session()
            if not session:
                self.current_idx += 1
                continue
            
            if self.current_idx - self.state.last_trade_idx < 10:
                self.current_idx += 1
                continue
            
            current_price = (bid + ask) / 2
            
            fvgs = self.detect_fvg(max_age=self.config.fvg_lookback)
            
            if not fvgs:
                self.current_idx += 1
                continue
            
            recent = self.df.iloc[max(0, self.current_idx-20):self.current_idx]
            trend_up = recent["close"].iloc[-1] > recent["close"].iloc[0]
            
            for fvg_type, fvg_low, fvg_high, fvg_50, fvg_idx in fvgs:
                stop = self.atr * self.config.atr_multiplier
                
                if fvg_type == "BULLISH_FVG" and trend_up:
                    entry = current_price
                    sl = round(entry - stop, 5)
                    tp = round(entry + stop * self.config.rrr, 5)
                    self.place_order("BUY", entry, sl, tp, session)
                    self.state.last_trade_idx = self.current_idx
                    print(f"  [{self.df.iloc[self.current_idx]['time']}] "
                          f"BUY @ {entry:.5f} | SL: {sl:.5f} | TP: {tp:.5f} | {session}")
                    break
                
                elif fvg_type == "BEARISH_FVG" and not trend_up:
                    entry = current_price
                    sl = round(entry + stop, 5)
                    tp = round(entry - stop * self.config.rrr, 5)
                    self.place_order("SELL", entry, sl, tp, session)
                    self.state.last_trade_idx = self.current_idx
                    print(f"  [{self.df.iloc[self.current_idx]['time']}] "
                          f"SELL @ {entry:.5f} | SL: {sl:.5f} | TP: {tp:.5f} | {session}")
                    break
            
            self.current_idx += 1
            
            if self.current_idx % 2000 == 0:
                print(f"  Progress: {self.current_idx}/{len(self.df)} candles | "
                      f"Balance: ${self.state.balance:,.2f} | "
                      f"Placed: {self.state.total_placed} | Vetoed: {self.state.kronos_vetoed}")
        
        self.print_results()
        self.save_results()
    
    def print_results(self):
        wins = [t for t in self.state.trades if t.outcome == "WIN"]
        losses = [t for t in self.state.trades if t.outcome == "LOSS"]
        
        total = len(self.state.trades)
        win_rate = (len(wins) / total * 100) if total > 0 else 0
        net_pnl = sum(t.pnl for t in self.state.trades)
        
        fill_rate = (self.state.filled_count / self.state.total_placed * 100) if self.state.total_placed > 0 else 0
        veto_rate = (self.state.kronos_vetoed / (self.state.kronos_vetoed + self.state.total_placed) * 100) if (self.state.kronos_vetoed + self.state.total_placed) > 0 else 0
        
        print(f"\n{'='*60}")
        print(f"  ICT M1 BACKTEST RESULTS")
        print(f"{'='*60}")
        print(f"  Total Candles    : {len(self.df)}")
        print(f"  Orders Placed   : {self.state.total_placed}")
        print(f"  Orders Filled   : {self.state.filled_count}")
        print(f"  Orders Expired  : {self.state.expired_count}")
        print(f"  Fill Rate       : {fill_rate:.1f}%")
        print(f"  ")
        print(f"  KRONOS FILTER")
        print(f"  Vetoed          : {self.state.kronos_vetoed}")
        print(f"  Veto Rate       : {veto_rate:.1f}%")
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
        
        print(f"\n  SESSION BREAKDOWN:")
        sessions = {}
        for t in self.state.trades:
            s = t.session
            if s not in sessions:
                sessions[s] = {"wins": 0, "losses": 0, "pnl": 0}
            if t.outcome == "WIN":
                sessions[s]["wins"] += 1
            else:
                sessions[s]["losses"] += 1
            sessions[s]["pnl"] += t.pnl
        
        for s, data in sessions.items():
            wr = data["wins"] / (data["wins"] + data["losses"]) * 100 if (data["wins"] + data["losses"]) > 0 else 0
            print(f"    {s:12}: {data['wins']+data['losses']:2} trades | {wr:5.1f}% WR | ${data['pnl']:+,.0f}")
        
        print(f"{'='*60}\n")
    
    def save_results(self):
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
                "session": t.session,
                "kronos_confidence": t.kronos_confidence
            })
        
        results = {
            "run_date": datetime.now(timezone.utc).isoformat(),
            "csv_file": str(self.config.csv_file.name),
            "config": {
                "initial_balance": self.config.initial_balance,
                "risk_per_trade": self.config.risk_per_trade,
                "rrr": self.config.rrr,
                "atr_multiplier": self.config.atr_multiplier,
                "fvg_lookback": self.config.fvg_lookback,
                "use_kronos": self.config.use_kronos,
                "kronos_threshold": self.config.kronos_threshold
            },
            "final_balance": round(self.state.balance, 2),
            "total_orders_placed": self.state.total_placed,
            "orders_filled": self.state.filled_count,
            "orders_expired": self.state.expired_count,
            "kronos_vetoed": self.state.kronos_vetoed,
            "trailing_sl_moves": self.state.trailing_sl_moves,
            "open_positions_at_end": len(self.state.open_positions),
            "total_trades": len(self.state.trades),
            "wins": len([t for t in self.state.trades if t.outcome == "WIN"]),
            "losses": len([t for t in self.state.trades if t.outcome == "LOSS"]),
            "win_rate_pct": round(len([t for t in self.state.trades if t.outcome == "WIN"]) / len(self.state.trades) * 100, 1) if self.state.trades else 0,
            "net_pnl": round(net_pnl := sum(t.pnl for t in self.state.trades), 2),
            "trades": trades_data
        }
        
        BASE_DIR.joinpath("data").mkdir(parents=True, exist_ok=True)
        
        with open(RESULTS_FILE, "w") as f:
            json.dump(results, f, indent=2)
        
        print(f"[OK] Results saved to {RESULTS_FILE}")


def main():
    parser = argparse.ArgumentParser(description="ICT M1 Scalper Backtest")
    parser.add_argument("--csv", type=Path, default=BASE_DIR / "archive/data/btcusdc_m1_90days.csv", 
                        help="CSV file path")
    parser.add_argument("--balance", type=float, default=10000.0, help="Initial balance")
    parser.add_argument("--risk", type=float, default=1.0, help="Risk %% per trade")
    parser.add_argument("--rrr", type=float, default=2.0, help="Risk:Reward ratio")
    parser.add_argument("--atr-mult", type=float, default=1.0, help="ATR multiplier for stops")
    parser.add_argument("--lookback", type=int, default=40, help="FVG lookback candles")
    parser.add_argument("--scan", type=int, default=15, help="Scan interval (candles)")
    parser.add_argument("--no-kronos", action="store_true", help="Disable Kronos filter")
    parser.add_argument("--kronos-threshold", type=float, default=0.50, help="Kronos confidence threshold")
    
    args = parser.parse_args()
    
    config = BacktestConfig(
        csv_file=args.csv,
        initial_balance=args.balance,
        risk_per_trade=args.risk,
        rrr=args.rrr,
        atr_multiplier=args.atr_mult,
        fvg_lookback=args.lookback,
        scan_interval=args.scan,
        use_kronos=not args.no_kronos,
        kronos_threshold=args.kronos_threshold
    )
    
    tester = M1Backtester(config)
    tester.run()


if __name__ == "__main__":
    main()
