"""Phase 49 — autonomous, resource-aware background execution.

Two classes of defect are covered here:

1. **Dependency enforcement** (`myra_app.pipeline_control`): `PipelineTask`
   declared `depends_on` but `_run_sequence` never read it, so a failed
   `daily_ingest` still let `enrichment` run against stale data, and there was
   no way to tell a blocked dependent from an unrelated skip.
2. **Admission control** (`myra_app.tasks.executor`): one thread per task with
   no execution limit, no write-resource serialization and no awareness of an
   interactive scan, so a nightly job could compete with a user waiting on the
   UI.

All tests are in-memory: no database is opened, no network is touched, no task
body executes.
"""

from __future__ import annotations

import threading
import time

import pytest

import myra_app.pipeline_control as pc
from myra_app.pipeline_control import (
    PIPELINE_ORDER,
    PIPELINE_TASKS,
    PipelineControl,
)
from myra_app.tasks import executor as ex
from myra_app.tasks.registry import TASKS, TaskSpec


def _wait(predicate, timeout=5.0, interval=0.01):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def _idle(control):
    return control.get_status()["overall"]["status"] not in ("running", "cancelling")


@pytest.fixture
def control(monkeypatch):
    """Fresh in-memory PipelineControl: no DB, no network, no real work."""
    monkeypatch.setattr(PipelineControl, "_load_history", lambda self: None)
    monkeypatch.setattr(PipelineControl, "_persist_run_state", lambda self: None)
    monkeypatch.setattr(
        PipelineControl, "_mark_run", staticmethod(lambda *a, **k: None)
    )
    for key in PIPELINE_ORDER:
        monkeypatch.setattr(PIPELINE_TASKS[key], "_run", lambda tr: None)

    PipelineControl._instance = None
    inst = PipelineControl()
    yield inst
    PipelineControl._instance = None


@pytest.fixture(autouse=True)
def _clean_gate():
    """Restore the module-level gate between tests (it is process-global)."""
    ex.scan_activity.reset()
    ex._reset_gate_for_tests(ex.MAX_CONCURRENT_TASKS_DEFAULT)
    yield
    ex.scan_activity.reset()
    ex._reset_gate_for_tests(ex.MAX_CONCURRENT_TASKS_DEFAULT)


# ---------------------------------------------------------------------------
# D1/D2 — dependency enforcement and the blocked state
# ---------------------------------------------------------------------------


def _fail(tr):
    raise RuntimeError("upstream broke")


def test_failed_dependency_blocks_dependent(control, monkeypatch):
    """A failed daily_ingest must stop enrichment instead of feeding it stale data."""
    ran: list[str] = []
    for key in PIPELINE_ORDER:
        monkeypatch.setattr(
            PIPELINE_TASKS[key], "_run", (lambda k: (lambda tr: ran.append(k)))(key)
        )
    monkeypatch.setattr(PIPELINE_TASKS["daily_ingest"], "_run", _fail)

    control.start_run("all", stop_on_fail=False)
    assert _wait(lambda: _idle(control))

    tasks = control.get_status()["tasks"]
    assert tasks["daily_ingest"]["current_status"] == "failed"
    assert tasks["enrichment"]["current_status"] == "blocked"
    # The dependency is identified both human-readably and machine-readably.
    assert "Daily Ingest" in (tasks["enrichment"]["error_message"] or "")
    assert tasks["enrichment"]["blocked_by"] == "daily_ingest"
    assert "enrichment" not in ran


def test_independent_tasks_still_run_after_a_failure(control, monkeypatch):
    """Failure isolation: blocking a dependent must not stall unrelated work."""
    ran: list[str] = []
    for key in PIPELINE_ORDER:
        monkeypatch.setattr(
            PIPELINE_TASKS[key], "_run", (lambda k: (lambda tr: ran.append(k)))(key)
        )
    monkeypatch.setattr(PIPELINE_TASKS["daily_ingest"], "_run", _fail)

    control.start_run("all", stop_on_fail=False)
    assert _wait(lambda: _idle(control))

    tasks = control.get_status()["tasks"]
    for key in ("etf_sync", "index_sync", "fundamentals_sync", "institutional_sync"):
        assert tasks[key]["current_status"] == "completed", key
        assert key in ran


