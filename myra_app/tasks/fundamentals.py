"""Task 6: Fundamentals Sync — weekly full sync + daily lightweight MS sync.

Single units of work: scheduling is owned by myra_app.tasks.executor.
`run_daily` keeps its weekday/18:00 gate (business rule); the executor
retries harmlessly until the gate opens because mark_on_success=False for
this task in the registry.
"""

import logging

from myra_app.tasks.context import TaskContext
from myra_app.utils.task_utils import _is_task_overdue, _mark_task_run, now_ist

logger = logging.getLogger(__name__)


def run(ctx: TaskContext):
    """Runs one weekly fundamentals full sync. Returns early on shutdown."""
    from myra_app.task_tracker import register, unregister

    if ctx.shutdown_event.is_set():
        return

    tid = register("Fundamentals sync", task_type="one-shot")
    try:
        logger.info("[MYRA BG] Fundamentals sync running...")
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
        raise
    finally:
        unregister(tid)


def run_daily(ctx: TaskContext):
    """One lightweight fundamentals sync attempt (weekdays after 6 PM IST).

    Returns without doing work outside the window; the executor's due-check
    keeps polling until a real sync succeeds (registry mark_on_success=False).
    """
    from myra_app.task_tracker import register, unregister

    if ctx.shutdown_event.is_set():
        return

    ist_now = now_ist()
    if ist_now.weekday() >= 5 or ist_now.hour < 18:
        return

    tid = register("Fundamentals daily", task_type="one-shot")
    try:
        logger.info("[MYRA BG] Daily lightweight fundamentals sync running...")
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
                shares_result = fs._refresh_stale_shares_outstanding()
                _mark_task_run("shares_outstanding_sync")
                logger.info(f"[MYRA BG] Stale shares refresh complete: {shares_result}")
            except Exception as e:
                logger.warning(f"[MYRA BG] Stale shares refresh failed: {e}")
    except Exception as e:
        logger.error(f"[MYRA BG] Fundamentals daily sync failed: {e}")
        raise
    finally:
        unregister(tid)
