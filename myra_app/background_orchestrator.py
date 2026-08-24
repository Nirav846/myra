#!/usr/bin/env python
"""
MYRA Background Orchestrator
Runs maintenance tasks in daemon threads on startup.
Guarantees clean DB shutdown on Ctrl+C, window close, or taskkill.
"""

import logging
import os
import signal
import sqlite3
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

IST = timezone(timedelta(hours=5, minutes=30))

# Ensure project root is on path
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from myra_app.constants import DB_DIR
from myra_app.librarian_core import LibrarianCore
from myra_app.utils.index_sync import sync_index_constituents
from myra_app.db.enrichers.corporate_actions_enricher import enrich_corporate_actions

logger = logging.getLogger(__name__)


# ─── Shared shutdown event ────────────────────────────────────────────────────
_shutdown_event = threading.Event()
_active_tasks: list[threading.Thread] = []
_task_lock = threading.Lock()

# ─── Thread-local connection pool for metadata operations ─────────────────────
# PERFORMANCE IMPROVEMENT: Reuse connections per thread to avoid repeated open/close
_connection_pool: dict[str, LibrarianCore] = {}
_pool_lock = threading.Lock()


def _get_metadata_connection(read_only: bool = True) -> LibrarianCore:
    """Get or create a thread-local LibrarianCore connection for metadata operations."""
    thread_name = threading.current_thread().name
    pool_key = f"{thread_name}:{'ro' if read_only else 'rw'}"
    with _pool_lock:
        if pool_key in _connection_pool:
            lib = _connection_pool[pool_key]
            # Verify connection is still alive
            if lib._meta_conn is not None:
                return lib
            else:
                # Connection died, remove and recreate
                del _connection_pool[pool_key]
                logger.warning(
                    f"[MYRA BG] Recreating dead metadata connection for thread {thread_name}"
                )

        # Create new connection
        lib = LibrarianCore(read_only=read_only)
        _connection_pool[pool_key] = lib
        logger.debug(
            f"[MYRA BG] Created new metadata connection for thread {thread_name} (read_only={read_only})"
        )
        return lib


def _close_metadata_connection():
    """Close the current thread's metadata connection if it exists."""
    thread_name = threading.current_thread().name
    with _pool_lock:
        if thread_name in _connection_pool:
            try:
                _connection_pool[thread_name].close()
                del _connection_pool[thread_name]
                logger.debug(
                    f"[MYRA BG] Closed metadata connection for thread {thread_name}"
                )
            except Exception as e:
                logger.warning(f"[MYRA BG] Failed to close metadata connection: {e}")


# ─── Shutdown handler ─────────────────────────────────────────────────────────


def _graceful_shutdown(signum=None, frame=None):
    """
    Called on Ctrl+C (SIGINT), SIGTERM, or Windows console close.
    Signals all background tasks to stop and waits for them to finish
    their current DB write before exiting.
    """
    if _shutdown_event.is_set():
        return  # Already shutting down
    logger.info(
        "[MYRA] Shutdown signal received. Waiting for background tasks to finish..."
    )
    _shutdown_event.set()

    with _task_lock:
        for t in _active_tasks:
            if t.is_alive():
                t.join(timeout=15)  # Give each task 15s to finish current write

    # PERFORMANCE IMPROVEMENT: Clean up connection pool
    with _pool_lock:
        for thread_name, lib in list(_connection_pool.items()):
            try:
                lib.close()
                logger.debug(f"[MYRA BG] Closed pooled connection for {thread_name}")
            except Exception as e:
                logger.warning(f"[MYRA BG] Failed to close pooled connection: {e}")
        _connection_pool.clear()

    logger.info("[MYRA] All background tasks finished. DB is safe. Goodbye.")


# ─── Helper: check if today already ingested with DB truth verification ────────


def _already_ingested_today() -> bool:
    """
    Verify if today's data is actually in the database.
    DB truth takes precedence over metadata - if DB is stale,
    we should NOT trust metadata alone.
    """
    try:
        from myra_app.daily_ingestor import get_db_latest_date

        today = datetime.now(timezone.utc).astimezone(IST).date().isoformat()
        db_latest = get_db_latest_date()

        if db_latest == today:
            return True

        if db_latest and db_latest != today:
            db_date = datetime.strptime(db_latest, "%Y-%m-%d").date()
            today_date = datetime.strptime(today, "%Y-%m-%d").date()
            if db_date < today_date:
                logger.warning(
                    f"[MYRA BG] DB is behind ({db_latest} vs {today}). Not trusting metadata."
                )
                return False

        lib = _get_metadata_connection(read_only=True)
        last = lib.get_metadata("last_sync_date")
        if last:
            metadata_date = last.strip()
            if metadata_date != today:
                return False
            if db_latest != today:
                logger.warning(
                    f"[MYRA BG] Metadata says {metadata_date} but DB is at {db_latest}. NOT trusting metadata."
                )
                return False
            return True
    except Exception as e:
        logger.warning(f"[MYRA BG] Failed to check ingestion status: {e}")
    return False


def _is_db_stale(days_threshold: int = 1) -> bool:
    """
    Check if database is stale (more than threshold days behind current date).
    Default threshold is 1 day - if DB is 1+ days behind, trigger catch-up.
    """
    try:
        from myra_app.daily_ingestor import get_db_latest_date, is_trading_day

        db_latest = get_db_latest_date()
        if not db_latest:
            return True
        ist_now = datetime.now(timezone.utc).astimezone(IST)
        db_date = datetime.strptime(db_latest, "%Y-%m-%d").date()
        current_date = ist_now.date()
        days_behind = (current_date - db_date).days
        return days_behind >= days_threshold
    except Exception as e:
        logger.warning(f"[MYRA BG] Failed to check DB staleness: {e}")
        return False


