"""Task 2: Daily Ingestor — EOD2 incremental sync / legacy NSE bhavcopy path."""

import logging
import threading

from myra_app.db.enrichers.corporate_actions_enricher import enrich_corporate_actions
from myra_app.librarian_core import LibrarianCore
from myra_app.tasks.context import TaskContext
from myra_app.utils.task_utils import _mark_task_attempt, _mark_task_run, now_ist

logger = logging.getLogger(__name__)

#: Guards against the executor and the watchdog (or a manual Control Room run)
#: starting ingestion concurrently. The attempt cooldown makes a *duplicate in
#: time* unlikely, but eligibility can be evaluated by both drivers at nearly the
#: same moment; this makes the overlap impossible regardless.
_INGEST_LOCK = threading.Lock()


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


def _mark_ingested_today():
    # PERFORMANCE IMPROVEMENT: Reuse thread-local connection instead of creating/closing
    try:
        today = now_ist().date().isoformat()
        lib = _get_metadata_connection(read_only=False)
        lib.set_metadata("last_sync_date", today)
        logger.info(f"[MYRA BG] Marked ingestion date: {today}")
    except Exception as e:
        logger.warning(
            f"[MYRA BG] Failed to mark ingestion date with pooled connection: {e}"
        )
        # Fallback to original method on error
        try:
            today = now_ist().date().isoformat()
            lib = LibrarianCore(read_only=False)
            lib.set_metadata("last_sync_date", today)
            lib.close()
        except Exception as e2:
            logger.warning(f"Could not mark ingestion date: {e2}")


# Status written to sync_log when an ingestion attempt completes without error
# but inserts no rows. It is deliberately NOT "success": freshness was not
# established. Writing the row still advances ``last_run``, which is the only
# field ``_is_due`` reads, so the task is throttled for its normal interval
# instead of retrying every executor poll (60s).
NO_NEW_DATA = "no_new_data"


def _record_no_new_data(reason: str) -> None:
    """Record an ingestion attempt that completed but ingested nothing.

    Keeps two things deliberately separate:
      * attempt bookkeeping — ``sync_log.last_run`` is written, so ``_is_due``
        throttles the task and no 60s hot loop appears;
      * freshness — ``last_status`` is ``no_new_data`` (never "success") and
        metadata ``last_sync_date`` is NOT advanced.
    """
    logger.info(
        "[MYRA BG] Ingestion completed with 0 rows inserted – "
        "data freshness NOT established by this run."
    )
    _mark_task_run("daily_ingest", status=NO_NEW_DATA, error_message=reason[:500])


def run(ctx: TaskContext, force: bool = False):
    """Run one ingestion attempt, guarded against concurrent execution.

    The executor and the watchdog evaluate eligibility independently, so both
    could decide to start ingestion at nearly the same moment. ``_INGEST_LOCK``
    makes that overlap impossible: the second caller is dropped with a warning
    rather than running a duplicate sync.
    """
    if not _INGEST_LOCK.acquire(blocking=False):
        logger.warning(
            "[MYRA BG] Ingestion already in progress — skipping this attempt."
        )
        return
    try:
        _run_ingest_locked(ctx, force=force)
    finally:
        _INGEST_LOCK.release()


def _run_ingest_locked(ctx: TaskContext, force: bool = False):
    """Daily ingest body. Callers must already hold ``_INGEST_LOCK``."""
    from myra_app.task_tracker import register, unregister

    if ctx.shutdown_event.is_set():
        return

    ist_now = now_ist()

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

    # Record the attempt BEFORE any work, so the shared 30-minute cooldown holds
    # even if this run crashes, hangs or is interrupted. This is bookkeeping
    # only: it never claims freshness and never advances last_sync_date.
    _mark_task_attempt("daily_ingest")

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
                # Established failure behaviour: leave last_run untouched so the
                # task stays eligible, and do not advance any freshness marker.
                logger.error(f"[MYRA BG] EOD2 sync error: {sync_result['error']}")
            elif inserted > 0:
                _mark_ingested_today()
                _mark_task_run("daily_ingest")
                logger.info("[MYRA BG] EOD2 sync complete – running post-ingest hooks.")
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
                    logger.debug(f"[MYRA BG] Portfolio refresh not available: {exc}")

                try:
                    enrich_corporate_actions()
                except Exception as exc:
                    logger.error(
                        f"[MYRA BG] Corporate actions enrichment failed: {exc}"
                    )
            else:
                # Completed without error but nothing new landed. The result
                # contract cannot distinguish "already current" from "source not
                # published yet", so report only what was observed.
                _record_no_new_data(
                    "EOD2 sync completed without error but inserted 0 rows "
                    "(0 rows inserted; data freshness not established by this run). "
                    f"rows_inserted={inserted} symbols_updated={symbols} "
                    f"skipped={sync_result.get('skipped', 0)}"
                )
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

        inserted_rows = result.get("total_rows_inserted", 0) or 0

        # Freshness is established by rows actually landing, never by the task
        # merely returning. `no_new_data` covers both "nothing failed and
        # nothing inserted" and "reported success but inserted nothing".
        if inserted_rows <= 0:
            if not result.get("success") and not result.get("dates_failed"):
                logger.info(
                    "[MYRA BG] Ingestion returned no new rows – freshness not established."
                )
            _record_no_new_data(
                "Legacy ingestion completed but inserted 0 rows "
                "(0 rows inserted; data freshness not established by this run). "
                f"success={result.get('success')} "
                f"failed_dates={result.get('dates_failed') or []}"
            )
        elif result.get("success"):
            new_latest = get_db_latest_date()
            logger.info(f"[MYRA BG] DB latest date after ingestion: {new_latest}")
            _mark_ingested_today()
            _mark_task_run("daily_ingest")
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
            failed_dates = result.get("dates_failed", [])
            error_msg = result.get("error", "Unknown error")
            logger.error(
                f"[MYRA BG] Ingestion failed! Failed dates: {failed_dates}, Error: {error_msg}"
            )
    except Exception as e:
        logger.error(f"[MYRA BG] Daily ingest failed with exception: {e}")
    finally:
        unregister(tid)
