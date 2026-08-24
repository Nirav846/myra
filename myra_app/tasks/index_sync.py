"""Task 5: Index Sync — weekly NIFTY constituents heal + benchmark refresh."""

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
    """Syncs NIFTY indices from NSE every 7 days. Runs immediately if overdue."""
    from myra_app.task_tracker import register, unregister

    if ctx.shutdown_event.is_set():
        return

    if _is_task_overdue("index_sync", days=7):
        tid = register("Index sync", task_type="one-shot")
        try:
            logger.info("[MYRA BG] Index sync overdue – running now...")
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
            logger.info("[MYRA BG] Index sync complete (catch-up).")
        except Exception as e:
            logger.error(f"[MYRA BG] Index sync (catch-up) failed: {e}")
        finally:
            unregister(tid)

    if ctx.shutdown_event.is_set():
        return

    tid = register("Index sync", task_type="indefinite")
    try:
        while not ctx.shutdown_event.is_set():
            try:
                if _is_task_due("index_sync", WEEKLY_INTERVAL_DAYS):
                    logger.info("[MYRA BG] Index sync due – running...")
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
            except Exception as e:
                logger.error(f"[MYRA BG] Index sync/heal failed: {e}")
            for _ in range(60):
                if ctx.shutdown_event.wait(60):
                    return
    finally:
        unregister(tid)