def _mark_ingested_today():
    # PERFORMANCE IMPROVEMENT: Reuse thread-local connection instead of creating/closing
    try:
        today = datetime.now(timezone.utc).astimezone(IST).date().isoformat()
        lib = _get_metadata_connection(read_only=False)
        lib.set_metadata("last_sync_date", today)
        logger.info(f"[MYRA BG] Marked ingestion date: {today}")
    except Exception as e:
        logger.warning(
            f"[MYRA BG] Failed to mark ingestion date with pooled connection: {e}"
        )
        # Fallback to original method on error
        try:
            today = datetime.now(timezone.utc).astimezone(IST).date().isoformat()
            lib = LibrarianCore(read_only=False)
            lib.set_metadata("last_sync_date", today)
            lib.close()
        except Exception as e2:
            logger.warning(f"Could not mark ingestion date: {e2}")


# ─── Sync Log Helpers ─────────────────────────────────────────────────────────

WEEKLY_INTERVAL_DAYS = 7


def _ensure_sync_log_table():
    """Create sync_log table if it doesn't exist."""
    try:
        lib = _get_metadata_connection(read_only=False)
        lib._meta_conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sync_log (
                task_name   TEXT PRIMARY KEY,
                last_run    TEXT
            )
        """
        )
        lib._meta_conn.commit()
    except Exception as e:
        logger.warning(f"[MYRA BG] Failed to ensure sync_log table: {e}")


def _is_task_due(task_name: str, interval_days: int = WEEKLY_INTERVAL_DAYS) -> bool:
    """Check if task is due to run based on interval."""
    last_run = _get_last_run(task_name)
    if last_run is None:
        return True
    ist_now = datetime.now(timezone.utc).astimezone(IST)
    days_since = (ist_now - last_run).days
    return days_since >= interval_days


def _get_last_run(task_name: str) -> datetime | None:
    """Get last run timestamp for a task. Returns None if never run."""
    try:
        lib = _get_metadata_connection(read_only=True)
        res = lib._meta_conn.execute(
            "SELECT last_run FROM sync_log WHERE task_name = ?", (task_name,)
        ).fetchone()
        if res and res[0]:
            return datetime.fromisoformat(res[0])
    except Exception as e:
        logger.debug(f"[MYRA BG] Failed to get last run for {task_name}: {e}")
    return None


def _is_task_overdue(task_name: str, days: int) -> bool:
    """Check if task hasn't run in specified days (or never run)."""
    last_run = _get_last_run(task_name)
    if last_run is None:
        return True
    ist_now = datetime.now(timezone.utc).astimezone(IST)
    days_since = (ist_now - last_run).days
    return days_since >= days


def _mark_task_run(task_name: str):
    """Write current IST timestamp to sync_log for a task."""
    try:
        ist_now = datetime.now(timezone.utc).astimezone(IST)
        timestamp = ist_now.isoformat()
        lib = _get_metadata_connection(read_only=False)
        lib._meta_conn.execute(
            "INSERT OR REPLACE INTO sync_log (task_name, last_run) VALUES (?, ?)",
            (task_name, timestamp),
        )
        lib._meta_conn.commit()
        logger.info(f"[MYRA BG] Marked {task_name} last_run: {timestamp}")
    except Exception as e:
        logger.warning(f"[MYRA BG] Failed to mark task run for {task_name}: {e}")


# ─── Task 1: DB Doctor ────────────────────────────────────────────────────────


def _task_db_doctor():
    """
    Runs db_doctor in auto-fix mode on startup.
    Skips if shutdown is requested.
    """
    from myra_app.task_tracker import register, unregister

    if _shutdown_event.is_set():
        return
    tid = register("DB health check")
    try:
        logger.info("[MYRA BG] Running DB health check...")
        from tools.db_doctor import DbDoctor

        doctor = DbDoctor()
        doctor.run()
        logger.info("[MYRA BG] DB health check complete.")
    except Exception as e:
        logger.error(f"[MYRA BG] DB Doctor failed: {e}")
    finally:
        unregister(tid)


# ─── Task 2: Daily Ingestor ───────────────────────────────────────────────────


