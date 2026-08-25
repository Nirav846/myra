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
import sys
import threading
import time
from datetime import timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))

# Ensure project root is on path
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from myra_app.constants import DB_DIR
from myra_app.librarian_core import LibrarianCore
from myra_app.utils.index_sync import sync_index_constituents
from myra_app.utils.task_utils import (  # noqa: F401  (re-exported for external callers, e.g. myra_web/routes/health.py)
    WEEKLY_INTERVAL_DAYS,
    _ensure_sync_log_table,
    _get_last_run,
    _is_task_overdue,
    _is_task_due,
    _mark_task_run,
    now_ist,
)

logger = logging.getLogger(__name__)


# ─── Shared shutdown event ────────────────────────────────────────────────────
_shutdown_event = threading.Event()
from myra_app.tasks.context import TaskContext

_CTX = TaskContext(shutdown_event=_shutdown_event, logger=logger)
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


# ─── Thin task wrappers ───────────────────────────────────────────────────────
# Removed in Phase 3: task scheduling is declarative now. See
# myra_app/tasks/registry.py (TASKS) and myra_app/tasks/executor.py
# (run_periodic). Manual web-trigger entry points live in myra_web and call
# myra_app.tasks.* directly via tasks.context.default_context().


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
    """Launch all background tasks as daemon threads from the TASKS registry."""
    from myra_app.tasks.executor import STAGGER_SECONDS, run_periodic
    from myra_app.tasks.registry import TASKS

    threads: list[tuple[str, threading.Thread]] = []
    prev_stagger = False
    with _task_lock:
        for name, spec in TASKS.items():
            if not spec.enabled:
                logger.info(f"[MYRA BG] Task disabled, skipping: {name}")
                prev_stagger = False
                continue
            t = threading.Thread(
                target=run_periodic,
                args=(name, spec, _CTX),
                name=f"myra-bg-{name}",
                daemon=True,
            )
            t.start()
            threads.append((name, t))
            if prev_stagger and not _shutdown_event.is_set():
                logger.info(f"[MYRA BG] Staggering next task by {STAGGER_SECONDS}s...")
                _shutdown_event.wait(STAGGER_SECONDS)
            prev_stagger = spec.stagger
        _active_tasks.extend(t for _, t in threads)
    for name, _ in threads:
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
    from myra_app.tasks.doctor import run as _run_db_doctor

    _run_db_doctor(_CTX)

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
            from myra_app.tasks.ingest import run as _run_daily_ingest

            _run_daily_ingest(_CTX, force=True)
            _mark_task_run("stale_catchup")
    except Exception as e:
        logger.warning(f"[MYRA BG] Daily ingest catch-up failed: {e}")

    _launch_background_threads()

    logger.info("[MYRA BG] Background orchestrator running.")
