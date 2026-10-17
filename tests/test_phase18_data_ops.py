"""Phase 18: the unified data-operations front door.

The risks this surface introduces are all about *restraint*: an operation that
runs when it should have asked, a recommendation that quietly executes itself,
a duplicate writer, or a dishonest claim that some task can produce data no
source provides. These tests pin those boundaries.

Nothing here dispatches a real operation - the runner and the registry-task
invoker are replaced by recording stubs, and the task lock is stubbed per test.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import myra_app.data_ops as dops

ROOT = str(Path(__file__).resolve().parents[1])

#: Captured before any fixture can replace it, so the real resolver stays
#: reachable from the test that exercises it.
_REAL_RESOLVE_RUNNER = dops._resolve_runner


@pytest.fixture(autouse=True)
def _no_real_execution(monkeypatch):
    """Record instead of run. No test may touch a database or the network."""
    calls: list = []

    def _fake_registered(name, *args, **kwargs):
        calls.append(("task", name))
        return {"stub": name}

    def _fake_resolve(dotted):
        def _runner(**options):
            calls.append(("runner", dotted, options))
            return {"stub": dotted}

        return _runner

    monkeypatch.setattr(dops, "_run_registered_task", _fake_registered)
    monkeypatch.setattr(dops, "_resolve_runner", _fake_resolve)
    return calls


@pytest.fixture(autouse=True)
def _free_lock(monkeypatch):
    """Default to a free, acquirable task lock."""
    import myra_app.utils.task_lock as task_lock

    monkeypatch.setattr(task_lock, "acquire", lambda name: (True, None))
    monkeypatch.setattr(task_lock, "release", lambda name: None)


def _scheduler(monkeypatch, state):
    monkeypatch.setattr(dops, "scheduler_state", lambda: dict(state))


def _readiness(monkeypatch, payload):
    monkeypatch.setattr(dops, "_run_readiness", lambda: payload)


def _v25_modes(state="blocked", reason="lake_empty", missing=(), blocked_by=()):
    return {
        "overall": {"state": "degraded", "label": "Partial", "detail": "d"},
        "modes": {
            "trigger": {"state": "operational", "missing": []},
            "v25_turbo": {
                "state": state,
                "reason": reason,
                "missing": list(missing),
                "blocked_by": list(blocked_by),
            },
        },
    }


# ── 1. The registry cannot drift from the scheduler's own task list ─────────


def test_every_registered_task_is_reachable_as_an_operation():
    from myra_app.tasks.registry import TASKS

    for name in TASKS:
        assert name in dops.OPERATIONS
        assert dops.OPERATIONS[name].scheduler_task == name


def test_every_scheduler_task_has_an_explicit_classification():
    """No task may inherit a default: a wrong default is a wrong warning."""
    from myra_app.tasks.registry import TASKS

    for name in TASKS:
        meta = dops._REGISTRY_META.get(name)
        assert meta is not None, f"'{name}' has no explicit classification"
        assert "network" in meta and "heavy" in meta
        assert isinstance(meta["network"], bool)
        assert isinstance(meta["heavy"], bool)


def test_no_scheduler_task_is_reported_as_read_only():
    for op in dops.OPERATIONS.values():
        if op.scheduler_task:
            assert op.writes is True


def test_the_operation_order_is_deterministic():
    assert list(dops.OPERATIONS) == sorted(dops.OPERATIONS)


def test_manual_operations_are_not_registry_derived():
    """The reviewed manual surface must not be shadowed by a registry entry."""
    for key in ("lake-build", "enrich-screener", "readiness"):
        assert dops.OPERATIONS[key].scheduler_task is None


def test_every_runner_reference_is_a_module_attribute_path():
    for op in dops.OPERATIONS.values():
        if op.runner:
            module_name, _, attr = op.runner.partition(":")
            assert module_name and attr, op.key


def test_the_runner_reference_resolves_to_a_real_callable():
    assert callable(_REAL_RESOLVE_RUNNER("myra_app.data_ops:_run_lake_status"))


# ── 2. Things that must never be offered ────────────────────────────────────


def test_a_self_managed_loop_is_not_offered():
    """The watchdog loops forever; dispatching it here would duplicate it."""
    op = dops.OPERATIONS["watchdog"]
    assert op.enabled is False
    assert "scheduler" in op.disabled_reason.lower()


def test_the_registry_disabled_task_is_not_offered():
    assert dops.OPERATIONS["screener-enrich"].enabled is False


def test_a_disabled_operation_cannot_be_run(monkeypatch, _no_real_execution):
    out = dops.run("watchdog", confirm=True)
    assert out["ran"] is False
    assert out["reason"] == "unavailable"
    assert _no_real_execution == []


def test_a_disabled_operation_is_never_recommended(monkeypatch):
    _scheduler(monkeypatch, {"state": "running"})
    _readiness(monkeypatch, {"state": "ready", "screening_modes": _v25_modes()})
    for rec in dops.recommend():
        if rec.operation:
            assert dops.OPERATIONS[rec.operation].enabled


def test_no_operation_promises_an_unsourced_field():
    """An operation may not claim it can produce what has no producer."""
    from myra_app.screening_modes import V25_LAKE_PRODUCED_INPUTS

    for op in dops.OPERATIONS.values():
        forbidden = set(op.cannot_produce) & set(V25_LAKE_PRODUCED_INPUTS)
        assert not forbidden, f"{op.key} contradicts itself: {forbidden}"


def test_the_lake_build_operation_states_what_it_cannot_produce():
    op = dops.OPERATIONS["lake-build"]
    assert op.cannot_produce
    assert "keltner_upper" in op.cannot_produce
    assert op.note and "not sufficient" in op.note.lower()


# ── 3. Confirmation and refusal semantics ───────────────────────────────────


def test_a_write_without_confirmation_is_refused(_no_real_execution):
    out = dops.run("lake-build")
    assert out["ran"] is False
    assert out["reason"] == "confirmation_required"
    assert _no_real_execution == []


def test_a_read_only_operation_needs_no_confirmation(_no_real_execution):
    out = dops.run("lake-status")
    assert out["ran"] is True
    assert _no_real_execution == [("runner", "myra_app.data_ops:_run_lake_status", {})]


def test_an_unknown_operation_is_refused():
    out = dops.run("not-an-operation", confirm=True)
    assert out["ran"] is False
    assert out["reason"] == "unknown_operation"


def test_a_duplicate_run_is_refused(monkeypatch, _no_real_execution):
    import myra_app.utils.task_lock as task_lock

    monkeypatch.setattr(task_lock, "acquire", lambda name: (False, 4242))
    out = dops.run("lake-build", confirm=True)

    assert out["ran"] is False
    assert out["reason"] == "already_running"
    assert out["owner_pid"] == 4242
    assert _no_real_execution == []


def test_a_confirmed_write_runs_once_inside_the_lock(monkeypatch, _no_real_execution):
    import myra_app.utils.task_lock as task_lock

    events: list = []
    monkeypatch.setattr(
        task_lock,
        "acquire",
        lambda name: (events.append(("acquire", name)), (True, None))[1],
    )
    monkeypatch.setattr(
        task_lock, "release", lambda name: events.append(("release", name))
    )

    out = dops.run("lake-build", confirm=True, options={"limit": 5})

    assert out["ran"] is True
    assert ("acquire", "lake-build") in events
    assert ("release", "lake-build") in events
    assert _no_real_execution == [
        ("runner", "myra_app.data_ops:_run_lake_build", {"limit": 5})
    ]


def test_a_task_a_live_scheduler_runs_is_refused(monkeypatch, _no_real_execution):
    _scheduler(monkeypatch, {"state": "running"})
    out = dops.run("cross-buy-sync", confirm=True)

    assert out["ran"] is False
    assert out["reason"] == "scheduler_owns_task"
    assert _no_real_execution == []


def test_acknowledging_the_scheduler_allows_dispatch(monkeypatch, _no_real_execution):
    _scheduler(monkeypatch, {"state": "running"})
    out = dops.run("cross-buy-sync", confirm=True, acknowledge_scheduler=True)

    assert out["ran"] is True
    assert _no_real_execution == [("task", "cross-buy-sync")]


def test_a_read_only_operation_ignores_scheduler_ownership(
    monkeypatch, _no_real_execution
):
    """Dispatching a read is harmless, so it is not gated on ownership."""
    _scheduler(monkeypatch, {"state": "running"})
    out = dops.run("lake-status")
    assert out["ran"] is True


@pytest.mark.parametrize(
    "state,expected",
    [
        ("running", True),
        ("starting", True),
        # Ambiguity is treated as live: refusing is the safe side, because the
        # alternative is two writers on the same database.
        ("unknown", True),
        ("degraded", False),
        ("disabled", False),
    ],
)
def test_liveness_treats_ambiguity_as_live(state, expected):
    assert dops.is_live({"state": state}) is expected


# ── 4. Failures are reported, never propagated ──────────────────────────────


def test_a_failing_operation_is_reported_not_raised(monkeypatch, _no_real_execution):
    def _boom(dotted):
        def _runner(**options):
            raise RuntimeError("vendor import failed: MORNINGSTAR_TOKEN")

        return _runner

    monkeypatch.setattr(dops, "_resolve_runner", _boom)
    out = dops.run("lake-build", confirm=True)

    assert out["ran"] is False
    assert out["reason"] == "failed"
    assert "MORNINGSTAR_TOKEN" in out["error"]
    assert "MORNINGSTAR_TOKEN" in out["hint"]


def test_the_lock_is_released_even_when_the_operation_fails(
    monkeypatch, _no_real_execution
):
    import myra_app.utils.task_lock as task_lock

    released: list = []
    monkeypatch.setattr(task_lock, "release", lambda name: released.append(name))

    def _boom(dotted):
        def _runner(**options):
            raise ValueError("nope")

        return _runner

    monkeypatch.setattr(dops, "_resolve_runner", _boom)
    out = dops.run("lake-build", confirm=True)

    assert out["ran"] is False
    assert released == ["lake-build"]


def test_an_unknown_hint_is_not_invented(monkeypatch, _no_real_execution):
    def _boom(dotted):
        def _runner(**options):
            raise ValueError("something else entirely")

        return _runner

    monkeypatch.setattr(dops, "_resolve_runner", _boom)
    assert "hint" not in dops.run("lake-build", confirm=True)


# ── 5. Recommendations advise; they never act ───────────────────────────────


def test_recommend_never_dispatches_anything(monkeypatch, _no_real_execution):
    _scheduler(monkeypatch, {"state": "degraded", "reason": "no live scheduler"})
    _readiness(monkeypatch, {"state": "ready", "screening_modes": _v25_modes()})

    recs = dops.recommend()

    assert recs
    assert _no_real_execution == []


def test_recommend_splits_derivable_from_unsourced(monkeypatch):
    from myra_app.screening_modes import V25_REQUIRED_INPUTS

    _scheduler(monkeypatch, {"state": "running"})
    _readiness(
        monkeypatch,
        {
            "state": "ready",
            "screening_modes": _v25_modes(missing=V25_REQUIRED_INPUTS),
        },
    )

    recs = dops.recommend()
    categories = {r.category for r in recs}
    assert "derived_missing" in categories
    assert "unsourced" in categories

    derivable = next(r for r in recs if r.category == "derived_missing")
    assert derivable.operation == "lake-build"
    assert derivable.requires_confirmation is True
    assert derivable.fixable is True

    unsourced = next(r for r in recs if r.category == "unsourced")
    assert unsourced.operation is None
    assert unsourced.fixable is False
    assert "no data operation can supply them" in unsourced.detail.lower()


def test_an_operational_mode_axis_produces_no_screening_recommendation(monkeypatch):
    _scheduler(monkeypatch, {"state": "running"})
    _readiness(
        monkeypatch,
        {"state": "ready", "screening_modes": _v25_modes(state="operational")},
    )
    recs = dops.recommend()
    assert [r.title for r in recs] == ["No data problems detected"]


def test_a_runtime_defect_is_not_offered_as_a_data_task(monkeypatch):
    _scheduler(monkeypatch, {"state": "running"})
    _readiness(
        monkeypatch,
        {
            "state": "ready",
            "screening_modes": _v25_modes(
                state="blocked", missing=(), blocked_by=("engine_implicit_schema",)
            ),
        },
    )
    defects = [r for r in dops.recommend() if "runtime" in r.title.lower()]
    assert len(defects) == 1
    assert defects[0].operation is None
    assert "code defect" in defects[0].detail


def test_a_stale_session_recommends_the_ingest(monkeypatch):
    _scheduler(monkeypatch, {"state": "running"})
    _readiness(
        monkeypatch,
        {
            "state": "stale",
            "technical": {"latest_date": "2026-01-01", "expected_date": "2026-01-02"},
        },
    )
    recs = dops.recommend()
    assert [r.operation for r in recs] == ["daily-ingest"]
    assert recs[0].requires_confirmation is True


def test_a_failed_ingestion_is_reported_and_actionable(monkeypatch):
    _scheduler(monkeypatch, {"state": "running"})
    _readiness(monkeypatch, {"state": "failed", "explanation": "last attempt failed"})
    recs = [r for r in dops.recommend() if r.operation == "daily-ingest"]
    assert len(recs) == 1


def test_a_null_is_recommended_rather_than_invented(monkeypatch):
    _scheduler(monkeypatch, {"state": "running"})
    _readiness(
        monkeypatch,
        {"state": "partial", "explanation": "coverage is partial"},
    )
    recs = dops.recommend()
    assert [r.operation for r in recs] == [None]
    assert "partial" in recs[0].detail


# ── 6. Scheduler messaging is actionable and not duplicated ────────────────


def test_a_stopped_scheduler_points_at_the_owning_entry_point(monkeypatch):
    reason = "no live scheduler owns the lock"
    _scheduler(monkeypatch, {"state": "degraded", "reason": reason})
    _readiness(monkeypatch, {"state": "ready"})

    recs = [r for r in dops.recommend() if "scheduler" in r.title.lower()]
    assert len(recs) == 1
    assert recs[0].detail.count(reason) == 1
    assert "run_fastapi.py" in recs[0].detail
    # Clearing the lock is explicitly called out as the wrong move.
    assert "not the fault" in recs[0].detail


def test_a_disabled_scheduler_is_distinguished_from_a_stopped_one(monkeypatch):
    _scheduler(monkeypatch, {"state": "disabled"})
    _readiness(monkeypatch, {"state": "ready"})

    recs = [r for r in dops.recommend() if "disabled" in r.title.lower()]
    assert len(recs) == 1
    assert "DISABLE_SCHEDULER" in recs[0].detail
    assert "run_fastapi.py" not in recs[0].detail


def test_a_live_scheduler_produces_no_scheduler_recommendation(monkeypatch):
    monkeypatch.setattr(
        dops, "_scheduler_recommendation", dops._scheduler_recommendation
    )
    _scheduler(monkeypatch, {"state": "running", "reason": "fine"})
    assert dops._scheduler_recommendation({"state": "running"}) is None


def test_recommendations_are_ordered_deterministically(monkeypatch):
    from myra_app.screening_modes import V25_REQUIRED_INPUTS

    _scheduler(monkeypatch, {"state": "degraded", "reason": "no live scheduler"})
    _readiness(
        monkeypatch,
        {"state": "ready", "screening_modes": _v25_modes(missing=V25_REQUIRED_INPUTS)},
    )
    first = [r.title for r in dops.recommend()]
    second = [r.title for r in dops.recommend()]
    assert first == second


# ── 7. status() is read-only and degrades per axis ─────────────────────────


def test_status_reports_every_axis(monkeypatch):
    _scheduler(monkeypatch, {"state": "running", "lock": "alive", "owner_pid": 1})
    _readiness(
        monkeypatch,
        {
            "state": "ready",
            "label": "Ready",
            "explanation": "ok",
            "screening": {"state": "ready", "reason": "ok"},
            "screening_modes": {"overall": {"state": "operational", "label": "All"}},
        },
    )

    report = dops.status()

    assert report["scheduler"]["state"] == "running"
    assert report["readiness"]["state"] == "ready"
    assert report["screening_modes"]["state"] == "operational"
    assert set(report["operations"]) == set(dops.OPERATIONS)
    json.dumps(report, default=str)


def test_status_degrades_an_unreadable_axis_instead_of_failing(monkeypatch):
    def _boom(*args, **kwargs):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(dops, "_run_scheduler_status", _boom)
    monkeypatch.setattr(dops, "_run_readiness", _boom)

    report = dops.status()

    assert report["scheduler"]["state"] == "unknown"
    assert report["readiness"]["state"] == "unknown"


# ── 8. Import safety (the vendor-module landmine) ──────────────────────────


def test_importing_data_ops_does_not_import_the_fragile_vendor_module():
    """``fundamental_sync`` reads MORNINGSTAR_TOKEN at import time.

    Importing this module must not drag it in, or ``status`` would crash on a
    machine that has not configured that token.
    """
    code = (
        "import myra_app.data_ops, sys;"
        "print('myra_app.fundamental_sync' in sys.modules)"
    )
    env = dict(os.environ)
    env.pop("MORNINGSTAR_TOKEN", None)
    env["PYTHONPATH"] = ROOT

    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env=env,
        cwd=ROOT,
        timeout=180,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "False"


def test_the_module_can_be_imported_without_the_token():
    """Same contract, asserted directly: no KeyError at import time."""
    env = dict(os.environ)
    env.pop("MORNINGSTAR_TOKEN", None)
    env["PYTHONPATH"] = ROOT

    result = subprocess.run(
        [sys.executable, "-c", "import myra_app.data_ops"],
        capture_output=True,
        text=True,
        env=env,
        cwd=ROOT,
        timeout=180,
    )

    assert result.returncode == 0, result.stderr


# ── 9. The CLI surface ──────────────────────────────────────────────────────


def test_the_cli_uses_a_terminal_only_for_the_interactive_menu(monkeypatch):
    monkeypatch.setattr(dops.sys.stdin, "isatty", lambda: False)
    import io

    monkeypatch.setattr(dops.sys, "stdout", io.StringIO())
    # With no arguments and no terminal, print usage rather than block on input.
    assert dops.main([]) == 0


def test_the_cli_returns_a_usage_error_without_an_operation(capsys):
    assert dops.main(["run"]) == 2
    assert "Usage" in capsys.readouterr().err


def test_the_cli_list_is_read_only(capsys):
    assert dops.main(["list"]) == 0
    out = capsys.readouterr().out
    assert "Available operations" in out
    assert "watchdog" in out


def test_the_cli_status_renders_without_probing_live_data(monkeypatch, capsys):
    _scheduler(monkeypatch, {"state": "running", "lock": "alive", "owner_pid": 1})
    _readiness(
        monkeypatch,
        {"state": "ready", "label": "Ready", "screening": {}, "screening_modes": {}},
    )

    assert dops.main(["status"]) == 0
    out = capsys.readouterr().out
    assert "MYRA data operations" in out
    assert "scheduler:  running" in out


def test_the_cli_recommend_json_is_parseable(monkeypatch, capsys):
    _scheduler(monkeypatch, {"state": "running"})
    _readiness(monkeypatch, {"state": "ready", "screening_modes": _v25_modes()})

    assert dops.main(["recommend", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert isinstance(payload, list)
    assert payload[0]["category"]


def test_the_cli_run_reports_the_refusal_as_a_nonzero_exit(capsys):
    assert dops.main(["run", "lake-build"]) == 1
    assert "confirmation_required" in capsys.readouterr().out


def test_the_cli_passes_options_through(monkeypatch, _no_real_execution):
    assert dops.main(["run", "lake-build", "--yes", "--limit", "7", "--force"]) == 0
    assert _no_real_execution == [
        ("runner", "myra_app.data_ops:_run_lake_build", {"limit": 7, "force": True})
    ]


def test_an_invalid_limit_is_ignored_rather_than_crashing(
    monkeypatch, _no_real_execution
):
    assert dops.main(["run", "lake-build", "--yes", "--limit", "not-a-number"]) == 0
    assert _no_real_execution == [("runner", "myra_app.data_ops:_run_lake_build", {})]