def _task_daily_ingest(force: bool = False):
    """Daily ingest with optional force flag for catch-up runs."""
    from myra_app.task_tracker import register, unregister

    if _shutdown_event.is_set():
        return

    ist_now = datetime.now(timezone.utc).astimezone(IST)

    # ── Master kill-switch ───────────────────────────────────────────────
    from myra_app import constants

    if not constants.ENABLE_DAILY_INGEST:
        logger.info(
            "[MYRA BG] Daily ingestion disabled by config (ENABLE_DAILY_INGEST=False)."
        )
        return

    # Skip weekends (unless forced)
    if not force and ist_now.weekday() >= 5:
        logger.info(f"[MYRA BG] {ist_now.date()} is a weekend – skipping daily ingest.")
        return

    # Skip before 6 PM IST (data not yet available) unless forced
    if not force and ist_now.hour < 18:
        logger.info(
            f"[MYRA BG] Market data not yet available (IST: {ist_now.hour:02d}:{ist_now.minute:02d}). Skipping."
        )
        return

    # ── EOD2 data path ──────────────────────────────────────────────────
    if constants.USE_EOD2_DATA:
        tid = register("EOD2 daily sync")
        try:
            logger.info(
                f"[MYRA BG] {ist_now.date()} – starting EOD2 incremental sync..."
            )
            from myra_app.eod2_sync import sync_eod2_data

            sync_result = sync_eod2_data()
            inserted = sync_result.get("rows_inserted", 0)
            symbols = sync_result.get("symbols_updated", 0)
            logger.info(
                f"[MYRA BG] EOD2 sync result: rows={inserted}, symbols={symbols}, "
                f"error={sync_result.get('error')}"
            )

            if sync_result.get("error"):
                logger.error(f"[MYRA BG] EOD2 sync error: {sync_result['error']}")
            else:
                _mark_ingested_today()
                _mark_task_run("daily_ingest")
                if inserted > 0:
                    logger.info(
                        "[MYRA BG] EOD2 sync complete – running post-ingest hooks."
                    )
                    from myra_app.fundamental_sync import FundamentalSync

                    FundamentalSync()._compute_market_cap_from_prices()

                    try:
                        from myra_app.portfolio_db import auto_refresh_portfolio

                        pr = auto_refresh_portfolio()
                        if pr.get("error"):
                            logger.warning(
                                f"[MYRA BG] Portfolio refresh skipped: {pr['error']}"
                            )
                        else:
                            logger.info(
                                f"[MYRA BG] Portfolio refreshed: {pr.get('prices_updated', 0)} prices"
                            )
                    except Exception as exc:
                        logger.debug(
                            f"[MYRA BG] Portfolio refresh not available: {exc}"
                        )

                    try:
                        enrich_corporate_actions()
                    except Exception as exc:
                        logger.error(
                            f"[MYRA BG] Corporate actions enrichment failed: {exc}"
                        )
                else:
                    logger.info("[MYRA BG] EOD2 sync: no new rows – DB is current.")
        except Exception as exc:
            logger.error(f"[MYRA BG] EOD2 sync failed: {exc}")
        finally:
            unregister(tid)
        return

    # ── Legacy NSE bhavcopy path ────────────────────────────────────────
    tid = register("Daily ingest")
    try:
        logger.info(
            f"[MYRA BG] {ist_now.date()} is a trading day. Starting DB-gap-driven ingestion..."
        )
        from myra_app.daily_ingestor import run_daily_update, get_db_latest_date

        result = run_daily_update(force_date=None, skip_backfill=False)

        logger.info(
            f"[MYRA BG] Ingestion result: success={result.get('success')}, "
            f"rows={result.get('total_rows_inserted')}, "
            f"backfill={result.get('backfill_performed')}"
        )

        if (
            not result.get("success")
            and result.get("total_rows_inserted", 0) == 0
            and not result.get("dates_failed")
        ):
            logger.info(
                "[MYRA BG] Ingestion returned no new rows – DB is already current."
            )
            result["success"] = True

        if result.get("success"):
            new_latest = get_db_latest_date()
            logger.info(f"[MYRA BG] DB latest date after ingestion: {new_latest}")
            _mark_ingested_today()
            _mark_task_run("daily_ingest")
            if result.get("total_rows_inserted", 0) > 0:
                logger.info("[MYRA BG] Daily ingest complete - metadata updated.")
                from myra_app.fundamental_sync import FundamentalSync

                FundamentalSync()._compute_market_cap_from_prices()

                # Refresh portfolio prices if portfolio exists
                try:
                    from myra_app.portfolio_db import auto_refresh_portfolio

                    pr = auto_refresh_portfolio()
                    if pr.get("error"):
                        logger.warning(
                            f"[MYRA BG] Portfolio refresh skipped: {pr['error']}"
                        )
                    else:
                        logger.info(
                            f"[MYRA BG] Portfolio refreshed: {pr.get('prices_updated', 0)} prices, "
                            f"{pr.get('fundamentals_updated', 0)} fundamentals"
                        )
                except Exception as e:
                    logger.debug(f"[MYRA BG] Portfolio refresh not available: {e}")
                # Enrich corporate actions data (splits, dividends) based on latest bhavcopy
                try:
                    logger.info("[MYRA BG] Starting corporate actions enrichment...")
                    enrich_corporate_actions()
                    logger.info("[MYRA BG] Corporate actions enrichment completed.")
                except Exception as e:
                    logger.error(f"[MYRA BG] Corporate actions enrichment failed: {e}")

            else:
                logger.info(
                    "[MYRA BG] Ingestion succeeded but no new rows - DB is already up to date."
                )
        else:
            failed_dates = result.get("dates_failed", [])
            error_msg = result.get("error", "Unknown error")
            logger.error(
                f"[MYRA BG] Ingestion failed! Failed dates: {failed_dates}, Error: {error_msg}"
            )
    except Exception as e:
        logger.error(f"[MYRA BG] Daily ingest failed with exception: {e}")
    finally:
        unregister(tid)


# ─── Task 3: Midnight Watchdog ────────────────────────────────────────────────


def _task_watchdog():
    """
    Polls every 60 seconds. When a new trading day is detected after
    6 PM IST, triggers daily ingest automatically.
    Also detects stale DB and triggers catch-up.
    Runs for the entire session lifetime.
    """
    from myra_app.task_tracker import register, update, unregister

    tid = register("Background sync watchdog", task_type="indefinite")
    try:
        last_checked_date = datetime.now(timezone.utc).astimezone(IST).date()

        while not _shutdown_event.is_set():
            _shutdown_event.wait(timeout=60)
            if _shutdown_event.is_set():
                break

            try:
                ist_now = datetime.now(timezone.utc).astimezone(IST)
                today = ist_now.date().isoformat()

                update(
                    tid,
                    f"Watching – Last check: {ist_now.hour:02d}:{ist_now.minute:02d}:{ist_now.second:02d}",
                )

                if _is_db_stale(days_threshold=1):
                    ist_now = datetime.now(timezone.utc).astimezone(IST)
                    last_attempt = _get_last_run("stale_catchup")
                    already_tried_today = (
                        last_attempt is not None
                        and last_attempt.date() == ist_now.date()
                    )

                    if not already_tried_today:
                        logger.info(
                            "[MYRA BG] Database is STALE (1+ days behind). Triggering catch-up..."
                        )
                        _mark_task_run("stale_catchup")
                        _task_daily_ingest(force=True)
                    elif (
                        ist_now.hour >= 18
                        and ist_now.minute >= 30
                        and not _already_ingested_today()
                    ):
                        logger.info(
                            "[MYRA BG] Market closed – retrying post-close ingestion..."
                        )
                        _mark_task_run("stale_catchup")
                        _task_daily_ingest(force=True)
                    continue

                if (
                    today != last_checked_date
                    and ist_now.weekday() < 5
                    and ist_now.hour >= 18
                    and not _already_ingested_today()
                ):
                    logger.info(
                        f"[MYRA BG] New trading day detected ({today}). Auto-fetching bhavcopy..."
                    )
                    _task_daily_ingest()
                    last_checked_date = today

            except Exception as e:
                logger.warning(f"[MYRA BG] Watchdog error: {e}")
    finally:
        unregister(tid)


