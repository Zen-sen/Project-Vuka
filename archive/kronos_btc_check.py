"""
Kronos BTC Prediction - With Volume
"""
import requests
import MetaTrader5 as mt5
import pandas as pd
import numpy as np

API = "http://127.0.0.1:8000"
SYMBOL = "BTCUSDc"

def main():
    print("\n=== KRONOS BTC PREDICTION ===\n")
    
    mt5.initialize()
    
    # Get M15 data
    rates = mt5.copy_rates_from_pos(SYMBOL, mt5.TIMEFRAME_M15, 0, 50)
    df = pd.DataFrame(rates)
    
    current = df['close'].iloc[-1]
    
    # Add dummy volume if needed
    if 'volume' not in df.columns or df['volume'].isna().any():
        df['volume'] = np.random.randint(100, 1000, len(df))
    
    # Build payload with all required columns
    payload = {
        "open": df['open'].tolist(),
        "high": df['high'].tolist(),
        "low": df['low'].tolist(),
        "close": df['close'].tolist(),
        "volume": df['volume'].astype(float).tolist()
    }
    
    print(f"Current: {current:.2f}")
    print(f"Calling Kronos...")
    
    try:
        r = requests.post(f"{API}/v1/predict-ohlcv", json=payload, timeout=5)
        result = r.json()
        
        agree = result.get('agree')
        conf = result.get('confidence', 0)
        reason = result.get('reason', '')
        
        print(f"\n--- RESULT ---")
        print(f"  Agree:      {agree}")
        print(f"  Confidence: {conf:.2f} ({conf*100:.0f}%)")
        print(f"  Reason:    {reason}")
        
        if agree:
            print(f"\n[KRONOS] SAYS: BUY")
        else:
            print(f"\n[KRONOS] SAYS: SELL")
            
    except Exception as e:
        print(f"Error: {e}")
    
    mt5.shutdown()

if __name__ == "__main__":
    main()