def test_blocked_is_distinct_from_skipped(control, monkeypatch):
    """`blocked` names a prerequisite; `skipped` means the batch stopped."""
    monkeypatch.setattr(PIPELINE_TASKS["daily_ingest"], "_run", _fail)
    control.start_run("all", stop_on_fail=False)
    assert _wait(lambda: _idle(control))

    tasks = control.get_status()["tasks"]
    assert tasks["enrichment"]["current_status"] == "blocked"
    assert tasks["enrichment"]["current_status"] != "skipped"
    assert tasks["enrichment"]["progress_pct"] is None
    assert tasks["enrichment"]["started_at"] is None


def test_blocked_task_does_not_write_sync_log(control, monkeypatch):
    """A blocked task has not run, so its last_run must not be stamped.

    Stamping it would postpone the next legitimate attempt by a whole
    interval, which is the opposite of retrying once prerequisites recover.
    """
    calls: list[tuple] = []
    monkeypatch.setattr(
        PipelineControl,
        "_mark_run",
        staticmethod(lambda *a, **k: calls.append((a, k))),
    )
    monkeypatch.setattr(PIPELINE_TASKS["daily_ingest"], "_run", _fail)

    control.start_run("all", stop_on_fail=False)
    assert _wait(lambda: _idle(control))

    marked = [a[0] for a, _ in calls]
    assert "daily_ingest" in marked
    assert "enrichment" not in marked


def test_dependency_absent_from_batch_does_not_block(control, monkeypatch):
    """Running one step on its own must still work (existing UI behaviour)."""
    ran: list[str] = []
    monkeypatch.setattr(
        PIPELINE_TASKS["enrichment"], "_run", lambda tr: ran.append("enrichment")
    )
    control.start_run("enrichment", stop_on_fail=False)
    assert _wait(lambda: _idle(control))

    assert ran == ["enrichment"]
    assert control.get_status()["tasks"]["enrichment"]["current_status"] == "completed"


def test_successful_dependency_lets_dependent_run(control, monkeypatch):
    ran: list[str] = []
    for key in PIPELINE_ORDER:
        monkeypatch.setattr(
            PIPELINE_TASKS[key], "_run", (lambda k: (lambda tr: ran.append(k)))(key)
        )
    control.start_run("all", stop_on_fail=False)
    assert _wait(lambda: _idle(control))

    assert control.get_status()["tasks"]["enrichment"]["current_status"] == "completed"
    assert ran.index("daily_ingest") < ran.index("enrichment")


def test_stop_on_fail_still_fails_fast(control, monkeypatch):
    """Manual fail-fast semantics are unchanged by dependency enforcement."""
    ran: list[str] = []
    for key in PIPELINE_ORDER:
        monkeypatch.setattr(
            PIPELINE_TASKS[key], "_run", (lambda k: (lambda tr: ran.append(k)))(key)
        )
    monkeypatch.setattr(PIPELINE_TASKS["etf_sync"], "_run", _fail)

    control.start_run("all", stop_on_fail=True)
    assert _wait(lambda: _idle(control))

    tasks = control.get_status()["tasks"]
    assert tasks["etf_sync"]["current_status"] == "failed"
    for key in PIPELINE_ORDER[PIPELINE_ORDER.index("etf_sync") + 1 :]:
        assert tasks[key]["current_status"] == "skipped", key


def test_batch_overall_status_failed_when_independent_task_fails(control, monkeypatch):
    """A batch with stop_on_fail=False reports overall 'failed' if any task fails,
    even when independent tasks complete successfully.
    """
    ran: list[str] = []
    for key in PIPELINE_ORDER:
        monkeypatch.setattr(
            PIPELINE_TASKS[key], "_run", (lambda k: (lambda tr: ran.append(k)))(key)
        )
    monkeypatch.setattr(PIPELINE_TASKS["etf_sync"], "_run", _fail)

    control.start_run("all", stop_on_fail=False)
    assert _wait(lambda: _idle(control))

    status = control.get_status()
    # Overall batch must be 'failed' because etf_sync failed
    assert status["overall"]["status"] == "failed"
    # The failed task
    assert status["tasks"]["etf_sync"]["current_status"] == "failed"
    # Independent tasks before the failure
    assert status["tasks"]["daily_ingest"]["current_status"] == "completed"
    assert status["tasks"]["enrichment"]["current_status"] == "completed"
    # Independent tasks after the failure (not dependent on etf_sync)
    for key in ("index_sync", "fundamentals_sync", "institutional_sync"):
        assert status["tasks"][key]["current_status"] == "completed", key


