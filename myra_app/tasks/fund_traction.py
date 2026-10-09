"""Task 10: Fund Traction Sync — monthly fund-holdings traction from GitHub Pages.

Single unit of work: scheduling is owned by myra_app.tasks.executor.
"""

import logging

from myra_app.tasks.context import TaskContext
from myra_app.utils.task_utils import _mark_task_run

logger = logging.getLogger(__name__)


def run(ctx: TaskContext):
    """Runs one fund traction sync. Returns early on shutdown.

    Smart gate: a cheap HEAD-only probe (``has_unsynced_month``) first checks
    whether any newly-published month is available upstream but not yet in our
    DB. If there is nothing new, we return WITHOUT marking sync_log, so the
    executor keeps polling every day (``mark_on_success=False``) and the real
    sync only fires within ~a day of a new month's report landing — instead of
    on a fixed 30-day timer that always lags.
    """
    from myra_app.task_tracker import register, unregister

    if ctx.shutdown_event.is_set():
        return

    # Cheap read-only gate: nothing new upstream since our watermark.
    from myra_app.fund_traction_sync import has_unsynced_month

    pending = has_unsynced_month()
    if not pending:
        logger.debug(
            "[MYRA BG] Fund traction sync: no new month upstream yet — "
            "skipping (will re-check next poll)."
        )
        return

    tid = register("Fund traction sync", task_type="one-shot")
    try:
        logger.info(
            f"[MYRA BG] Fund traction sync running (new month(s): {pending})..."
        )
        from myra_app.fund_traction_sync import sync_fund_traction

        result = sync_fund_traction()
        # Audit fix: sync_fund_traction returns success=False on
        # empty/no-data or probe-failure WITHOUT raising. Report explicitly so
        # the dashboard doesn't show a phantom success.
        if result.get("success") is False:
            _mark_task_run(
                "fund_traction_sync",
                status="failed",
                error_message=(
                    result.get("error") or "sync_fund_traction reported failure"
                )[:500],
            )
        else:
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
