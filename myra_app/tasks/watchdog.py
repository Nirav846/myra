"""Task 3: Midnight Watchdog — detects new trading day / stale DB, triggers ingest.

Freshness is judged against the **expected trading date**, not the calendar date.
Before 18:30 IST the current session's bhavcopy cannot exist yet, and on weekends
/ market holidays the previous trading day's data is still current — so a plain
"calendar date changed" test wrongly declared the database stale and fired an
ingestion attempt at midnight (which then recorded a false success).

The watchdog therefore:
  * computes the latest trading day whose data *should* already be present,
    reusing ``daily_ingestor.is_trading_day`` (weekends + NSE holidays +
    ``market_calendar``);
  * never calls ingestion with ``force=True`` — ``tasks.ingest.run`` keeps its own
    weekend/after-close guards, which are only reached once the watchdog has
    already established that an attempt is legitimate;
  * writes the ``stale_catchup`` marker only when the stored data date actually
    advanced, so a no-op can never masquerade as established freshness;
  * rate-limits attempts with a cooldown, reusing the ``daily_ingest`` sync_log
    row as a cross-restart attempt clock.
"""

import logging
import threading
from datetime import date as _date
from datetime import datetime, time as _time, timedelta

from myra_app.librarian_core import LibrarianCore
from myra_app.tasks.context import TaskContext
from myra_app.utils.task_utils import _mark_task_run, now_ist

logger = logging.getLogger(__name__)

#: IST time from which the current session's bhavcopy is expected to exist.
#: AGENTS.md: ingestion happens "after 18:30 IST close".
PUBLICATION_READY_IST = _time(18, 30)

#: Minimum gap between catch-up attempts while the database is behind the
#: expected trading date. Prevents a 60s poll from turning into a hot retry loop
#: when the upstream file is not published yet.
STALE_RETRY_COOLDOWN_MINUTES = 30


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


# ─── Freshness model: expected trading date, not calendar date ────────────────


def _is_trading_day(day: _date) -> bool:
    """True if `day` is an NSE trading day.

    Delegates to the single shared authority, ``daily_ingestor.is_trading_day``,
    which owns the weekend / ``NSE_HOLIDAYS_BASELINE`` / ``market_calendar``
    rules *and* the database-truth override for the legacy
    "Likely holiday (zero rows)" heuristic.

    Keeping the override in one place matters: ``market_calendar`` classifies some
    real sessions that hold thousands of EOD rows as likely holidays. Trusting
    that made ``_expected_trading_date()`` walk back past the real latest session
    and report the database permanently fresh, so ingestion would never run again.
    """
    from myra_app.daily_ingestor import is_trading_day

    try:
        return bool(is_trading_day(datetime.combine(day, _time(12, 0))))
    except Exception as exc:  # calendar DB unreadable - fall back to weekday
        logger.warning(f"[MYRA BG] Trading-day lookup failed ({exc}); using weekday")
        return day.weekday() < 5


def _previous_trading_day(day: _date, max_lookback: int = 15) -> _date:
    """Most recent trading day strictly before `day`."""
    candidate = day - timedelta(days=1)
    for _ in range(max_lookback):
        if _is_trading_day(candidate):
            return candidate
        candidate -= timedelta(days=1)
    return candidate


def _expected_trading_date(now: datetime | None = None) -> _date:
    """Latest trading day whose EOD data should already be in the database.

    * Trading day at/after 18:30 IST -> today (the file should be published).
    * Trading day before 18:30 IST  -> previous trading day (today's cannot exist).
    * Weekend / market holiday      -> previous trading day (Friday is still current).
    """
    now = now or now_ist()
    today = now.date()
    if _is_trading_day(today) and _after_publication_window(now):
        return today
    return _previous_trading_day(today)


def _after_publication_window(now: datetime | None = None) -> bool:
    """True from 18:30 IST onwards.

    Implemented as a time comparison rather than ``hour >= 18 and minute >= 30``,
    which wrongly excluded 19:00–19:29 (and 20:00–20:29, ...).
    """
    now = now or now_ist()
    return now.time() >= PUBLICATION_READY_IST


def _db_latest_date() -> str | None:
    """Latest date present in technical_data (ISO string), or None."""
    try:
        from myra_app.daily_ingestor import get_db_latest_date

        return get_db_latest_date()
    except Exception as exc:
        logger.warning(f"[MYRA BG] Failed to read DB latest date: {exc}")
        return None


def _is_db_stale(now: datetime | None = None) -> bool:
    """True when the stored data date is behind the expected trading date.

    ISO date strings compare correctly with ``<``/``>=``.
    """
    expected = _expected_trading_date(now).isoformat()
    db_latest = _db_latest_date()
    if db_latest is None:
        return True
    return db_latest < expected


