"""Task 10: Fund Traction Sync — monthly fund-holdings traction from GitHub Pages."""

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
    """Weekly fund traction sync from GitHub Pages. Runs immediately if overdue."""
    from myra_app.task_tracker import register, unregister

    if ctx.shutdown_event.is_set():
        return

    if _is_task_overdue("fund_traction_sync", days=7):
        tid = register("Fund traction sync", task_type="one-shot")
        try:
            logger.info("[MYRA BG] Fund traction sync overdue – running now...")
            from myra_app.fund_traction_sync import sync_fund_traction

            result = sync_fund_traction()
            _mark_task_run("fund_traction_sync")
            logger.info(
                f"[MYRA BG] Fund traction sync complete (catch-up). "
                f"Months: {result['months_synced']}, Rows: {result['rows_inserted']}"
            )
        except Exception as e:
            logger.error(f"[MYRA BG] Fund traction sync (catch-up) failed: {e}")
        finally:
            unregister(tid)

    if ctx.shutdown_event.is_set():
        return

    tid = register("Fund traction sync", task_type="indefinite")
    try:
        while not ctx.shutdown_event.is_set():
            try:
                if _is_task_due("fund_traction_sync", WEEKLY_INTERVAL_DAYS):
                    from myra_app.fund_traction_sync import sync_fund_traction

                    logger.info("[MYRA BG] Fund traction sync due – running...")
                    result = sync_fund_traction()
                    _mark_task_run("fund_traction_sync")
                    logger.info(
                        f"[MYRA BG] Fund traction sync complete. "
                        f"Months: {result['months_synced']}, Rows: {result['rows_inserted']}"
                    )
            except Exception as e:
                logger.error(f"[MYRA BG] Fund traction sync failed: {e}")
            for _ in range(60):
                if ctx.shutdown_event.wait(60):
                    return
    finally:
        unregister(tid)
