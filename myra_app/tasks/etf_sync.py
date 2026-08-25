"""Task 4: ETF Sync — weekly ETF blocklist refresh from NSE.

Single unit of work: scheduling is owned by myra_app.tasks.executor.
"""

import logging

from myra_app.tasks.context import TaskContext
from myra_app.utils.task_utils import _mark_task_run

logger = logging.getLogger(__name__)


def run(ctx: TaskContext):
    """Syncs ETF blocklist from NSE once. Returns early on shutdown."""
    from myra_app.task_tracker import register, unregister

    if ctx.shutdown_event.is_set():
        return

    tid = register("ETF sync", task_type="one-shot")
    try:
        logger.info("[MYRA BG] ETF sync running...")
        from myra_app.utils.etf_sync import sync_etf_list

        sync_etf_list(task_id=tid)
        _mark_task_run("etf_sync")
        logger.info("[MYRA BG] ETF sync complete.")
    except Exception as e:
        logger.error(f"[MYRA BG] ETF sync failed: {e}")
        raise
    finally:
        unregister(tid)
