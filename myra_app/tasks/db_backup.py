"""Task 8: Daily DB Backup — nightly backup + PRAGMA optimize across sidecars."""

import logging
import os

from myra_app.constants import DB_DIR
from myra_app.librarian_core import LibrarianCore
from myra_app.tasks.context import TaskContext
from myra_app.utils.task_utils import _is_task_due, _mark_task_run

logger = logging.getLogger(__name__)


def run(ctx: TaskContext):
    """Runs a full DB backup daily at midnight IST and keeps last 7 daily backups."""
    from myra_app.task_tracker import register, unregister

    tid = register("DB backup", task_type="indefinite")
    try:
        while not ctx.shutdown_event.is_set():
            try:
                if _is_task_due("db_backup", interval_days=1):
                    from myra_app.utils.db_backup import rotate_backups

                    logger.info("[MYRA BG] Running nightly DB backup...")
                    rotate_backups(task_id=tid, keep_last_days=7)
                    logger.info("[MYRA BG] Nightly DB backup complete.")
                    _mark_task_run("db_backup")

                    # Optimize databases for query performance
                    import sqlite3

                    for db_name, db_file in LibrarianCore.DB_MAP.items():
                        db_path = os.path.join(DB_DIR, db_file)
                        if os.path.exists(db_path):
                            try:
                                conn = sqlite3.connect(db_path)
                                conn.execute("PRAGMA optimize")  # noqa: PG-NPLUS1
                                conn.execute("ANALYZE")  # noqa: PG-NPLUS1
                                conn.close()
                            except Exception:
                                pass
                    logger.info("[MYRA BG] Database optimization complete")

                # Check again in 30 minutes
                # PERFORMANCE IMPROVEMENT: Replace long wait with responsive loop
                for _ in range(30):  # 30 * 60 = 1800 seconds total
                    if ctx.shutdown_event.wait(60):
                        return
            except Exception as e:
                logger.error(f"[MYRA BG] Daily DB backup failed: {e}")
                # PERFORMANCE IMPROVEMENT: Replace long wait with responsive loop
                for _ in range(30):  # 30 * 60 = 1800 seconds total
                    if ctx.shutdown_event.wait(60):
                        return
    finally:
        unregister(tid)
