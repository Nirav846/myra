"""Task 4: ETF Sync — weekly ETF blocklist refresh from NSE."""

import logging

from myra_app.tasks.context import TaskContext
from myra_app.utils.task_utils import (
    WEEKLY_INTERVAL_DAYS,
    _is_task_due,
    _is_task_overdue,
    _mark_task_run,
)

logger = logging.getLogger(__name__)


def run(ctx: TaskContext):
    """Syncs ETF blocklist from NSE every 7 days. Runs immediately if overdue."""
    from myra_app.task_tracker import register, unregister

    if ctx.shutdown_event.is_set():
        return

    if _is_task_overdue("etf_sync", days=7):
        tid = register("ETF sync", task_type="one-shot")
        try:
            logger.info("[MYRA BG] ETF sync overdue – running now...")
            from myra_app.utils.etf_sync import sync_etf_list

            sync_etf_list(task_id=tid)
            _mark_task_run("etf_sync")
            logger.info("[MYRA BG] ETF sync complete (catch-up).")
        except Exception as e:
            logger.error(f"[MYRA BG] ETF sync (catch-up) failed: {e}")
        finally:
            unregister(tid)

    if ctx.shutdown_event.is_set():
        return

    tid = register("ETF sync", task_type="indefinite")
    try:
        while not ctx.shutdown_event.is_set():
            try:
                if _is_task_due("etf_sync", WEEKLY_INTERVAL_DAYS):
                    from myra_app.utils.etf_sync import sync_etf_list

                    logger.info("[MYRA BG] ETF sync due – running...")
                    sync_etf_list(task_id=tid)
                    _mark_task_run("etf_sync")
            except Exception as e:
                logger.error(f"[MYRA BG] ETF sync failed: {e}")
            for _ in range(60):
                if ctx.shutdown_event.wait(60):
                    return
    finally:
        unregister(tid)
