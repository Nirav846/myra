import sqlite3
import duckdb
import os
import numpy as np

def migrate_metadata():
    duck_db_path = os.path.join("db", "myra_market_data.db")
    sqlite_db_path = os.path.join("db", "meta.db")
    
    if not os.path.exists(duck_db_path):
        print(f"[!] DuckDB not found at {duck_db_path}")
        return

    print(f"[MYRA] Migrating Metadata to {sqlite_db_path}...")
    
    duck_conn = duckdb.connect(duck_db_path, read_only=True)
    sqlite_conn = sqlite3.connect(sqlite_db_path)
    cursor = sqlite_conn.cursor()

    # 1. Create Tables
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS symbols_master (
            symbol TEXT PRIMARY KEY,
            first_seen TEXT,
            last_seen TEXT,
            in_active_universe INTEGER DEFAULT 0,
            in_nifty500 INTEGER DEFAULT 0,
            sector TEXT,
            is_active INTEGER DEFAULT 1
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS index_constituents (
            index_name TEXT,
            symbol TEXT,
            PRIMARY KEY (index_name, symbol)
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS benchmarks (
            symbol TEXT,
            date TEXT,
            close REAL,
            PRIMARY KEY (symbol, date)
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS metadata (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    sqlite_conn.commit()

    # 2. Migrate symbols_master
    try:
        print("[*] Migrating symbols_master...")
        # DuckDB: symbol, first_seen, last_seen, is_active, in_nifty500, in_active_universe, last_fundamental_update
        # Sector not present in DuckDB master, adding as NULL for now
        df = duck_conn.execute("SELECT symbol, CAST(first_seen AS VARCHAR), CAST(last_seen AS VARCHAR), in_active_universe, in_nifty500, NULL as sector, is_active FROM symbols_master").df()
        if not df.empty:
            df = df.replace({np.nan: None})
            # Convert bool to int for SQLite
            for col in ['in_active_universe', 'in_nifty500', 'is_active']:
                df[col] = df[col].astype(int)
            records = df.values.tolist()
            cursor.executemany("INSERT OR REPLACE INTO symbols_master VALUES (?,?,?,?,?,?,?)", records)
            sqlite_conn.commit()
            print(f"[+] Migrated {len(df)} symbols.")
    except Exception as e:
        print(f"[!] Error migrating symbols_master: {e}")

    # 3. Migrate index_constituents
    try:
        print("[*] Migrating index_constituents...")
        df = duck_conn.execute("SELECT index_name, symbol FROM index_constituents").df()
        if not df.empty:
            df = df.replace({np.nan: None})
            records = df.values.tolist()
            cursor.executemany("INSERT OR REPLACE INTO index_constituents VALUES (?,?)", records)
            sqlite_conn.commit()
            print(f"[+] Migrated {len(df)} index constituents.")
    except Exception as e:
        print(f"[!] Error migrating index_constituents: {e}")

    # 4. Migrate benchmarks
    try:
        print("[*] Migrating benchmarks...")
        # DuckDB table is called 'benchmark'
        df = duck_conn.execute("SELECT symbol, CAST(date AS VARCHAR) as date, close FROM benchmark").df()
        if not df.empty:
            df = df.replace({np.nan: None})
            records = df.values.tolist()
            cursor.executemany("INSERT OR REPLACE INTO benchmarks VALUES (?,?,?)", records)
            sqlite_conn.commit()
            print(f"[+] Migrated {len(df)} benchmark records.")
    except Exception as e:
        print(f"[!] Error migrating benchmarks: {e}")

    # 5. Migrate metadata
    try:
        print("[*] Migrating metadata...")
        df = duck_conn.execute("SELECT key, value FROM metadata").df()
        if not df.empty:
            df = df.replace({np.nan: None})
            records = df.values.tolist()
            cursor.executemany("INSERT OR REPLACE INTO metadata VALUES (?,?)", records)
            sqlite_conn.commit()
            print(f"[+] Migrated {len(df)} metadata keys.")
    except Exception as e:
        print(f"[!] Error migrating metadata: {e}")

    duck_conn.close()
    sqlite_conn.close()
    print("[MYRA] Metadata migration complete.")

if __name__ == "__main__":
    migrate_metadata()
