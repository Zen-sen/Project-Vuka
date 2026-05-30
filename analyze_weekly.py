
import sqlite3
from datetime import datetime, timedelta

def analyze_weekly_trades():
    try:
        conn = sqlite3.connect('vuka_trading.db')
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # Calculate date for one week ago
        one_week_ago = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')
        
        # Fetch trades from the last week
        cursor.execute("SELECT * FROM trades WHERE time >= ? ORDER BY time ASC", (one_week_ago,))
        trades = cursor.fetchall()
        
        if not trades:
            print("No trades found for the past week.")
            return

        total_trades = len(trades)
        
        # We don't have exit prices, but we can look at SL movements to infer if a trade was saved
        # or if it was closed. Actually, the DB only logs entries. 
        # To get a real feedback, I should probably use the existing skills/trade_log_analyzer.py
        # but let's first summarize what was PLACED.
        
        symbols = {}
        sessions = {}
        
        for t in trades:
            sym = t['symbol']
            sess = t['session']
            symbols[sym] = symbols.get(sym, 0) + 1
            sessions[sess] = sessions.get(sess, 0) + 1
            
        print(f"--- Weekly Trade Summary (Last 7 Days) ---")
        print(f"Total Trades Placed: {total_trades}")
        print(f"\nBy Symbol:")
        for s, c in symbols.items():
            print(f"  {s}: {c}")
        print(f"\nBy Session:")
        for s, c in sessions.items():
            print(f"  {s}: {c}")
        
        # Check for most recent trades details
        print(f"\nRecent Trades:")
        for t in trades[-3:]:
            print(f"  {t['time']} | {t['symbol']} | {t['direction']} | RR: {t['effective_rr']}")
            
        conn.close()
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    analyze_weekly_trades()
