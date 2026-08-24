"""Task 2: Daily Ingestor — EOD2 incremental sync / legacy NSE bhavcopy path."""

import logging
import threading

from myra_app.db.enrichers.corporate_actions_enricher import enrich_corporate_actions
from myra_app.librarian_core import LibrarianCore
from myra_app.tasks.context import TaskContext
from myra_app.utils.task_utils import _mark_task_run, now_ist

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


def run(ctx: TaskContext, force: bool = False):
    """Daily ingest with optional force flag for catch-up runs."""
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
