import argparse
import os
import sqlite3
import sys

# Anchor to project root regardless of where script is launched from
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from datetime import datetime, timedelta, timezone, date
from myra_app.librarian_core import LibrarianCore

DB_DIR = os.path.join(PROJECT_ROOT, "myra_app", "db")
DB_MAP = LibrarianCore.DB_MAP

# Meta DB path matches task_utils.py / librarian_core.DB_MAP["meta"] convention.
META_DB_PATH = os.path.join(DB_DIR, DB_MAP["meta"])


class _TeeStdout:
    """File-like that forwards every write to a real stream and retains a copy.

    Used to capture audit output during a doctor run without altering the
    on-screen console output. Captured text is parsed by
    ``_extract_issue_lines`` after the run completes.
    """

    def __init__(self, real):
        self._real = real
        self._buf: list[str] = []

    def write(self, s):
        self._real.write(s)
        self._buf.append(s)
        return len(s)

    def flush(self):
        try:
            self._real.flush()
        except Exception:
            pass

    def isatty(self):
        try:
            return self._real.isatty()
        except Exception:
            return False

    @property
    def captured(self) -> str:
        return "".join(self._buf)


# Issue-line keywords to capture for the doctor_runs JSON payload. The
# existing audit uses these exact tokens in its print() lines.
_ISSUE_KEYWORDS = ("[WARNING]", "[CRITICAL]", "[ERROR]")


def _extract_issue_lines(text: str) -> list[str]:
    """Pull WARNING/CRITICAL/ERROR lines from captured stdout, ordered as they
    appeared. Order is preserved so a stable top-N still reflects the audit's
    actual emission order rather than sorted-by-severity."""
    out: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        for kw in _ISSUE_KEYWORDS:
            if kw in stripped:
                # Trim leading two-space indent + "[KEYWORD] " token for storage.
                msg = stripped
                # Drop the two-space prefix if present.
                if msg.startswith("  "):
                    msg = msg[2:]
                # Drop the [KEYWORD] token (keep the message body).
                if msg.startswith(kw):
                    msg = msg[len(kw) :].lstrip()
                out.append(f"{kw} {msg}")
                break
    return out


def _ensure_doctor_runs_table(conn: sqlite3.Connection) -> None:
    """Idempotent CREATE TABLE IF NOT EXISTS for doctor_runs.

    Schema mirrors the prevailing style in librarian_schema.py:78-89
    (lineage_tracking) and etf_sync.py:369 — lowercase snake_case columns,
    ``INTEGER PRIMARY KEY AUTOINCREMENT`` for event-log tables.
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS doctor_runs (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            when_utc        DATETIME,
            issues_found    INTEGER,
            issues_fixed    INTEGER,
            issues_failed   INTEGER,
            critical_json   TEXT
        )
        """
    )
    conn.commit()


