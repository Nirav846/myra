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


def rotate_backups():
    """Copy myra_technical.db to backups/ with date stamp. Keep last 2."""
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

        # Cleanup: keep only last 2 backups
        backups = sorted(
            [f for f in os.listdir(backup_dir) if f.startswith("myra_technical_")]
        )
        while len(backups) > 2:
            old = backups.pop(0)
            os.remove(os.path.join(backup_dir, old))
            print(f"[MYRA BACKUP] Removed old backup: {old}")
    except Exception as e:
        logger.warning(f"Backup failed: {e}")
