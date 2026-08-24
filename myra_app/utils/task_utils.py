"""Shared scheduling/sync-log utilities for background tasks.

Extracted from background_orchestrator.py (Phase 1 refactor).
All functions are stateless apart from one module-level write lock;
they access sync_log in myra_metadata.db via short-lived direct connections.
"""

import logging
import os
import sqlite3
import threading
from datetime import datetime, timedelta, timezone

from myra_app.constants import DB_DIR

logger = logging.getLogger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))

WEEKLY_INTERVAL_DAYS = 7

# Serialises sync_log writes across orchestrator threads.
_WRITE_LOCK = threading.Lock()

# NOTE: filename mirrors LibrarianCore.DB_MAP["meta"]; intentionally NOT imported
# here to keep this module dependency-free (librarian_core pulls heavy deps and
# risks import cycles from utility call sites).
_META_DB_FILENAME = "myra_metadata.db"


def _metadata_db_path() -> str:
    """
    Resolve the absolute path of myra_metadata.db.

    Returns:
        Absolute path string to the metadata sidecar database.
    """
    return os.path.join(DB_DIR, _META_DB_FILENAME)


def now_ist() -> datetime:
    """
    Return the current time as a timezone-aware IST datetime.

    Returns:
        datetime in IST (UTC+05:30).
    """
    return datetime.now(timezone.utc).astimezone(IST)


def _connect(read_only: bool = False) -> sqlite3.Connection:
    """
    Open a short-lived connection to myra_metadata.db.

    Args:
        read_only: Kept for call-site parity with the old pooled API;
            sqlite3.connect opens the same file handle either way.

    Returns:
        An open sqlite3.Connection. The caller MUST close it.
    """
    return sqlite3.connect(_metadata_db_path(), timeout=30)


def _ensure_sync_log_table() -> None:
    """Create sync_log table if it doesn't exist."""
    try:
        conn = _connect()
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS sync_log (
                    task_name   TEXT PRIMARY KEY,
                    last_run    TEXT
                )
            """
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        logger.warning(f"[MYRA BG] Failed to ensure sync_log table: {e}")


def _is_due(task_name: str, interval_days: int) -> bool:
    """
    Core due check: a task is due if never run or its last run is older
    than interval_days.

    Args:
        task_name: sync_log primary key for the task.
        interval_days: Minimum days since last run before the task is due.

    Returns:
        True if the task should run now.
    """
    last_run = _get_last_run(task_name)
    if last_run is None:
        return True
    days_since = (now_ist() - last_run).days
    return days_since >= interval_days


def _is_task_due(task_name: str, interval_days: int = WEEKLY_INTERVAL_DAYS) -> bool:
    """Check if task is due to run based on interval."""
    return _is_due(task_name, interval_days)


def _is_task_overdue(task_name: str, days: int) -> bool:
    """Check if task hasn't run in specified days (or never run)."""
    return _is_due(task_name, days)


def _get_last_run(task_name: str) -> datetime | None:
    """Get last run timestamp for a task. Returns None if never run."""
    try:
        conn = _connect(read_only=True)
        try:
            res = conn.execute(
                "SELECT last_run FROM sync_log WHERE task_name = ?", (task_name,)
            ).fetchone()
            if res and res[0]:
                return datetime.fromisoformat(res[0])
        finally:
            conn.close()
    except Exception as e:
        logger.debug(f"[MYRA BG] Failed to get last run for {task_name}: {e}")
    return None


def _mark_task_run(task_name: str) -> None:
    """Write current IST timestamp to sync_log for a task."""
    try:
        timestamp = now_ist().isoformat()
        with _WRITE_LOCK:
            conn = _connect()
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO sync_log (task_name, last_run) VALUES (?, ?)",
                    (task_name, timestamp),
                )
                conn.commit()
            finally:
                conn.close()
        logger.info(f"[MYRA BG] Marked {task_name} last_run: {timestamp}")
    except Exception as e:
        logger.warning(f"[MYRA BG] Failed to mark task run for {task_name}: {e}")
