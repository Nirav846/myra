"""Task 5: Index Sync — weekly NIFTY constituents heal + benchmark refresh.

Single unit of work: scheduling is owned by myra_app.tasks.executor.
"""

import logging

from myra_app.tasks.context import TaskContext
from myra_app.utils.task_utils import _mark_task_run

logger = logging.getLogger(__name__)


def run(ctx: TaskContext):
    """Syncs NIFTY indices once. Returns early on shutdown."""
    from myra_app.task_tracker import register, unregister

    if ctx.shutdown_event.is_set():
        return

    tid = register("Index sync", task_type="one-shot")
    try:
        logger.info("[MYRA BG] Index sync running...")
        from myra_app.utils.index_sync import (
            heal_index_if_stale,
            sync_index_constituents,
        )

        for idx in ["NIFTY 50", "NIFTY 500", "NIFTY SMALLCAP 250"]:
            sync_index_constituents(idx, task_id=tid)

        heal_index_if_stale("NIFTY 500", expected_count=500)

        # Refresh Nifty benchmark closes for RS calculations
        try:
            from myra_app.utils.index_sync import sync_nifty_benchmarks

            sync_nifty_benchmarks()
            logger.info("[MYRA BG] Nifty benchmark data refreshed")
        except Exception as e:
            logger.warning(f"[MYRA BG] Nifty benchmark refresh failed: {e}")

        _mark_task_run("index_sync")
        logger.info("[MYRA BG] Index sync complete.")
    except Exception as e:
        logger.error(f"[MYRA BG] Index sync/heal failed: {e}")
        raise
    finally:
        unregister(tid)
