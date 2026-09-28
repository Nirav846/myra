"""Weekly task: append-only point-in-time archive capture.

Append-only: writes only to myra_app/db/myra_archive.db. Never writes to
the valuation DB (owned by the upstox fetcher; fundamental writers are
disabled). Scheduling is owned by myra_app.tasks.executor.
"""

import logging

from myra_app.tasks.context import TaskContext
from myra_app.utils.task_utils import _mark_task_run

logger = logging.getLogger(__name__)


def run(ctx: TaskContext):
    """Runs one archive capture. Returns early on shutdown."""
    from myra_app.task_tracker import register, unregister

    if ctx.shutdown_event.is_set():
        return

    tid = register("Point-in-time archive", task_type="one-shot")
    try:
        logger.info("[MYRA BG] Point-in-time archive running...")
        from myra_app.point_in_time_archive import run_archive

        result = run_archive()
        fund = result.get("fundamentals", {})
        raw = result.get("raw", {})
        errors = "; ".join(
            e
            for e in (
                fund.get("error"),
                raw.get("error"),
            )
            if e
        )
        if errors:
            _mark_task_run(
                "point_in_time_archive",
                status="failed",
                error_message=errors[:500],
            )
        else:
            _mark_task_run("point_in_time_archive")
        logger.info(
            "[MYRA BG] Point-in-time archive complete. "
            f"Fundamentals archived: {fund.get('rows_archived', 0)}, "
            f"unchanged: {fund.get('rows_unchanged', 0)}; "
            f"raw files archived: {raw.get('files_archived', 0)}, "
            f"unchanged: {raw.get('files_unchanged', 0)}"
        )
    except Exception as e:
        logger.error(f"[MYRA BG] Point-in-time archive failed: {e}")
        raise
    finally:
        unregister(tid)
