#!/usr/bin/env python3
"""
fetch_mt5_data.py — Fetch M15 candles from MT5
Run this on your PC with MT5 Terminal running
"""

import MetaTrader5 as mt5
from datetime import datetime, timedelta
import pandas as pd

SYMBOL = "GBPUSDc"
TIMEFRAME = mt5.TIMEFRAME_M15
DAYS_BACK = 30

def main():
    if not mt5.initialize():
        print(f"MT5 initialize failed: {mt5.last_error()}")
        return
    
    print(f"Connected to MT5 version {mt5.version()}")
    
    # Calculate date range
    to_date = datetime.now()
    from_date = to_date - timedelta(days=DAYS_BACK)
    
    print(f"Fetching {SYMBOL} M15 data from {from_date.date()} to {to_date.date()}...")
    
    rates = mt5.copy_rates_range(SYMBOL, TIMEFRAME, from_date, to_date)
    
    if rates is None:
        print(f"Failed to fetch data: {mt5.last_error()}")
        mt5.shutdown()
        return
    
    df = pd.DataFrame(rates)
    df['time'] = pd.to_datetime(df['time'], unit='s')
    
    output_file = f"{SYMBOL.lower()}_m15_{DAYS_BACK}days.csv"
    df.to_csv(output_file, index=False)
    
    print(f"Exported {len(df)} candles to {output_file}")
    print(f"Date range: {df['time'].min()} to {df['time'].max()}")
    
    mt5.shutdown()

if __name__ == "__main__":
    main()
