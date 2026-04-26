import os
import sys
import sqlite3
import argparse

# Anchor to project root regardless of where script is launched from
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from myra_app.librarian_core import LibrarianCore

DB_DIR = os.path.join(PROJECT_ROOT, "myra_app", "db")
DB_MAP = LibrarianCore.DB_MAP

class DbDoctor:
    def __init__(self, dry_run=False):
        self.dry_run = dry_run
        self.issues_found = 0
        self.issues_fixed = 0
        self.issues_failed = 0

    def run(self):
        print("\n[MYRA DB DOCTOR] Starting full audit...\n")
        self.check_db_files_exist()
        self.check_technical_schema()
        self.check_meta_schema()
        self.check_valuation_schema()
        self.check_institutional_schema()
        self.check_technical_data_quality()
        self.check_wal_mode()
        self.print_summary()

    def _get_connection(self, db_key):
        db_file = DB_MAP.get(db_key)
        if not db_file:
            return None
        db_path = os.path.join(DB_DIR, db_file)
        if not os.path.exists(db_path):
            return None
        return sqlite3.connect(db_path, check_same_thread=False)

    def check_db_files_exist(self):
        print("--- Checking DB files existence ---")
        for key, filename in DB_MAP.items():
            db_path = os.path.join(DB_DIR, filename)
            if not os.path.exists(db_path):
                print(f"  [WARNING] DB file missing: {filename} (key: {key})")
                self.issues_found += 1
            else:
                print(f"  [OK] Found {filename}")
        print()

    def check_technical_schema(self):
        print("--- Checking Technical DB Schema ---")
        conn = self._get_connection("technical")
        if not conn:
            print("  [SKIP] Technical DB not found.")
            print()
            return

        try:
            c = conn.cursor()

            # Check if technical_data table exists
            c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='technical_data'")
            if not c.fetchone():
                print("  [WARNING] 'technical_data' table missing!")
                self.issues_found += 1
                return

            TECHNICAL_EXPECTED_COLS = {
                "symbol":           "TEXT NOT NULL",
                "date":             "TEXT NOT NULL",
                "open":             "REAL",
                "high":             "REAL",
                "low":              "REAL",
                "close":            "REAL",
                "volume":           "INTEGER",
                "delivery":         "INTEGER",
                "trades":           "INTEGER",
                "vwap":             "REAL",
                "delivery_ratio":   "REAL",
                "delivery_source":  "TEXT",
            }

            c.execute("PRAGMA table_info(technical_data)")
            existing_cols = {row[1] for row in c.fetchall()}

            missing_cols = {col: col_type for col, col_type in TECHNICAL_EXPECTED_COLS.items() if col not in existing_cols}
            for col, col_type in missing_cols.items():
                print(f"  [WARNING] Missing column in technical_data: {col}")
                self.issues_found += 1
                if self.dry_run:
                    print(f"  [DRY RUN] Would fix: ALTER TABLE technical_data ADD COLUMN {col} {col_type}")

            if missing_cols and not self.dry_run:
                try:
                    conn.execute("BEGIN")
                    for col, col_type in missing_cols.items():
                        c.execute(f"ALTER TABLE technical_data ADD COLUMN {col} {col_type}")
                        print(f"  [FIXED] Added column {col}")
                    conn.commit()
                    self.issues_fixed += len(missing_cols)
                except Exception as e:
                    conn.rollback()
                    print(f"  [ERROR] Failed to add columns: {e}")
                    self.issues_failed += len(missing_cols)

            # Verify PRIMARY KEY
            c.execute("PRAGMA index_list(technical_data)")
            indexes = c.fetchall()
            pk_found = False
            for idx in indexes:
                if idx[3] == 'pk': # origin column in PRAGMA index_list
                    pk_found = True
                    break

            # Check PRAGMA table_info for primary key as well just in case (sqlite sometimes does inline PKs differently)
            c.execute("PRAGMA table_info(technical_data)")
            pk_cols = [row[1] for row in c.fetchall() if row[5] > 0]

            if not pk_found and len(pk_cols) == 0:
                print("  [WARNING] PRIMARY KEY missing on technical_data. Note: Run a full rebuild to fix.")
                self.issues_found += 1

        finally:
            conn.close()
        print()

    def check_meta_schema(self):
        print("--- Checking Metadata DB Schema ---")
        conn = self._get_connection("meta")
        if not conn:
            print("  [SKIP] Metadata DB not found.")
            print()
            return

        try:
            c = conn.cursor()

            expected_tables = ["symbols_master", "index_constituents", "benchmarks", "metadata", "lineage_tracking"]
            for table in expected_tables:
                c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,))
                if not c.fetchone():
                    print(f"  [WARNING] Missing table in meta DB: {table}")
                    self.issues_found += 1

            # Check symbols_master columns
            c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='symbols_master'")
            if c.fetchone():
                META_EXPECTED_COLS = {
                    "symbol":                "TEXT PRIMARY KEY",
                    "first_seen":            "TEXT",
                    "last_seen":             "TEXT",
                    "in_active_universe":    "INTEGER DEFAULT 0",
                    "in_nifty500":           "INTEGER DEFAULT 0",
                    "sector":                "TEXT",
                    "industry":              "TEXT",
                    "source":                "TEXT",
                    "confidence":            "REAL",
                    "last_updated_sector":   "TEXT",
                    "sector_locked":         "INTEGER DEFAULT 0",
                    "is_active":             "INTEGER DEFAULT 1",
                    "instrument_type":       "TEXT DEFAULT 'EQUITY'",
                    "last_fundamental_update": "TEXT",
                }

                c.execute("PRAGMA table_info(symbols_master)")
                existing_cols = {row[1] for row in c.fetchall()}

                missing_cols = {col: col_type for col, col_type in META_EXPECTED_COLS.items() if col not in existing_cols}
                for col, col_type in missing_cols.items():
                    print(f"  [WARNING] Missing column in symbols_master: {col}")
                    self.issues_found += 1
                    if self.dry_run:
                        print(f"  [DRY RUN] Would fix: ALTER TABLE symbols_master ADD COLUMN {col} {col_type.replace('PRIMARY KEY', '')}")

                if missing_cols and not self.dry_run:
                    try:
                        conn.execute("BEGIN")
                        for col, col_type in missing_cols.items():
                            add_type = col_type.replace("PRIMARY KEY", "")
                            c.execute(f"ALTER TABLE symbols_master ADD COLUMN {col} {add_type}")
                            print(f"  [FIXED] Added column {col}")
                        conn.commit()
                        self.issues_fixed += len(missing_cols)
                    except Exception as e:
                        conn.rollback()
                        print(f"  [ERROR] Failed to add columns: {e}")
                        self.issues_failed += len(missing_cols)

        finally:
            conn.close()
        print()

    def check_valuation_schema(self):
        print("--- Checking Valuation DB Schema ---")
        conn = self._get_connection("valuation")
        if not conn:
            print("  [SKIP] Valuation DB not found.")
            print()
            return

        try:
            c = conn.cursor()

            c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='fundamentals'")
            if c.fetchone():
                c.execute("PRAGMA table_info(fundamentals)")
                existing_cols = {row[1] for row in c.fetchall()}

                missing_cols = [col for col in ["symbol", "sector"] if col not in existing_cols]
                for col in missing_cols:
                    print(f"  [WARNING] Missing column in fundamentals: {col}")
                    self.issues_found += 1
                    if self.dry_run:
                        print(f"  [DRY RUN] Would fix: ALTER TABLE fundamentals ADD COLUMN {col} TEXT")

                if missing_cols and not self.dry_run:
                    try:
                        conn.execute("BEGIN")
                        for col in missing_cols:
                            c.execute(f"ALTER TABLE fundamentals ADD COLUMN {col} TEXT")
                            print(f"  [FIXED] Added column {col}")
                        conn.commit()
                        self.issues_fixed += len(missing_cols)
                    except Exception as e:
                        conn.rollback()
                        print(f"  [ERROR] Failed to add columns: {e}")
                        self.issues_failed += len(missing_cols)

        finally:
            conn.close()
        print()

    def check_institutional_schema(self):
        print("--- Checking Institutional DB Schema ---")
        conn = self._get_connection("institutional")
        if not conn:
            print("  [SKIP] Institutional DB not found.")
            print()
            return

        try:
            c = conn.cursor()

            c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='insider_trades'")
            if c.fetchone():
                c.execute("PRAGMA table_info(insider_trades)")
                existing_cols = {row[1] for row in c.fetchall()}

                # Note: insider_trades uses original NSE column names.
                # Canonical aliases (transaction_type, price, value) are applied
                # at query time in engine.py and institutional_pipe.py.
                INSTITUTIONAL_EXPECTED_COLS = [
                    "symbol", "acq_name", "category", "type",
                    "mode", "value_cr", "avg_price", "date"
                ]
                for col in INSTITUTIONAL_EXPECTED_COLS:
                    if col not in existing_cols:
                        print(f"  [WARNING] Missing column in insider_trades: {col}")
                        self.issues_found += 1

        finally:
            conn.close()
        print()

    def check_technical_data_quality(self):
        print("--- Checking Technical Data Quality ---")
        conn = self._get_connection("technical")
        if not conn:
            print("  [SKIP] Technical DB not found.")
            print()
            return

        try:
            c = conn.cursor()

            checks = [
                ("Rows with zero/negative close price",
                 "SELECT COUNT(*) FROM technical_data WHERE close <= 0"),

                ("Rows with delivery > 0 but volume = 0",
                 "SELECT COUNT(*) FROM technical_data WHERE volume = 0 AND delivery > 0"),

                ("Rows with delivery_ratio > 1.0",
                 "SELECT COUNT(*) FROM technical_data WHERE delivery_ratio > 1.0"),

                ("Future-dated rows",
                 "SELECT COUNT(*) FROM technical_data WHERE date > date('now')"),

                ("Rows with delivery data but NULL delivery_source",
                 "SELECT COUNT(*) FROM technical_data WHERE delivery IS NOT NULL AND delivery_source IS NULL"),
            ]

            for desc, query in checks:
                try:
                    c.execute(query)
                    count = c.fetchone()[0]
                    if count > 0:
                        print(f"  [WARNING] {desc}: {count}")
                        self.issues_found += 1

                        if "NULL delivery_source" in desc:
                            if self.dry_run:
                                print("  [DRY RUN] Would fix: UPDATE technical_data SET delivery_source = 'raw_qty' WHERE delivery IS NOT NULL AND delivery_source IS NULL")
                            else:
                                try:
                                    c.execute("UPDATE technical_data SET delivery_source = 'raw_qty' WHERE delivery IS NOT NULL AND delivery_source IS NULL")
                                    conn.commit()
                                    print(f"  [FIXED] Updated delivery_source for {count} rows")
                                    self.issues_fixed += 1
                                except Exception as e:
                                    print(f"  [ERROR] Failed to update delivery_source: {e}")
                                    self.issues_failed += 1
                except Exception as e:
                    print(f"  [ERROR] Quality check failed ({desc}): {e}")
                    self.issues_failed += 1

        finally:
            conn.close()
        print()

    def check_wal_mode(self):
        print("--- Checking WAL Mode ---")
        for key, filename in DB_MAP.items():
            db_path = os.path.join(DB_DIR, filename)
            if not os.path.exists(db_path):
                continue

            try:
                conn = sqlite3.connect(db_path, check_same_thread=False)
                c = conn.cursor()

                c.execute("PRAGMA journal_mode")
                mode = c.fetchone()[0].upper()

                if mode != "WAL":
                    print(f"  [WARNING] {filename} is not in WAL mode (current: {mode})")
                    self.issues_found += 1
                    if self.dry_run:
                        print(f"  [DRY RUN] Would fix: PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL; on {filename}")
                    else:
                        c.execute("PRAGMA journal_mode=WAL")
                        c.execute("PRAGMA synchronous=NORMAL")
                        conn.commit()
                        print(f"  [FIXED] Enabled WAL mode on {filename}")
                        self.issues_fixed += 1
                else:
                    # just ensure synchronous=NORMAL if it's already WAL
                    if not self.dry_run:
                        c.execute("PRAGMA synchronous=NORMAL")
                        conn.commit()

            except Exception as e:
                print(f"  [ERROR] Failed to configure WAL mode for {filename}: {e}")
                self.issues_failed += 1
            finally:
                if 'conn' in locals() and conn:
                    conn.close()
        print()

    def print_summary(self):
        print("[MYRA DB DOCTOR] Audit complete.")
        print(f"  Issues found  : {self.issues_found}")
        print(f"  Issues fixed  : {self.issues_fixed}")
        if self.issues_failed > 0:
            print(f"  Issues failed : {self.issues_failed}")
        if self.issues_found > self.issues_fixed:
            attention_needed = self.issues_found - self.issues_fixed
            print(f"  Needs attention: {attention_needed} (see warnings above)")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Audit and heal MYRA SQLite databases.")
    parser.add_argument("--dry-run", action="store_true", help="Report only, no changes")
    args = parser.parse_args()

    doctor = DbDoctor(dry_run=args.dry_run)
    doctor.run()
