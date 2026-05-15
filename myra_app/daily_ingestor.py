import argparse
import io
import json
import logging
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

import pandas as pd

from myra_app.fetcher import DataFetcher
from myra_app.librarian_core import LibrarianCore

DB_PATH = os.path.join("db", LibrarianCore.DB_MAP["technical"])

IST = timezone(timedelta(hours=5, minutes=30))


def get_db_latest_date(db_path: str = None) -> str | None:
    """Get the latest date in technical_data DB. Returns ISO date string or None."""
    if db_path is None:
        db_path = DB_PATH
    if not os.path.exists(db_path):
        return None
    try:
        conn = sqlite3.connect(db_path, timeout=5)
        res = conn.execute("SELECT MAX(date) FROM technical_data").fetchone()
        conn.close()
        return res[0] if res and res[0] else None
    except Exception as e:
        print(f"[WARN] Could not get DB latest date: {e}")
        return None


def get_db_row_count(db_path: str = None, target_date: str = None) -> int:
    """Get row count for a specific date in technical_data."""
    if db_path is None:
        db_path = DB_PATH
    if not os.path.exists(db_path):
        return 0
    try:
        conn = sqlite3.connect(db_path, timeout=5)
        if target_date:
            res = conn.execute(
                "SELECT COUNT(*) FROM technical_data WHERE date = ?", (target_date,)
            ).fetchone()
        else:
            res = conn.execute("SELECT COUNT(*) FROM technical_data").fetchone()
        conn.close()
        return res[0] if res else 0
    except Exception as e:
        print(f"[WARN] Could not get DB row count: {e}")
        return 0


def is_trading_day(dt: datetime) -> bool:
    """Check if a given datetime is a trading day (not weekend, not holiday)."""
    if dt.weekday() >= 5:
        return False
    calendar_db_path = os.path.join("db", LibrarianCore.DB_MAP["calendar"])
    if os.path.exists(calendar_db_path):
        try:
            with sqlite3.connect(calendar_db_path) as cal_conn:
                date_str = dt.date().isoformat()
                cal_res = cal_conn.execute(
                    "SELECT is_trading_day FROM market_calendar WHERE date = ?",
                    (date_str,),
                ).fetchone()
                if cal_res and not cal_res[0]:
                    return False
        except Exception:
            pass
    return True


def get_next_trading_day(from_date: datetime, days_ahead: int = 1) -> datetime:
    """Get the next trading day N days ahead from the given date."""
    current = from_date + timedelta(days=days_ahead)
    max_iterations = 10
    iterations = 0
    while iterations < max_iterations:
        if is_trading_day(current):
            return current
        current += timedelta(days=1)
        iterations += 1
    return current


def calculate_missing_dates(db_latest_date: str, target_date: datetime) -> list:
    """Calculate list of missing trading dates between DB latest and target."""
    missing = []
    if not db_latest_date:
        return missing
    try:
        db_date = datetime.strptime(db_latest_date, "%Y-%m-%d").date()
        target = target_date.date()
        current = db_date + timedelta(days=1)
        while current <= target:
            if is_trading_day(datetime.combine(current, datetime.min.time())):
                missing.append(current.isoformat())
            current += timedelta(days=1)
    except Exception as e:
        print(f"[WARN] Could not calculate missing dates: {e}")
    return missing