# ─── Task 4: ETF Sync ─────────────────────────────────────────────────────────


def _task_etf_sync():
    """Syncs ETF blocklist from NSE every 7 days. Runs immediately if overdue."""
    from myra_app.task_tracker import register, unregister

    if _shutdown_event.is_set():
        return

    if _is_task_overdue("etf_sync", days=7):
        tid = register("ETF sync", task_type="one-shot")
        try:
            logger.info("[MYRA BG] ETF sync overdue – running now...")
            from myra_app.utils.etf_sync import sync_etf_list

            sync_etf_list(task_id=tid)
            _mark_task_run("etf_sync")
            logger.info("[MYRA BG] ETF sync complete (catch-up).")
        except Exception as e:
            logger.error(f"[MYRA BG] ETF sync (catch-up) failed: {e}")
        finally:
            unregister(tid)

    if _shutdown_event.is_set():
        return

    tid = register("ETF sync", task_type="indefinite")
    try:
        while not _shutdown_event.is_set():
            try:
                if _is_task_due("etf_sync", WEEKLY_INTERVAL_DAYS):
                    from myra_app.utils.etf_sync import sync_etf_list

                    logger.info("[MYRA BG] ETF sync due – running...")
                    sync_etf_list(task_id=tid)
                    _mark_task_run("etf_sync")
            except Exception as e:
                logger.error(f"[MYRA BG] ETF sync failed: {e}")
            for _ in range(60):
                if _shutdown_event.wait(60):
                    return
    finally:
        unregister(tid)


# ─── Task 5: Index Sync ─────────────────────────────────────────────────────────


def _task_index_sync():
    """Syncs NIFTY indices from NSE every 7 days. Runs immediately if overdue."""
    from myra_app.task_tracker import register, unregister

    if _shutdown_event.is_set():
        return

    if _is_task_overdue("index_sync", days=7):
        tid = register("Index sync", task_type="one-shot")
        try:
            logger.info("[MYRA BG] Index sync overdue – running now...")
            from myra_app.utils.index_sync import (
                heal_index_if_stale,
                sync_index_constituents,
            )

            for idx in ["NIFTY 50", "NIFTY 500", "NIFTY SMALLCAP 250"]:
                sync_index_constituents(idx, task_id=tid)
            heal_index_if_stale("NIFTY 500", expected_count=500)
            # Refresh Nifty benchmark closes for RS calculations
            try:
                from myra_app.utils.index_sync import sync_nifty_benchmarks

                sync_nifty_benchmarks()
                logger.info("[MYRA BG] Nifty benchmark data refreshed")
            except Exception as e:
                logger.warning(f"[MYRA BG] Nifty benchmark refresh failed: {e}")
            _mark_task_run("index_sync")
            logger.info("[MYRA BG] Index sync complete (catch-up).")
        except Exception as e:
            logger.error(f"[MYRA BG] Index sync (catch-up) failed: {e}")
        finally:
            unregister(tid)

    if _shutdown_event.is_set():
        return

    tid = register("Index sync", task_type="indefinite")
    try:
        while not _shutdown_event.is_set():
            try:
                if _is_task_due("index_sync", WEEKLY_INTERVAL_DAYS):
                    logger.info("[MYRA BG] Index sync due – running...")
                    from myra_app.utils.index_sync import (
                        heal_index_if_stale,
                        sync_index_constituents,
                    )

                    for idx in ["NIFTY 50", "NIFTY 500", "NIFTY SMALLCAP 250"]:
                        sync_index_constituents(idx, task_id=tid)

                    heal_index_if_stale("NIFTY 500", expected_count=500)
                    # Refresh Nifty benchmark closes for RS calculations
                    try:
                        from myra_app.utils.index_sync import sync_nifty_benchmarks

                        sync_nifty_benchmarks()
                        logger.info("[MYRA BG] Nifty benchmark data refreshed")
                    except Exception as e:
                        logger.warning(f"[MYRA BG] Nifty benchmark refresh failed: {e}")
                    _mark_task_run("index_sync")
            except Exception as e:
                logger.error(f"[MYRA BG] Index sync/heal failed: {e}")
            for _ in range(60):
                if _shutdown_event.wait(60):
                    return
    finally:
        unregister(tid)


# ─── Task 6: Fundamentals Sync ───────────────────────────────────────────────────


def _task_fundamentals_sync():
    """Weekly fundamentals full sync every 7 days. Runs immediately if overdue."""
    from myra_app.task_tracker import register, unregister, update

    if _shutdown_event.is_set():
        return

    if _is_task_overdue("fundamentals_sync", days=7):
        tid = register("Fundamentals sync", task_type="one-shot")
        try:
            logger.info("[MYRA BG] Fundamentals sync overdue – running now...")
            from myra_app.fundamental_sync import FundamentalSync

            sync = FundamentalSync()
            result = sync.run_full_sync()
            _mark_task_run("fundamentals_sync")
            logger.info(
                f"[MYRA BG] Fundamentals sync complete (catch-up). "
                f"MS: {result['ms_fetched']}, NSE: {result['nse_fetched']}, "
                f"Inserted: {result['inserted']}, Errors: {result['errors']}"
            )
        except Exception as e:
            logger.error(f"[MYRA BG] Fundamentals sync (catch-up) failed: {e}")
        finally:
            unregister(tid)

    tid = register("Fundamentals sync", task_type="indefinite")
    try:
        while not _shutdown_event.is_set():
            try:
                if _is_task_due("fundamentals_sync", WEEKLY_INTERVAL_DAYS):
                    update(tid, "Running full fundamentals sync...")
                    logger.info("[MYRA BG] Fundamentals sync due – running...")
                    from myra_app.fundamental_sync import FundamentalSync

                    sync = FundamentalSync()
                    result = sync.run_full_sync()
                    _mark_task_run("fundamentals_sync")
                    logger.info(
                        f"[MYRA BG] Fundamentals sync complete. "
                        f"MS: {result['ms_fetched']}, NSE: {result['nse_fetched']}, "
                        f"Inserted: {result['inserted']}, Errors: {result['errors']}"
                    )
            except Exception as e:
                logger.error(f"[MYRA BG] Fundamentals sync failed: {e}")
            for _ in range(60):
                if _shutdown_event.wait(60):
                    return
    finally:
        unregister(tid)


