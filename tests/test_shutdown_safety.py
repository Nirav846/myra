"""Ctrl+C / SIGTERM shutdown safety.

Shutdown is the one path where a mistake is only discovered in production, so
these tests pin the properties an operator relies on when they stop MYRA:

* the first signal takes the **full** graceful path — every task is joined so
  none is killed mid-write, pooled connections are closed, the WAL is
  checkpointed and the single-scheduler lock is released;
* the work is bounded, and a task that will not stop is reported rather than
  silently waited on forever;
* a **second** signal escalates instead of being swallowed, so nobody is ever
  trapped holding Ctrl+C against a wedged thread.

Nothing here sends a real signal or exits the process: the force-exit path is
invoked through a patched ``os._exit``.
"""

from __future__ import annotations

import signal
import threading
import time

import pytest

from myra_app import background_orchestrator as orch


@pytest.fixture(autouse=True)
def _reset_orchestrator_state():
    """Keep module-level shutdown state from leaking between tests.

    The force-exit countdown is *disarmed* here rather than left to expire. A
    countdown that outlives its test would call the real ``os._exit`` after
    monkeypatch has been undone, killing the whole pytest process (exit 130) -
    exactly the bug this fixture exists to prevent.
    """
    orch._cancel_force_exit()
    orch._shutdown_event.clear()
    orch._shutdown_started.clear()
    orch._started = False
    yield
    orch._cancel_force_exit()
    orch._shutdown_event.clear()
    orch._shutdown_started.clear()
    orch._started = False


@pytest.fixture(autouse=True)
def _no_side_effects(monkeypatch):
    """Stub the two operations that touch real state during shutdown."""
    monkeypatch.setattr(
        "myra_app.librarian_core.checkpoint_all_databases", lambda: None
    )
    monkeypatch.setattr("myra_app.utils.scheduler_lock.release", lambda: None)


# ── first signal: the full graceful path ────────────────────────────────────


def test_first_signal_sets_the_shutdown_event():
    orch._graceful_shutdown()
    assert orch._shutdown_event.is_set(), "tasks must be told to stop"


def test_first_signal_joins_every_running_task(monkeypatch):
    """A task must never be left mid-write when the process exits."""
    started = threading.Event()
    released = threading.Event()

    def _slow_task():
        started.set()
        released.wait(5)

    threads = []
    for i in range(3):
        t = threading.Thread(target=_slow_task, name=f"slow-{i}", daemon=True)
        t.start()
        started.wait(2)
        threads.append(t)

    monkeypatch.setattr(orch, "_active_tasks", threads)
    orch._graceful_shutdown()
    for t in threads:
        assert not t.is_alive(), f"{t.name} was not joined before exit"

    for t in threads:
        released.set()


def test_shutdown_reports_a_task_that_will_not_stop(monkeypatch, caplog):
    """A wedged task is surfaced, not silently waited on."""

    def _wedged():
        time.sleep(30)

    t = threading.Thread(target=_wedged, name="wedged", daemon=True)
    t.start()
    time.sleep(0.1)
    monkeypatch.setattr(orch, "_active_tasks", [t])
    monkeypatch.setattr(orch, "TASK_JOIN_SECONDS", 0.1)

    with caplog.at_level("WARNING"):
        started = time.time()
        orch._graceful_shutdown()
        elapsed = time.time() - started

    assert elapsed < 5, "shutdown must stay bounded when a task is wedged"
    assert any("did not stop" in r.message for r in caplog.records)


def test_shutdown_checkpoints_the_wal(monkeypatch):
    """A clean stop leaves no WAL backlog behind."""
    called = []
    monkeypatch.setattr(
        "myra_app.librarian_core.checkpoint_all_databases",
        lambda: called.append(True),
    )
    orch._graceful_shutdown()
    assert called, "WAL checkpoint must run on a graceful stop"


def test_shutdown_releases_the_scheduler_lock(monkeypatch):
    """Otherwise the next start is refused as a 'duplicate scheduler'."""
    released = []
    monkeypatch.setattr(
        "myra_app.utils.scheduler_lock.release", lambda: released.append(True)
    )
    orch._graceful_shutdown()
    assert released, "the single-scheduler lock must be released on shutdown"


def test_shutdown_clears_the_started_flag():
    orch._started = True
    orch._graceful_shutdown()
    assert orch._started is False


def test_a_wal_checkpoint_failure_does_not_prevent_the_rest_of_shutdown(monkeypatch):
    """One failure must not strand the scheduler lock."""

    def _boom():
        raise RuntimeError("checkpoint exploded")

    monkeypatch.setattr("myra_app.librarian_core.checkpoint_all_databases", _boom)
    released = []
    monkeypatch.setattr(
        "myra_app.utils.scheduler_lock.release", lambda: released.append(True)
    )
    orch._graceful_shutdown()
    assert released, "a checkpoint failure must not skip the lock release"


# ── second signal: escalation, so nobody is trapped ────────────────────────


