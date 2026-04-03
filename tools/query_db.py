import sqlite3
import os
import sys

def check_history(symbol):
    db_path = os.path.join("db", "technical.db")
    if not os.path.exists(db_path):
        print(f"[!] {db_path} missing.")
        return
        
    conn = sqlite3.connect(db_path)
    res = conn.execute(f"SELECT date FROM technical_data WHERE symbol = '{symbol}' ORDER BY date DESC LIMIT 10").fetchall()
    print(f"Last 10 dates for {symbol}:")
    for r in res:
        print(f"  - {r[0]}")
    
    count = conn.execute(f"SELECT COUNT(*) FROM technical_data WHERE symbol = '{symbol}'").fetchone()[0]
    print(f"Total rows for {symbol}: {count}")
    conn.close()

if __name__ == "__main__":
    sym = sys.argv[1] if len(sys.argv) > 1 else "DCAL"
    check_history(sym)
