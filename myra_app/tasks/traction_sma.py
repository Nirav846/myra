"""Task 12: Traction SMA Daily Update — daily SMA/pct_vs_sma refresh."""

import logging

from myra_app.tasks.context import TaskContext
from myra_app.utils.task_utils import _is_task_due, _is_task_overdue, _mark_task_run

logger = logging.getLogger(__name__)


def run(ctx: TaskContext):
    """Daily traction SMA/pct_vs_sma refresh from technical_data. Runs immediately if overdue."""
    from myra_app.task_tracker import register, unregister

    if ctx.shutdown_event.is_set():
        return

    if _is_task_overdue("traction_sma_update", days=1):
        tid = register("Traction SMA update", task_type="one-shot")
        try:
            logger.info("[MYRA BG] Traction SMA update overdue – running now...")
            from myra_app.fund_traction_sync import update_traction_sma

            result = update_traction_sma()
            _mark_task_run("traction_sma_update")
            logger.info(
                f"[MYRA BG] Traction SMA update complete (catch-up). "
                f"Month: {result['month']}, Updated: {result['updated']}, "
                f"Skipped(no sma): {result['skipped_no_sma']}"
            )
        except Exception as e:
            logger.error(f"[MYRA BG] Traction SMA update (catch-up) failed: {e}")
        finally:
            unregister(tid)

    if ctx.shutdown_event.is_set():
        return

    tid = register("Traction SMA update", task_type="indefinite")
    try:
        while not ctx.shutdown_event.is_set():
            try:
                if _is_task_due("traction_sma_update", interval_days=1):
                    from myra_app.fund_traction_sync import update_traction_sma

                    logger.info("[MYRA BG] Traction SMA update due – running...")
                    result = update_traction_sma()
                    _mark_task_run("traction_sma_update")
                    logger.info(
                        f"[MYRA BG] Traction SMA update complete. "
                        f"Month: {result['month']}, Updated: {result['updated']}, "
                        f"Skipped(no sma): {result['skipped_no_sma']}"
                    )
            except Exception as e:
                logger.error(f"[MYRA BG] Traction SMA update failed: {e}")
            for _ in range(60):
                if ctx.shutdown_event.wait(1):
                    return
    finally:
        unregister(tid)
