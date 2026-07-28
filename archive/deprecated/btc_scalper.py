#!/usr/bin/env python3
"""
btc_scalper.py — BTCUSD Scalping Bot with Kronos AI
Three strategies: EMA Pullback (M5), MACD+Stochastic (M1), Range Fading (M1)
"""

import MetaTrader5 as mt5
from datetime import datetime, timedelta, timezone
import pandas as pd
import numpy as np
import time
import json
import os
import sys
import hashlib
import requests

try:
    from kronos_guardian import KronosVetoGate
    KRONOS_VETO_GATE = KronosVetoGate()
except ImportError:
    KRONOS_VETO_GATE = None

# =======================================================
#    BTC SCALPER v1.0
#    Leveraging Kronos AI for precision scalping
# =======================================================

ARG_STRATEGY = sys.argv[1].upper() if len(sys.argv) > 1 else "AUTO"
ARG_CHECK = "--check" in sys.argv

VALID_STRATEGIES = ("EMA_PULLBACK", "MACD_STOCH", "RANGE_FADE", "AUTO")
VALID_TIMEFRAMES = ("M1", "M5")

# Configuration
TIMEFRAME_M5 = mt5.TIMEFRAME_M5
TIMEFRAME_M1 = mt5.TIMEFRAME_M1
SYMBOL = "BTCUSDc"

RISK_PERCENT = 1.0
HARD_LOT_CAP = 0.20
SCAN_INTERVAL_SEC = 60
MAX_DAILY_LOSS = 50.0

# EMA Settings (EMA Pullback)
EMA_FAST = 20
EMA_SLOW = 50

# MACD Settings (MACD+Stochastic)
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9
STOCH_K = 8
STOCH_D = 3
STOCH_SLOW = 3

# Range Fading
RANGE_LOOKBACK = 50

# Kronos
KRONOS_THRESHOLD = 0.70
KRONOS_LOOKBACK = 300

SA_OFFSET = 2

def log(msg: str, level: str = "INFO"):
    prefix = {"INFO": "   ", "WARN": "[W] ", "ERROR": "[E] ", "TRADE": "[T] "}.get(level, "   ")
    print(f"{prefix}{msg}")

def now_sast():
    return datetime.now(timezone.utc) + timedelta(hours=SA_OFFSET)

def get_kronos_prediction(df: pd.DataFrame, direction: str) -> tuple[bool, float]:
    """Get Kronos AI prediction for the next 1-5 candles"""
    if KRONOS_VETO_GATE is None:
        return True, 1.0
    
    try:
        allowed, reason = KRONOS_VETO_GATE.validate(direction, df, "BTCUSD")
        confidence = 1.0 if allowed else 0.0
        return allowed, confidence
    except Exception as e:
        log(f"Kronos error: {e}", "WARN")
        return True, 1.0

def get_candles(timeframe, count=500):
    """Fetch candles from MT5"""
    tf = TIMEFRAME_M5 if timeframe == "M5" else TIMEFRAME_M1
    rates = mt5.copy_rates_from_pos(SYMBOL, tf, 0, count)
    if rates is None:
        return None
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
    return df

def calculate_ema(df, period):
    """Calculate EMA"""
    return df["close"].ewm(span=period, adjust=False).mean()

def calculate_macd(df):
    """Calculate MACD"""
    ema_fast = df["close"].ewm(span=MACD_FAST, adjust=False).mean()
    ema_slow = df["close"].ewm(span=MACD_SLOW, adjust=False).mean()
    macd = ema_fast - ema_slow
    signal = macd.ewm(span=MACD_SIGNAL, adjust=False).mean()
    histogram = macd - signal
    return macd, signal, histogram

def calculate_stochastic(df):
    """Calculate Stochastic"""
    low_min = df["low"].rolling(window=STOCH_K).min()
    high_max = df["high"].rolling(window=STOCH_K).max()
    
    stoch_k = 100 * (df["close"] - low_min) / (high_max - low_min)
    stoch_d = stoch_k.rolling(window=STOCH_D).mean()
    
    return stoch_k, stoch_d

def find_range(df, lookback=50):
    """Find support/resistance range"""
    recent = df.tail(lookback)
    highs = recent["high"]
    lows = recent["low"]
    
    resistance = highs.max()
    support = lows.min()
    mid = (resistance + support) / 2
    
    current = df["close"].iloc[-1]
    in_range = support <= current <= resistance
    
    return {"support": support, "resistance": resistance, "mid": mid, "in_range": in_range}

