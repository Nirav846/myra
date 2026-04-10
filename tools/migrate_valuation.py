import sqlite3
import duckdb
import os
import pandas as pd
import numpy as np
from myra_core.utils.myra_log import myra_log


def migrate_valuation():
    duck_db_path = os.path.join("db", "myra_market_data.db")
    sqlite_db_path = os.path.join("db", "valuation.db")

    if not os.path.exists(duck_db_path):
        print(f"[!] DuckDB not found at {duck_db_path}")
        return

    print(f"[MYRA] Migrating Valuation Data to {sqlite_db_path}...")

    duck_conn = duckdb.connect(duck_db_path, read_only=True)
    sqlite_conn = sqlite3.connect(sqlite_db_path)
    cursor = sqlite_conn.cursor()

    # 1. Create Tables
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS fundamentals (
            symbol TEXT PRIMARY KEY,
            pe REAL,
            roe REAL,
            eps REAL,
            book_value REAL,
            market_cap REAL,
            sector TEXT,
            last_updated TEXT
        )
    """
    )
    # Quarterly results might be in a separate table or need creation
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS quarterly_results (
            symbol TEXT,
            report_date TEXT,
            revenue REAL,
            net_profit REAL,
            eps REAL,
            opm_pct REAL,
            PRIMARY KEY (symbol, report_date)
        )
    """
    )
    sqlite_conn.commit()

    # 2. Migrate Fundamentals
    try:
        print("[*] Migrating fundamentals...")
        # DuckDB: symbol, pe, roe, pb, sales_growth, profit_growth, debt_to_equity, market_cap, last_updated, ev_ebitda, ps, sector, industry, inst_holding, insider_holding, deep_valuation
        # We'll map what we can and use NULL for eps for now
        df = duck_conn.execute(
            "SELECT symbol, pe, roe, NULL as eps, pb as book_value, market_cap, sector, CAST(last_updated AS VARCHAR) FROM fundamentals"
        ).df()
        if not df.empty:
            df = df.replace({np.nan: None})
            records = df.values.tolist()
            cursor.executemany(
                "INSERT OR REPLACE INTO fundamentals VALUES (?,?,?,?,?,?,?,?)", records
            )
            sqlite_conn.commit()
            print(f"[+] Migrated {len(df)} fundamentals.")
    except Exception as e:
        print(f"[!] Error migrating fundamentals: {e}")

    # 3. Check for Quarterly Results
    try:
        tables = duck_conn.execute("SHOW TABLES").df()["name"].tolist()
        if "quarterly_results" in tables:
            print("[*] Migrating quarterly_results...")
            df = duck_conn.execute(
                "SELECT symbol, CAST(report_date AS VARCHAR), revenue, net_profit, eps, opm_pct FROM quarterly_results"
            ).df()
            if not df.empty:
                df = df.replace({np.nan: None})
                records = df.values.tolist()
                cursor.executemany(
                    "INSERT OR REPLACE INTO quarterly_results VALUES (?,?,?,?,?,?)",
                    records,
                )
                sqlite_conn.commit()
                print(f"[+] Migrated {len(df)} quarterly records.")
    except Exception as e:
        print(f"[*] Note: quarterly_results migration skipped or not found: {e}")

    duck_conn.close()
    sqlite_conn.close()
    print("[MYRA] Valuation migration complete.")


if __name__ == "__main__":
    migrate_valuation()