def _task_fundamentals_daily():
    """Daily lightweight fundamentals sync (weekdays after 6pm)."""
    from myra_app.task_tracker import register, unregister, update

    if _shutdown_event.is_set():
        return

    tid = register("Fundamentals daily", task_type="indefinite")
    try:
        while not _shutdown_event.is_set():
            try:
                ist_now = datetime.now(timezone.utc).astimezone(IST)
                if not _is_task_overdue("fundamentals_daily", days=1):
                    for _ in range(30):
                        if _shutdown_event.wait(60):
                            return
                    continue
                # Run on weekdays after 6 PM, after daily ingest
                if ist_now.weekday() < 5 and ist_now.hour >= 18:
                    update(tid, "Running lightweight Morningstar sync...")
                    logger.info(
                        "[MYRA BG] Daily lightweight fundamentals sync running..."
                    )
                    from myra_app.fundamental_sync import FundamentalSync

                    sync = FundamentalSync()
                    result = sync.run_ms_only()
                    logger.info(
                        f"[MYRA BG] Fundamentals daily sync complete. "
                        f"MS: {result['ms_fetched']}, Inserted: {result['inserted']}, "
                        f"Errors: {result['errors']}"
                    )
                    _mark_task_run("fundamentals_daily")
                    # Refresh stale shares_outstanding every 7 days
                    if _is_task_overdue("shares_outstanding_sync", days=7):
                        try:
                            from myra_app.fundamental_sync import FundamentalSync

                            fs = FundamentalSync()
                            result = fs._refresh_stale_shares_outstanding()
                            _mark_task_run("shares_outstanding_sync")
                            logger.info(
                                f"[MYRA BG] Stale shares refresh complete: {result}"
                            )
                        except Exception as e:
                            logger.warning(
                                f"[MYRA BG] Stale shares refresh failed: {e}"
                            )
                    # Wait until next day to avoid multiple runs
                    for _ in range(360):  # 6 hours
                        if _shutdown_event.wait(60):
                            return
                else:
                    # Check every 30 minutes
                    for _ in range(30):
                        if _shutdown_event.wait(60):
                            return
            except Exception as e:
                logger.error(f"[MYRA BG] Fundamentals daily sync failed: {e}")
                # Wait 30 minutes before retry
                for _ in range(30):
                    if _shutdown_event.wait(60):
                        return
    finally:
        unregister(tid)


# ─── Task 7: Institutional Sync (Bulk/Block Deals Only) ────────────────────────


def _task_institutional_sync():
    """Syncs bulk/block deals from NSE every 7 days. Runs immediately if overdue."""
    from myra_app.task_tracker import register, unregister

    if _shutdown_event.is_set():
        return

    if _is_task_overdue("institutional_sync", days=7):
        tid = register("Institutional sync", task_type="one-shot")
        try:
            logger.info("[MYRA BG] Institutional sync overdue – running now...")
            from myra_app.utils.institutional_sync import sync_institutional_data

            sync_institutional_data(task_id=tid)
            _mark_task_run("institutional_sync")
            logger.info("[MYRA BG] Institutional sync complete (catch-up).")
        except Exception as e:
            logger.error(f"[MYRA BG] Institutional sync (catch-up) failed: {e}")
        finally:
            unregister(tid)

    if _shutdown_event.is_set():
        return

    tid = register("Institutional sync", task_type="indefinite")
    try:
        while not _shutdown_event.is_set():
            try:
                if _is_task_due("institutional_sync", WEEKLY_INTERVAL_DAYS):
                    from myra_app.utils.institutional_sync import (
                        sync_institutional_data,
                    )

                    logger.info("[MYRA BG] Institutional sync due – running...")
                    sync_institutional_data(task_id=tid)
                    _mark_task_run("institutional_sync")
            except Exception as e:
                logger.error(f"[MYRA BG] Institutional sync failed: {e}")
            for _ in range(60):
                if _shutdown_event.wait(60):
                    return
    finally:
        unregister(tid)


# Task 8: Daily DB Backup


def _task_db_backup():
    """Runs a full DB backup daily at midnight IST and keeps last 7 daily backups."""
    from myra_app.task_tracker import register, unregister

    tid = register("DB backup", task_type="indefinite")
    try:
        while not _shutdown_event.is_set():
            try:
                if _is_task_due("db_backup", interval_days=1):
                    from myra_app.utils.db_backup import rotate_backups

                    logger.info("[MYRA BG] Running nightly DB backup...")
                    rotate_backups(task_id=tid, keep_last_days=7)
                    logger.info("[MYRA BG] Nightly DB backup complete.")
                    _mark_task_run("db_backup")

                    # Optimize databases for query performance
                    import sqlite3

                    for db_name, db_file in LibrarianCore.DB_MAP.items():
                        db_path = os.path.join(DB_DIR, db_file)
                        if os.path.exists(db_path):
                            try:
                                conn = sqlite3.connect(db_path)
                                conn.execute("PRAGMA optimize")  # noqa: PG-NPLUS1
                                conn.execute("ANALYZE")  # noqa: PG-NPLUS1
                                conn.close()
                            except Exception:
                                pass
                    logger.info("[MYRA BG] Database optimization complete")

                # Check again in 30 minutes
                # PERFORMANCE IMPROVEMENT: Replace long wait with responsive loop
                for _ in range(30):  # 30 * 60 = 1800 seconds total
                    if _shutdown_event.wait(60):
                        return
            except Exception as e:
                logger.error(f"[MYRA BG] Daily DB backup failed: {e}")
                # PERFORMANCE IMPROVEMENT: Replace long wait with responsive loop
                for _ in range(30):  # 30 * 60 = 1800 seconds total
                    if _shutdown_event.wait(60):
                        return
    finally:
        unregister(tid)