def strategy_ema_pullback(df):
    """EMA Pullback Strategy (M5) - Trend following"""
    if df is None or len(df) < 50:
        return None, "Insufficient data"
    
    ema20 = calculate_ema(df, EMA_FAST)
    ema50 = calculate_ema(df, EMA_SLOW)
    
    last_close = df["close"].iloc[-1]
    last_ema20 = ema20.iloc[-1]
    last_ema50 = ema50.iloc[-1]
    
    prev_ema20 = ema20.iloc[-2]
    prev_ema50 = ema50.iloc[-2]
    
    # Trend: EMA20 above EMA50 = bullish
    bullish = last_ema20 > last_ema50
    bearish = last_ema20 < last_ema50
    
    # Price touches EMA20
    touch_tolerance = last_ema20 * 0.002
    
    # BUY: Bullish trend + price at/above EMA20 + green candle
    if bullish and last_close >= (last_ema20 - touch_tolerance):
        if df["close"].iloc[-1] > df["open"].iloc[-1]:
            return "BUY", f"EMA Pullback: Trend=Bullish, Price={last_close:.2f}, EMA20={last_ema20:.2f}"
    
    # SELL: Bearish trend + price at/below EMA20 + red candle
    if bearish and last_close <= (last_ema20 + touch_tolerance):
        if df["close"].iloc[-1] < df["open"].iloc[-1]:
            return "SELL", f"EMA Pullback: Trend=Bearish, Price={last_close:.2f}, EMA20={last_ema20:.2f}"
    
    return None, "No EMA signal"

def strategy_macd_stoch(df):
    """MACD + Stochastic Strategy (M1) - Momentum scalping"""
    if df is None or len(df) < 50:
        return None, "Insufficient data"
    
    macd, signal, hist = calculate_macd(df)
    stoch_k, stoch_d = calculate_stochastic(df)
    
    last_macd = macd.iloc[-1]
    last_hist = hist.iloc[-1]
    prev_hist = hist.iloc[-2]
    
    last_stoch_k = stoch_k.iloc[-1]
    prev_stoch_k = stoch_k.iloc[-2]
    
    # BUY: Stochastic crosses up from below 20 + MACD histogram turning positive
    if prev_stoch_k < 20 and last_stoch_k >= 20 and prev_hist < 0 and last_hist > 0:
        return "BUY", f"MACD+Stoch: Stoch={last_stoch_k:.1f}, Hist={last_hist:.2f}"
    
    # SELL: Stochastic crosses down from above 80 + MACD histogram turning negative
    if prev_stoch_k > 80 and last_stoch_k <= 80 and prev_hist > 0 and last_hist < 0:
        return "SELL", f"MACD+Stoch: Stoch={last_stoch_k:.1f}, Hist={last_hist:.2f}"
    
    return None, "No MACD/Stoch signal"

def strategy_range_fade(df):
    """Range Fading Strategy (M1) - Sideways trading"""
    if df is None or len(df) < RANGE_LOOKBACK:
        return None, "Insufficient data"
    
    r = find_range(df, RANGE_LOOKBACK)
    
    if not r["in_range"]:
        return None, f"Price outside range ({r['support']:.2f} - {r['resistance']:.2f})"
    
    last_close = df["close"].iloc[-1]
    last_open = df["open"].iloc[-1]
    
    # Distance from edges
    dist_to_support = last_close - r["support"]
    dist_to_resistance = r["resistance"] - last_close
    
    # Bearish reversal at resistance
    if dist_to_resistance < dist_to_support * 0.3:
        if last_close < last_open:  # Red candle
            return "SELL", f"Range Fade: At resistance {r['resistance']:.2f}"
    
    # Bullish reversal at support
    if dist_to_support < dist_to_resistance * 0.3:
        if last_close > last_open:  # Green candle
            return "BUY", f"Range Fade: At support {r['support']:.2f}"
    
    return None, "No range signal"

def evaluate_strategies(df_m5, df_m1):
    """Evaluate all strategies and return best signal"""
    signals = []
    
    # Strategy 1: EMA Pullback (M5)
    signal, reason = strategy_ema_pullback(df_m5)
    if signal:
        signals.append(("EMA_PULLBACK", signal, reason, 0.8))
    
    # Strategy 2: MACD+Stochastic (M1)
    signal, reason = strategy_macd_stoch(df_m1)
    if signal:
        signals.append(("MACD_STOCH", signal, reason, 0.9))
    
    # Strategy 3: Range Fading (M1)
    signal, reason = strategy_range_fade(df_m1)
    if signal:
        signals.append(("RANGE_FADE", signal, reason, 0.7))
    
    if not signals:
        return None, "No strategy signals"
    
    # Sort by confidence and pick best
    signals.sort(key=lambda x: x[3], reverse=True)
    best = signals[0]
    
    # Get Kronos confirmation
    direction = best[1]
    kronos_allowed, kronos_conf = get_kronos_prediction(df_m1, direction)
    
    if not kronos_allowed:
        return None, f"{best[0]}: Kronos rejected ({kronos_conf:.0%})"
    
    return best, f"{best[0]} {best[1]} - Kronos: {kronos_conf:.0%}"