def run_daily_update_for_date(current_date: datetime, force: bool = False) -> dict:
    """
    Run daily update for a specific date.
    Returns dict with success status and details.
    """
    from myra_app.task_tracker import register, update, unregister

    result = {
        "success": False,
        "date": current_date.date().isoformat(),
        "rows_inserted": 0,
        "db_before": None,
        "db_after": None,
        "error": None,
    }

    tid = register(f"Daily bhavcopy ingestion {current_date.date()}", task_type="batch")
    try:
        print(f"[MYRA] Processing date: {current_date.date().isoformat()}")

        db_before = get_db_row_count(target_date=current_date.date().isoformat())
        result["db_before"] = db_before

        calendar_db_path = os.path.join("db", LibrarianCore.DB_MAP["calendar"])
        if os.path.exists(calendar_db_path):
            try:
                with sqlite3.connect(calendar_db_path) as cal_conn:
                    date_str = current_date.date().isoformat()
                    cal_res = cal_conn.execute(
                        "SELECT is_trading_day, holiday_name FROM market_calendar WHERE date = ?",
                        (date_str,),
                    ).fetchone()

                    is_trading = True
                    holiday_reason = ""
                    if cal_res:
                        is_trading = bool(cal_res[0])
                        holiday_reason = cal_res[1]
                    elif current_date.weekday() >= 5:
                        is_trading = False
                        holiday_reason = "Weekend"

                    if not is_trading:
                        print(
                            f"[INFO] Market is closed on {date_str} ({holiday_reason or 'Holiday'}). Skipping."
                        )
                        result["success"] = True
                        result["skipped"] = True
                        result["skip_reason"] = holiday_reason
                        return result
            except Exception as e:
                print(f"⚠️ Warning: Could not query calendar DB: {e}")

        fetcher = DataFetcher()

        update(tid, "Fetching data…")
        print(
            f"[MYRA] Requesting data for {current_date.day:02d}-{current_date.month:02d}-{current_date.year}..."
        )

        data_csv, source = fetcher.fetch_ohlcv_delivery(current_date)

        if data_csv == "too_early":
            print(
                "⚠️ Data not yet released. (IST Shield active: It is strictly before 6 PM IST)."
            )
            result["error"] = "too_early"
            result["success"] = False
            return result
        elif data_csv == "holiday_skip":
            print("🛑 Market Holiday or Weekend. Skipping fetch.")
            result["success"] = True
            result["skipped"] = True
            result["skip_reason"] = "holiday_or_weekend"
            return result
        elif not data_csv:
            print("❌ Fetch failed. NSE WAF block or 404 Not Found.")
            result["error"] = "fetch_failed"
            result["success"] = False
            return result

        try:
            archive_csv = clean_bhavcopy_for_archive(data_csv)
            archives_dir = os.path.join("data", "Market_Archives")
            os.makedirs(archives_dir, exist_ok=True)
            csv_path = os.path.join(
                archives_dir, f"nse_full_{current_date.date().isoformat()}.csv"
            )
            with open(csv_path, "w", encoding="utf-8") as f:
                f.write(archive_csv)
            print(f"✅ Cleaned CSV saved to {csv_path}")
        except Exception as e:
            logging.warning(f"Could not save CSV archive: {e}")

        try:
            df = pd.read_csv(io.StringIO(data_csv))
            df.columns = [c.strip().lower() for c in df.columns]

            EXPECTED_COLS = {
                "symbol", "series", "date1", "prev_close", "open_price",
                "high_price", "low_price", "last_price", "close_price",
                "avg_price", "ttl_trd_qnty", "turnover_lacs", "no_of_trades",
                "deliv_qty", "deliv_per",
            }
            actual_cols = set(df.columns)
            unknown = actual_cols - EXPECTED_COLS
            if unknown:
                warn_msg = f"[NSE FORMAT CHANGE] {current_date.date().isoformat()} - unknown columns: {unknown}"
                print(f"  {warn_msg}")
                with open(os.path.join("logs", "nse_warnings.log"), "a") as f:
                    f.write(warn_msg + "\n")
            missing = EXPECTED_COLS - actual_cols
            if missing:
                print(f"  Missing expected columns: {missing}")

            if "series" in df.columns:
                df = df[df["series"].isin(["EQ", "BE", "SM"])]

            from myra_app.utils.etf_sync import get_etf_symbols
            etf_symbols = get_etf_symbols()
            if etf_symbols and "symbol" in df.columns:
                df = df[~df["symbol"].str.upper().isin(etf_symbols)]

            rename_map = {
                "open_price": "open", "high_price": "high", "low_price": "low",
                "close_price": "close", "ttl_trd_qnty": "volume",
                "deliv_qty": "delivery", "deliv_per": "delivery_pct",
            }
            df = df.rename(columns=rename_map)
            df["date"] = current_date.date().isoformat()

            os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
            lib = LibrarianCore(read_only=False)
            conn = sqlite3.connect(DB_PATH, check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            cursor = conn.cursor()

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS ingestion_rejects (
                    symbol TEXT, date TEXT, reason TEXT, raw_values TEXT,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.commit()

            cursor = lib.safe_execute("PRAGMA table_info(technical_data)", conn=conn)
            valid_cols = [info[1] for info in cursor.fetchall()]

            def validate_row(row):
                reasons = []
                for col in ['open', 'high', 'low', 'close']:
                    if col in row and (pd.isna(row[col]) or float(row[col]) <= 0):
                        reasons.append(f"{col} <= 0")
                if 'volume' in row and (pd.isna(row['volume']) or int(row['volume']) <= 0):
                    reasons.append("volume <= 0")
                if 'delivery' in row and 'volume' in row:
                    if not pd.isna(row['delivery']) and not pd.isna(row['volume']):
                        delivery_val = float(row['delivery'])
                        volume_val = int(row['volume'])
                        if delivery_val < 0 or delivery_val > volume_val:
                            reasons.append("delivery out of range [0, volume]")
                return reasons

            valid_rows = []
            reject_rows = []
            for _, row in df.iterrows():
                reject_reasons = validate_row(row)
                if reject_reasons:
                    raw_values = {col: row[col] for col in ['symbol', 'date', 'open', 'high', 'low', 'close', 'volume', 'delivery'] if col in row}
                    cursor.execute(
                        "INSERT INTO ingestion_rejects (symbol, date, reason, raw_values) VALUES (?, ?, ?, ?)",
                        (row.get('symbol', ''), row.get('date', ''), '; '.join(reject_reasons), str(raw_values))
                    )
                    reject_rows.append(row)
                else:
                    valid_rows.append(row)

            if reject_rows:
                conn.commit()
                print(f"  [REJECTED] {len(reject_rows)} invalid rows skipped and logged")

            df_to_insert = pd.DataFrame(valid_rows)
            df_to_insert = df_to_insert[[c for c in df_to_insert.columns if c in valid_cols]]

            if not df_to_insert.empty:
                cols = df_to_insert.columns.tolist()
                placeholders = ", ".join(["?"] * len(cols))
                col_names = ", ".join(cols)
                sql = f"INSERT OR REPLACE INTO technical_data ({col_names}) VALUES ({placeholders})"
                conn.executemany(sql, df_to_insert.values.tolist())
                conn.commit()

            print(f"✅ Successfully added {len(df_to_insert)} rows to Atomic Vault from {source}.")
            result["rows_inserted"] = len(df_to_insert)

            db_after = get_db_row_count(target_date=current_date.date().isoformat())
            result["db_after"] = db_after

            if result["rows_inserted"] == 0 and db_after == db_before:
                print(f"⚠️ WARNING: No new rows inserted for {current_date.date().isoformat()}")
                result["success"] = False
                result["error"] = "no_rows_inserted"
                conn.close()
                return result

            if db_after <= db_before:
                print(f"⚠️ WARNING: DB row count did not increase after insertion")
                result["success"] = False
                result["error"] = "db_not_advanced"
                conn.close()
                return result

            try:
                from myra_app.feature_enrichment import process_enrichment_pipeline
                from myra_app.librarian import Librarian
                enrichment_lib = Librarian(read_only=False)
                enrichment_lib.connect()
                print("[MYRA] Running enrichment on new rows...")
                process_enrichment_pipeline(enrichment_lib, conn)
                print("[MYRA] Enrichment complete.")
            except Exception as e:
                print(f"[!] Enrichment after ingestion failed: {e}")

            conn.close()

            result["success"] = True

        except Exception as e:
            print(f"❌ Critical Database Error: {e}")
            result["error"] = str(e)
            result["success"] = False
    finally:
        unregister(tid)

    return result


def clean_bhavcopy_for_archive(data_csv: str) -> str:
    """Remove non‑equity rows (GS, bonds, etc.) from a raw NSE CSV. ETFs are kept."""
    import io

    import pandas as pd

    df = pd.read_csv(io.StringIO(data_csv))
    df.columns = [c.strip().lower() for c in df.columns]
    if "series" in df.columns:
        df = df[df["series"].isin(["EQ", "BE", "SM"])]
    return df.to_csv(index=False)


def run_daily_update(force_date: str = None, skip_backfill: bool = False) -> dict:
    """
    Main entry point for daily data ingestion.
    Uses DB-gap-driven approach: determines what needs to be fetched based on DB state.

    Returns:
        dict with success status, dates processed, and any errors
    """
    from myra_app.task_tracker import register, unregister

    tid = register("Daily bhavcopy ingestion", task_type="batch")
    overall_result = {
        "success": False,
        "dates_processed": [],
        "dates_failed": [],
        "total_rows_inserted": 0,
        "backfill_performed": False,
    }

    try:
        print("[MYRA] Initiating daily data ingestion (DB-gap-driven)...")

        os.makedirs("logs", exist_ok=True)

        parser = argparse.ArgumentParser(description="MYRA Daily Ingestor")
        parser.add_argument("--date", type=str, help="Date in DD-MM-YYYY format")
        args, _ = parser.parse_known_args()

        target_date_str = force_date or args.date

        ist_now = datetime.now(timezone.utc).astimezone(IST)
        print(f"[MYRA] Current IST time: {ist_now.strftime('%Y-%m-%d %H:%M:%S')}")

        if target_date_str:
            current_date = datetime.strptime(target_date_str, "%d-%m-%Y")
            print(f"[MYRA] Forced target date: {current_date.date().isoformat()}")
            result = run_daily_update_for_date(current_date)
            overall_result["dates_processed"].append(result)
            if result["success"]:
                overall_result["total_rows_inserted"] += result.get("rows_inserted", 0)
                overall_result["success"] = True
            else:
                overall_result["dates_failed"].append(result.get("date"))
            unregister(tid)
            return overall_result

        db_latest_date = get_db_latest_date()
        print(f"[MYRA] Database latest date: {db_latest_date or 'empty'}")

        if not db_latest_date:
            print("[MYRA] DB is empty. Fetching initial data...")
            target_date = get_next_trading_day(ist_now)
            result = run_daily_update_for_date(target_date)
            overall_result["dates_processed"].append(result)
            if result["success"]:
                overall_result["total_rows_inserted"] += result.get("rows_inserted", 0)
                overall_result["success"] = True
            else:
                overall_result["dates_failed"].append(result.get("date"))
            unregister(tid)
            return overall_result

        missing_dates = calculate_missing_dates(db_latest_date, ist_now)
        if missing_dates:
            print(f"[MYRA] Found {len(missing_dates)} missing trading days: {missing_dates[:5]}...")
            if not skip_backfill:
                overall_result["backfill_performed"] = True
                for date_str in missing_dates:
                    try:
                        target_date = datetime.strptime(date_str, "%Y-%m-%d")
                        result = run_daily_update_for_date(target_date)
                        overall_result["dates_processed"].append(result)
                        if result["success"]:
                            overall_result["total_rows_inserted"] += result.get("rows_inserted", 0)
                            if not overall_result["success"]:
                                overall_result["success"] = True
                        else:
                            overall_result["dates_failed"].append(date_str)
                            print(f"[MYRA] Failed to ingest {date_str}: {result.get('error')}")
                    except Exception as e:
                        print(f"[MYRA] Error processing {date_str}: {e}")
                        overall_result["dates_failed"].append(date_str)
            else:
                print("[MYRA] Backfill skipped by request")
        else:
            print("[MYRA] No missing dates detected")

        if ist_now.hour < 18:
            print(f"[MYRA] Before market close (IST: {ist_now.hour}:00). Today's data not yet available.")
            print(f"[MYRA] Latest available is likely: {db_latest_date}")
            if not overall_result["dates_processed"]:
                overall_result["success"] = True
                overall_result["no_data_reason"] = "before_market_close"
            unregister(tid)
            return overall_result

        today_str = ist_now.date().isoformat()
        if is_trading_day(ist_now):
            if today_str not in missing_dates and today_str != db_latest_date:
                print(f"[MYRA] Fetching today's data: {today_str}")
                result = run_daily_update_for_date(ist_now)
                overall_result["dates_processed"].append(result)
                if result["success"]:
                    overall_result["total_rows_inserted"] += result.get("rows_inserted", 0)
                    if not overall_result["success"]:
                        overall_result["success"] = True
                else:
                    overall_result["dates_failed"].append(today_str)
        else:
            print(f"[MYRA] Today ({today_str}) is not a trading day")

        if overall_result["total_rows_inserted"] > 0:
            print(f"[MYRA] Total rows inserted: {overall_result['total_rows_inserted']}")

        unregister(tid)
        return overall_result

    except Exception as e:
        print(f"❌ Critical error in run_daily_update: {e}")
        overall_result["error"] = str(e)
        overall_result["success"] = False
        try:
            unregister(tid)
        except Exception:
            pass
        return overall_result


def get_db_health_status() -> dict:
    """
    Get database health status for observability/monitoring.
    Returns dict with staleness info and counts.
    """
    health = {
        "is_healthy": True,
        "db_latest_date": None,
        "current_ist_date": None,
        "days_behind": 0,
        "is_stale": False,
        "total_rows": 0,
        "warnings": [],
    }
    try:
        db_latest = get_db_latest_date()
        health["db_latest_date"] = db_latest

        ist_now = datetime.now(timezone.utc).astimezone(IST)
        health["current_ist_date"] = ist_now.date().isoformat()

        if db_latest:
            db_date = datetime.strptime(db_latest, "%Y-%m-%d").date()
            days_behind = (ist_now.date() - db_date).days
            health["days_behind"] = days_behind
            health["is_stale"] = days_behind >= 1
            if health["is_stale"]:
                health["is_healthy"] = False
                health["warnings"].append(f"Database is {days_behind} days behind current date")
        else:
            health["is_healthy"] = False
            health["warnings"].append("Database has no data")

        health["total_rows"] = get_db_row_count()

    except Exception as e:
        health["is_healthy"] = False
        health["warnings"].append(f"Error checking DB health: {e}")

    return health


if __name__ == "__main__":
    result = run_daily_update()
    print(f"\n=== INGESTION RESULT ===")
    print(f"Success: {result.get('success')}")
    print(f"Dates processed: {len(result.get('dates_processed', []))}")
    print(f"Dates failed: {result.get('dates_failed', [])}")
    print(f"Total rows: {result.get('total_rows_inserted')}")
    print(f"Backfill: {result.get('backfill_performed')}")
