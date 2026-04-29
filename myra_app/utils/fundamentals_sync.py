import json
import logging
import os
import sqlite3
import time
from datetime import datetime, timedelta, timezone

from myra_app.fetcher import DataFetcher
# --- UPDATED: Import FundamentalManager directly from myra_app ---
from myra_app.fundamental_manager import FundamentalManager

logger = logging.getLogger("myra.fundamentals_sync")

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_DIR = os.path.join(_HERE, "db")
IST = timezone(timedelta(hours=5, minutes=30))


def sync_fundamentals(force=False):
    """
    Syncs all fundamental fields (PE, ROE, MCap, sector, etc.) using the
    multi-source FundamentalManager (Screener.in → Yahoo → Google → Finology → NSE).
    Resumable: survives shutdowns.
    """
    today = datetime.now(timezone.utc).astimezone(IST).date().isoformat()

    # --- 1. Progress tracking via myra_metadata.db ---
    meta_path = os.path.join(DB_DIR, "myra_metadata.db")
    meta_conn = sqlite3.connect(meta_path, timeout=10)
    meta_conn.execute("PRAGMA journal_mode=WAL")

    # Read existing progress
    cur = meta_conn.execute(
        "SELECT value FROM metadata WHERE key = 'fundamentals_sync_status'"
    )
    row = cur.fetchone()
    progress = (
        json.loads(row[0])
        if row and row[0]
        else {
            "status": "idle",
            "last_processed_symbol": None,
            "updated_count": 0,
            "failed_count": 0,
            "start_time": None,
            "last_sync_date": None,
        }
    )

    if not force and progress.get("status") == "complete":
        last_date = progress.get("last_sync_date", "")
        if last_date:
            try:
                days = (
                    datetime.strptime(today, "%Y-%m-%d").date()
                    - datetime.strptime(last_date, "%Y-%m-%d").date()
                ).days
                if days < 30:
                    print(f"[MYRA FUNDA] Already synced {days} days ago. Skipping.")
                    meta_conn.close()
                    return
            except ValueError:
                pass

    # --- 2. Get symbols that need fundamentals ---
    val_path = os.path.join(DB_DIR, "myra_valuation.db")
    val_conn = sqlite3.connect(val_path, timeout=10)

    # Pick symbols where ANY fundamental column is NULL (not just sector)
    symbols_needed = [
        r[0]
        for r in val_conn.execute(
            "SELECT symbol FROM fundamentals WHERE "
            "sector IS NULL OR sector = '' OR pe IS NULL OR roe IS NULL OR market_cap IS NULL "
            "ORDER BY symbol"
        ).fetchall()
    ]

    if not symbols_needed:
        print("[MYRA FUNDA] All fundamentals up to date.")
        progress["status"] = "complete"
        progress["last_sync_date"] = today
        meta_conn.execute(
            "INSERT OR REPLACE INTO metadata (key, value) VALUES (?,?)",
            ("fundamentals_sync_status", json.dumps(progress)),
        )
        meta_conn.commit()
        meta_conn.close()
        val_conn.close()
        return

    # Resume if interrupted
    if progress.get("status") == "in_progress" and progress.get(
        "last_processed_symbol"
    ):
        last = progress["last_processed_symbol"]
        symbols_needed = [s for s in symbols_needed if s > last]
        print(f"[MYRA FUNDA] Resuming from {last}. {len(symbols_needed)} remaining.")
    else:
        progress["status"] = "in_progress"
        progress["updated_count"] = 0
        progress["failed_count"] = 0
        progress["start_time"] = datetime.now(timezone.utc).isoformat()
        print(f"[MYRA FUNDA] Starting sync for {len(symbols_needed)} symbols...")

    # DEBUG: skip batch API for now (unreliable), go directly to multi-source fallback
    mgr = FundamentalManager(db_dir=DB_DIR)
    mgr.set_fetcher(DataFetcher())

    meta_conn.execute(
        "INSERT OR REPLACE INTO metadata (key, value) VALUES (?,?)",
        ("fundamentals_sync_status", json.dumps(progress)),
    )
    meta_conn.commit()

    # --- 3. Skip BATCH fetch (unreliable) ---
    print("[MYRA FUNDA] Skipping batch – using multi-source fallback for all symbols.")

    remaining_symbols = list(symbols_needed)

    # --- Per-symbol fallback using multi-source FundamentalManager ---
    if remaining_symbols:
        print(
            f"[MYRA FUNDA] Using multi-source fallback for {len(remaining_symbols)} symbols..."
        )
        updated_fallback = 0
        failed_fallback = 0

        for i, symbol in enumerate(remaining_symbols):
            try:
                ok = mgr.fetch_fundamentals(symbol)
                if ok:
                    updated_fallback += 1
                    progress["updated_count"] += 1
                else:
                    print(f"  [FAIL] {symbol}: multi-source fallback exhausted")
                    failed_fallback += 1
                    progress["failed_count"] += 1
            except Exception as e:
                print(f"  [FAIL] {symbol}: {type(e).__name__} – {e}")
                failed_fallback += 1
                progress["failed_count"] += 1

            progress["last_processed_symbol"] = symbol

            if (i + 1) % 10 == 0:
                meta_conn.execute(
                    "INSERT OR REPLACE INTO metadata (key, value) VALUES (?,?)",
                    ("fundamentals_sync_status", json.dumps(progress)),
                )
                meta_conn.commit()
                print(f"[MYRA FUNDA] Fallback progress: {i+1}/{len(remaining_symbols)}")

        print(
            f"[MYRA FUNDA] Fallback done. Updated: {updated_fallback}, Failed: {failed_fallback}"
        )

    # --- 5. Finalize ---
    progress["status"] = "complete"
    progress["last_sync_date"] = today
    meta_conn.execute(
        "INSERT OR REPLACE INTO metadata (key, value) VALUES (?,?)",
        ("fundamentals_sync_status", json.dumps(progress)),
    )
    meta_conn.commit()
    print(
        f"[MYRA FUNDA] Sync complete. Updated: {progress['updated_count']}, Failed: {progress['failed_count']}"
    )
    meta_conn.close()
    val_conn.close()


# Keep backward compatibility
def get_etf_symbols():
    return set()  # not used by fundamentals sync
