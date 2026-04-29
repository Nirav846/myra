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

    print("[MYRA] All background tasks finished. DB is safe. Goodbye.")


# ─── Helper: check if today already ingested ──────────────────────────────────


def _already_ingested_today() -> bool:
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
    try:
        today = datetime.now(timezone.utc).astimezone(IST).date().isoformat()
        lib = LibrarianCore(read_only=False)
        lib.set_metadata("last_sync_date", today)
        lib.close()
    except Exception as e:
        logger.warning(f"Could not mark ingestion date: {e}")


# ─── Task 1: DB Doctor ────────────────────────────────────────────────────────


def _task_db_doctor():
    """
    Runs db_doctor in auto-fix mode on startup.
    Skips if shutdown is requested.
    """
    if _shutdown_event.is_set():
        return
    try:
        print("[MYRA BG] Running DB health check...")
        from tools.db_doctor import DbDoctor

        doctor = DbDoctor()
        doctor.run()
        print("[MYRA BG] DB health check complete.")
    except Exception as e:
        logger.error(f"[MYRA BG] DB Doctor failed: {e}")


# ─── Task 2: Daily Ingestor ───────────────────────────────────────────────────


def _task_daily_ingest():
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


# ─── Task 3: Midnight Watchdog ────────────────────────────────────────────────


def _task_watchdog():
    """
    Polls every 60 seconds. When a new trading day is detected after
    6 PM IST, triggers daily ingest automatically.
    Runs for the entire session lifetime.
    """
    last_checked_date = None

    while not _shutdown_event.is_set():
        _shutdown_event.wait(timeout=60)  # Sleep 60s, wake early on shutdown
        if _shutdown_event.is_set():
            break

        try:
            ist_now = datetime.now(timezone.utc).astimezone(IST)
            today = ist_now.date().isoformat()

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


# ─── Task 4: ETF Sync ─────────────────────────────────────────────────────────


def _task_etf_sync():
    """Syncs ETF blocklist from NSE every Sunday."""
    while not _shutdown_event.is_set():
        try:
            ist_now = datetime.now(timezone.utc).astimezone(IST)
            if ist_now.weekday() == 6:  # Sunday
                from myra_app.utils.etf_sync import sync_etf_list

                print("[MYRA BG] Sunday ETF sync running...")
                sync_etf_list()
        except Exception as e:
            logger.error(f"[MYRA BG] ETF sync failed: {e}")
        _shutdown_event.wait(timeout=3600)  # Check every hour


# ─── Task 5: Index Sync ─────────────────────────────────────────────────────────


def _task_index_sync():
    """Syncs NIFTY indices from NSE every Sunday."""
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
                    sync_index_constituents(idx)

                # Self-heal: verify NIFTY 500 count (most critical for default universe)
                heal_index_if_stale("NIFTY 500", expected_count=500)
        except Exception as e:
            logger.error(f"[MYRA BG] Index sync/heal failed: {e}")
        _shutdown_event.wait(timeout=3600)  # Check every hour


# ─── Task 6: Fundamentals Sync ───────────────────────────────────────────────────


def _task_fundamentals_sync():
    """Monthly fundamentals sync with resumable progress tracking."""
    if _shutdown_event.is_set():
        return
    try:
        from myra_app.utils.fundamentals_sync import sync_fundamentals

        print("[MYRA BG] Checking fundamentals sync...")
        sync_fundamentals()  # the function itself decides if it needs to run
    except Exception as e:
        logger.error(f"[MYRA BG] Fundamentals sync failed: {e}")


# ─── Task 7: Institutional Sync ─────────────────────────────────────────────────


def _task_institutional_sync():
    if _shutdown_event.is_set():
        return
    try:
        from myra_app.utils.institutional_sync import sync_institutional_data

        print("[MYRA BG] Running institutional sync...")
        sync_institutional_data()  # Only sync if table is empty
    except Exception as e:
        logger.error(f"[MYRA BG] Institutional sync failed: {e}")


# Task 8: Daily DB Backup


def _task_db_backup():
    """Runs a full DB backup daily at 2 AM IST."""
    while not _shutdown_event.is_set():
        try:
            ist_now = datetime.now(timezone.utc).astimezone(IST)
            # Run between 02:00 and 02:59 IST
            if ist_now.hour == 2:
                from myra_app.utils.db_backup import rotate_backups
                print("[MYRA BG] Running scheduled daily DB backup...")
                rotate_backups()
                print("[MYRA BG] Daily DB backup complete.")
                # Wait until next hour to avoid multiple runs
                _shutdown_event.wait(timeout=3600)
            else:
                # Check again in 30 minutes
                _shutdown_event.wait(timeout=1800)
        except Exception as e:
            logger.error(f"[MYRA BG] Daily DB backup failed: {e}")
            _shutdown_event.wait(timeout=1800)  # wait and retry


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
    except Exception as e:
        logger.warning(f"ETF seed failed: {e}")

    # Seed NIFTY 500 on first run if empty
    try:
        from myra_app.librarian import Librarian

        lib = Librarian()
        lib.connect()
        if len(lib.get_index_symbols("NIFTY 500")) < 100:
            print("[MYRA BG] Seeding NIFTY 500 constituents...")
            sync_index_constituents("NIFTY 500", force=True)
        lib.close()
    except Exception as e:
        logger.warning(f"NIFTY 500 seed failed: {e}")

    # Seed fundamentals if empty
    try:
        import os
        import sqlite3

        from myra_app.constants import DB_DIR

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
    except Exception as e:
        logger.warning(f"Fundamentals seed check failed: {e}")

    # Seed institutional data if table is empty
    try:
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
