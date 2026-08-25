"""Task 8: DB Backup — nightly backup + PRAGMA optimize across sidecars.

Single unit of work: scheduling is owned by myra_app.tasks.executor.
"""

import logging
import os
import sqlite3

from myra_app.constants import DB_DIR
from myra_app.librarian_core import LibrarianCore
from myra_app.tasks.context import TaskContext
from myra_app.utils.task_utils import _mark_task_run

logger = logging.getLogger(__name__)


def run(ctx: TaskContext):
    """Runs one full DB backup + optimization pass, keeping last 7 daily backups."""
    from myra_app.task_tracker import register, unregister

    if ctx.shutdown_event.is_set():
        return

    tid = register("DB backup", task_type="one-shot")
    try:
        from myra_app.utils.db_backup import rotate_backups

        logger.info("[MYRA BG] Running nightly DB backup...")
        rotate_backups(task_id=tid, keep_last_days=7)
        logger.info("[MYRA BG] Nightly DB backup complete.")
        _mark_task_run("db_backup")

        # Optimize databases for query performance
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
    except Exception as e:
        logger.error(f"[MYRA BG] Daily DB backup failed: {e}")
        raise
    finally:
        unregister(tid)
