"""
Database backup utility - weekly rotation of technical_data.
Keeps the last 2 backups, deleting older ones.
"""

import logging
import os
import shutil
from datetime import date

from myra_app.constants import DB_DIR

logger = logging.getLogger("myra.db_backup")


def rotate_backups(task_id: int = None, keep_last_days: int = 2):
    """Copy myra_technical.db to backups/ with date stamp. Keep last N backups."""
    from myra_app.task_tracker import update

    if task_id is not None:
        update(task_id, "Creating database backup…")

    src = os.path.join(DB_DIR, "myra_technical.db")
    if not os.path.exists(src):
        return

    backup_dir = os.path.join(DB_DIR, "backups")
    os.makedirs(backup_dir, exist_ok=True)

    stamp = date.today().strftime("%Y-%m-%d")  # noqa: PG-STRFTIME
    dest = os.path.join(backup_dir, f"myra_technical_{stamp}.db")

    try:
        shutil.copy2(src, dest)
        print(f"[MYRA BACKUP] Saved {dest}")

        if task_id is not None:
            update(task_id, f"Backup saved as {stamp}")

        backups = sorted(
            [f for f in os.listdir(backup_dir) if f.startswith("myra_technical_")]
        )
        while len(backups) > keep_last_days:
            old = backups.pop(0)
            os.remove(os.path.join(backup_dir, old))
            print(f"[MYRA BACKUP] Removed old backup: {old}")

        if task_id is not None:
            update(task_id, "Backup rotation complete")
    except Exception as e:
        logger.warning(f"Backup failed: {e}")
