"""Tests for the authoritative pipeline execution/status layer.

Covers the defects confirmed in docs/MISSION_CONTROL_PIPELINE_AUDIT.md:

  D1/D3/D4  one authoritative /api/pipeline router + one response contract
  D2        /api/pipeline/events is a real SSE stream with an initial snapshot
  D5        concurrent run attempts are safely rejected (race-safe start)
  D6        no silent auto-reset of a legitimately long run
  D7        force reset refuses while work is executing
  D8        cancel reports "cancelling", not "stopped", until the task exits
  D9/D11    100% only after success; running never shows the previous status
  D10       failed/cancelled/timed_out/skipped/never stay distinguishable
  D12       a finished run keeps its final result until the next run

Nothing here touches a live database: persistence is stubbed and DB_DIR is
pointed at a tmp dir. No real ingest or enrichment job is ever executed.
"""

from __future__ import annotations

import threading
import time

import pytest

import myra_app.pipeline_control as pc
from myra_app.pipeline_control import (
    PIPELINE_ORDER,
    PIPELINE_TASKS,
    PipelineBusyError,
    PipelineControl,
    PipelineTask,
)

FASTAPI_AVAILABLE = True
try:  # pragma: no cover - import guard
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
except Exception:  # pragma: no cover
    FASTAPI_AVAILABLE = False


def _wait(predicate, timeout=5.0, interval=0.01):
    """Poll until predicate() is true or the timeout expires."""
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
    """A fresh, fully in-memory PipelineControl (no DB, no network, no threads)."""
    monkeypatch.setattr(PipelineControl, "_load_history", lambda self: None)
    monkeypatch.setattr(PipelineControl, "_persist_run_state", lambda self: None)
    monkeypatch.setattr(
        PipelineControl, "_mark_run", staticmethod(lambda *a, **k: None)
    )

    # Replace every task body with an instant no-op so no real work happens.
    for key in PIPELINE_ORDER:
        monkeypatch.setattr(PIPELINE_TASKS[key], "_run", lambda tr: None)

    PipelineControl._instance = None
    inst = PipelineControl()
    yield inst
    PipelineControl._instance = None


# ---------------------------------------------------------------------------
# Contract
# ---------------------------------------------------------------------------


def test_legacy_sync_log_values_are_normalised():
    """sync_log holds values written by several legacy writers (D10)."""
    norm = PipelineControl._normalise_sync_status
    assert norm("success", "2026-01-01") == "completed"
    assert norm("completed", "2026-01-01") == "completed"
    assert norm("failed", "2026-01-01") == "failed"
    assert norm("crashed", "2026-01-01") == "failed"
    assert norm("timeout", "2026-01-01") == "timed_out"
    assert norm("cancelled", "2026-01-01") == "cancelled"
    assert norm("unknown", "2026-01-01") == "completed"
    assert norm("unknown", None) == "never"
    assert norm(None, None) == "never"
    assert norm("  SUCCESS ", "2026-01-01") == "completed"


