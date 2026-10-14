import json
import logging
import os
import sqlite3
import threading
from datetime import datetime, timedelta

from myra_app.constants import DB_DIR

logger = logging.getLogger(__name__)

#: Reentrant: the public helpers call ``_get_conn()`` *while already holding*
#: the lock, so that the connection lookup and the statement that uses it are one
#: atomic step. That is what stops ``reset_connection()`` (test isolation) from
#: closing a handle another thread is executing on.
_lock = threading.RLock()

_conn = None
_use_fallback = False

_fallback_tasks = []


def reset_connection() -> None:
    """Drop the cached connection, closing it under ``_lock``.

    Test isolation calls this between tests (``tests/conftest.py``). SQLite
    connections created with ``check_same_thread=False`` may be *used* from any
    thread, but closing one while another thread is mid-``execute`` frees the
    underlying handle and segfaults the interpreter. Doing the swap **and** the
    close under the same lock the writers take makes that impossible.

    Keeping this in the module (instead of conftest closing ``_conn`` directly)
    is what preserves the Phase 7 guarantee: the cached handle is still dropped
    so it can never keep pointing at a live database.
    """
    global _conn, _use_fallback
    with _lock:
        conn, _conn = _conn, None
        _use_fallback = False
        _fallback_tasks.clear()
    if conn is not None:
        try:
            conn.close()
        except Exception:  # noqa: BLE001 - cleanup must never raise
            pass


def _get_conn():
    global _conn, _use_fallback
    if _use_fallback:
        return None
    if _conn is not None:
        return _conn
    meta_path = os.path.join(DB_DIR, "myra_metadata.db")
    try:
        c = sqlite3.connect(meta_path, check_same_thread=False)
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=NORMAL")
        c.execute("""
            CREATE TABLE IF NOT EXISTS task_registry (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                status TEXT DEFAULT 'running',
                message TEXT DEFAULT '',
                progress REAL,
                eta TEXT,
                task_type TEXT DEFAULT 'indefinite',
                safe_to_exit INTEGER DEFAULT 1,
                started_at TEXT NOT NULL,
                updated_at TEXT,
                expiry TEXT,
                data TEXT DEFAULT '{}'
            )
        """)
        _conn = c
        return _conn
    except Exception as e:
        logger.warning(f"task_tracker: DB connection failed, using in-memory: {e}")
        _use_fallback = True
        return None


def register(name, status="starting", task_type="indefinite"):
    with _lock:
        conn = _get_conn()
        if conn is not None:
            now = datetime.now().isoformat()
            safe = 1 if task_type in ("indefinite",) else 0
            cur = conn.execute(
                """INSERT INTO task_registry
                   (name, status, message, task_type, safe_to_exit, started_at, updated_at)
                   VALUES (?, ?, '', ?, ?, ?, ?)""",
                (name, status, task_type, safe, now, now),
            )
            conn.commit()
            return cur.lastrowid
        tid = len(_fallback_tasks) + 1
        task = {
            "id": tid,
            "name": name,
            "status": status,
            "progress": None,
            "eta": None,
            "type": task_type,
            "started": datetime.now(),
            "safe_to_exit": task_type in ("indefinite",),
        }
        _fallback_tasks.append(task)
        return tid


def update(tid, status=None, progress=None, eta=None):
    with _lock:
        conn = _get_conn()
        if conn is not None:
            now = datetime.now().isoformat()
            updates = ["updated_at = ?"]
            params = [now]
            if status is not None:
                updates.append("message = ?")
                params.append(status)
            if progress is not None:
                updates.append("progress = ?")
                params.append(progress)
                updates.append(
                    "safe_to_exit = CASE WHEN task_type = 'batch' AND ? > 0 THEN 0 ELSE safe_to_exit END"
                )
                params.append(progress)
            if eta is not None:
                updates.append("eta = ?")
                params.append(eta)
            params.append(tid)
            conn.execute(
                f"UPDATE task_registry SET {', '.join(updates)} WHERE id = ?",
                params,
            )
            conn.commit()
            return
        for t in _fallback_tasks:
            if t["id"] == tid:
                if status is not None:
                    t["status"] = status
                if progress is not None:
                    t["progress"] = progress
                if eta is not None:
                    t["eta"] = eta
                if t["type"] == "batch" and progress is not None and progress > 0:
                    t["safe_to_exit"] = False
                break


