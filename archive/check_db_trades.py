
import sqlite3

def check_recent_trades():
    try:
        conn = sqlite3.connect('vuka_trading.db')
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM trades WHERE time >= '2026-05-01' ORDER BY time DESC LIMIT 10;")
        rows = cursor.fetchall()
        for row in rows:
            print(row)
        conn.close()
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    check_recent_trades()