def test_second_signal_escalates_to_a_forced_exit(monkeypatch):
    """The regression: a repeat Ctrl+C used to be swallowed entirely.

    The escalation window is *during* a shutdown - after a clean stop the flag
    is cleared, so the next Ctrl+C is a legitimate first signal again. So this
    reproduces the mid-shutdown state (event set, work still in progress)
    rather than calling shutdown twice sequentially.
    """
    escalations = []
    monkeypatch.setattr(
        orch, "_force_exit_on_repeat_signal", lambda: escalations.append(True)
    )
    monkeypatch.setattr(orch.os, "_exit", lambda code: escalations.append(code))

    # Mid-shutdown: threads have been told to stop but have not finished.
    orch._shutdown_event.set()
    orch._shutdown_started.set()

    orch._graceful_shutdown()

    assert escalations, "a repeat signal must escalate, not return silently"


def test_force_exit_actually_exits_after_the_grace_period(monkeypatch):
    exits = []
    monkeypatch.setattr(orch.os, "_exit", lambda code: exits.append(code))
    monkeypatch.setattr(orch, "FORCE_EXIT_GRACE_SECONDS", 0.2)

    orch._force_exit_on_repeat_signal()
    deadline = time.time() + 3
    while time.time() < deadline and not exits:
        time.sleep(0.05)

    assert exits, "the forced exit must actually fire"
    assert exits[0] == 130, "128+SIGINT is the conventional code for Ctrl+C"


def test_force_exit_runs_on_a_separate_thread(monkeypatch):
    """The handler must not block on the grace period."""
    exits = []
    monkeypatch.setattr(orch.os, "_exit", lambda code: exits.append(code))
    monkeypatch.setattr(orch, "FORCE_EXIT_GRACE_SECONDS", 5)

    started = time.time()
    orch._force_exit_on_repeat_signal()
    assert time.time() - started < 1, "must return immediately, not wait"
    assert not exits


def test_a_clean_shutdown_clears_the_escalation_flag():
    """After a full stop, the next stop is a 'first' signal again."""
    orch._graceful_shutdown()
    assert not orch._shutdown_started.is_set()


def test_a_single_signal_does_not_escalate(monkeypatch):
    """The first signal must stay fully graceful."""
    escalations = []
    monkeypatch.setattr(
        orch, "_force_exit_on_repeat_signal", lambda: escalations.append(True)
    )
    monkeypatch.setattr(orch.os, "_exit", lambda code: escalations.append(code))

    orch._graceful_shutdown()

    assert escalations == [], "a single Ctrl+C must never force an exit"


def test_a_pending_forced_exit_is_cancelled_by_a_clean_stop(monkeypatch):
    """The graceful path finishing first must disarm the countdown.

    Otherwise a process that was already shutting down cleanly gets killed by a
    forced exit nobody needs any more.
    """
    exits = []
    monkeypatch.setattr(orch.os, "_exit", lambda code: exits.append(code))
    monkeypatch.setattr(orch, "FORCE_EXIT_GRACE_SECONDS", 0.3)

    orch._force_exit_on_repeat_signal()
    orch._cancel_force_exit()

    time.sleep(0.6)
    assert exits == [], "a cancelled countdown must never fire"


def test_repeat_signals_do_not_stack_countdowns(monkeypatch):
    """A third Ctrl+C must not arm a second countdown behind the first."""
    starts = []
    real_thread = orch.threading.Thread

    class _CountingThread(real_thread):
        def start(self):
            starts.append(self.name)
            super().start()

    monkeypatch.setattr(orch.threading, "Thread", _CountingThread)
    orch._cancel_force_exit()
    orch._force_exit_on_repeat_signal()
    first = len(starts)
    orch._force_exit_on_repeat_signal()
    assert len(starts) == first, "only one countdown may be armed"
    orch._cancel_force_exit()


# ── signal registration ─────────────────────────────────────────────────────


def test_sigint_and_sigterm_are_installed_by_the_registration_hook():
    """Both Ctrl+C and a non-forced kill must reach the same graceful path."""
    original_int = signal.getsignal(signal.SIGINT)
    original_term = signal.getsignal(signal.SIGTERM)
    try:
        orch._register_signals()
        assert signal.getsignal(signal.SIGINT) is orch._graceful_shutdown
        assert signal.getsignal(signal.SIGTERM) is orch._graceful_shutdown
    finally:
        signal.signal(signal.SIGINT, original_int)
        signal.signal(signal.SIGTERM, original_term)


def test_run_pipeline_does_not_double_signal_the_orchestrator():
    """`run_pipeline`'s `finally` must not look like a second Ctrl+C.

    A double call would arm the force-exit countdown on an otherwise clean stop.
    """
    from pathlib import Path

    source = Path("run_pipeline.py").read_text(encoding="utf-8")
    assert (
        "if not shutdown_event.is_set():" in source
    ), "run_pipeline must only call handle_exit() when no signal already did"