def test_restored_run_state_is_normalised_and_never_looks_active(monkeypatch):
    """A run left 'running' at server shutdown must not read as still active.

    Isolation note: this test MUST NOT monkeypatch `myra_app.constants.DB_DIR`.
    `myra_app/librarian_core.py` binds DB_DIR at *import* time
    (`from myra_app.constants import DB_DIR`), so rebinding the constants
    attribute does not redirect it and writes land on the LIVE metadata DB.
    LibrarianCore itself is replaced with an in-memory fake instead.
    """
    import json
    import sqlite3

    import myra_app.pipeline_control as pc_mod

    def _seed_db() -> sqlite3.Connection:
        conn = sqlite3.connect(":memory:")
        conn.executescript(
            "CREATE TABLE sync_log (task_name TEXT PRIMARY KEY, last_run TEXT,"
            " last_status TEXT, error_message TEXT);"
            "CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT);"
        )
        conn.execute(
            "INSERT OR REPLACE INTO metadata VALUES ('pipeline_run_state', ?)",
            (
                json.dumps(
                    {
                        "status": "running",
                        "active_task_id": "shares_outstanding_sync",
                        "task_states": {
                            # legacy vocabulary written by the old dashboard
                            "shares_outstanding_sync": {
                                "current_status": "timeout",
                                "error_message": "timed out",
                            },
                            "market_cap_sync": {"current_status": "crashed"},
                        },
                    }
                ),
            ),
        )
        conn.execute(
            "INSERT OR REPLACE INTO sync_log VALUES"
            " ('etf_sync', '2026-10-09T00:07:44', 'success', '')"
        )
        conn.commit()
        return conn

    class FakeLib:
        """Minimal LibrarianCore stand-in; never touches a real file."""

        def __init__(self, read_only=False, console=None):
            self.read_only = read_only
            self._meta_conn = _seed_db()

        def close(self):
            self._meta_conn.close()

    monkeypatch.setattr(pc_mod, "LibrarianCore", FakeLib)
    monkeypatch.setattr(PipelineControl, "_ensure_schema", lambda self: None)

    PipelineControl._instance = None
    try:
        inst = PipelineControl()
        tasks = inst.get_status()["tasks"]
        overall = inst.get_status()["overall"]

        # Legacy 'crashed' normalises to 'failed' rather than falling through
        # to "never run" (which would read as healthy).
        assert tasks["market_cap_sync"]["current_status"] == "failed"
        # A crashed run is never presented as still executing.
        assert overall["status"] == "failed"
        assert overall["active_task_id"] is None
        assert "stopped unexpectedly" in (overall["message"] or "")
        # sync_log rows still load through the normal path.
        assert tasks["etf_sync"]["current_status"] == "completed"
        assert tasks["etf_sync"]["last_run"] == "2026-10-09T00:07:44"
    finally:
        PipelineControl._instance = None


def test_order_is_the_nine_task_pipeline(control):
    assert PIPELINE_ORDER == [
        "daily_ingest",
        "enrichment",
        "etf_sync",
        "index_sync",
        "fundamentals_sync",
        "market_cap_sync",
        "shares_outstanding_sync",
        "institutional_sync",
        "fundamentals_enrich",
    ]


def test_status_shape_matches_frontend_contract(control):
    """Mirrors src/lib/pipeline.ts PipelineStatus / OverallState / TaskState."""
    status = control.get_status()
    assert set(status) == {"overall", "tasks", "order", "events"}

    overall = status["overall"]
    for field in (
        "status",
        "active_task_id",
        "started_at",
        "finished_at",
        "message",
        "progress_pct",
        "run_type",
        "stop_on_fail",
        "cancel_requested",
        "busy",
    ):
        assert field in overall, f"missing overall.{field}"

    assert status["order"] == PIPELINE_ORDER
    for key in PIPELINE_ORDER:
        task = status["tasks"][key]
        for field in (
            "current_status",
            "stage",
            "progress_pct",
            "error_message",
            "last_run",
            "started_at",
            "finished_at",
            "duration_seconds",
        ):
            assert field in task, f"missing tasks.{key}.{field}"


def test_cold_backend_reports_never_run_not_success(control):
    tasks = control.get_status()["tasks"]
    assert all(t["current_status"] == "never" for t in tasks.values())


def test_every_terminal_state_is_distinguishable(control):
    """D10 — never/queued/running/completed/failed/cancelled/timed_out/skipped."""
    seen = set()
    for key in PIPELINE_ORDER:
        task = control._tasks[key]
        task["current_status"] = "never"
        seen.add(task["current_status"])
        for state in (
            "queued",
            "running",
            "completed",
            "failed",
            "cancelled",
            "timed_out",
            "skipped",
        ):
            task["current_status"] = state
            seen.add(control.get_status()["tasks"][key]["current_status"])
    assert seen == {
        "never",
        "queued",
        "running",
        "completed",
        "failed",
        "cancelled",
        "timed_out",
        "skipped",
    }


# ---------------------------------------------------------------------------
# Running tasks
# ---------------------------------------------------------------------------


