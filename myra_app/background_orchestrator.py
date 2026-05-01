#!/usr/bin/env python
"""
MYRA Background Orchestrator
Runs maintenance tasks in daemon threads on startup.
Guarantees clean DB shutdown on Ctrl+C, window close, or taskkill.
"""

import logging
import os
import signal
import sys
import threading
import time
from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))

# Ensure project root is on path
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from myra_app.librarian_core import LibrarianCore
from myra_app.utils.index_sync import sync_index_constituents

logger = logging.getLogger("myra.orchestrator")

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
    with _pool_lock:
        if thread_name in _connection_pool:
            lib = _connection_pool[thread_name]
            # Verify connection is still alive
            if lib._meta_conn is not None:
                return lib
            else:
                # Connection died, remove and recreate
                del _connection_pool[thread_name]
                logger.warning(f"[MYRA BG] Recreating dead metadata connection for thread {thread_name}")
        
        # Create new connection
        lib = LibrarianCore(read_only=read_only)
        _connection_pool[thread_name] = lib
        logger.debug(f"[MYRA BG] Created new metadata connection for thread {thread_name}")
        return lib


def _close_metadata_connection():
    """Close the current thread's metadata connection if it exists."""
    thread_name = threading.current_thread().name
    with _pool_lock:
        if thread_name in _connection_pool:
            try:
                _connection_pool[thread_name].close()
                del _connection_pool[thread_name]
                logger.debug(f"[MYRA BG] Closed metadata connection for thread {thread_name}")
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
    print(
        "\n[MYRA] Shutdown signal received. Waiting for background tasks to finish..."
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

    print("[MYRA] All background tasks finished. DB is safe. Goodbye.")


# ─── Helper: check if today already ingested ──────────────────────────────────


def _already_ingested_today() -> bool:
    # PERFORMANCE IMPROVEMENT: Reuse thread-local connection instead of creating/closing
    try:
        lib = _get_metadata_connection(read_only=True)
        last = lib.get_metadata("last_sync_date")
        if last:
            today = datetime.now(timezone.utc).astimezone(IST).date().isoformat()
            return last.strip() == today
    except Exception as e:
        logger.warning(f"[MYRA BG] Failed to check ingestion status: {e}")
        # Fallback to original method on error
        try:
            lib = LibrarianCore(read_only=True)
            last = lib.get_metadata("last_sync_date")
            lib.close()
            if last:
                today = datetime.now(timezone.utc).astimezone(IST).date().isoformat()
                return last.strip() == today
        except Exception:
            pass
    return False


def _mark_ingested_today():
    # PERFORMANCE IMPROVEMENT: Reuse thread-local connection instead of creating/closing
    try:
        today = datetime.now(timezone.utc).astimezone(IST).date().isoformat()
        lib = _get_metadata_connection(read_only=False)
        lib.set_metadata("last_sync_date", today)
        logger.info(f"[MYRA BG] Marked ingestion date: {today}")
    except Exception as e:
        logger.warning(f"[MYRA BG] Failed to mark ingestion date with pooled connection: {e}")
        # Fallback to original method on error
        try:
            today = datetime.now(timezone.utc).astimezone(IST).date().isoformat()
            lib = LibrarianCore(read_only=False)
            lib.set_metadata("last_sync_date", today)
            lib.close()
        except Exception as e2:
            logger.warning(f"Could not mark ingestion date: {e2}")


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
        print("[MYRA BG] Running DB health check...")
        from tools.db_doctor import DbDoctor

        doctor = DbDoctor()
        doctor.run()
        print("[MYRA BG] DB health check complete.")
    except Exception as e:
        logger.error(f"[MYRA BG] DB Doctor failed: {e}")
    finally:
        unregister(tid)


# ─── Task 2: Daily Ingestor ───────────────────────────────────────────────────


def _task_daily_ingest():
    from myra_app.task_tracker import register, unregister

    if _shutdown_event.is_set():
        return

    ist_now = datetime.now(timezone.utc).astimezone(IST)

    # Skip weekends
    if ist_now.weekday() >= 5:
        print(f"[MYRA BG] {ist_now.date()} is a weekend – skipping daily ingest.")
        return

    # Skip before 6 PM IST (data not yet available)
    if ist_now.hour < 18:
        print(
            f"[MYRA BG] Market data not yet available (IST: {ist_now.strftime('%H:%M')}). Skipping."  # noqa: PG-STRFTIME
        )
        return

    # Skip if already ingested today
    if _already_ingested_today():
        print("[MYRA BG] Today's data already ingested. Skipping.")
        return

    tid = register("Daily ingest")
    try:
        print(
            f"[MYRA BG] {ist_now.date()} is a trading day. Fetching today's bhavcopy..."
        )
        from myra_app.daily_ingestor import run_daily_update

        run_daily_update()
        _mark_ingested_today()
        print("[MYRA BG] Daily ingest complete.")
    except Exception as e:
        logger.error(f"[MYRA BG] Daily ingest failed: {e}")
    finally:
        unregister(tid)


# ─── Task 3: Midnight Watchdog ────────────────────────────────────────────────


def _task_watchdog():
    """
    Polls every 60 seconds. When a new trading day is detected after
    6 PM IST, triggers daily ingest automatically.
    Runs for the entire session lifetime.
    """
    from myra_app.task_tracker import register, update, unregister

    tid = register("Background sync watchdog", task_type="indefinite")
    try:
        last_checked_date = None

        while not _shutdown_event.is_set():
            _shutdown_event.wait(timeout=60)  # Sleep 60s, wake early on shutdown
            if _shutdown_event.is_set():
                break

            try:
                # PERFORMANCE IMPROVEMENT: Compute timezone once per iteration
                ist_now = datetime.now(timezone.utc).astimezone(IST)
                today = ist_now.date().isoformat()

                # Update watchdog status with timestamp
                update(tid, f"Watching – Last check: {ist_now.strftime('%H:%M:%S')}")

                # New day detected after 6 PM IST
                if (
                    today != last_checked_date
                    and ist_now.weekday() < 5
                    and ist_now.hour >= 18
                    and not _already_ingested_today()
                ):
                    print(
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
    """Syncs ETF blocklist from NSE every Sunday."""
    from myra_app.task_tracker import register, unregister

    tid = register("ETF sync", task_type="indefinite")
    try:
        while not _shutdown_event.is_set():
            try:
                ist_now = datetime.now(timezone.utc).astimezone(IST)
                if ist_now.weekday() == 6:  # Sunday
                    from myra_app.utils.etf_sync import sync_etf_list

                    print("[MYRA BG] Sunday ETF sync running...")
                    sync_etf_list(task_id=tid)
            except Exception as e:
                logger.error(f"[MYRA BG] ETF sync failed: {e}")
            # PERFORMANCE IMPROVEMENT: Replace long wait with responsive loop
            for _ in range(60):  # 60 * 60 = 3600 seconds total
                if _shutdown_event.wait(60):
                    return
    finally:
        unregister(tid)


# ─── Task 5: Index Sync ─────────────────────────────────────────────────────────


def _task_index_sync():
    """Syncs NIFTY indices from NSE every Sunday."""
    from myra_app.task_tracker import register, unregister

    tid = register("Index sync", task_type="indefinite")
    try:
        while not _shutdown_event.is_set():
            try:
                ist_now = datetime.now(timezone.utc).astimezone(IST)
                if ist_now.weekday() == 6:  # Sunday
                    print("[MYRA BG] Sunday index sync running...")
                    from myra_app.utils.index_sync import (
                        heal_index_if_stale,
                        sync_index_constituents,
                    )

                    for idx in ["NIFTY 50", "NIFTY 500", "NIFTY SMALLCAP 250"]:
                        sync_index_constituents(idx, task_id=tid)

                    # Self-heal: verify NIFTY 500 count (most critical for default universe)
                    heal_index_if_stale("NIFTY 500", expected_count=500)
            except Exception as e:
                logger.error(f"[MYRA BG] Index sync/heal failed: {e}")
            # PERFORMANCE IMPROVEMENT: Replace long wait with responsive loop
            for _ in range(60):  # 60 * 60 = 3600 seconds total
                if _shutdown_event.wait(60):
                    return
    finally:
        unregister(tid)


# ─── Task 6: Fundamentals Sync ───────────────────────────────────────────────────


def _task_fundamentals_sync():
    """Monthly fundamentals sync with resumable progress tracking."""
    from myra_app.task_tracker import register, unregister

    if _shutdown_event.is_set():
        return
    tid = register("Fundamentals sync", task_type="indefinite")
    try:
        from myra_app.utils.fundamentals_sync import sync_fundamentals

        print("[MYRA BG] Checking fundamentals sync...")
        sync_fundamentals(task_id=tid)  # the function itself decides if it needs to run
    except Exception as e:
        logger.error(f"[MYRA BG] Fundamentals sync failed: {e}")
    finally:
        unregister(tid)


# ─── Task 7: Institutional Sync (Bulk/Block Deals Only) ────────────────────────


def _task_institutional_sync():
    """Syncs bulk/block deals from NSE. Insider trades removed."""
    from myra_app.task_tracker import register, unregister

    if _shutdown_event.is_set():
        return
    tid = register("Institutional sync", task_type="indefinite")
    try:
        from myra_app.utils.institutional_sync import sync_institutional_data

        print("[MYRA BG] Running institutional sync...")
        sync_institutional_data(task_id=tid)  # Only sync if table is empty
    except Exception as e:
        logger.error(f"[MYRA BG] Institutional sync failed: {e}")
    finally:
        unregister(tid)


# Task 8: Daily DB Backup


def _task_db_backup():
    """Runs a full DB backup daily at 2 AM IST."""
    from myra_app.task_tracker import register, unregister

    tid = register("DB backup", task_type="indefinite")
    try:
        while not _shutdown_event.is_set():
            try:
                ist_now = datetime.now(timezone.utc).astimezone(IST)
                # Run between 02:00 and 02:59 IST
                if ist_now.hour == 2:
                    from myra_app.utils.db_backup import rotate_backups
                    print("[MYRA BG] Running scheduled daily DB backup...")
                    rotate_backups(task_id=tid)
                    print("[MYRA BG] Daily DB backup complete.")
                    # Wait until next hour to avoid multiple runs
                    # PERFORMANCE IMPROVEMENT: Replace long wait with responsive loop
                    for _ in range(60):  # 60 * 60 = 3600 seconds total
                        if _shutdown_event.wait(60):
                            return
                else:
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


# ─── Public entry point ───────────────────────────────────────────────────────


def start():
    """
    Call this from myra.py on startup.
    Launches all background tasks as daemon threads.
    """
    # Register signal handlers here, not at module level
    signal.signal(signal.SIGINT, _graceful_shutdown)
    signal.signal(signal.SIGTERM, _graceful_shutdown)
    try:
        import win32api

        win32api.SetConsoleCtrlHandler(
            lambda e: (_graceful_shutdown(), time.sleep(3), True)[-1], True
        )
    except ImportError:
        pass

    # Run DB Doctor synchronously first — schema must be ready before any data tasks
    print("[MYRA BG] Running startup DB health check (synchronous)...")
    _task_db_doctor()

    # Seed ETF list on first run if DB is empty
    try:
        import os
        import sqlite3

        from myra_app.librarian_core import LibrarianCore
        from myra_app.utils.etf_sync import sync_etf_list

        # PERFORMANCE IMPROVEMENT: Use persistent metadata flag to avoid re-seeding
        lib = LibrarianCore(read_only=True)
        seed_flag = lib.get_metadata("seed_etf_done")
        lib.close()
        
        if seed_flag != "1":
            _meta_db = os.path.join(
                os.path.dirname(os.path.abspath(__file__)),
                "db",
                LibrarianCore.DB_MAP["meta"],
            )
            _needs_seed = True
            if os.path.exists(_meta_db):
                with sqlite3.connect(_meta_db, timeout=5) as _c:
                    try:
                        _count = _c.execute(
                            "SELECT COUNT(*) FROM etf_blocklist"
                        ).fetchone()[0]
                        _needs_seed = _count < 50
                    except Exception:
                        _needs_seed = True
            if _needs_seed:
                print("[MYRA BG] Seeding ETF blocklist for first time...")
                sync_etf_list(force=True)
                # Mark as seeded
                lib = LibrarianCore(read_only=False)
                lib.set_metadata("seed_etf_done", "1")
                lib.close()
                logger.info("[MYRA BG] ETF seeding marked as complete")
            else:
                # Already seeded, mark flag
                lib = LibrarianCore(read_only=False)
                lib.set_metadata("seed_etf_done", "1")
                lib.close()
                logger.info("[MYRA BG] ETF seeding flag set (already has data)")
        else:
            logger.info("[MYRA BG] ETF seeding already done, skipping")
    except Exception as e:
        logger.warning(f"ETF seed failed: {e}")

    # Seed NIFTY 500 on first run if empty
    try:
        from myra_app.librarian import Librarian
        from myra_app.librarian_core import LibrarianCore

        # PERFORMANCE IMPROVEMENT: Use persistent metadata flag to avoid re-seeding
        lib_meta = LibrarianCore(read_only=True)
        seed_flag = lib_meta.get_metadata("seed_index_done")
        lib_meta.close()
        
        if seed_flag != "1":
            lib = Librarian()
            lib.connect()
            if len(lib.get_index_symbols("NIFTY 500")) < 100:
                print("[MYRA BG] Seeding NIFTY 500 constituents...")
                sync_index_constituents("NIFTY 500", force=True)
                # Mark as seeded
                lib_meta = LibrarianCore(read_only=False)
                lib_meta.set_metadata("seed_index_done", "1")
                lib_meta.close()
                logger.info("[MYRA BG] Index seeding marked as complete")
            else:
                # Already seeded, mark flag
                lib_meta = LibrarianCore(read_only=False)
                lib_meta.set_metadata("seed_index_done", "1")
                lib_meta.close()
                logger.info("[MYRA BG] Index seeding flag set (already has data)")
            lib.close()
        else:
            logger.info("[MYRA BG] Index seeding already done, skipping")
    except Exception as e:
        logger.warning(f"NIFTY 500 seed failed: {e}")

    # Seed fundamentals if empty
    try:
        import os
        import sqlite3

        from myra_app.constants import DB_DIR
        from myra_app.librarian_core import LibrarianCore

        # PERFORMANCE IMPROVEMENT: Use persistent metadata flag to avoid re-seeding
        lib_meta = LibrarianCore(read_only=True)
        seed_flag = lib_meta.get_metadata("seed_fundamentals_done")
        lib_meta.close()
        
        if seed_flag != "1":
            val_db = os.path.join(DB_DIR, "myra_valuation.db")
            if os.path.exists(val_db):
                with sqlite3.connect(val_db, timeout=5) as vconn:
                    # Check if ANY fundamental metric is missing
                    missing = vconn.execute(
                        "SELECT COUNT(*) FROM fundamentals WHERE pe IS NULL OR pe=0 "
                        "OR roe IS NULL OR roe=0 OR market_cap IS NULL OR market_cap=0"
                    ).fetchone()[0]
                    if missing > 500:  # >500 stocks with blanks → seed
                        print(f"[MYRA BG] Seeding fundamentals for {missing} stocks...")
                        sync_fundamentals(force=True)
                        # Mark as seeded
                        lib_meta = LibrarianCore(read_only=False)
                        lib_meta.set_metadata("seed_fundamentals_done", "1")
                        lib_meta.close()
                        logger.info("[MYRA BG] Fundamentals seeding marked as complete")
                    else:
                        # Already seeded, mark flag
                        lib_meta = LibrarianCore(read_only=False)
                        lib_meta.set_metadata("seed_fundamentals_done", "1")
                        lib_meta.close()
                        logger.info("[MYRA BG] Fundamentals seeding flag set (already has data)")
        else:
            logger.info("[MYRA BG] Fundamentals seeding already done, skipping")
    except Exception as e:
        logger.warning(f"Fundamentals seed check failed: {e}")

    # Seed institutional data if table is empty
    try:
        import os
        import sqlite3

        from myra_app.constants import DB_DIR
        from myra_app.librarian_core import LibrarianCore

        # PERFORMANCE IMPROVEMENT: Use persistent metadata flag to avoid re-seeding
        lib_meta = LibrarianCore(read_only=True)
        seed_flag = lib_meta.get_metadata("seed_institutional_done")
        lib_meta.close()
        
        if seed_flag != "1":
            inst_db = os.path.join(DB_DIR, "myra_institutional.db")
            if os.path.exists(inst_db):
                with sqlite3.connect(inst_db, timeout=5) as iconn:
                    count = iconn.execute("SELECT COUNT(*) FROM large_deals").fetchone()[0]
                    if count < 100:
                        print("[MYRA BG] Seeding institutional data...")
                        from myra_app.utils.institutional_sync import (
                            sync_institutional_data,
                        )

                        sync_institutional_data(force=True)
                        # Mark as seeded
                        lib_meta = LibrarianCore(read_only=False)
                        lib_meta.set_metadata("seed_institutional_done", "1")
                        lib_meta.close()
                        logger.info("[MYRA BG] Institutional seeding marked as complete")
                    else:
                        # Already seeded, mark flag
                        lib_meta = LibrarianCore(read_only=False)
                        lib_meta.set_metadata("seed_institutional_done", "1")
                        lib_meta.close()
                        logger.info("[MYRA BG] Institutional seeding flag set (already has data)")
        else:
            logger.info("[MYRA BG] Institutional seeding already done, skipping")
    except Exception as e:
        logger.warning(f"Institutional seed check failed: {e}")

    # Initial backup on first startup
    try:
        from myra_app.utils.db_backup import rotate_backups

        backup_dir = os.path.join(DB_DIR, "backups")
        if not os.path.exists(backup_dir) or len(os.listdir(backup_dir)) == 0:
            print("[MYRA BG] Creating initial DB backup...")
            rotate_backups()
    except Exception as e:
        logger.warning(f"Initial backup check failed: {e}")

    # Now launch remaining tasks as background threads
    tasks = [
        ("daily-ingest", _task_daily_ingest),
        ("watchdog", _task_watchdog),
        ("etf-sync", _task_etf_sync),
        ("index-sync", _task_index_sync),
        ("fundamentals-sync", _task_fundamentals_sync),
        ("institutional-sync", _task_institutional_sync),
        ("db-backup", _task_db_backup),
    ]
    with _task_lock:
        for name, fn in tasks:
            t = threading.Thread(target=fn, name=f"myra-bg-{name}", daemon=True)
            t.start()
            _active_tasks.append(t)  # noqa: PG-APPEND
            logger.info(f"[MYRA BG] Started task: {name}")

    print("[MYRA BG] Background orchestrator running.")
