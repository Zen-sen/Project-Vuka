#!/usr/bin/env python3
"""
btc_scalper_backtest.py — Backtest the BTC Scalper Strategies
"""

import pandas as pd
import numpy as np
import json
from datetime import datetime

DATA_FILE = "btcusdc_m1_90days.csv"
INITIAL_BALANCE = 4339.67
RISK_PERCENT = 1.0
RRR = 3.5

def load_data():
    """Load BTCUSD data"""
    df = pd.read_csv(DATA_FILE)
    if 'time' in df.columns:
        df['time'] = pd.to_datetime(df['time'])
    else:
        df['time'] = pd.to_datetime(df.iloc[:, 0])
    print(f"Loaded {len(df)} candles")
    return df

def calculate_ema(series, period):
    return series.ewm(span=period, adjust=False).mean()

def calculate_macd(series):
    ema_fast = series.ewm(span=12, adjust=False).mean()
    ema_slow = series.ewm(span=26, adjust=False).mean()
    macd = ema_fast - ema_slow
    signal = macd.ewm(span=9, adjust=False).mean()
    histogram = macd - signal
    return macd, signal, histogram

def calculate_stochastic(df):
    low_min = df["low"].rolling(window=8).min()
    high_max = df["high"].rolling(window=8).max()
    stoch_k = 100 * (df["close"] - low_min) / (high_max - low_min)
    stoch_d = stoch_k.rolling(window=3).mean()
    return stoch_k, stoch_d

def find_range(df, lookback=50):
    recent = df.tail(lookback)
    resistance = recent["high"].max()
    support = recent["low"].min()
    mid = (resistance + support) / 2
    current = df["close"].iloc[-1]
    in_range = support <= current <= resistance
    return {"support": support, "resistance": resistance, "mid": mid, "in_range": in_range}

def simulate_ema_pullback(df, idx):
    """EMA Pullback on M5"""
    if idx < 50:
        return None
    
    # Use M5 candles (every 5th row)
    m5_indices = list(range(0, len(df), 5))
    if idx not in m5_indices:
        return None
    
    # Get last 20 M5 candles
    m5_idx = m5_indices.index(idx)
    if m5_idx < 20:
        return None
    
    m5_start = m5_indices[m5_idx - 20]
    m5_data = df.iloc[m5_start:idx+1:5]
    
    if len(m5_data) < 20:
        return None
    
    ema20 = calculate_ema(m5_data["close"], 20)
    ema50 = calculate_ema(m5_data["close"], 50)
    
    last_close = m5_data["close"].iloc[-1]
    last_ema20 = ema20.iloc[-1]
    last_ema50 = ema50.iloc[-1]
    
    if last_ema20 > last_ema50 and last_close >= last_ema20 - last_ema20 * 0.002:
        if m5_data["close"].iloc[-1] > m5_data["open"].iloc[-1]:
            return "BUY"
    elif last_ema20 < last_ema50 and last_close <= last_ema20 + last_ema20 * 0.002:
        if m5_data["close"].iloc[-1] < m5_data["open"].iloc[-1]:
            return "SELL"
    
    return None

def simulate_macd_stoch(df, idx):
    """MACD + Stochastic on M1"""
    if idx < 50:
        return None
    
    recent = df.iloc[idx-50:idx+1]
    
    macd, signal, hist = calculate_macd(recent["close"])
    stoch_k, stoch_d = calculate_stochastic(recent)
    
    last_hist = hist.iloc[-1]
    prev_hist = hist.iloc[-2]
    last_stoch = stoch_k.iloc[-1]
    prev_stoch = stoch_k.iloc[-2]
    
    if prev_stoch < 20 and last_stoch >= 20 and prev_hist < 0 and last_hist > 0:
        return "BUY"
    if prev_stoch > 80 and last_stoch <= 80 and prev_hist > 0 and last_hist < 0:
        return "SELL"
    
    return None

def simulate_range_fade(df, idx):
    """Range Fading"""
    if idx < 50:
        return None
    
    r = find_range(df.iloc[:idx+1], 50)
    
    if not r["in_range"]:
        return None
    
    last_close = df["close"].iloc[idx]
    last_open = df["open"].iloc[idx]
    
    dist_support = last_close - r["support"]
    dist_resistance = r["resistance"] - last_close
    
    if dist_resistance < dist_support * 0.3 and last_close < last_open:
        return "SELL"
    if dist_support < dist_resistance * 0.3 and last_close > last_open:
        return "BUY"
    
    return None