def test_cycle_is_rejected_at_import():
    tasks = {
        "a": pc.PipelineTask("a", "A", 1, lambda tr: None, depends_on=("b",)),
        "b": pc.PipelineTask("b", "B", 1, lambda tr: None, depends_on=("a",)),
    }
    with pytest.raises(ValueError, match="cycle"):
        pc._validate_dependencies(tasks)


def test_self_dependency_is_a_cycle():
    tasks = {
        "a": pc.PipelineTask("a", "A", 1, lambda tr: None, depends_on=("a",)),
    }
    with pytest.raises(ValueError, match="cycle"):
        pc._validate_dependencies(tasks)


def test_unknown_dependency_is_rejected():
    tasks = {
        "a": pc.PipelineTask("a", "A", 1, lambda tr: None, depends_on=("ghost",)),
    }
    with pytest.raises(ValueError, match="unknown"):
        pc._validate_dependencies(tasks)


def test_shipped_graph_is_acyclic():
    """The real PIPELINE_TASKS graph must import cleanly (validated at import)."""
    pc._validate_dependencies(PIPELINE_TASKS)


# ---------------------------------------------------------------------------
# D3 — interactive-scan priority
# ---------------------------------------------------------------------------


def test_heavy_task_is_deferred_while_a_scan_runs():
    spec = TASKS["daily-ingest"]
    with ex.scan_activity.scanning("wyckoff"):
        reason = ex.deferral_reason(spec)
    assert reason is not None
    assert "wyckoff" in reason


def test_light_task_is_not_deferred_while_a_scan_runs():
    """A cheap local task must still make progress during a scan."""
    with ex.scan_activity.scanning("wyckoff"):
        assert ex.deferral_reason(TASKS["symbol-identity"]) is None


def test_deferral_ends_when_the_scan_ends():
    spec = TASKS["daily-ingest"]
    with ex.scan_activity.scanning("lfd"):
        assert ex.deferral_reason(spec) is not None
    assert ex.deferral_reason(spec) is None


def test_deferral_survives_a_crashing_scan():
    """`scanning()` releases in `finally`, so a crash cannot wedge the gate."""
    with pytest.raises(RuntimeError):
        with ex.scan_activity.scanning("dcb"):
            raise RuntimeError("scanner blew up")
    assert ex.deferral_reason(TASKS["daily-ingest"]) is None


def test_run_admitted_does_not_execute_while_scanning(monkeypatch):
    called: list[str] = []
    ctx = _ctx()
    with ex.scan_activity.scanning("multibagger"):
        ex._run_admitted("daily_ingest", TASKS["daily-ingest"], called.append, ctx)

    assert called == []
    deferred = ex.get_deferrals()
    assert "daily_ingest" in deferred
    assert "multibagger" in deferred["daily_ingest"]["reason"]


def test_run_admitted_executes_and_clears_deferral():
    called: list[str] = []
    ctx = _ctx()
    ex._record_deferral("daily_ingest", "interactive scan in progress (x)")
    ex._run_admitted(
        "daily_ingest",
        TASKS["daily-ingest"],
        lambda _ctx: called.append("daily_ingest"),
        ctx,
    )

    assert called == ["daily_ingest"]
    assert "daily_ingest" not in ex.get_deferrals()


def test_deferral_counter_increments_without_duplicating_entries():
    ex._record_deferral("t", "same")
    ex._record_deferral("t", "same")
    ex._record_deferral("t", "different")
    entry = ex.get_deferrals()["t"]
    assert entry["count"] == 1  # reason changed -> new transition
    assert entry["reason"] == "different"


# ---------------------------------------------------------------------------
# D4 — bounded concurrency and write-resource serialization
# ---------------------------------------------------------------------------


def test_same_resource_never_overlaps():
    a = TaskSpec(module="m", label="a", interval_days=1, resource="technical")
    b = TaskSpec(module="m", label="b", interval_days=1, resource="technical")
    with ex.dispatch_slot(a) as first:
        assert first is True
        with ex.dispatch_slot(b) as second:
            assert second is False