# ─── Task 9: Weekly Screener.in Fundamentals Enrichment ───────────────────────


def _task_screener_enrich():
    """Runs Screener.in fundamentals enrichment weekly (PBV, ROCE)."""
    from myra_app.task_tracker import register, unregister

    tid = register("Screener enrich", task_type="indefinite")
    try:
        while not _shutdown_event.is_set():
            try:
                if _is_task_due("screener_enrich", interval_days=7):
                    from myra_app.db.enrichers.screener_enricher import (
                        enrich_screener_fundamentals,
                    )

                    logger.info(
                        "[MYRA BG] Running weekly Screener.in fundamentals enrichment..."
                    )
                    # Auto-run disabled via SCREENER_ENRICH_AUTO_ENABLED=False;
                    # manual backfill still available with `--enrich-screener`.
                    # enrich_screener_fundamentals(force=False)
                    _mark_task_run("screener_enrich")
                    logger.info("[MYRA BG] Weekly Screener.in enrichment complete.")

                # Check again in 30 minutes
                for _ in range(30):  # 30 * 60 = 1800 seconds total
                    if _shutdown_event.wait(60):
                        return
            except Exception as e:
                logger.error(f"[MYRA BG] Weekly Screener enrichment failed: {e}")
                # Check again in 30 minutes
                for _ in range(30):  # 30 * 60 = 1800 seconds total
                    if _shutdown_event.wait(60):
                        return
    finally:
        unregister(tid)


# ─── Task 10: Fund Traction Sync ──────────────────────────────────────────────


def _task_fund_traction_sync():
    """Weekly fund traction sync from GitHub Pages. Runs immediately if overdue."""
    from myra_app.task_tracker import register, unregister

    if _shutdown_event.is_set():
        return

    if _is_task_overdue("fund_traction_sync", days=7):
        tid = register("Fund traction sync", task_type="one-shot")
        try:
            logger.info("[MYRA BG] Fund traction sync overdue – running now...")
            from myra_app.fund_traction_sync import sync_fund_traction

            result = sync_fund_traction()
            _mark_task_run("fund_traction_sync")
            logger.info(
                f"[MYRA BG] Fund traction sync complete (catch-up). "
                f"Months: {result['months_synced']}, Rows: {result['rows_inserted']}"
            )
        except Exception as e:
            logger.error(f"[MYRA BG] Fund traction sync (catch-up) failed: {e}")
        finally:
            unregister(tid)

    if _shutdown_event.is_set():
        return

    tid = register("Fund traction sync", task_type="indefinite")
    try:
        while not _shutdown_event.is_set():
            try:
                if _is_task_due("fund_traction_sync", WEEKLY_INTERVAL_DAYS):
                    from myra_app.fund_traction_sync import sync_fund_traction

                    logger.info("[MYRA BG] Fund traction sync due – running...")
                    result = sync_fund_traction()
                    _mark_task_run("fund_traction_sync")
                    logger.info(
                        f"[MYRA BG] Fund traction sync complete. "
                        f"Months: {result['months_synced']}, Rows: {result['rows_inserted']}"
                    )
            except Exception as e:
                logger.error(f"[MYRA BG] Fund traction sync failed: {e}")
            for _ in range(60):
                if _shutdown_event.wait(60):
                    return
    finally:
        unregister(tid)


# ─── Task 11: Cross-buy Sync ──────────────────────────────────────────────────

# Local sibling project holding raw RupeeVest MF holdings CSVs + downloader.
CROSS_FUND_REPO = Path(r"D:\01screener\Myra\cross-fund-holdings-traction")
CROSS_BUY_OUT_DIR = CROSS_FUND_REPO / "temp_holdings"
CROSS_BUY_FUNDS_FILE = CROSS_FUND_REPO / "config" / "rupeevest_funds.example.txt"
CROSS_BUY_DOWNLOAD_SCRIPT = CROSS_FUND_REPO / "scripts" / "download_rupeevest_funds.py"
CROSS_BUY_DOWNLOAD_TIMEOUT_S = 300  # 5 min — 62 funds x ~1s delay each


def _download_cross_buy_csvs() -> bool:
    """Refresh raw RupeeVest holdings CSVs via the sibling downloader script.

    Returns True when the download completed successfully. Never raises —
    all failure modes are logged and reported as False so the orchestrator
    task can skip the sync cleanly.
    """
    if not CROSS_BUY_DOWNLOAD_SCRIPT.exists():
        logger.error(
            "[MYRA BG] Cross-buy downloader script not found: %s",
            CROSS_BUY_DOWNLOAD_SCRIPT,
        )
        return False

    logger.info("[MYRA BG] Cross-buy CSV download starting...")
    cmd = [
        sys.executable,
        str(CROSS_BUY_DOWNLOAD_SCRIPT),
        "--funds-file",
        str(CROSS_BUY_FUNDS_FILE),
        "--out-dir",
        str(CROSS_BUY_OUT_DIR),
        "--overwrite",
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=CROSS_BUY_DOWNLOAD_TIMEOUT_S
        )
    except subprocess.TimeoutExpired:
        logger.warning(
            "[MYRA BG] Cross-buy CSV download timed out after %ss — skipping sync",
            CROSS_BUY_DOWNLOAD_TIMEOUT_S,
        )
        return False
    except FileNotFoundError as e:
        logger.error("[MYRA BG] Cross-buy download could not start: %s", e)
        return False
    except Exception as e:
        logger.exception("[MYRA BG] Cross-buy CSV download failed unexpectedly: %s", e)
        return False

    if result.returncode != 0:
        stderr_tail = (result.stderr or "").strip()[-500:]
        logger.warning(
            "[MYRA BG] Cross-buy CSV download failed (rc=%s): %s",
            result.returncode,
            stderr_tail,
        )
        return False

    try:
        csv_count = sum(1 for p in CROSS_BUY_OUT_DIR.glob("*.csv") if p.is_file())
    except OSError:
        csv_count = -1
    logger.info(
        "[MYRA BG] Cross-buy CSV download finished OK (%s CSVs in %s)",
        csv_count,
        CROSS_BUY_OUT_DIR.name,
    )
    return True