def unregister(tid):
    with _lock:
        conn = _get_conn()
        if conn is not None:
            now = datetime.now().isoformat()
            expiry = (datetime.now() + timedelta(seconds=5)).isoformat()
            conn.execute(
                "UPDATE task_registry SET status = 'Done', updated_at = ?, expiry = ? WHERE id = ?",
                (now, expiry, tid),
            )
            conn.commit()
            return
        for t in _fallback_tasks[:]:
            if t["id"] == tid:
                t["status"] = "Done"
                t["expiry"] = datetime.now() + timedelta(seconds=5)
                break


def get_active_tasks():
    with _lock:
        conn = _get_conn()
        if conn is not None:
            now = datetime.now().isoformat()
            conn.execute(
                "DELETE FROM task_registry WHERE expiry IS NOT NULL AND expiry < ?",
                (now,),
            )
            conn.commit()
            rows = conn.execute("""SELECT id, name, status, message, progress, eta,
                          task_type, started_at, expiry
                   FROM task_registry
                   ORDER BY id DESC LIMIT 50""").fetchall()
            result = []
            for r in rows:
                task = {
                    "id": r[0],
                    "name": r[1],
                    "status": r[2] if r[2] else r[3],
                    "progress": r[4],
                    "eta": r[5],
                    "type": r[6],
                    "started": datetime.fromisoformat(r[7]) if r[7] else datetime.min,
                    "safe_to_exit": r[6] in ("indefinite",),
                }
                result.append(task)  # noqa: PG-APPEND
            return result
        now = datetime.now()
        for t in _fallback_tasks[:]:
            if "expiry" in t and now > t["expiry"]:
                _fallback_tasks.remove(t)
            if (
                t["type"] == "indefinite"
                and "expiry" not in t
                and now - t["started"] > timedelta(seconds=30)
            ):
                _fallback_tasks.remove(t)
        return list(_fallback_tasks)


# --- New public API ---


def create_task(name):
    return register(name, status="running", task_type="batch")


def get_task(tid):
    with _lock:
        conn = _get_conn()
        if conn is not None:
            row = conn.execute(
                """SELECT id, name, status, message, progress, eta,
                          task_type, started_at, updated_at, expiry, data
                   FROM task_registry WHERE id = ?""",
                (tid,),
            ).fetchone()
            if row is None:
                return None
            return {
                "id": row[0],
                "name": row[1],
                "status": row[2],
                "message": row[3] or "",
                "progress": row[4],
                "eta": row[5],
                "task_type": row[6],
                "started_at": row[7],
                "updated_at": row[8],
                "expiry": row[9],
                "data": json.loads(row[10]) if row[10] else {},
            }
        for t in _fallback_tasks:
            if t["id"] == tid:
                return {
                    "id": t["id"],
                    "name": t["name"],
                    "status": t["status"],
                    "message": t.get("status", ""),
                    "progress": t.get("progress"),
                    "eta": t.get("eta"),
                    "task_type": t.get("type", ""),
                    "started_at": t["started"].isoformat(),
                    "updated_at": None,
                    "expiry": t.get("expiry"),
                    "data": {},
                }
        return None


def list_tasks(limit=50):
    with _lock:
        conn = _get_conn()
        if conn is not None:
            rows = conn.execute(
                """SELECT id, name, status, message, progress, eta,
                          task_type, started_at, updated_at, data
                   FROM task_registry ORDER BY id DESC LIMIT ?""",
                (limit,),
            ).fetchall()
            return [
                {
                    "id": r[0],
                    "name": r[1],
                    "status": r[2],
                    "message": r[3] or "",
                    "progress": r[4],
                    "eta": r[5],
                    "task_type": r[6],
                    "started_at": r[7],
                    "updated_at": r[8],
                    "data": json.loads(r[9]) if r[9] else {},
                }
                for r in rows
            ]
        return [
            {
                "id": t["id"],
                "name": t["name"],
                "status": t["status"],
                "message": t.get("status", ""),
                "progress": t.get("progress"),
                "eta": t.get("eta"),
                "task_type": t.get("type", ""),
                "started_at": t["started"].isoformat(),
                "updated_at": None,
                "data": {},
            }
            for t in _fallback_tasks[-limit:]
        ]