def test_different_resources_overlap():
    a = TaskSpec(module="m", label="a", interval_days=1, resource="technical")
    b = TaskSpec(module="m", label="b", interval_days=1, resource="metadata")
    with ex.dispatch_slot(a) as first:
        assert first is True
        with ex.dispatch_slot(b) as second:
            assert second is True


def test_concurrency_limit_is_enforced():
    ex._reset_gate_for_tests(2)
    a = TaskSpec(module="m", label="a", interval_days=1, resource="technical")
    b = TaskSpec(module="m", label="b", interval_days=1, resource="metadata")
    c = TaskSpec(module="m", label="c", interval_days=1, resource="valuation")
    with ex.dispatch_slot(a) as first:
        with ex.dispatch_slot(b) as second:
            with ex.dispatch_slot(c) as third:
                assert (first, second, third) == (True, True, False)


def test_exclusive_task_blocks_every_other_resource():
    backup = TaskSpec(
        module="m", label="backup", interval_days=1, resource="backup", exclusive=True
    )
    other = TaskSpec(module="m", label="x", interval_days=1, resource="technical")
    ex._reset_gate_for_tests(4)
    with ex.dispatch_slot(backup) as held:
        assert held is True
        with ex.dispatch_slot(other) as second:
            assert second is False


def test_other_task_blocks_an_exclusive_task():
    backup = TaskSpec(
        module="m", label="backup", interval_days=1, resource="backup", exclusive=True
    )
    other = TaskSpec(module="m", label="x", interval_days=1, resource="technical")
    ex._reset_gate_for_tests(4)
    with ex.dispatch_slot(other) as held:
        assert held is True
        with ex.dispatch_slot(backup) as second:
            assert second is False


def test_exclusive_task_releases_every_lock():
    backup = TaskSpec(
        module="m", label="backup", interval_days=1, resource="backup", exclusive=True
    )
    ex._reset_gate_for_tests(4)
    with ex.dispatch_slot(backup) as held:
        assert held is True
    for key in ex._RESOURCE_KEYS:
        assert not ex._resource_locks[key].locked(), key


def test_slot_is_released_after_an_exception():
    spec = TaskSpec(module="m", label="a", interval_days=1, resource="technical")
    with pytest.raises(RuntimeError):
        with ex.dispatch_slot(spec) as granted:
            assert granted is True
            raise RuntimeError("boom")
    assert not ex._resource_locks["technical"].locked()
    with ex.dispatch_slot(spec) as again:
        assert again is True


def test_slot_is_released_on_thread_exit():
    """A crashed task thread must not leak its slot (would stall the scheduler)."""
    spec = TaskSpec(module="m", label="a", interval_days=1, resource="technical")
    done = threading.Event()

    def worker():
        with ex.dispatch_slot(spec) as granted:
            assert granted is True
        done.set()

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    assert done.wait(5)
    t.join(5)
    assert not ex._resource_locks["technical"].locked()


# ---------------------------------------------------------------------------
# D4 (real threads) — overlap/exclusion proved with a barrier, not inferred
# from nested context managers on one thread. A barrier only returns when both
# threads are simultaneously inside their slot, so this cannot pass on a
# serialised implementation.
# ---------------------------------------------------------------------------


def test_two_threads_hold_independent_resources_at_the_same_time():
    ex._reset_gate_for_tests(2)
    barrier = threading.Barrier(2, timeout=5)
    granted: dict[str, bool] = {}

    def hold(label, resource):
        spec = TaskSpec(
            module="m",
            label=label,
            interval_days=1,
            resource=resource,
            mark_on_success=False,
            mark_on_failure=False,
        )
        with ex.dispatch_slot(spec) as ok:
            granted[label] = bool(ok)
            if ok:
                barrier.wait()  # returns only if the other thread is inside too

    a = threading.Thread(target=hold, args=("a", "technical"))
    b = threading.Thread(target=hold, args=("b", "metadata"))
    a.start()
    b.start()
    a.join(10)
    b.join(10)

    assert granted == {"a": True, "b": True}
    assert not a.is_alive() and not b.is_alive(), "barrier timed out: no overlap"