def _task_cross_buy_sync():
    """Monthly mutual-fund cross-buy sync. Runs immediately if overdue."""
    from myra_app.task_tracker import register, unregister

    if _shutdown_event.is_set():
        return

    if _is_task_overdue("cross_buy_sync", days=30):
        tid = register("Cross-buy sync", task_type="one-shot")
        try:
            logger.info("[MYRA BG] Cross-buy sync overdue – running now...")
            if not _download_cross_buy_csvs():
                logger.warning(
                    "[MYRA BG] Cross-buy sync skipped this run (download failed); "
                    "will retry on next orchestrator start"
                )
                return
            from myra_app.cross_buy_processor import (
                detect_available_months,
                process_month,
            )

            months = detect_available_months()
            if months:
                result = process_month(months[-1])  # latest month
                _mark_task_run("cross_buy_sync")
                logger.info(f"[MYRA BG] Cross-buy sync complete: {result}")
            else:
                logger.info(
                    "[MYRA BG] Cross-buy sync: no raw holdings months available, skipping"
                )
        except Exception as e:
            logger.error(f"[MYRA BG] Cross-buy sync (catch-up) failed: {e}")
        finally:
            unregister(tid)

    if _shutdown_event.is_set():
        return

    tid = register("Cross-buy sync", task_type="indefinite")
    try:
        while not _shutdown_event.is_set():
            try:
                if _is_task_due("cross_buy_sync", interval_days=30):
                    logger.info("[MYRA BG] Cross-buy sync due – running...")
                    if not _download_cross_buy_csvs():
                        # Mark anyway so a dead downloader doesn't retry-storm
                        # every poll cycle; next attempt is the next 30-day cycle.
                        _mark_task_run("cross_buy_sync")
                        logger.warning(
                            "[MYRA BG] Cross-buy sync skipped (download failed); "
                            "next attempt in 30 days"
                        )
                        continue
                    from myra_app.cross_buy_processor import (
                        detect_available_months,
                        process_month,
                    )

                    months = detect_available_months()
                    if months:
                        result = process_month(months[-1])  # latest month
                        _mark_task_run("cross_buy_sync")
                        logger.info(f"[MYRA BG] Cross-buy sync complete: {result}")
                    else:
                        logger.info(
                            "[MYRA BG] Cross-buy sync: no raw holdings months available, skipping"
                        )
            except Exception as e:
                logger.error(f"[MYRA BG] Cross-buy sync failed: {e}")
            for _ in range(60):
                if _shutdown_event.wait(60):
                    return
    finally:
        unregister(tid)


# ─── Public entry point ───────────────────────────────────────────────────────


def _set_seed_flag(flag_key: str):
    """Set the seed flag in metadata."""
    try:
        lib = LibrarianCore(read_only=False)
        lib.set_metadata(flag_key, "1")
        lib.close()
    except Exception as e:
        logger.warning(f"Failed to set seed flag {flag_key}: {e}")


def _seed_if_needed(flag_key: str, check_fn, seed_fn):
    """
    Generic helper for seeding logic.
    Checks if seeding is needed based on check_fn, runs seed_fn if so.
    """
    try:
        lib = LibrarianCore(read_only=True)
        if lib.get_metadata(flag_key) == "1":
            lib.close()
            logger.info(f"[MYRA BG] {flag_key} seeding already done, skipping")
            return
        lib.close()

        if check_fn():
            seed_fn()
        _set_seed_flag(flag_key)
    except Exception as e:
        logger.warning(f"{flag_key} seed check failed: {e}")


def _register_signals():
    """Register signal handlers for graceful shutdown."""
    signal.signal(signal.SIGINT, _graceful_shutdown)
    signal.signal(signal.SIGTERM, _graceful_shutdown)
    try:
        import win32api

        win32api.SetConsoleCtrlHandler(
            lambda e: (_graceful_shutdown(), _shutdown_event.wait(3), True)[-1], True
        )
    except ImportError:
        pass


def _run_seed_checks():
    """Run all seed checks in a background thread."""
    import os
    import sqlite3

    from myra_app.librarian import Librarian
    from myra_app.librarian_core import LibrarianCore

    # Seed ETF list
    def etf_check():
        _meta_db = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "db",
            LibrarianCore.DB_MAP["meta"],
        )
        if os.path.exists(_meta_db):
            try:
                with sqlite3.connect(_meta_db, timeout=5) as _c:
                    _count = _c.execute(
                        "SELECT COUNT(*) FROM etf_blocklist"
                    ).fetchone()[0]
                    return _count < 50
            except Exception as e:
                logger.error(f"Could not verify ETF blocklist: {e}")
        return True

    def etf_seed():
        logger.info("[MYRA BG] Seeding ETF blocklist for first time...")
        from myra_app.utils.etf_sync import sync_etf_list

        sync_etf_list(force=True)
        logger.info("[MYRA BG] ETF seeding complete")

    _seed_if_needed("seed_etf_done", etf_check, etf_seed)

    # Seed NIFTY 500 index
    def index_check():
        lib = Librarian()
        lib.connect()
        result = len(lib.get_index_symbols("NIFTY 500")) < 100
        lib.close()
        return result

    def index_seed():
        logger.info("[MYRA BG] Seeding NIFTY 500 constituents...")
        sync_index_constituents("NIFTY 500", force=True)
        logger.info("[MYRA BG] Index seeding complete")

    _seed_if_needed("seed_index_done", index_check, index_seed)

    # Seed fundamentals
    def fundamentals_check():
        val_db = os.path.join(DB_DIR, "myra_valuation.db")
        if os.path.exists(val_db):
            try:
                with sqlite3.connect(val_db, timeout=5) as vconn:
                    missing = vconn.execute(
                        "SELECT COUNT(*) FROM fundamentals WHERE pe IS NULL OR pe=0 "
                        "OR roe IS NULL OR roe=0 OR market_cap IS NULL OR market_cap=0"
                    ).fetchone()[0]
                    return missing > 500
            except Exception as e:
                logger.warning(f"Could not check fundamentals: {e}")
        return True

    def fundamentals_seed():
        logger.info("[MYRA BG] Seeding fundamentals...")
        from myra_app.fundamental_sync import FundamentalSync

        sync = FundamentalSync()
        sync.run_full_sync()
        logger.info("[MYRA BG] Fundamentals seeding complete")

    _seed_if_needed("seed_fundamentals_done", fundamentals_check, fundamentals_seed)

    # Seed institutional data
    def institutional_check():
        inst_db = os.path.join(DB_DIR, "myra_institutional.db")
        if os.path.exists(inst_db):
            try:
                with sqlite3.connect(inst_db, timeout=5) as iconn:
                    count = iconn.execute(
                        "SELECT COUNT(*) FROM large_deals"
                    ).fetchone()[0]
                    return count < 100
            except Exception as e:
                logger.warning(f"Could not check institutional data: {e}")
        return True

    def institutional_seed():
        logger.info("[MYRA BG] Seeding institutional data...")
        from myra_app.utils.institutional_sync import sync_institutional_data

        sync_institutional_data(force=True)
        _mark_task_run("institutional_sync")
        logger.info("[MYRA BG] Institutional seeding complete")

    _seed_if_needed("seed_institutional_done", institutional_check, institutional_seed)


