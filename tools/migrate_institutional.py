import sqlite3
import duckdb
import os
import pandas as pd
import numpy as np
from tqdm import tqdm

def migrate_institutional():
    duck_db_path = os.path.join("db", "myra_market_data.db")
    sqlite_db_path = os.path.join("db", "institutional.db")
    
    if not os.path.exists(duck_db_path):
        print(f"[!] DuckDB not found at {duck_db_path}")
        return

    print(f"[MYRA] Migrating Institutional Data to {sqlite_db_path}...")
    
    duck_conn = duckdb.connect(duck_db_path, read_only=True)
    sqlite_conn = sqlite3.connect(sqlite_db_path)
    cursor = sqlite_conn.cursor()

    # 1. Create Tables
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS insider_trades (
            symbol TEXT,
            acq_name TEXT,
            category TEXT,
            type TEXT,
            mode TEXT,
            value_cr REAL,
            date TEXT,
            PRIMARY KEY (symbol, acq_name, date, value_cr)
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS large_deals (
            symbol TEXT,
            type TEXT,
            client TEXT,
            buy_sell TEXT,
            qty INTEGER,
            price REAL,
            date TEXT,
            PRIMARY KEY (symbol, client, date, qty, price)
        )
    """)
    sqlite_conn.commit()

    # 2. Migrate Insider Trades
    try:
        print("[*] Migrating insider_trades...")
        # Map 'person' from DuckDB to 'acq_name' in SQLite
        df_insider = duck_conn.execute("SELECT symbol, person as acq_name, category, type, mode, value_cr, CAST(date AS VARCHAR) as date FROM insider_trades").df()
        if not df_insider.empty:
            df_insider = df_insider.replace({np.nan: None})
            records = df_insider.values.tolist()
            cursor.executemany("INSERT OR IGNORE INTO insider_trades VALUES (?,?,?,?,?,?,?)", records)
            sqlite_conn.commit()
            print(f"[+] Migrated {len(df_insider)} insider trades.")
    except Exception as e:
        print(f"[!] Error migrating insider_trades: {e}")

    # 3. Migrate Large Deals
    try:
        print("[*] Migrating large_deals...")
        df_deals = duck_conn.execute("SELECT symbol, type, client, buy_sell, qty, price, CAST(date AS VARCHAR) as date FROM large_deals").df()
        if not df_deals.empty:
            df_deals = df_deals.replace({np.nan: None})
            records = df_deals.values.tolist()
            cursor.executemany("INSERT OR IGNORE INTO large_deals VALUES (?,?,?,?,?,?,?)", records)
            sqlite_conn.commit()
            print(f"[+] Migrated {len(df_deals)} large deals.")
    except Exception as e:
        print(f"[!] Error migrating large_deals: {e}")

    duck_conn.close()
    sqlite_conn.close()
    print("[MYRA] Institutional migration complete.")

if __name__ == "__main__":
    migrate_institutional()
