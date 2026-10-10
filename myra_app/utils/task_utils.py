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
    """Create sync_log table if it doesn't exist.

    The DDL declares the full column set (task_name, last_run, last_status,
    error_message, progress_pct) so fresh deployments get the same shape the
    rest of the codebase already operates on. Live DBs that were migrated to
    include last_status / error_message / progress_pct via a different DDL
    path are unaffected — CREATE TABLE IF NOT EXISTS is a no-op when the
    table already exists with any shape.
    """
    try:
        conn = _connect()
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS sync_log (
                    task_name      TEXT PRIMARY KEY,
                    last_run       TEXT,
                    last_status    TEXT     DEFAULT 'unknown',
                    error_message  TEXT,
                    progress_pct   REAL     DEFAULT 0
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


def _mark_task_run(
    task_name: str,
    status: str = "success",
    error_message: str | None = None,
) -> None:
    """Write current IST timestamp + status/error to sync_log for a task.

    ``status`` is "success" or "failed"; ``error_message`` is the exception
    text (already truncated by the caller if needed) or None. Retry-gating
    behavior is unchanged — this function is only invoked by the executor
    per the spec's mark_on_success / mark_on_failure rules; adding the
    status/error columns does not change when the task is considered "due"
    next cycle.
    """
    try:
        timestamp = now_ist().isoformat()
        with _WRITE_LOCK:
            conn = _connect()
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO sync_log "
                    "(task_name, last_run, last_status, error_message) "
                    "VALUES (?, ?, ?, ?)",
                    (task_name, timestamp, status, error_message),
                )
                conn.commit()
            finally:
                conn.close()
        logger.info(
            f"[MYRA BG] Marked {task_name} last_run={timestamp} status={status}"
        )
    except Exception as e:
        logger.warning(f"[MYRA BG] Failed to mark task run for {task_name}: {e}")


# ─── Attempt bookkeeping (cooldown only, never a freshness signal) ─────────────
#
# ``sync_log`` carries one row per task. For daily ingestion that row answers two
# different questions: "how fresh is the data" (interval_days = 1) and "when did we
# last try" (needs a much shorter cooldown). A hard error writes no success marker,
# so a single row cannot express both — and reusing the freshness row as the
# attempt clock either suppresses retries for 24h or, if nothing is written, lets
# the scheduler hammer the upstream source every poll.
#
# Attempts therefore get their own row, named with INTERNAL_SYNC_LOG_SUFFIX and
# carrying the status ATTEMPT_STATUS. It is bookkeeping ONLY:
#   * it never advances metadata ``last_sync_date``;
#   * it is filtered out of the human-facing sync_log listing;
#   * it is normalised to a non-success state by pipeline_control.

INTERNAL_SYNC_LOG_SUFFIX = "_attempt"
ATTEMPT_STATUS = "attempted"


def attempt_label(task_name: str) -> str:
    """sync_log label used for a task's *attempt* bookkeeping row."""
    return f"{task_name}{INTERNAL_SYNC_LOG_SUFFIX}"


def is_internal_sync_log_label(task_name: str) -> bool:
    """True for bookkeeping rows that must not surface as real tasks."""
    return str(task_name or "").endswith(INTERNAL_SYNC_LOG_SUFFIX)


def _mark_task_attempt(task_name: str) -> None:
    """Record that an attempt started, regardless of its eventual outcome.

    Written at the *start* of the attempt so an interrupted or crashed run still
    leaves a cooldown timestamp behind.
    """
    label = attempt_label(task_name)
    try:
        timestamp = now_ist().isoformat()
        with _WRITE_LOCK:
            conn = _connect()
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO sync_log "
                    "(task_name, last_run, last_status, error_message) "
                    "VALUES (?, ?, ?, NULL)",
                    (label, timestamp, ATTEMPT_STATUS),
                )
                conn.commit()
            finally:
                conn.close()
        logger.debug(f"[MYRA BG] Marked {label} attempt at {timestamp}")
    except Exception as e:
        logger.warning(f"[MYRA BG] Failed to mark attempt for {task_name}: {e}")


def _attempt_cooldown_elapsed(
    task_name: str, minutes: int, now: datetime | None = None
) -> bool:
    """True when enough time has passed since the last *attempt*.

    Shares one mechanism between the executor and the watchdog so the two
    independent drivers can never both fire an attempt for the same window.
    """
    now = now or now_ist()
    last = _get_last_run(attempt_label(task_name))
    if last is None:
        return True
    try:
        if last.tzinfo is None:
            last = last.replace(tzinfo=now.tzinfo)
        return (now - last) >= timedelta(minutes=minutes)
    except Exception as e:
        logger.warning(f"[MYRA BG] Attempt cooldown check failed for {task_name}: {e}")
        return True
