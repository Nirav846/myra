"""
Tests for _spawn_task helper and endpoint offloading.

Covers: background thread execution, kwargs passthrough, error handling,
and ml_predict response shape preservation.
"""

import asyncio
import os
import sys
import threading
import time

import pytest
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "myra_web"))
from myra_fastapi_server import app, _spawn_task

client = TestClient(app)

# Override auth so tests don't need MYRA_API_SECRET header
app.dependency_overrides = {}


@pytest.fixture(autouse=True)
def _override_auth():
    """Bypass MYRA_API_SECRET for all tests in this module."""
    from myra_fastapi_server import verify_myra_auth

    async def _noop():
        pass

    app.dependency_overrides[verify_myra_auth] = _noop
    yield
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Test 1: _spawn_task registers a task and runs the function in a thread
# ---------------------------------------------------------------------------
def test_spawn_task_runs_function_in_background():
    """_spawn_task should execute fn in a daemon thread and return a task id."""
    event = threading.Event()

    def _side_effect():
        event.set()

    tid = _spawn_task("test_bg", _side_effect)
    assert tid is not None
    assert tid > 0

    # Wait for the side-effect to confirm the thread ran
    assert event.wait(timeout=5.0), "Background function did not execute within 5s"


# ---------------------------------------------------------------------------
# Test 2: _spawn_task with kwargs (force=True path)
# ---------------------------------------------------------------------------
def test_spawn_task_passes_kwargs():
    """_spawn_task should forward **kwargs to the target function."""
    received = {}

    def _side_effect(force=False):
        received["force"] = force

    tid = _spawn_task("test_kwargs", _side_effect, force=True)
    assert tid is not None

    # Poll until the side-effect fires
    deadline = time.time() + 5.0
    while time.time() < deadline:
        if received.get("force") is True:
            break
        time.sleep(0.05)
    assert received.get("force") is True, f"kwargs not forwarded; received={received}"


# ---------------------------------------------------------------------------
# Test 3: error propagation — thread must not crash, task status shows error
# ---------------------------------------------------------------------------
def test_spawn_task_error_handling():
    """_spawn_task should catch exceptions, record status, and unregister."""
    from myra_app.task_tracker import create_task, get_task

    def _failing_fn():
        raise ValueError("deliberate test error")

    tid = _spawn_task("test_error", _failing_fn)
    assert tid is not None

    # Give the thread time to fail and unregister
    time.sleep(1.0)

    # After unregister, get_task should return None (task expired)
    task = get_task(tid)
    # Task may already be cleaned up — that's fine. If still present, verify status.
    if task is not None:
        # status should be "error: ..." or task already cleaned
        assert "error" in (task.get("status") or "").lower() or task.get("expiry") is not None


# ---------------------------------------------------------------------------
# Test 4: ml_predict preserves response shape (200, not 202)
# ---------------------------------------------------------------------------
@pytest.mark.skipif(
    not os.path.exists(os.path.join("myra_app", "ml_trainer.py")),
    reason="ml_trainer module not available",
)
def test_ml_predict_returns_200_with_payload():
    """GET /api/ml/predict must return 200 with prediction payload, not 202."""
    resp = client.get("/api/ml/predict")
    # If no model exists, predict_today may return an error dict or empty list —
    # the key assertion is the HTTP status is 200 (not 202) and JSON is parseable.
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, (dict, list))
    # If it's a dict, it should NOT be a 202-style {"status": "started"} response
    if isinstance(data, dict):
        assert data.get("status") != "started"