def run_backtest():
    print("=" * 60)
    print("   BTC SCALPER BACKTEST")
    print("=" * 60)
    
    df = load_data()
    
    trades = []
    wins = 0
    losses = 0
    
    balance = INITIAL_BALANCE
    
    # Simulate every M5 candle
    m5_indices = list(range(0, len(df), 5))
    
    for i, idx in enumerate(m5_indices[20:-5], 20):
        # Check all strategies
        signal = None
        strategy = None
        
        # EMA Pullback
        s = simulate_ema_pullback(df, idx)
        if s:
            signal = s
            strategy = "EMA_PULLBACK"
        
        # MACD+Stoch (check more frequently)
        s = simulate_macd_stoch(df, idx)
        if s:
            signal = s
            strategy = "MACD_STOCH"
        
        # Range Fade
        s = simulate_range_fade(df, idx)
        if s:
            signal = s
            strategy = "RANGE_FADE"
        
        if not signal:
            continue
        
        # Calculate trade
        entry = df["close"].iloc[idx]
        
        # ATR-based stop
        atr = df["high"].iloc[idx-14:idx].max() - df["low"].iloc[idx-14:idx].min()
        sl_dist = atr * 1.5
        tp_dist = atr * RRR
        
        if signal == "BUY":
            sl = entry - sl_dist
            tp = entry + tp_dist
        else:
            sl = entry + sl_dist
            tp = entry - tp_dist
        
        # Simulate exit
        future = df.iloc[idx+1:idx+6]["close"]
        
        if signal == "BUY":
            hit_tp = (future >= tp).any()
            hit_sl = (future <= sl).any()
        else:
            hit_tp = (future <= tp).any()
            hit_sl = (future >= sl).any()
        
        if hit_tp:
            pnl = tp_dist * 100  # Simplified
            outcome = "WIN"
            wins += 1
        elif hit_sl:
            pnl = -sl_dist * 100
            outcome = "LOSS"
            losses += 1
        else:
            continue  # No exit in 5 candles
        
        balance += pnl
        
        trades.append({
            "idx": idx,
            "strategy": strategy,
            "signal": signal,
            "entry": entry,
            "outcome": outcome,
            "pnl": pnl
        })
    
    # Results
    total = wins + losses
    win_rate = (wins / total * 100) if total > 0 else 0
    net_pnl = balance - INITIAL_BALANCE
    roi = (net_pnl / INITIAL_BALANCE) * 100
    
    print("\n============================================================")
    print("   BACKTEST RESULTS")
    print("============================================================")
    print(f"Period: {df['time'].iloc[0]} to {df['time'].iloc[-1]}")
    print(f"Total Trades: {total}")
    print(f"Won / Lost: {wins}W / {losses}L")
    print(f"Win Rate: {win_rate:.1f}%")
    print(f"Net P&L: ${net_pnl:.2f} ({roi:+.1f}%)")
    print(f"Final Balance: ${balance:.2f}")
    print("============================================================")
    
    # By strategy
    strat_results = {}
    for t in trades:
        s = t["strategy"]
        if s not in strat_results:
            strat_results[s] = {"wins": 0, "losses": 0, "pnl": 0}
        strat_results[s]["wins" if t["outcome"] == "WIN" else "losses"] += 1
        strat_results[s]["pnl"] += t["pnl"]
    
    print("\nBy Strategy:")
    for s, r in strat_results.items():
        wr = r["wins"] / (r["wins"] + r["losses"]) * 100 if (r["wins"] + r["losses"]) > 0 else 0
        print(f"  {s}: {r['wins']}W/{r['losses']}L ({wr:.0f}%), PnL: ${r['pnl']:.2f}")
    
    # Save results
    with open("data/btc_scalper_backtest.json", "w") as f:
        json.dump({
            "total_trades": total,
            "wins": wins,
            "losses": losses,
            "win_rate": win_rate,
            "net_pnl": net_pnl,
            "roi": roi,
            "final_balance": balance,
            "by_strategy": strat_results
        }, f, indent=2)
    
    print("Results saved to data/btc_scalper_backtest.json")

if __name__ == "__main__":
    run_backtest()