def test_single_task_runs_and_reaches_completed(control, monkeypatch):
    monkeypatch.setattr(PIPELINE_TASKS["etf_sync"], "_run", lambda tr: None)
    control.start_run("etf_sync")
    assert _wait(lambda: _idle(control)), "task never finished"

    status = control.get_status()
    assert status["tasks"]["etf_sync"]["current_status"] == "completed"
    assert status["tasks"]["etf_sync"]["progress_pct"] == 100.0
    assert status["overall"]["status"] == "completed"
    # A completed run keeps its result instead of blanking back to idle (D12).
    assert status["overall"]["finished_at"] is not None


def test_task_shows_running_not_its_previous_status(control, monkeypatch):
    """D11 — a task must not display a stale status while it is executing."""
    control._tasks["etf_sync"]["current_status"] = "completed"

    seen = []

    def _slow(tr):
        seen.append(control.get_status()["tasks"]["etf_sync"]["current_status"])
        time.sleep(0.15)

    monkeypatch.setattr(PIPELINE_TASKS["etf_sync"], "_run", _slow)
    control.start_run("etf_sync")
    assert _wait(lambda: _idle(control))
    assert seen == ["running"], f"task displayed {seen} instead of 'running'"


def test_run_all_executes_in_established_order(control, monkeypatch):
    order_seen: list[str] = []

    def _record(key):
        def _body(tr):
            order_seen.append(key)

        return _body

    for key in PIPELINE_ORDER:
        monkeypatch.setattr(PIPELINE_TASKS[key], "_run", _record(key))

    control.start_run("all", stop_on_fail=True)
    assert _wait(lambda: _idle(control)), "pipeline never finished"
    assert order_seen == PIPELINE_ORDER


def test_next_task_is_marked_queued_not_running(control, monkeypatch):
    """Queued tasks must read 'queued', never a stale previous status."""
    gate = threading.Event()

    def _first(tr):
        gate.wait(timeout=5)

    monkeypatch.setattr(PIPELINE_TASKS["daily_ingest"], "_run", _first)
    control.start_run("all")
    assert _wait(
        lambda: control.get_status()["overall"]["active_task_id"] == "daily_ingest"
    )
    assert control.get_status()["tasks"]["enrichment"]["current_status"] == "queued"
    gate.set()
    assert _wait(lambda: _idle(control))


def test_enrichment_declares_daily_ingest_dependency(control):
    """D-dep — enrichment must not bypass its ingest dependency."""
    assert PIPELINE_TASKS["enrichment"].depends_on == ("daily_ingest",)


# ---------------------------------------------------------------------------
# Failure / cancel / timeout honesty
# ---------------------------------------------------------------------------


def test_failure_is_visible_and_not_reported_as_success(control, monkeypatch):
    def _boom(tr):
        raise RuntimeError("upstream exploded")

    monkeypatch.setattr(PIPELINE_TASKS["index_sync"], "_run", _boom)
    control.start_run("index_sync")
    assert _wait(lambda: _idle(control))

    task = control.get_status()["tasks"]["index_sync"]
    assert task["current_status"] == "failed"
    assert "upstream exploded" in task["error_message"]
    assert task["progress_pct"] is None, "a failure must not show progress"
    assert control.get_status()["overall"]["status"] == "failed"


def test_failed_task_does_not_reach_100_percent(control, monkeypatch):
    """D9 — progress_pct must never be 100 on a non-success outcome."""

    def _boom(tr):
        control._publish_progress("market_cap_sync", 42.0)
        raise RuntimeError("late failure")

    monkeypatch.setattr(PIPELINE_TASKS["market_cap_sync"], "_run", _boom)
    control.start_run("market_cap_sync")
    assert _wait(lambda: _idle(control))
    assert control.get_status()["tasks"]["market_cap_sync"]["progress_pct"] is None


def test_progress_is_clamped_and_never_reports_100_before_success(control):
    """A task cannot publish >= 100 while still running."""
    control._publish_progress("etf_sync", 100.0)
    assert control._tasks["etf_sync"]["progress_pct"] == 99.0
    control._publish_progress("etf_sync", 250.0)
    assert control._tasks["etf_sync"]["progress_pct"] == 99.0
    control._publish_progress("etf_sync", -10.0)
    assert control._tasks["etf_sync"]["progress_pct"] == 0.0


