"""Task 12: Traction SMA Daily Update — daily SMA/pct_vs_sma refresh.

Single unit of work: scheduling is owned by myra_app.tasks.executor.
"""

import logging

from myra_app.tasks.context import TaskContext
from myra_app.utils.task_utils import _mark_task_run

logger = logging.getLogger(__name__)


def run(ctx: TaskContext):
    """Runs one traction SMA/pct_vs_sma refresh. Returns early on shutdown."""
    from myra_app.task_tracker import register, unregister

    if ctx.shutdown_event.is_set():
        return

    tid = register("Traction SMA update", task_type="one-shot")
    try:
        logger.info("[MYRA BG] Traction SMA update running...")
        from myra_app.fund_traction_sync import update_traction_sma

        result = update_traction_sma()
        # Audit fix: update_traction_sma returns a result dict with
        # success=False on partial/data errors WITHOUT raising. The executor's
        # exception-handler cannot catch this. Report it explicitly so the
        # dashboard doesn't see a stale "success" row.
        if result.get("success") is False:
            _mark_task_run(
                "traction_sma_update",
                status="failed",
                error_message=(
                    result.get("error") or "update_traction_sma reported failure"
                )[:500],
            )
        else:
            _mark_task_run("traction_sma_update")
        logger.info(
            f"[MYRA BG] Traction SMA update complete. "
            f"Month: {result['month']}, Updated: {result['updated']}, "
            f"Skipped(no sma): {result['skipped_no_sma']}"
        )
    except Exception as e:
        logger.error(f"[MYRA BG] Traction SMA update failed: {e}")
        raise
    finally:
        unregister(tid)