def test_two_threads_never_hold_the_same_resource_together():
    ex._reset_gate_for_tests(4)
    spec = TaskSpec(
        module="m",
        label="a",
        interval_days=1,
        resource="technical",
        mark_on_success=False,
        mark_on_failure=False,
    )
    held = threading.Event()
    release = threading.Event()
    first: dict[str, bool] = {}

    def holder():
        with ex.dispatch_slot(spec) as ok:
            first["granted"] = bool(ok)
            held.set()
            release.wait(5)

    t = threading.Thread(target=holder)
    t.start()
    assert held.wait(5), "first holder never acquired"
    try:
        with ex.dispatch_slot(spec) as second:
            assert second is False
    finally:
        release.set()
        t.join(5)
    assert first == {"granted": True}
    with ex.dispatch_slot(spec) as after_release:
        assert after_release is True


def test_admitted_body_failure_releases_the_slot_for_the_next_task():
    """`_execute_once` swallows the body error, so the gate must still reopen."""
    ex._reset_gate_for_tests(1)
    spec = TaskSpec(
        module="m",
        label="a",
        interval_days=1,
        resource="technical",
        mark_on_success=False,
        mark_on_failure=False,
    )
    called: list[str] = []

    def boom(_ctx):
        raise RuntimeError("task blew up")

    ex._run_admitted("a", spec, boom, _ctx())
    ex._run_admitted("b", spec, lambda _c: called.append("b"), _ctx())

    assert called == ["b"], "the failed attempt must not pin the only slot"


def test_note_deferral_records_a_reason_only_when_blocked():
    """Public probe used by the watchdog's catch-up driver."""
    heavy = TASKS["daily-ingest"]
    light = TASKS["symbol-identity"]

    assert ex.note_deferral("daily_ingest", heavy) is None
    assert ex.get_deferrals() == {}

    with ex.scan_activity.scanning("wyckoff"):
        reason = ex.note_deferral("daily_ingest", heavy)
        assert reason is not None and "wyckoff" in reason
        assert "daily_ingest" in ex.get_deferrals()
        # A cheap task is still admitted, and is never recorded as deferred.
        assert ex.note_deferral("symbol_identity", light) is None
        assert "symbol_identity" not in ex.get_deferrals()


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def test_concurrency_default(monkeypatch):
    monkeypatch.delenv("MYRA_TASK_CONCURRENCY", raising=False)
    assert ex.concurrency_limit() == ex.MAX_CONCURRENT_TASKS_DEFAULT


def test_concurrency_env_override(monkeypatch):
    monkeypatch.setenv("MYRA_TASK_CONCURRENCY", "3")
    assert ex.concurrency_limit() == 3


def test_concurrency_is_clamped_to_ceiling(monkeypatch):
    monkeypatch.setenv("MYRA_TASK_CONCURRENCY", "999")
    assert ex.concurrency_limit() == ex.MAX_CONCURRENT_TASKS_CEILING


def test_concurrency_floor_is_one(monkeypatch):
    monkeypatch.setenv("MYRA_TASK_CONCURRENCY", "0")
    assert ex.concurrency_limit() == 1
    monkeypatch.setenv("MYRA_TASK_CONCURRENCY", "-5")
    assert ex.concurrency_limit() == 1


def test_invalid_concurrency_falls_back(monkeypatch):
    monkeypatch.setenv("MYRA_TASK_CONCURRENCY", "not-a-number")
    assert ex.concurrency_limit() == ex.MAX_CONCURRENT_TASKS_DEFAULT


def test_gate_snapshot_shape():
    snap = ex.gate_snapshot()
    assert set(snap) == {
        "concurrency_limit",
        "resource_keys",
        "scan_active",
        "active_scanners",
        "deferred",
    }
    assert isinstance(snap["concurrency_limit"], int)
    assert snap["scan_active"] is False


# ---------------------------------------------------------------------------
# Registry metadata sanity
# ---------------------------------------------------------------------------


def test_watchdog_is_never_deferred():
    """The watchdog is the recovery mechanism; gating it would defeat the point."""
    assert TASKS["watchdog"].self_loop is True
    assert TASKS["watchdog"].heavy is False


def test_db_backup_is_exclusive():
    assert TASKS["db-backup"].exclusive is True


def test_every_task_declares_a_known_resource():
    for key, spec in TASKS.items():
        assert spec.resource in ex._RESOURCE_KEYS, key


def _ctx():
    """Minimal TaskContext for `_run_admitted` (no shutdown set)."""
    import logging

    from myra_app.tasks.context import TaskContext

    return TaskContext(
        shutdown_event=threading.Event(), logger=logging.getLogger("test")
    )
