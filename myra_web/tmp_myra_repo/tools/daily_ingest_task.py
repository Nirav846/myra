"""Standalone script for Task Scheduler – smart daily ingestion with weekend/holiday awareness."""

import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

# Project root and path setup
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
os.chdir(PROJECT_ROOT)

# IST timezone (same as background_orchestrator)
IST = timezone(timedelta(hours=5, minutes=30))
now_ist = datetime.now(timezone.utc).astimezone(IST)

# 1. Skip weekends
if now_ist.weekday() >= 5:
    print(f"[MYRA Scheduler] {now_ist.date()} is a weekend – skipping ingestion.")
    sys.exit(0)

# 2. Skip before 6 PM IST (data not yet available)
if now_ist.hour < 18:
    print(
        f"[MYRA Scheduler] Market data not yet available (IST: {now_ist.strftime('%H:%M')}). Skipping."  # noqa: PG-STRFTIME
    )
    sys.exit(0)

# 3. Skip if already ingested today
try:
    from myra_app.librarian_core import LibrarianCore

    meta_db = os.path.join(PROJECT_ROOT, "myra_app", "db", LibrarianCore.DB_MAP["meta"])
    with sqlite3.connect(meta_db) as conn:
        res = conn.execute(
            "SELECT value FROM metadata WHERE key = 'last_sync_date'"
        ).fetchone()
        if res and res[0] == now_ist.date().isoformat():
            print(f"[MYRA Scheduler] Today's data already ingested. Skipping.")
            sys.exit(0)
except Exception as e:
    print(f"[MYRA Scheduler] Warning: Could not check last sync date: {e}")

# All checks passed – run ingestion
from myra_app.daily_ingestor import run_daily_update

print(f"[MYRA Scheduler] {now_ist.date()} is a trading day. Starting ingestion...")
run_daily_update()
print("[MYRA Scheduler] Ingestion complete.")