def _persist_doctor_run(
    when_utc: str,
    issues_found: int,
    issues_fixed: int,
    issues_failed: int,
    issues: list[str],
) -> None:
    """Write one doctor_runs row. Failure of this write must never crash the
    audit (prompt safety requirement). All exceptions are caught and logged."""
    import json

    try:
        # Cap stored payload to keep the table row compact while preserving
        # all CRITICAL entries (highest-severity first within the head of the
        # list, since audit emits CRITICAL inline).
        critical = [s for s in issues if s.startswith("[CRITICAL]")]
        non_critical = [s for s in issues if not s.startswith("[CRITICAL]")]
        top = critical + non_critical
        # If total issues exceed the top-N cap (5), keep all criticals + the
        # first (5 - #criticals) non-criticals.
        KEEP = 5
        if len(top) > KEEP:
            kept = critical[:KEEP]
            remaining = KEEP - len(kept)
            if remaining > 0:
                kept += non_critical[:remaining]
            top = kept

        payload = json.dumps(top, ensure_ascii=False)
    except Exception as exc:
        payload = json.dumps([f"<<payload-construction-failed: {exc}>>"])

    try:
        conn = sqlite3.connect(META_DB_PATH, timeout=10)
        try:
            _ensure_doctor_runs_table(conn)
            conn.execute(
                "INSERT INTO doctor_runs "
                "(when_utc, issues_found, issues_fixed, issues_failed, critical_json) "
                "VALUES (?, ?, ?, ?, ?)",
                (when_utc, issues_found, issues_fixed, issues_failed, payload),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:
        # Last-ditch: log and continue. The audit results stand regardless.
        print(f"  [WARNING] Failed to persist doctor_runs row: {exc}")


class DbDoctor:
    def __init__(self, dry_run=False):
        self.dry_run = dry_run
        self.issues_found = 0
        self.issues_fixed = 0
        self.issues_failed = 0

    def run(self):
        # Install stdout tee so we can capture audit output without changing
        # what the operator sees on the console. Restored in the finally block
        # even if any check raises.
        real_stdout = sys.stdout
        tee = _TeeStdout(real_stdout)
        sys.stdout = tee
        when_utc = datetime.now(timezone.utc).isoformat()
        try:
            print("\n[MYRA DB DOCTOR] Starting full audit...\n")
            self.check_db_files_exist()
            self.check_technical_schema()
            self.check_meta_schema()
            self.check_valuation_schema()
            self.check_technical_data_quality()
            self.check_etf_contamination()
            self._record_audit_run()
            self.check_wal_mode()
            self.print_summary()
        finally:
            sys.stdout = real_stdout
        # Persist one summary row to doctor_runs. This runs after stdout is
        # restored so any failure in the persistence layer cannot corrupt the
        # operator's console output above. A failure to persist is logged
        # but never crashes this method.
        _persist_doctor_run(
            when_utc=when_utc,
            issues_found=self.issues_found,
            issues_fixed=self.issues_fixed,
            issues_failed=self.issues_failed,
            issues=_extract_issue_lines(tee.captured),
        )

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
            c.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='technical_data'"
            )
            if not c.fetchone():
                print("  [WARNING] 'technical_data' table missing!")
                self.issues_found += 1
                return

            TECHNICAL_EXPECTED_COLS = {
                "symbol": "TEXT NOT NULL",
                "date": "TEXT NOT NULL",
                "open": "REAL",
                "high": "REAL",
                "low": "REAL",
                "close": "REAL",
                "volume": "INTEGER",
                "delivery": "INTEGER",
                "trades": "INTEGER",
                "vwap": "REAL",
                "delivery_ratio": "REAL",
                "delivery_source": "TEXT",
            }

            c.execute("PRAGMA table_info(technical_data)")
            existing_cols = {row[1] for row in c.fetchall()}

            missing_cols = {
                col: col_type
                for col, col_type in TECHNICAL_EXPECTED_COLS.items()
                if col not in existing_cols
            }
            for col, col_type in missing_cols.items():
                print(f"  [WARNING] Missing column in technical_data: {col}")
                self.issues_found += 1
                if self.dry_run:
                    print(
                        f"  [DRY RUN] Would fix: ALTER TABLE technical_data ADD COLUMN {col} {col_type}"
                    )

            if missing_cols and not self.dry_run:
                try:
                    conn.execute("BEGIN")
                    for col, col_type in missing_cols.items():
                        c.execute(  # noqa: PG-NPLUS1
                            f"ALTER TABLE technical_data ADD COLUMN {col} {col_type}"
                        )
                        print(f"  [FIXED] Added column {col}")
                        conn.commit()
                        self.issues_fixed += 1
                except Exception as e:
                    conn.rollback()
                    print(f"  [ERROR] Failed to add columns: {e}")
                    self.issues_failed += len(missing_cols)

            # Verify PRIMARY KEY
            c.execute("PRAGMA index_list(technical_data)")
            indexes = c.fetchall()
            pk_found = False
            for idx in indexes:
                if idx[3] == "pk":  # origin column in PRAGMA index_list
                    pk_found = True
                    break

            # Check PRAGMA table_info for primary key as well just in case (sqlite sometimes does inline PKs differently)
            c.execute("PRAGMA table_info(technical_data)")
            pk_cols = [row[1] for row in c.fetchall() if row[5] > 0]

            # If no explicit PRIMARY KEY found, check for UNIQUE index on (symbol, date)
            if not pk_found and len(pk_cols) == 0:
                unique_index_found = False
                for idx in indexes:
                    idx_name = idx[1]  # index name
                    idx_sql = idx[4]  # SQL for the index

                    # Check if this is a UNIQUE index covering (symbol, date)
                    if (
                        "UNIQUE" in idx_sql
                        and "symbol" in idx_sql
                        and "date" in idx_sql
                    ):
                        unique_index_found = True
                        break

                if not unique_index_found:
                    print(
                        "  [WARNING] PRIMARY KEY missing on technical_data. Note: Run a full rebuild to fix."
                    )
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

            expected_tables = [
                "symbols_master",
                "index_constituents",
                "benchmarks",
                "metadata",
                "lineage_tracking",
                "sync_log",
            ]
            c.execute("SELECT name FROM sqlite_master WHERE type='table'")
            existing = {row[0] for row in c.fetchall()}

            for table in expected_tables:
                if table not in existing:
                    print(f"  [WARNING] Missing table in meta DB: {table}")
                    self.issues_found += 1

            # Auto-create sync_log if missing
            if "sync_log" not in existing:
                if self.dry_run:
                    print("  [DRY RUN] Would create sync_log table")
                else:
                    try:
                        conn.execute(
                            """
                            CREATE TABLE sync_log (
                                task_name TEXT PRIMARY KEY,
                                last_run TEXT
                            )
                        """
                        )
                        conn.commit()
                        print("  [FIXED] Created sync_log table")
                        self.issues_fixed += 1
                    except Exception as e:
                        print(f"  [ERROR] Failed to create sync_log table: {e}")
                        self.issues_failed += 1

            # Check symbols_master columns
            c.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='symbols_master'"
            )
            if c.fetchone():
                META_EXPECTED_COLS = {
                    "symbol": "TEXT PRIMARY KEY",
                    "first_seen": "TEXT",
                    "last_seen": "TEXT",
                    "in_active_universe": "INTEGER DEFAULT 0",
                    "in_nifty500": "INTEGER DEFAULT 0",
                    "sector": "TEXT",
                    "industry": "TEXT",
                    "source": "TEXT",
                    "confidence": "REAL",
                    "last_updated_sector": "TEXT",
                    "sector_locked": "INTEGER DEFAULT 0",
                    "is_active": "INTEGER DEFAULT 1",
                    "instrument_type": "TEXT DEFAULT 'EQUITY'",
                    "last_fundamental_update": "TEXT",
                }

                c.execute("PRAGMA table_info(symbols_master)")
                existing_cols = {row[1] for row in c.fetchall()}

                missing_cols = {
                    col: col_type
                    for col, col_type in META_EXPECTED_COLS.items()
                    if col not in existing_cols
                }
                for col, col_type in missing_cols.items():
                    print(f"  [WARNING] Missing column in symbols_master: {col}")
                    self.issues_found += 1
                    if self.dry_run:
                        print(
                            f"  [DRY RUN] Would fix: ALTER TABLE symbols_master ADD COLUMN {col} {col_type.replace('PRIMARY KEY', '')}"
                        )

                if missing_cols and not self.dry_run:
                    try:
                        conn.execute("BEGIN")
                        for col, col_type in missing_cols.items():
                            add_type = col_type.replace("PRIMARY KEY", "")
                            c.execute(  # noqa: PG-NPLUS1
                                f"ALTER TABLE symbols_master ADD COLUMN {col} {add_type}"
                            )
                            print(f"  [FIXED] Added column {col}")
                            self.issues_fixed += 1
                        conn.commit()
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

            # Expected fundamentals table schema
            FUNDAMENTALS_EXPECTED_COLS = {
                "symbol": "TEXT NOT NULL",
                "date": "TEXT NOT NULL",
                "pe": "REAL",
                "sector_pe": "REAL",
                "market_cap": "REAL",
                "face_value": "REAL",
                "issued_size": "INTEGER",
                "net_margin": "REAL",
                "roe_ttm": "REAL",
                "dividend_yield": "REAL",
                "daily_volatility": "REAL",
                "annual_volatility": "REAL",
                "impact_cost": "REAL",
                "source_ms": "TEXT",
                "source_nse": "TEXT",
            }

            c.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='fundamentals'"
            )
            if not c.fetchone():
                print("  [WARNING] 'fundamentals' table missing!")
                self.issues_found += 1
                if self.dry_run:
                    print(
                        "  [DRY RUN] Would create fundamentals table with full schema"
                    )
                else:
                    try:
                        conn.execute(
                            """
                            CREATE TABLE fundamentals (
                                symbol TEXT NOT NULL,
                                date TEXT NOT NULL,
                                pe REAL,
                                sector_pe REAL,
                                market_cap REAL,
                                face_value REAL,
                                issued_size INTEGER,
                                net_margin REAL,
                                roe_ttm REAL,
                                dividend_yield REAL,
                                daily_volatility REAL,
                                annual_volatility REAL,
                                impact_cost REAL,
                                source_ms TEXT,
                                source_nse TEXT,
                                PRIMARY KEY (symbol, date)
                            )
                        """
                        )
                        conn.commit()
                        print("  [FIXED] Created fundamentals table")
                        self.issues_fixed += 1
                    except Exception as e:
                        print(f"  [ERROR] Failed to create fundamentals table: {e}")
                        self.issues_failed += 1
            else:
                c.execute("PRAGMA table_info(fundamentals)")
                existing_cols = {row[1] for row in c.fetchall()}

                missing_cols = {
                    col: col_type
                    for col, col_type in FUNDAMENTALS_EXPECTED_COLS.items()
                    if col not in existing_cols
                }
                for col, col_type in missing_cols.items():
                    print(f"  [WARNING] Missing column in fundamentals: {col}")
                    self.issues_found += 1
                    if self.dry_run:
                        print(
                            f"  [DRY RUN] Would fix: ALTER TABLE fundamentals ADD COLUMN {col} {col_type}"
                        )

                if missing_cols and not self.dry_run:
                    try:
                        conn.execute("BEGIN")
                        for col, col_type in missing_cols.items():
                            c.execute(  # noqa: PG-NPLUS1
                                f"ALTER TABLE fundamentals ADD COLUMN {col} {col_type}"
                            )
                            print(f"  [FIXED] Added column {col}")
                            self.issues_fixed += 1
                        conn.commit()
                    except Exception as e:
                        conn.rollback()
                        print(f"  [ERROR] Failed to add columns: {e}")
                        self.issues_failed += len(missing_cols)

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

            # Combine all COUNT(*) queries into a single table scan
            quality_query = """
                SELECT
                    SUM(CASE WHEN close <= 0 THEN 1 ELSE 0 END) AS bad_close,
                    SUM(CASE WHEN volume = 0 AND delivery > 0 THEN 1 ELSE 0 END) AS bad_del,
                    SUM(CASE WHEN delivery_ratio > 1.0 THEN 1 ELSE 0 END) AS bad_ratio,
                    SUM(CASE WHEN date > date('now') THEN 1 ELSE 0 END) AS future_dates,
                    SUM(CASE WHEN delivery IS NOT NULL AND delivery_source IS NULL THEN 1 ELSE 0 END) AS null_source
                FROM technical_data
            """
            c.execute(quality_query)
            result = c.fetchone()
            bad_close, bad_del, bad_ratio, future_dates, null_source = result

            if bad_close > 0:
                print(f"  [WARNING] Rows with zero/negative close price: {bad_close}")
                self.issues_found += 1
            if bad_del > 0:
                print(f"  [WARNING] Rows with delivery > 0 but volume = 0: {bad_del}")
                self.issues_found += 1
            if bad_ratio > 0:
                print(f"  [WARNING] Rows with delivery_ratio > 1.0: {bad_ratio}")
                self.issues_found += 1
            if future_dates > 0:
                print(f"  [WARNING] Future-dated rows: {future_dates}")
                self.issues_found += 1
            if null_source > 0:
                print(
                    f"  [WARNING] Rows with delivery data but NULL delivery_source: {null_source}"
                )
                self.issues_found += 1
                if self.dry_run:
                    print(
                        "  [DRY RUN] Would fix: UPDATE technical_data SET delivery_source = 'raw_qty' WHERE delivery IS NOT NULL AND delivery_source IS NULL"
                    )
                else:
                    try:
                        c.execute(
                            "UPDATE technical_data SET delivery_source = 'raw_qty' WHERE delivery IS NOT NULL AND delivery_source IS NULL"
                        )
                        conn.commit()
                        print(
                            f"  [FIXED] Updated delivery_source for {null_source} rows"
                        )
                        self.issues_fixed += 1
                    except Exception as e:
                        print(f"  [ERROR] Failed to update delivery_source: {e}")
                        self.issues_failed += 1

            # Backfill delivery_pct for rows that have delivery but NULL delivery_pct
            null_pct = conn.execute(
                "SELECT COUNT(*) FROM technical_data WHERE delivery IS NOT NULL AND volume > 0 AND delivery_pct IS NULL"
            ).fetchone()[0]

            if null_pct > 0:
                self.issues_found += 1
                print(
                    f"  [WARNING] Rows with NULL delivery_pct but valid delivery: {null_pct:,}"
                )
                if not self.dry_run:
                    conn.execute(
                        """
                        UPDATE technical_data
                        SET delivery_pct = ROUND((delivery * 100.0 / volume), 2)
                        WHERE delivery IS NOT NULL AND volume > 0 AND delivery_pct IS NULL
                    """
                    )
                    conn.commit()
                    self.issues_fixed += 1
                    print(f"  [FIXED] Backfilled delivery_pct for {null_pct:,} rows")
                else:
                    print(
                        f"  [DRY RUN] Would backfill delivery_pct for {null_pct:,} rows"
                    )

            try:
                ist_now = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)

                latest_date = conn.execute(
                    "SELECT MAX(date) FROM technical_data"
                ).fetchone()[0]

                if latest_date:
                    db_date = datetime.strptime(latest_date, "%Y-%m-%d").date()
                    current_date = ist_now.date()
                    days_behind = (current_date - db_date).days

                    print(f"  [INFO] DB latest date: {latest_date}")
                    print(f"  [INFO] Current IST date: {current_date.isoformat()}")
                    print(f"  [INFO] Days behind: {days_behind}")

                    if days_behind >= 2:
                        print(
                            f"  [CRITICAL] Database is STALE - {days_behind} days behind current date!"
                        )
                        self.issues_found += 1
                    elif days_behind > 0:
                        print(
                            f"  [WARNING] Database is slightly behind - {days_behind} days"
                        )
                        self.issues_found += 1
                    else:
                        print(f"  [OK] Database is up to date")
                else:
                    print(f"  [ERROR] No data found in technical_data table")
                    self.issues_found += 1

            except Exception as e:
                print(f"  [ERROR] Staleness check failed: {e}")
                self.issues_failed += 1

        finally:
            conn.close()
        print()

    def check_etf_contamination(self):
        print("--- Checking ETF Contamination in Technical DB ---")
        try:
            from myra_app.utils.etf_sync import purge_etf_rows_from_technical_db

            count = purge_etf_rows_from_technical_db(dry_run=self.dry_run)
            if count > 0:
                self.issues_found += 1
                if not self.dry_run:
                    self.issues_fixed += 1
        except Exception as e:
            print(f"  [ERROR] ETF contamination check failed: {e}")
            self.issues_failed += 1
        print()

    def _record_audit_run(self):
        """Record audit run timestamp for tracking."""
        import json

        log_path = os.path.join(PROJECT_ROOT, "logs", "scanner_skips.json")

        try:
            existing = {}
            if os.path.exists(log_path):
                with open(log_path, "r") as f:
                    existing = json.load(f)

            # Record today's audit timestamp
            today = date.today().isoformat()
            existing[today] = {
                "audit_run": str(datetime.now()),
            }

            # Keep only last 90 days
            keys = sorted(existing.keys())[-90:]
            trimmed = {k: existing[k] for k in keys}

            os.makedirs(os.path.dirname(log_path), exist_ok=True)
            with open(log_path, "w") as f:
                json.dump(trimmed, f, indent=2)
            print("  [INFO] Audit run logged")
        except Exception as e:
            print(f"  [WARNING] Audit logging failed: {e}")
        print()

    def check_wal_mode(self):
        print("--- Checking WAL Mode ---")
        for key, filename in DB_MAP.items():
            db_path = os.path.join(DB_DIR, filename)
            if not os.path.exists(db_path):
                continue

            conn = None
            try:
                conn = sqlite3.connect(db_path, check_same_thread=False)
                c = conn.cursor()

                c.execute("PRAGMA journal_mode")  # noqa: PG-NPLUS1
                mode = c.fetchone()[0].upper()

                if mode != "WAL":
                    print(
                        f"  [WARNING] {filename} is not in WAL mode (current: {mode})"
                    )
                    self.issues_found += 1
                    if self.dry_run:
                        print(
                            f"  [DRY RUN] Would fix: PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL; on {filename}"
                        )
                    else:
                        c.execute("PRAGMA journal_mode=WAL")  # noqa: PG-NPLUS1
                        c.execute("PRAGMA synchronous=NORMAL")  # noqa: PG-NPLUS1
                        conn.commit()
                        print(f"  [FIXED] Enabled WAL mode on {filename}")
                        self.issues_fixed += 1
                else:
                    # just ensure synchronous=NORMAL if it's already WAL
                    if not self.dry_run:
                        c.execute("PRAGMA synchronous=NORMAL")  # noqa: PG-NPLUS1
                        conn.commit()

            except Exception as e:
                print(f"  [ERROR] Failed to configure WAL mode for {filename}: {e}")
                self.issues_failed += 1
            finally:
                if conn:
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
    parser = argparse.ArgumentParser(
        description="Audit and heal MYRA SQLite databases."
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Report only, no changes"
    )
    args = parser.parse_args()

    doctor = DbDoctor(dry_run=args.dry_run)
    doctor.run()
