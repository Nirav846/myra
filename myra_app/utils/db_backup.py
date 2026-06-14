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


def rotate_backups(task_id: int = None, keep_last_days: int = 7):
    """Copy all MYRA databases to backups/ with date stamp. Keep last N backups per DB."""
    from myra_app.task_tracker import update
    from myra_app.librarian_core import LibrarianCore

    if task_id is not None:
        update(task_id, "Creating database backups…")

    backup_dir = os.path.join(DB_DIR, "backups")
    os.makedirs(backup_dir, exist_ok=True)
    stamp = date.today().isoformat()

    for db_key, db_name in LibrarianCore.DB_MAP.items():
        db_path = os.path.join(DB_DIR, db_name)
        if not os.path.exists(db_path):
            logger.warning(f"Backup skipped – DB not found: {db_path}")
            continue

        dest = os.path.join(backup_dir, f"{db_key}_{stamp}.db")
        try:
            shutil.copy2(db_path, dest)
            print(f"[MYRA BACKUP] Saved {db_key} -> {dest}")

            # rotate old backups for this specific DB
            backups = sorted(
                [f for f in os.listdir(backup_dir) if f.startswith(db_key + "_")]
            )
            while len(backups) > keep_last_days:
                old = backups.pop(0)
                os.remove(os.path.join(backup_dir, old))
                print(f"[MYRA BACKUP] Removed old backup: {old}")

        except Exception as e:
            logger.error(f"Backup failed for {db_key}: {e}")

    if task_id is not None:
        update(task_id, "Backup rotation complete")