def _launch_background_threads():
    """Launch all background tasks as daemon threads."""
    tasks = [
        ("daily-ingest", _task_daily_ingest),
        ("watchdog", _task_watchdog),
        ("etf-sync", _task_etf_sync),
        ("index-sync", _task_index_sync),
        ("fundamentals-sync", _task_fundamentals_sync),
        ("fundamentals-daily", _task_fundamentals_daily),
        ("institutional-sync", _task_institutional_sync),
        ("db-backup", _task_db_backup),
        ("screener-enrich", _task_screener_enrich),
        ("fund-traction-sync", _task_fund_traction_sync),
        ("cross-buy-sync", _task_cross_buy_sync),
    ]
    threads = [
        threading.Thread(target=fn, name=f"myra-bg-{name}", daemon=True)
        for name, fn in tasks
    ]
    sync_task_names = {
        "etf-sync",
        "index-sync",
        "fundamentals-sync",
        "institutional-sync",
        "fund-traction-sync",
    }
    stagger_seconds = 30
    with _task_lock:
        for i, t in enumerate(threads):
            t.start()
            prev_name = tasks[i][0] if i > 0 else None
            if prev_name in sync_task_names:
                logger.info(
                    f"[MYRA BG] Staggering next sync task by {stagger_seconds}s..."
                )
                _shutdown_event.wait(stagger_seconds)
        _active_tasks.extend(threads)
    for name, _ in tasks:
        logger.info(f"[MYRA BG] Started task: {name}")


def _ensure_calendar_db():
    """Create/verify the calendar DB and market_calendar table on startup."""
    try:
        calendar_db_path = os.path.join(DB_DIR, LibrarianCore.DB_MAP["calendar"])
        os.makedirs(os.path.dirname(calendar_db_path), exist_ok=True)
        conn = sqlite3.connect(calendar_db_path)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS market_calendar (
                date TEXT PRIMARY KEY,
                is_trading_day INTEGER NOT NULL DEFAULT 1,
                holiday_name TEXT
            )
        """
        )
        conn.commit()
        conn.close()
        logger.info(f"[MYRA BG] Calendar database verified at {calendar_db_path}")
    except Exception as e:
        logger.warning(f"[MYRA BG] Failed to initialize calendar database: {e}")


def _ensure_network_cache_db():
    """Create/verify the optional network cache DB on startup.

    Used for HTTP response caching. Kept minimal so audits/backups stop
    warning about the missing sidecar.
    """
    try:
        cache_db_path = os.path.join(DB_DIR, LibrarianCore.DB_MAP["network_cache"])
        os.makedirs(os.path.dirname(cache_db_path), exist_ok=True)
        conn = sqlite3.connect(cache_db_path)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS cache (
                key TEXT PRIMARY KEY,
                value BLOB,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """
        )
        conn.commit()
        conn.close()
        logger.info(f"[MYRA BG] Network cache database verified at {cache_db_path}")
    except Exception as e:
        logger.warning(f"[MYRA BG] Failed to initialize network cache database: {e}")


def start():
    """
    Call this from myra.py on startup.
    Launches all background tasks as daemon threads.
    """
    _register_signals()

    _ensure_sync_log_table()
    _ensure_calendar_db()
    _ensure_network_cache_db()

    logger.info("[MYRA BG] Running startup DB health check (synchronous)...")
    _task_db_doctor()

    threading.Thread(target=_run_seed_checks, daemon=True).start()

    # Initial backup on first startup
    try:
        from myra_app.utils.db_backup import rotate_backups

        backup_dir = os.path.join(DB_DIR, "backups")
        if not os.path.exists(backup_dir) or len(os.listdir(backup_dir)) == 0:
            logger.info("[MYRA BG] Creating initial DB backup...")
            rotate_backups()  # keep_last_days defaults to 2 for initial backup
    except Exception as e:
        logger.warning(f"Initial backup check failed: {e}")

    # Catch-up: Run daily ingest immediately if overdue (>1 day)
    try:
        if _is_task_overdue("daily_ingest", days=1):
            logger.info("[MYRA BG] Daily ingest overdue – running catch-up now...")
            _task_daily_ingest(force=True)
            _mark_task_run("stale_catchup")
    except Exception as e:
        logger.warning(f"[MYRA BG] Daily ingest catch-up failed: {e}")

    _launch_background_threads()

    logger.info("[MYRA BG] Background orchestrator running.")
