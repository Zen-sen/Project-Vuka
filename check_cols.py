
import sqlite3

def check_columns():
    try:
        conn = sqlite3.connect('vuka_trading.db')
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(trades);")
        columns = cursor.fetchall()
        print(f"Columns of trades table: {columns}")
        conn.close()
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    check_columns()
