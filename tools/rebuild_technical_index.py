#!/usr/bin/env python
"""
MYRA Technical DB Rebuilder
Adds PRIMARY KEY (symbol, date) and performance index to technical_data.
Safe to run — zero duplicates confirmed. Takes ~30-60 seconds on large DBs.
Run from project root: python tools/rebuild_technical_index.py
"""

import os
import sqlite3
import sys
import time

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from myra_app.librarian_core import LibrarianCore

DB_PATH = os.path.join(
    PROJECT_ROOT, "myra_app", "db", LibrarianCore.DB_MAP["technical"]
)


def rebuild():
    print(f"[MYRA] Connecting to {DB_PATH}")
    conn = sqlite3.connect(DB_PATH, timeout=60)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA cache_size=-64000")  # 64MB cache for speed

    try:
        # Step 1: Confirm zero duplicates before touching anything
        print("[1/6] Verifying zero duplicates...")
        total = conn.execute("SELECT COUNT(*) FROM technical_data").fetchone()[0]
        unique = conn.execute(
            "SELECT COUNT(*) FROM (SELECT symbol, date FROM technical_data GROUP BY symbol, date)"
        ).fetchone()[0]
        dupes = total - unique
        if dupes > 0:
            print(f"[!] ABORT: {dupes} duplicate rows found. Run db_doctor.py first.")
            return False
        print(f"    Total rows: {total}, Duplicates: 0 — safe to proceed.")

        # Step 2: Create new table with PRIMARY KEY
        print("[2/6] Creating new table with PRIMARY KEY...")
        conn.execute("BEGIN EXCLUSIVE")
        conn.execute("DROP TABLE IF EXISTS technical_data_new")
        conn.execute("""
            CREATE TABLE technical_data_new (
                symbol                       TEXT NOT NULL,
                date                         TEXT NOT NULL,
                open                         REAL,
                high                         REAL,
                low                          REAL,
                close                        REAL,
                volume                       INTEGER,
                delivery                     INTEGER,
                trades                       INTEGER,
                vwap                         REAL,
                delivery_pct                 REAL,
                delivery_ratio               REAL,
                delivery_qty                 REAL,
                stock_return                 REAL,
                market_return                REAL,
                delivery_divergence_score    REAL,
                volatility_compression_score REAL,
                relative_volume_score        REAL,
                nifty_outperformance_score   REAL,
                delivery_source              TEXT,
                PRIMARY KEY (symbol, date)
            )
        """)

        # Step 3: Copy all data ordered correctly
        print("[3/6] Copying data (this may take 30-60 seconds)...")
        t0 = time.time()
        conn.execute("""
            INSERT INTO technical_data_new
            SELECT
                symbol, date, open, high, low, close, volume,
                delivery, trades, vwap, delivery_pct, delivery_ratio,
                delivery_qty, stock_return, market_return,
                delivery_divergence_score, volatility_compression_score,
                relative_volume_score, nifty_outperformance_score,
                delivery_source
            FROM technical_data
            ORDER BY symbol, date
        """)
        elapsed = round(time.time() - t0, 1)
        copied = conn.execute("SELECT COUNT(*) FROM technical_data_new").fetchone()[0]
        print(f"    Copied {copied} rows in {elapsed}s")

        if copied != total:
            print(f"[!] ABORT: Row count mismatch ({copied} vs {total}). Rolling back.")
            conn.execute("DROP TABLE technical_data_new")
            conn.rollback()
            return False

        # Step 4: Atomic swap
        print("[4/6] Swapping tables...")
        conn.execute("ALTER TABLE technical_data RENAME TO technical_data_old")
        conn.execute("ALTER TABLE technical_data_new RENAME TO technical_data")

        # Step 5: Add performance index
        print("[5/6] Creating performance index...")
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_technical_symbol_date
            ON technical_data (symbol, date DESC)
        """)

        # Step 6: Drop old table and vacuum
        print("[6/6] Cleaning up...")
        conn.execute("DROP TABLE technical_data_old")
        conn.commit()
        conn.execute("PRAGMA optimize")
        conn.execute("VACUUM")

        print(f"\n[MYRA] Rebuild complete.")
        print(f"  Rows preserved : {copied}")
        print(f"  PRIMARY KEY    : (symbol, date) ✓")
        print(f"  Index          : idx_technical_symbol_date ✓")
        return True

    except Exception as e:
        print(f"\n[!] ERROR: {e}")
        print("[!] Rolling back — your data is safe.")
        conn.rollback()
        try:
            # If swap already happened, swap back
            conn.execute("ALTER TABLE technical_data RENAME TO technical_data_new")
            conn.execute("ALTER TABLE technical_data_old RENAME TO technical_data")
            conn.commit()
            print("[!] Table restored to original state.")
        except Exception:
            pass
        return False

    finally:
        conn.close()


if __name__ == "__main__":
    print("\n[MYRA] Technical DB Rebuilder")
    print("=" * 40)
    print("This will rebuild technical_data with a PRIMARY KEY.")
    print("Your data is safe — zero duplicates confirmed.")
    print("Do NOT run any scans or ingestion while this is running.\n")
    confirm = input("Type YES to proceed: ").strip()
    if confirm != "YES":
        print("Aborted.")
        sys.exit(0)
    success = rebuild()
    sys.exit(0 if success else 1)
