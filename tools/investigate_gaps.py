import os
import sys
import sqlite3
import pandas as pd

# Fix path
sys.path.append(os.getcwd())

def investigate_gappy_symbol(symbol):
    db_path = "db/technical.db"
    conn = sqlite3.connect(db_path)
    
    print(f"[*] Investigating symbol: {symbol}")
    
    # 1. Check current DB count
    res = conn.execute(f"SELECT COUNT(*), MIN(date), MAX(date) FROM technical_data WHERE symbol = '{symbol}'").fetchone()
    print(f"    - DB Rows: {res[0]}, Range: {res[1]} to {res[2]}")
    
    # 2. Check gaps in missing_data.csv
    df_missing = pd.read_csv("data/missing_data.csv")
    sym_missing = df_missing[df_missing['symbol'] == symbol]
    print(f"    - CSV Gaps: {len(sym_missing)}")
    if not sym_missing.empty:
        print(f"    - Sample Missing Dates: {sym_missing['missing_date'].head(5).tolist()}")
    
    # 3. Check if it's a known symbol change or new listing via Google Search
    # (We'll do this in the next turn if needed)
    
    conn.close()

if __name__ == "__main__":
    # INA or LTM from the top gaps list
    investigate_gappy_symbol("LTM")
