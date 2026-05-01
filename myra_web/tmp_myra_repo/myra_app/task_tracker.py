import threading
import time
from datetime import datetime, timedelta

_tasks = []
_lock = threading.Lock()


def register(name, status="starting", task_type="indefinite"):
    with _lock:
        tid = len(_tasks) + 1
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
        _tasks.append(task)
        return tid


def update(tid, status=None, progress=None, eta=None):
    with _lock:
        for t in _tasks:
            if t["id"] == tid:
                if status is not None:
                    t["status"] = status
                if progress is not None:
                    t["progress"] = progress
                if eta is not None:
                    t["eta"] = eta
                # Batch tasks that are writing to DB are unsafe to exit
                if t["type"] == "batch" and progress is not None and progress > 0:
                    t["safe_to_exit"] = False
                break


def unregister(tid):
    with _lock:
        for t in _tasks[:]:
            if t["id"] == tid:
                # Keep the task visible for 5 more seconds (auto-expire)
                t["status"] = "Done"
                t["expiry"] = datetime.now() + timedelta(seconds=5)
                break


def get_active_tasks():
    with _lock:
        now = datetime.now()
        # Auto-remove expired tasks
        for t in _tasks[:]:
            if "expiry" in t and now > t["expiry"]:
                _tasks.remove(t)
            # Quick tasks (indefinite) disappear after 30s if not updated
            if t["type"] == "indefinite" and "expiry" not in t and now - t["started"] > timedelta(seconds=30):
                _tasks.remove(t)
        return list(_tasks)
