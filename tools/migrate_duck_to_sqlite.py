import sqlite3
import duckdb
import os
import pandas as pd
import numpy as np
from tqdm import tqdm

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))

def migrate_duckdb_to_sqlite():
    duck_db_path = os.path.join(PROJECT_ROOT, "db", "myra_market_data.db")
    sqlite_db_path = os.path.join(PROJECT_ROOT, "db", "technical.db")
    
    if not os.path.exists(duck_db_path):
        print(f"[!] DuckDB not found at {duck_db_path}")
        return

    print(f"[MYRA] Migrating saved data from {duck_db_path} to {sqlite_db_path}...")
    
    # Connect to both
    duck_conn = duckdb.connect(duck_db_path, read_only=True)
    sqlite_conn = sqlite3.connect(sqlite_db_path)
    cursor = sqlite_conn.cursor()
    
    # 1. Get row count for progress bar
    total_rows = duck_conn.execute("SELECT COUNT(*) FROM prices").fetchone()[0]
    print(f"[*] Total rows to migrate: {total_rows}")
    
    # 2. Fetch data in chunks from DuckDB
    chunk_size = 100000
    offset = 0
    
    stats = {"migrated": 0, "errors": 0}
    
    pbar = tqdm(total=total_rows, desc="Migrating")
    while True:
        try:
            query = f"""
                SELECT 
                    symbol, 
                    CAST(date AS VARCHAR) as date, 
                    open, high, low, close, volume, 
                    delivery_qty as delivery,
                    NULL as trades,
                    close as vwap,
                    delivery_percent as delivery_pct,
                    (CASE WHEN volume > 0 THEN CAST(delivery_qty AS DOUBLE) / volume ELSE 0 END) as delivery_ratio
                FROM prices
                LIMIT {chunk_size} OFFSET {offset}
            """
            df_chunk = duck_conn.execute(query).df()
            
            if df_chunk.empty:
                break
            
            # --- CLEANING FOR SQLITE ---
            # Convert NAType/NaN to None
            df_chunk = df_chunk.replace({np.nan: None})
            # Ensure types are correct
            for col in ['open', 'high', 'low', 'close', 'volume', 'delivery', 'vwap', 'delivery_pct', 'delivery_ratio']:
                if col in df_chunk.columns:
                    df_chunk[col] = pd.to_numeric(df_chunk[col], errors='coerce').replace({np.nan: None})
            
            records = df_chunk.values.tolist()
            # Double check for NAType in records
            records = [[(None if pd.isna(x) else x) for x in r] for r in records]
            
            cursor.executemany("""
                INSERT OR IGNORE INTO technical_data 
                (symbol, date, open, high, low, close, volume, delivery, trades, vwap, delivery_pct, delivery_ratio)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, records)
            
            sqlite_conn.commit()
            
            stats["migrated"] += len(df_chunk)
            pbar.update(len(df_chunk))
            offset += chunk_size
            
        except Exception as e:
            print(f"[!] Error at offset {offset}: {e}")
            stats["errors"] += 1
            break
            
    pbar.close()
    duck_conn.close()
    sqlite_conn.close()
    
    print("\n" + "="*30)
    print("MIGRATION COMPLETE")
    print("="*30)
    print(f"Rows Migrated: {stats['migrated']}")
    print(f"Errors:        {stats['errors']}")
    print("="*30)

if __name__ == "__main__":
    migrate_duckdb_to_sqlite()
