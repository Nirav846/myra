"""Task 10: Fund Traction Sync — monthly fund-holdings traction from GitHub Pages.

Single unit of work: scheduling is owned by myra_app.tasks.executor.
"""

import logging

from myra_app.tasks.context import TaskContext
from myra_app.utils.task_utils import _mark_task_run

logger = logging.getLogger(__name__)


def run(ctx: TaskContext):
    """Runs one fund traction sync. Returns early on shutdown."""
    from myra_app.task_tracker import register, unregister

    if ctx.shutdown_event.is_set():
        return

    tid = register("Fund traction sync", task_type="one-shot")
    try:
        logger.info("[MYRA BG] Fund traction sync running...")
        from myra_app.fund_traction_sync import sync_fund_traction

        result = sync_fund_traction()
        _mark_task_run("fund_traction_sync")
        logger.info(
            f"[MYRA BG] Fund traction sync complete. "
            f"Months: {result['months_synced']}, Rows: {result['rows_inserted']}"
        )
    except Exception as e:
        logger.error(f"[MYRA BG] Fund traction sync failed: {e}")
        raise
    finally:
        unregister(tid)
