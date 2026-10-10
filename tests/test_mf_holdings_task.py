"""Tests for the RupeeVest holdings sync background task (smart gate + marking)."""

import logging
import threading

import pytest

from myra_app.tasks import mf_holdings as task
from myra_app.tasks.context import TaskContext


def _ctx(shutdown=False):
    ev = threading.Event()
    if shutdown:
        ev.set()
    return TaskContext(shutdown_event=ev, logger=logging.getLogger("t"))


@pytest.fixture
def marks(monkeypatch):
    """Capture ``_mark_task_run`` calls and stub task_tracker registration."""
    seen = []
    monkeypatch.setattr(task, "_mark_task_run", lambda *a, **k: seen.append((a, k)))
    monkeypatch.setattr("myra_app.task_tracker.register", lambda *a, **k: 1)
    monkeypatch.setattr("myra_app.task_tracker.unregister", lambda *a, **k: None)
    return seen


def _patch_sync(monkeypatch, *, names, coverage, result=None):
    """Stub load_fund_names/month_coverage/sync_mf_holdings; return call log."""
    import myra_app.mf_holdings_sync as mhs

    calls = []
    monkeypatch.setattr(mhs, "load_fund_names", lambda *a, **k: names)
    monkeypatch.setattr(mhs, "month_coverage", lambda *a, **k: coverage)

    def _sync(*a, **k):
        calls.append(k)
        return result

    monkeypatch.setattr(mhs, "sync_mf_holdings", _sync)
    return calls


def test_noop_when_previous_month_fully_reported(monkeypatch, marks):
    calls = _patch_sync(monkeypatch, names=["A", "B"], coverage=2)

    task.run(_ctx())

    assert calls == []  # nothing to fetch
    assert marks[0][0][0] == task.LABEL
    assert marks[0][1].get("status") is None


def test_syncs_with_writes_when_month_incomplete(monkeypatch, marks):
    calls = _patch_sync(
        monkeypatch,
        names=["A", "B"],
        coverage=1,
        result={
            "success": True,
            "funds_synced": 2,
            "funds_seen": 2,
            "holdings_rows": 10,
            "aum_rows": 2,
            "months": ["2026-09"],
        },
    )

    task.run(_ctx())

    assert len(calls) == 1 and calls[0].get("dry_run") is False
    assert marks[0][1].get("status") is None


def test_failed_result_marks_failure(monkeypatch, marks):
    _patch_sync(
        monkeypatch,
        names=["A", "B"],
        coverage=0,
        result={
            "success": False,
            "error": "5 fund(s) failed (tolerance 3)",
            "funds_synced": 0,
            "funds_seen": 2,
            "holdings_rows": 0,
            "aum_rows": 0,
            "months": [],
        },
    )

    task.run(_ctx())

    assert marks[0][1]["status"] == "failed"
    assert "failed" in marks[0][1]["error_message"]


def test_shutdown_returns_early(monkeypatch, marks):
    import myra_app.mf_holdings_sync as mhs

    monkeypatch.setattr(
        mhs, "load_fund_names", lambda *a, **k: pytest.fail("should not be read")
    )
    task.run(_ctx(shutdown=True))
    assert marks == []