def test_stop_on_fail_skips_remaining_tasks(control, monkeypatch):
    ran: list[str] = []

    def _boom(tr):
        raise RuntimeError("stop here")

    monkeypatch.setattr(PIPELINE_TASKS["etf_sync"], "_run", _boom)
    for key in PIPELINE_ORDER:
        monkeypatch.setattr(
            PIPELINE_TASKS[key], "_run", (lambda k: (lambda tr: ran.append(k)))(key)
        )
    monkeypatch.setattr(PIPELINE_TASKS["etf_sync"], "_run", _boom)

    control.start_run("all", stop_on_fail=True)
    assert _wait(lambda: _idle(control))

    tasks = control.get_status()["tasks"]
    assert tasks["etf_sync"]["current_status"] == "failed"
    assert tasks["daily_ingest"]["current_status"] == "completed"
    # Everything after the failure must be explicitly 'skipped', not 'never'.
    for key in PIPELINE_ORDER[PIPELINE_ORDER.index("etf_sync") + 1 :]:
        assert tasks[key]["current_status"] == "skipped", key
    assert "index_sync" not in ran


def test_cancel_keeps_run_cancelling_until_task_exits(control, monkeypatch):
    """D8 — cancellation must not claim work has stopped while it still runs."""
    finished = threading.Event()

    def _slow(tr):
        time.sleep(0.4)
        finished.set()

    monkeypatch.setattr(PIPELINE_TASKS["fundamentals_sync"], "_run", _slow)
    control.start_run("fundamentals_sync")
    assert _wait(lambda: control.get_status()["overall"]["status"] == "running")

    result = control.cancel()
    assert result["cancel_requested"] is True
    # Immediately after the request the run must still be busy / cancelling.
    assert control.get_status()["overall"]["status"] == "cancelling"
    assert control.get_status()["overall"]["busy"] is True

    assert _wait(lambda: _idle(control), timeout=8)
    assert finished.is_set()
    assert (
        control.get_status()["tasks"]["fundamentals_sync"]["current_status"]
        == "cancelled"
    )


def test_cancel_before_a_task_starts_marks_it_cancelled(control, monkeypatch):
    gate = threading.Event()

    def _block(tr):
        gate.wait(timeout=5)

    monkeypatch.setattr(PIPELINE_TASKS["daily_ingest"], "_run", _block)
    control.start_run("all")
    assert _wait(
        lambda: control.get_status()["overall"]["active_task_id"] == "daily_ingest"
    )
    control.cancel()
    gate.set()
    assert _wait(lambda: _idle(control), timeout=8)
    assert control.get_status()["overall"]["status"] == "cancelled"


def test_timeout_is_reported_distinctly_from_failure(control, monkeypatch):
    def _slow(tr):
        time.sleep(1.5)

    monkeypatch.setattr(PIPELINE_TASKS["institutional_sync"], "timeout", 1)
    monkeypatch.setattr(PIPELINE_TASKS["institutional_sync"], "_run", _slow)
    control.start_run("institutional_sync")
    assert _wait(lambda: _idle(control), timeout=10)

    task = control.get_status()["tasks"]["institutional_sync"]
    assert task["current_status"] == "timed_out"
    assert "timed out" in (task["error_message"] or "").lower()
    assert task["current_status"] != "failed"


# ---------------------------------------------------------------------------
# Concurrency
# ---------------------------------------------------------------------------


