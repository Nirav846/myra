import sqlite3
import os
import sys

def check_raw_data(symbol):
    db_path = os.path.join("db", "technical.db")
    conn = sqlite3.connect(db_path)
    res = conn.execute(f"SELECT date, close, vwap, volume, delivery, delivery_pct FROM technical_data WHERE symbol = '{symbol}' ORDER BY date DESC LIMIT 5").fetchall()
    print(f"Raw data for {symbol}:")
    for r in res:
        print(f"  - Date: {r[0]}, Close: {r[1]}, VWAP: {r[2]}, Vol: {r[3]}, Del: {r[4]}, Del%: {r[5]}")
    conn.close()

if __name__ == "__main__":
    sym = sys.argv[1] if len(sys.argv) > 1 else "RELIANCE"
    check_raw_data(sym)
