"""Task 3: Midnight Watchdog — detects new trading day / stale DB, triggers ingest."""

import logging
import threading
from datetime import datetime

from myra_app.librarian_core import LibrarianCore
from myra_app.tasks.context import TaskContext
from myra_app.utils.task_utils import _get_last_run, _mark_task_run, now_ist

logger = logging.getLogger(__name__)


# ─── Thread-local connection pool for metadata operations ─────────────────────
# PERFORMANCE IMPROVEMENT: Reuse connections per thread to avoid repeated open/close
# (private copy: the orchestrator pool is not importable from tasks/* by design)
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


# ─── Helper: check if today already ingested with DB truth verification ────────


def _already_ingested_today() -> bool:
    """
    Verify if today's data is actually in the database.
    DB truth takes precedence over metadata - if DB is stale,
    we should NOT trust metadata alone.
    """
    try:
        from myra_app.daily_ingestor import get_db_latest_date

        today = now_ist().date().isoformat()
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
        ist_now = now_ist()
        db_date = datetime.strptime(db_latest, "%Y-%m-%d").date()
        current_date = ist_now.date()
        days_behind = (current_date - db_date).days
        return days_behind >= days_threshold
    except Exception as e:
        logger.warning(f"[MYRA BG] Failed to check DB staleness: {e}")
        return False


def _check_last_sync_date_today() -> bool:
    """True iff metadata 'last_sync_date' equals today's IST date.

    Set only by ingest.run()'s success branch. Used after kicking off
    run_daily_ingest() so the watchdog's stale_catchup record reflects a
    real success rather than a silent failure.
    """
    try:
        lib = _get_metadata_connection(read_only=True)
        last = lib.get_metadata("last_sync_date")
        if not last:
            return False
        return last.strip() == now_ist().date().isoformat()
    except Exception:
        return False


def run(ctx: TaskContext):
    """
    Polls every 60 seconds. When a new trading day is detected after
    6 PM IST, triggers daily ingest automatically.
    Also detects stale DB and triggers catch-up.
    Runs for the entire session lifetime.
    """
    from myra_app.task_tracker import register, update, unregister
    from myra_app.tasks.ingest import run as run_daily_ingest

    tid = register("Background sync watchdog", task_type="indefinite")
    try:
        last_checked_date = now_ist().date()

        while not ctx.shutdown_event.is_set():
            ctx.shutdown_event.wait(timeout=60)
            if ctx.shutdown_event.is_set():
                break

            try:
                ist_now = now_ist()
                today = ist_now.date().isoformat()

                update(
                    tid,
                    f"Watching – Last check: {ist_now.hour:02d}:{ist_now.minute:02d}:{ist_now.second:02d}",
                )

                if _is_db_stale(days_threshold=1):
                    ist_now = now_ist()
                    last_attempt = _get_last_run("stale_catchup")
                    already_tried_today = (
                        last_attempt is not None
                        and last_attempt.date() == ist_now.date()
                    )

                    if not already_tried_today:
                        logger.info(
                            "[MYRA BG] Database is STALE (1+ days behind). Triggering catch-up..."
                        )
                        # Audit fix: only record stale_catchup as run after
                        # the ingest actually succeeded (otherwise a silent
                        # failure would masquerade as success here).
                        run_daily_ingest(ctx, force=True)
                        if _check_last_sync_date_today():
                            _mark_task_run("stale_catchup")
                    elif (
                        ist_now.hour >= 18
                        and ist_now.minute >= 30
                        and not _already_ingested_today()
                    ):
                        logger.info(
                            "[MYRA BG] Market closed – retrying post-close ingestion..."
                        )
                        run_daily_ingest(ctx, force=True)
                        if _check_last_sync_date_today():
                            _mark_task_run("stale_catchup")
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
                    run_daily_ingest(ctx)
                    last_checked_date = today

            except Exception as e:
                logger.warning(f"[MYRA BG] Watchdog error: {e}")
    finally:
        unregister(tid)