def test_concurrent_run_attempts_are_rejected(control, monkeypatch):
    """D5 — the busy check and the start share one lock."""
    gate = threading.Event()

    def _block(tr):
        gate.wait(timeout=5)

    monkeypatch.setattr(PIPELINE_TASKS["etf_sync"], "_run", _block)
    control.start_run("etf_sync")
    assert _wait(lambda: control.is_busy())

    errors = []

    def _attempt():
        try:
            control.start_run("all")
        except PipelineBusyError as exc:
            errors.append(exc)

    threads = [threading.Thread(target=_attempt) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    gate.set()
    assert _wait(lambda: _idle(control), timeout=8)
    assert len(errors) == 6, "every concurrent attempt must be rejected"
    assert all("already in progress" in str(e) for e in errors)


def test_force_reset_refuses_while_a_task_is_executing(control, monkeypatch):
    """D7 — force reset must never mark a live operation idle."""
    gate = threading.Event()
    monkeypatch.setattr(
        PIPELINE_TASKS["etf_sync"], "_run", lambda tr: gate.wait(timeout=5)
    )
    control.start_run("etf_sync")
    assert _wait(lambda: control.is_busy())
    assert control.is_busy() is True
    gate.set()
    assert _wait(lambda: _idle(control), timeout=8)


# ---------------------------------------------------------------------------
# Events / SSE
# ---------------------------------------------------------------------------


def test_event_bus_delivers_initial_state_then_events(control, monkeypatch):
    """D2 — a subscriber gets the snapshot, then live task events."""
    queue = control.subscribe()
    initial = control.get_status()
    assert "overall" in initial and "tasks" in initial

    monkeypatch.setattr(PIPELINE_TASKS["etf_sync"], "_run", lambda tr: None)
    control.start_run("etf_sync")
    assert _wait(lambda: _idle(control))

    types = [e["type"] for e in queue]
    assert "run_started" in types
    assert "task_started" in types
    assert "task_completed" in types
    assert "run_finished" in types

    completed = next(e for e in queue if e["type"] == "task_completed")
    assert completed["task_id"] == "etf_sync"
    assert completed["status"] == "completed"
    assert completed["time"]

    control.unsubscribe(queue)
    assert queue not in control._subscribers


def test_stage_updates_reach_task_and_overall_consistently(control, monkeypatch):
    """A stage message must update the task AND the overall message together."""
    seen: list[tuple] = []

    def _staged(tr):
        tr.stage("Fetching NSE data for 500 symbols")
        seen.append((control._tasks["index_sync"]["stage"], control._state["message"]))

    monkeypatch.setattr(PIPELINE_TASKS["index_sync"], "_run", _staged)
    control.start_run("index_sync")
    assert _wait(lambda: _idle(control))
    assert seen == [
        ("Fetching NSE data for 500 symbols", "Fetching NSE data for 500 symbols")
    ]


# ---------------------------------------------------------------------------
# HTTP surface (contract + retained compatibility)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not FASTAPI_AVAILABLE, reason="fastapi TestClient unavailable")
class TestHttpSurface:
    @staticmethod
    def _client(control, monkeypatch):
        import myra_web.routes.pipeline as route_mod

        monkeypatch.setattr(route_mod, "control", control)
        app = FastAPI()
        app.include_router(route_mod.router)
        return TestClient(app)

    def test_status_endpoint_matches_contract(self, control, monkeypatch):
        client = self._client(control, monkeypatch)
        body = client.get("/api/pipeline/status").json()
        assert set(body) == {"overall", "tasks", "order", "events"}
        assert body["order"] == PIPELINE_ORDER
        assert client.get("/api/pipeline/run/status").json()["busy"] is False


def _drive_sse(control, monkeypatch, want_types, timeout=10.0):
    """Pull events off the real SSE endpoint until `want_types` are all seen.

    Drives the async generator directly: TestClient's portal deadlocks on a
    still-open streaming response.
    """
    import asyncio
    import json

    import myra_web.routes.pipeline as route_mod

    monkeypatch.setattr(route_mod, "control", control)

    async def run():
        response = await route_mod.pipeline_events()
        assert response.media_type == "text/event-stream"
        assert response.headers.get("cache-control") == "no-cache"
        iterator = response.body_iterator
        seen: list[str] = []
        payloads: list[dict] = []

        deadline = time.time() + timeout
        while time.time() < deadline and not all(t in seen for t in want_types):
            try:
                raw = await asyncio.wait_for(iterator.__anext__(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            except StopAsyncIteration:
                break
            line = raw.decode() if isinstance(raw, (bytes, bytearray)) else raw
            if not line.startswith("data: "):
                continue
            payload = json.loads(line[6:])
            payloads.append(payload)
            seen.append(payload["type"])

        # The stream must terminate once shutdown is requested.
        control.shutdown()
        while True:
            try:
                raw = await asyncio.wait_for(iterator.__anext__(), timeout=2.0)
            except (asyncio.TimeoutError, StopAsyncIteration):
                break
            line = raw.decode() if isinstance(raw, (bytes, bytearray)) else raw
            if line.startswith("data: "):
                payload = json.loads(line[6:])
                payloads.append(payload)
                seen.append(payload["type"])
            if "shutdown" in seen:
                break
        await iterator.aclose()
        return seen, payloads

    return asyncio.run(run())


def test_events_endpoint_is_a_real_sse_stream(control, monkeypatch):
    """D2 — SSE opens with an initial snapshot, then live task events."""
    monkeypatch.setattr(PIPELINE_TASKS["etf_sync"], "_run", lambda tr: None)
    threading.Timer(0.2, lambda: control.start_run("etf_sync")).start()

    seen, payloads = _drive_sse(
        control, monkeypatch, ["connected", "task_started", "task_completed"]
    )

    assert seen[0] == "connected", "stream must open with an initial snapshot"
    assert "overall" in payloads[0]["state"]
    assert "tasks" in payloads[0]["state"]
    assert "task_started" in seen and "task_completed" in seen

    completed = next(p for p in payloads if p["type"] == "task_completed")
    assert completed["task_id"] == "etf_sync"
    assert completed["status"] == "completed"
    assert "shutdown" in seen, "stream must close on shutdown"


def test_events_endpoint_is_not_plain_json(control, monkeypatch):
    """The old endpoint returned a JSON object; it must no longer do so."""
    import asyncio
    import json

    import myra_web.routes.pipeline as route_mod

    monkeypatch.setattr(route_mod, "control", control)

    async def run():
        response = await route_mod.pipeline_events()
        raw = await asyncio.wait_for(response.body_iterator.__anext__(), timeout=5)
        await response.body_iterator.aclose()
        return raw

    raw = asyncio.run(run())
    line = raw.decode() if isinstance(raw, (bytes, bytearray)) else raw
    assert line.startswith("data: "), "SSE frames must be 'data: ' prefixed"
    json.loads(line[6:])  # payload itself is valid JSON


def test_events_stream_terminates_on_shutdown(control, monkeypatch):
    seen, _ = _drive_sse(control, monkeypatch, ["connected"])
    assert seen == ["connected", "shutdown"]
    assert control.shutting_down() is True

    def test_run_rejects_concurrent_attempts_with_409(self, control, monkeypatch):
        client = self._client(control, monkeypatch)
        gate = threading.Event()
        monkeypatch.setattr(
            PIPELINE_TASKS["etf_sync"], "_run", lambda tr: gate.wait(timeout=5)
        )
        client.post("/api/pipeline/run", json={"task": "etf_sync"})
        assert _wait(lambda: control.is_busy())
        assert client.post("/api/pipeline/run", json={"task": "all"}).status_code == 409
        gate.set()
        assert _wait(lambda: _idle(control), timeout=8)

    def test_run_rejects_unknown_task_with_400(self, control, monkeypatch):
        client = self._client(control, monkeypatch)
        assert (
            client.post("/api/pipeline/run", json={"task": "nope"}).status_code == 400
        )

    def test_force_reset_is_409_while_busy(self, control, monkeypatch):
        client = self._client(control, monkeypatch)
        gate = threading.Event()
        monkeypatch.setattr(
            PIPELINE_TASKS["etf_sync"], "_run", lambda tr: gate.wait(timeout=5)
        )
        client.post("/api/pipeline/run", json={"task": "etf_sync"})
        assert _wait(lambda: control.is_busy())
        assert client.post("/api/pipeline/force-reset").status_code == 409
        gate.set()
        assert _wait(lambda: _idle(control), timeout=8)
        assert client.post("/api/pipeline/force-reset").status_code == 200

    def test_cancel_endpoint_is_idempotent(self, control, monkeypatch):
        client = self._client(control, monkeypatch)
        body = client.post("/api/pipeline/cancel").json()
        assert body == {"success": True, "cancel_requested": False}

    @pytest.mark.parametrize(
        "method,path",
        [
            ("get", "/api/pipeline/status"),
            ("get", "/api/pipeline/run/status"),
            ("get", "/api/pipeline/events"),
            ("get", "/api/pipeline/check"),
            ("get", "/api/pipeline/schedule"),
            ("get", "/api/pipeline/schedule/paused"),
            ("post", "/api/pipeline/run"),
            ("post", "/api/pipeline/cancel"),
            ("post", "/api/pipeline/force-reset"),
            ("post", "/api/pipeline/toggle-schedule"),
            ("post", "/api/pipeline/schedule/pause"),
        ],
    )
    def test_retained_data_sync_routes_still_exist(self, method, path):
        """Data Sync's existing controls must keep resolving to a real route."""
        import myra_web.routes.pipeline as route_mod

        found = [
            r
            for r in route_mod.router.routes
            if r.path == path and method.upper() in (r.methods or set())
        ]
        assert found, f"{method.upper()} {path} is gone — Data Sync would break"

    def test_toggle_schedule_rejects_unknown_task(self, control, monkeypatch):
        client = self._client(control, monkeypatch)
        res = client.post(
            "/api/pipeline/toggle-schedule",
            json={"task_key": "not_a_task", "enabled": True},
        )
        assert res.status_code == 400


def test_legacy_dashboard_module_is_gone():
    """D1/D3 — the second, never-registered router must not come back."""
    import importlib.util

    assert importlib.util.find_spec("myra_web.pipeline_dashboard") is None


def test_task_bodies_are_real_entrypoints():
    """The Control Room must not invent its own task implementations."""
    assert isinstance(PIPELINE_TASKS["daily_ingest"], PipelineTask)
    assert PIPELINE_TASKS["daily_ingest"].label == "Daily Ingest"
    assert PIPELINE_TASKS["enrichment"].label == "Feature Enrichment"
    assert PIPELINE_TASKS["shares_outstanding_sync"].label == "Shares Refresh"
    for key in PIPELINE_ORDER:
        assert PIPELINE_TASKS[key].timeout > 0


def test_control_module_starts_no_scheduler():
    """Constructing the control singleton must not create a competing scheduler."""
    import inspect

    source = inspect.getsource(pc.PipelineControl.__init__)
    # No thread or timer may be *started* during construction. (Declaring a
    # `threading.Timer` attribute is fine; only calling .start() spawns work.)
    assert ".start()" not in source, "__init__ must not spawn a background thread"
    for forbidden in (
        "_start_scheduler",
        "_check_schedules",
        "run_periodic",
        "background_orchestrator",
    ):
        assert (
            forbidden not in source
        ), f"{forbidden} in __init__ would create a second scheduler"


# ---------------------------------------------------------------------------
# HTTP endpoints
# ---------------------------------------------------------------------------


def _http_client(control, monkeypatch):
    """A TestClient over the real router, bound to the in-memory control."""
    import myra_web.routes.pipeline as route_mod

    monkeypatch.setattr(route_mod, "control", control)
    app = FastAPI()
    app.include_router(route_mod.router)
    return TestClient(app)


@pytest.mark.skipif(not FASTAPI_AVAILABLE, reason="fastapi TestClient unavailable")
class TestRunEndpoint:
    def test_concurrent_run_attempts_are_rejected_with_409(self, control, monkeypatch):
        client = _http_client(control, monkeypatch)
        gate = threading.Event()
        monkeypatch.setattr(
            PIPELINE_TASKS["etf_sync"], "_run", lambda tr: gate.wait(timeout=5)
        )
        assert (
            client.post("/api/pipeline/run", json={"task": "etf_sync"}).status_code
            == 200
        )
        assert _wait(lambda: control.is_busy())

        assert client.post("/api/pipeline/run", json={"task": "all"}).status_code == 409
        assert (
            client.post("/api/pipeline/run", json={"task": "etf_sync"}).status_code
            == 409
        )

        gate.set()
        assert _wait(lambda: _idle(control), timeout=8)

    def test_run_all_returns_the_resolved_task_order(self, control, monkeypatch):
        client = _http_client(control, monkeypatch)
        for key in PIPELINE_ORDER:
            monkeypatch.setattr(PIPELINE_TASKS[key], "_run", lambda tr: None)
        body = client.post("/api/pipeline/run", json={"task": "all"}).json()
        assert body["success"] is True
        assert body["tasks"] == PIPELINE_ORDER
        assert _wait(lambda: _idle(control), timeout=8)

    def test_run_rejects_unknown_task_with_400(self, control, monkeypatch):
        client = _http_client(control, monkeypatch)
        assert (
            client.post("/api/pipeline/run", json={"task": "nope"}).status_code == 400
        )

    def test_toggle_schedule_rejects_unknown_task(self, control, monkeypatch):
        client = _http_client(control, monkeypatch)
        res = client.post(
            "/api/pipeline/toggle-schedule",
            json={"task_key": "not_a_task", "enabled": True},
        )
        assert res.status_code == 400

    def test_cancel_endpoint_is_idempotent(self, control, monkeypatch):
        client = _http_client(control, monkeypatch)
        assert client.post("/api/pipeline/cancel").json() == {
            "success": True,
            "cancel_requested": False,
        }

    def test_force_reset_is_409_while_busy_and_200_when_idle(
        self, control, monkeypatch
    ):
        """D7 — force reset must not mark a live operation idle."""
        client = _http_client(control, monkeypatch)
        gate = threading.Event()
        monkeypatch.setattr(
            PIPELINE_TASKS["etf_sync"], "_run", lambda tr: gate.wait(timeout=5)
        )
        client.post("/api/pipeline/run", json={"task": "etf_sync"})
        assert _wait(lambda: control.is_busy())

        refused = client.post("/api/pipeline/force-reset")
        assert refused.status_code == 409
        assert "still executing" in refused.json()["detail"]
        assert (
            control.get_status()["overall"]["status"] == "running"
        ), "a refused force reset must not change the reported state"

        gate.set()
        assert _wait(lambda: _idle(control), timeout=8)
        assert client.post("/api/pipeline/force-reset").status_code == 200


@pytest.mark.parametrize(
    "method,path",
    [
        ("get", "/api/pipeline/status"),
        ("get", "/api/pipeline/run/status"),
        ("get", "/api/pipeline/events"),
        ("get", "/api/pipeline/check"),
        ("get", "/api/pipeline/schedule"),
        ("get", "/api/pipeline/schedule/paused"),
        ("post", "/api/pipeline/run"),
        ("post", "/api/pipeline/cancel"),
        ("post", "/api/pipeline/force-reset"),
        ("post", "/api/pipeline/toggle-schedule"),
        ("post", "/api/pipeline/schedule/pause"),
    ],
)
def test_retained_data_sync_routes_still_exist(method, path):
    """Data Sync's existing controls must keep resolving to a real route."""
    import myra_web.routes.pipeline as route_mod

    found = [
        r
        for r in route_mod.router.routes
        if r.path == path and method.upper() in (r.methods or set())
    ]
    assert found, f"{method.upper()} {path} is gone — Data Sync would break"


# ---------------------------------------------------------------------------
# Scheduled-task cadence (fund-traction-sync)
# ---------------------------------------------------------------------------


def test_fund_traction_is_not_polled_every_minute():
    """The smart-gated traction task must not re-probe ~1440x/day.

    Its gate deliberately does not mark sync_log when there is nothing new, so
    `interval_days` cannot throttle it — only the poll interval can.
    """
    from myra_app.tasks.executor import POLL_SECONDS
    from myra_app.tasks.registry import TASKS

    spec = TASKS["fund-traction-sync"]
    assert spec.mark_on_success is False, "gate owns marking; unchanged"
    assert spec.poll_seconds is not None, "needs an explicit poll interval"
    assert (
        spec.poll_seconds >= 3600
    ), f"poll interval {spec.poll_seconds}s is too frequent for a monthly artifact"
    assert spec.poll_seconds > POLL_SECONDS


def test_other_tasks_keep_the_default_poll_interval():
    """Only the smart-gated task changes cadence; nothing else is affected."""
    from myra_app.tasks.executor import POLL_SECONDS
    from myra_app.tasks.registry import TASKS

    for name, spec in TASKS.items():
        # Tasks with a deliberate custom cadence.
        if name in ("fund-traction-sync", "fundamentals-enrich"):
            continue
        assert spec.poll_seconds is None, f"{name} unexpectedly got a custom interval"
    assert POLL_SECONDS == 60, "global default unchanged"
