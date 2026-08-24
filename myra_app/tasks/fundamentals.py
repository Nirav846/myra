"""Task 6: Fundamentals Sync — weekly full sync + daily lightweight MS sync."""

import logging

from myra_app.tasks.context import TaskContext
from myra_app.utils.task_utils import (
    WEEKLY_INTERVAL_DAYS,
    _is_task_due,
    _is_task_overdue,
    _mark_task_run,
    now_ist,
)

logger = logging.getLogger(__name__)


def run(ctx: TaskContext):
    """Weekly fundamentals full sync every 7 days. Runs immediately if overdue."""
    from myra_app.task_tracker import register, unregister, update

    if ctx.shutdown_event.is_set():
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
        while not ctx.shutdown_event.is_set():
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
                if ctx.shutdown_event.wait(60):
                    return
    finally:
        unregister(tid)


def run_daily(ctx: TaskContext):
    """Daily lightweight fundamentals sync (weekdays after 6pm)."""
    from myra_app.task_tracker import register, unregister, update

    if ctx.shutdown_event.is_set():
        return

    tid = register("Fundamentals daily", task_type="indefinite")
    try:
        while not ctx.shutdown_event.is_set():
            try:
                ist_now = now_ist()
                if not _is_task_overdue("fundamentals_daily", days=1):
                    for _ in range(30):
                        if ctx.shutdown_event.wait(60):
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
                        if ctx.shutdown_event.wait(60):
                            return
                else:
                    # Check every 30 minutes
                    for _ in range(30):
                        if ctx.shutdown_event.wait(60):
                            return
            except Exception as e:
                logger.error(f"[MYRA BG] Fundamentals daily sync failed: {e}")
                # Wait 30 minutes before retry
                for _ in range(30):
                    if ctx.shutdown_event.wait(60):
                        return
    finally:
        unregister(tid)
