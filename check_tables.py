
import sqlite3

def check_table_names():
    conn = sqlite3.connect('vuka_trading.db')
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
    print(cursor.fetchall())
    conn.close()

if __name__ == "__main__":
    check_table_names()
