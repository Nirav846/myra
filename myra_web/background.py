"""
MYRA background task helper.

Extracted from myra_fastapi_server.py (Phase 3 of monolith refactor).
Runs fn(*args, **kwargs) in a daemon thread and registers the task in the
task_tracker so API consumers can poll progress.
"""

import threading


def _spawn_task(name, fn, *args, **kwargs):
    """Run fn(*args, **kwargs) in a daemon thread; register in task_tracker; return task id."""
    from myra_app.task_tracker import create_task, update, unregister

    tid = create_task(name)

    def _wrapped():
        try:
            fn(*args, **kwargs)
            update(tid, status="completed")
        except Exception as e:
            update(tid, status=f"error: {e}")
        finally:
            unregister(tid)

    threading.Thread(target=_wrapped, name=f"myra-task-{name}", daemon=True).start()
    return tid
