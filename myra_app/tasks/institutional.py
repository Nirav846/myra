"""Task 7: Institutional Sync — weekly bulk/block deals refresh from NSE.

Single unit of work: scheduling is owned by myra_app.tasks.executor.
"""

import logging

from myra_app.tasks.context import TaskContext
from myra_app.utils.task_utils import _mark_task_run

logger = logging.getLogger(__name__)


def run(ctx: TaskContext):
    """Syncs bulk/block deals once. Returns early on shutdown."""
    from myra_app.task_tracker import register, unregister

    if ctx.shutdown_event.is_set():
        return

    tid = register("Institutional sync", task_type="one-shot")
    try:
        logger.info("[MYRA BG] Institutional sync running...")
        from myra_app.utils.institutional_sync import sync_institutional_data

        sync_institutional_data(task_id=tid)
        _mark_task_run("institutional_sync")
        logger.info("[MYRA BG] Institutional sync complete.")
    except Exception as e:
        logger.error(f"[MYRA BG] Institutional sync failed: {e}")
        raise
    finally:
        unregister(tid)