def calculate_lot_size():
    """Calculate lot size based on risk"""
    account = mt5.account_info()
    if account is None:
        return 0.01
    
    tick = mt5.symbol_info_tick(SYMBOL)
    if tick is None:
        return 0.01
    
    # Get ATR for stop calculation
    df = get_candles("M1", 100)
    if df is not None and len(df) > 14:
        high = df["high"]
        low = df["low"]
        close = df["close"]
        tr = np.maximum(high - low, 
               np.abs(high - close.shift(1)),
               np.abs(low - close.shift(1)))
        atr = tr.rolling(14).mean().iloc[-1]
    else:
        atr = 30  # Default for BTC
    
    risk = account.equity * (RISK_PERCENT / 100)
    stop_distance = atr * 1.5
    
    lot = risk / (stop_distance * SYMBOL.replace("USDc", "").count("BTC") * 100000 + 1)
    lot = min(max(lot, 0.01), HARD_LOT_CAP)
    return round(lot, 2)

def place_trade(direction, lot_size):
    """Execute trade"""
    tick = mt5.symbol_info_tick(SYMBOL)
    if tick is None:
        log("No tick data", "ERROR")
        return None
    
    entry = tick.ask if direction == "BUY" else tick.bid
    
    # Calculate SL/TP based on ATR
    df = get_candles("M1", 100)
    if df is not None and len(df) > 14:
        high = df["high"]
        low = df["low"]
        close = df["close"]
        tr = np.maximum(high - low, 
               np.abs(high - close.shift(1)),
               np.abs(low - close.shift(1)))
        atr = tr.rolling(14).mean().iloc[-1]
    else:
        atr = 30
    
    sl_distance = atr * 1.5
    tp_distance = atr * 3.5
    
    if direction == "BUY":
        sl = entry - sl_distance
        tp = entry + tp_distance
    else:
        sl = entry + sl_distance
        tp = entry - tp_distance
    
    order_type = mt5.ORDER_TYPE_BUY if direction == "BUY" else mt5.ORDER_TYPE_SELL
    
    result = mt5.order_send({
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": SYMBOL,
        "volume": lot_size,
        "type": order_type,
        "price": entry,
        "sl": sl,
        "tp": tp,
        "deviation": 10,
        "magic": 888888,
        "comment": "BTC_SCALPER",
        "type_time": mt5.ORDER_TIME_GTC,
    })
    
    return result

def main():
    print("=" * 60)
    print("   BTC SCALPER v1.0 + KRONOS AI")
    print("   Strategies: EMA Pullback | MACD+Stoch | Range Fade")
    print("=" * 60)
    
    if not mt5.initialize():
        log(f"MT5 init failed: {mt5.last_error()}", "ERROR")
        return
    
    mt5.symbol_select(SYMBOL, True)
    
    account = mt5.account_info()
    log(f"Account: {account.balance} USC | Equity: {account.equity} USC")
    
    lot_size = calculate_lot_size()
    log(f"Lot size: {lot_size}")
    
    if ARG_CHECK:
        log("CHECK MODE: Running single scan...")
        
        df_m5 = get_candles("M5", 100)
        df_m1 = get_candles("M1", 300)
        
        if df_m5 is not None:
            log(f"M5 data: {len(df_m5)} candles, latest: {df_m5['time'].iloc[-1]}")
        if df_m1 is not None:
            log(f"M1 data: {len(df_m1)} candles, latest: {df_m1['time'].iloc[-1]}")
        
        signal, reason = evaluate_strategies(df_m5, df_m1)
        log(f"Signal: {signal}")
        log(f"Reason: {reason}")
        
        mt5.shutdown()
        return
    
    log("Starting continuous scan (Ctrl+C to stop)...")
    
    while True:
        try:
            now = now_sast()
            log(f"Scan at {now.strftime('%H:%M:%S')} SAST")
            
            # Fetch data
            df_m5 = get_candles("M5", 100)
            df_m1 = get_candles("M1", 300)
            
            if df_m5 is None or df_m1 is None:
                log("Data fetch failed, retrying...", "WARN")
                time.sleep(SCAN_INTERVAL_SEC)
                continue
            
            # Evaluate
            signal, reason = evaluate_strategies(df_m5, df_m1)
            
            if signal:
                log(f"[TARGET] SIGNAL: {signal[1]} | {signal[2]}", "TRADE")
                
                # Execute
                result = place_trade(signal[1], lot_size)
                if result and result.retcode == mt5.TRADE_RETCODE_DONE:
                    log(f"[OK] Trade placed: {signal[1]} @ {result.price}", "TRADE")
                else:
                    log(f"[FAIL] Trade failed: {result.retcode if result else 'None'}", "ERROR")
            else:
                log(f"No signal: {reason}")
            
            time.sleep(SCAN_INTERVAL_SEC)
            
        except KeyboardInterrupt:
            log("Stopped by user")
            break
        except Exception as e:
            log(f"Error: {e}", "ERROR")
            time.sleep(SCAN_INTERVAL_SEC)
    
    mt5.shutdown()

if __name__ == "__main__":
    main()