"""Task 1: DB Doctor — startup auto-fix health check."""

import logging

from myra_app.tasks.context import TaskContext

logger = logging.getLogger(__name__)


def run(ctx: TaskContext):
    """
    Runs db_doctor in auto-fix mode on startup.
    Skips if shutdown is requested.
    """
    from myra_app.task_tracker import register, unregister

    if ctx.shutdown_event.is_set():
        return
    tid = register("DB health check")
    try:
        logger.info("[MYRA BG] Running DB health check...")
        from tools.db_doctor import DbDoctor

        doctor = DbDoctor()
        doctor.run()
        logger.info("[MYRA BG] DB health check complete.")
    except Exception as e:
        logger.error(f"[MYRA BG] DB Doctor failed: {e}")
    finally:
        unregister(tid)