def _freshness_established(now: datetime | None = None) -> bool:
    """True iff the DB has caught up to (or passed) the expected trading date.

    This is the condition that legitimately earns a ``stale_catchup`` marker.
    It replaces the old metadata-based ``_check_last_sync_date_today()``, which
    trusted ``metadata.last_sync_date`` rather than the database itself.
    """
    expected = _expected_trading_date(now).isoformat()
    db_latest = _db_latest_date()
    return db_latest is not None and db_latest >= expected


def _cooldown_elapsed(
    now: datetime | None = None, minutes: int = STALE_RETRY_COOLDOWN_MINUTES
) -> bool:
    """Rate-limit catch-up attempts using the durable attempt row.

    Delegates to ``task_utils._attempt_cooldown_elapsed`` so the watchdog and the
    executor share one cooldown mechanism and one clock. It reads the
    ``daily_ingest_attempt`` row — written at the *start* of every attempt,
    including hard errors — rather than the ``daily_ingest`` freshness row,
    which a failure deliberately leaves untouched.
    """
    from myra_app.utils.task_utils import _attempt_cooldown_elapsed

    return _attempt_cooldown_elapsed("daily_ingest", minutes, now)


# ─── Backwards-compatible helpers (kept; semantics unchanged) ─────────────────


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


def _check_last_sync_date_today() -> bool:
    """True iff metadata 'last_sync_date' equals today's IST date.

    DEPRECATED for gating: ``tasks.ingest.run`` only advances ``last_sync_date``
    on real ingestion now, but on weekends/holidays it legitimately holds an
    earlier date. Use :func:`_freshness_established` (DB-truth) instead.
    """
    try:
        lib = _get_metadata_connection(read_only=True)
        last = lib.get_metadata("last_sync_date")
        if not last:
            return False
        return last.strip() == now_ist().date().isoformat()
    except Exception:
        return False


def _maybe_trigger_ingest(ctx, ist_now: datetime, run_daily_ingest) -> str | None:
    """Decide whether a catch-up ingestion attempt is warranted right now.

    Returns the outcome for logging/tests: ``None`` when no action was taken,
    ``"success"`` when the stored data date advanced, otherwise
    ``"no_new_data"``.

    The call is deliberately made **without** ``force=True``: by the time we get
    here the expected-date, publication-window and cooldown checks have already
    established that the attempt is legitimate, so ``tasks.ingest.run`` keeps
    ownership of its own weekend / after-close guards.
    """
    expected = _expected_trading_date(ist_now).isoformat()
    db_latest = _db_latest_date()

    if db_latest is not None and db_latest >= expected:
        return None  # already current for the expected trading date

    if not _after_publication_window(ist_now):
        logger.debug(
            "[MYRA BG] Before 18:30 IST – %s data cannot be published yet; "
            "deferring catch-up.",
            expected,
        )
        return None

    if not _cooldown_elapsed(ist_now):
        logger.debug(
            "[MYRA BG] Catch-up cooldown active (last attempt < %d min ago); skipping.",
            STALE_RETRY_COOLDOWN_MINUTES,
        )
        return None

    logger.info(
        "[MYRA BG] Database behind expected trading date (%s < %s) – "
        "triggering catch-up ingestion.",
        db_latest or "empty",
        expected,
    )

    before = db_latest
    run_daily_ingest(ctx)

    after = _db_latest_date()
    if after is not None and (before is None or after > before):
        _mark_task_run("stale_catchup")
        logger.info(f"[MYRA BG] Catch-up ingestion advanced DB to {after}.")
        return "success"

    # Freshness NOT established. Do not write a stale_catchup success marker.
    # tasks.ingest.run has already recorded the attempt itself (success or
    # no_new_data), which the cooldown reads.
    logger.info(
        f"[MYRA BG] Catch-up ingestion did not advance the stored date "
        f"(still {after or 'empty'}); freshness not established."
    )
    return "no_new_data"


def run(ctx: TaskContext):
    """
    Polls every 60 seconds and triggers daily ingest when the stored data date
    falls behind the expected trading date and the after-close publication
    window has opened. Runs for the entire session lifetime.
    """
    from myra_app.task_tracker import register, update, unregister
    from myra_app.tasks.ingest import run as run_daily_ingest

    tid = register("Background sync watchdog", task_type="indefinite")
    try:
        while not ctx.shutdown_event.is_set():
            ctx.shutdown_event.wait(timeout=60)
            if ctx.shutdown_event.is_set():
                break

            try:
                ist_now = now_ist()
                update(
                    tid,
                    f"Watching – Last check: {ist_now.hour:02d}:{ist_now.minute:02d}:{ist_now.second:02d}",
                )
                _maybe_trigger_ingest(ctx, ist_now, run_daily_ingest)
            except Exception as e:
                logger.warning(f"[MYRA BG] Watchdog error: {e}")
    finally:
        unregister(tid)
