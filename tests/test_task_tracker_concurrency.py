"""``task_tracker`` must be safe when a connection is reset while in use.

CI (Linux, Python 3.12) segfaulted with exit 139 during the Phase 8 run:

    Current thread ... File "myra_app/task_tracker.py", line 134 in unregister
    Thread ...         File "tests/conftest.py", line 256 in _reset_cached_connections

``task_tracker`` caches its SQLite connection in a module global created with
``check_same_thread=False``. SQLite allows such a connection to be *used* from
any thread, but closing it while another thread is inside ``execute`` frees the
underlying handle and kills the process. Test isolation resets that cache
between tests, so a background thread still running (e.g. a ``myra_web``
task thread) could be executing on the handle at the moment it was closed.

The fix keeps the reset inside the module (``reset_connection``), taking the
same lock the writers take. These tests pin that contract; they cannot assert
"no segfault" directly, so they assert the *invariant* that makes it impossible
and exercise the race under repetition.
"""

import threading

import pytest

from myra_app import task_tracker as tt

pytestmark = pytest.mark.usefixtures("fixture_db_dir")


def test_lock_is_reentrant():
    """The helpers call ``_get_conn()`` while already holding the lock."""
    assert hasattr(tt._lock, "_release_save"), "task_tracker lock must be an RLock"
    acquired_outside = tt._lock.acquire()
    try:
        # A plain Lock would deadlock here (non-reentrant).
        tt._get_conn()
    finally:
        tt._lock.release()
    assert acquired_outside


def test_reset_connection_clears_state(fixture_db_dir):
    tt._conn = None
    tt._use_fallback = False
    tt._fallback_tasks = []
    # Force a real cached connection, then reset it.
    tt.register("probe", status="running")
    assert tt._conn is not None, "expected task_tracker to cache a connection"
    tt.reset_connection()
    assert tt._conn is None
    assert tt._use_fallback is False
    assert tt._fallback_tasks == []


def test_concurrent_writes_and_reset_do_not_crash():
    """Hammer the exact interleaving that segfaulted CI.

    Many threads registering/unregistering (i.e. executing on the cached
    handle) while another thread repeatedly resets it. Under the old code this
    freed a handle mid-``execute``; the test asserts it completes cleanly and
    that no writer ever touched a closed connection.
    """
    errors: list[BaseException] = []
    stop = threading.Event()
    start = threading.Barrier(5)

    def writer(idx: int) -> None:
        try:
            start.wait(timeout=10)
            for _ in range(200):
                if stop.is_set():
                    break
                tid = tt.register(f"probe-{idx}", status="running")
                tt.update(tid, status="completed")
                tt.unregister(tid)
                tt.get_task(tid)
        except BaseException as exc:  # noqa: BLE001 - surfaced below
            errors.append(exc)

    def resetter() -> None:
        try:
            start.wait(timeout=10)
            for _ in range(200):
                if stop.is_set():
                    break
                tt.reset_connection()
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(4)]
    threads.append(threading.Thread(target=resetter))
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    stop.set()

    assert not any(t.is_alive() for t in threads), "a worker thread hung"
    assert not errors, f"concurrent reset/use raised: {errors!r}"


def test_reset_is_idempotent_and_safe_when_never_opened():
    tt._conn = None
    tt.reset_connection()
    tt.reset_connection()
    assert tt._conn is None
