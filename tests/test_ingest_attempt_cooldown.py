"""Regression tests for the durable ingestion attempt cooldown (Defect B).

Previously the cooldown read ``sync_log.daily_ingest.last_run``, which a hard
error deliberately never writes — so neither the executor nor the watchdog was
actually throttled and both could invoke ingestion every 60 seconds.

Attempts now live in their own ``daily_ingest_attempt`` row, written at the start
of every attempt. These tests use the isolated ``fixture_db_dir`` database, so the
attempt row really is persisted and re-read.
"""

from __future__ import annotations

import datetime as dt
import threading

import pytest

from myra_app.tasks import executor as ex
from myra_app.tasks import ingest as ingest_mod
from myra_app.tasks import watchdog as wd
from myra_app.tasks.context import TaskContext
from myra_app.tasks.registry import TASKS
from myra_app.utils import task_utils

pytestmark = pytest.mark.usefixtures("fixture_db_dir")

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
COOLDOWN = 30


def _ist(minutes_ago: float = 0.0) -> dt.datetime:
    """A real, tz-aware IST timestamp offset back from now.

    Deliberately anchored to the real clock: ``_mark_task_attempt`` stamps with
    ``now_ist()``, so comparing against an invented future date would make every
    cooldown look expired.
    """
    return task_utils.now_ist() - dt.timedelta(minutes=minutes_ago)


def _meta_path():
    import os

    from myra_app.constants import DB_DIR

    return os.path.join(DB_DIR, "myra_metadata.db")


def _age_row(now: dt.datetime, minutes: int) -> None:
    """Backdate the attempt row so the cooldown appears elapsed/active."""
    import sqlite3

    ts = (now - dt.timedelta(minutes=minutes)).isoformat()
    conn = sqlite3.connect(_meta_path())
    try:
        conn.execute(
            "INSERT OR REPLACE INTO sync_log "
            "(task_name, last_run, last_status, error_message) VALUES (?, ?, ?, NULL)",
            (task_utils.attempt_label("daily_ingest"), ts, task_utils.ATTEMPT_STATUS),
        )
        conn.commit()
    finally:
        conn.close()


def _read(label: str):
    import sqlite3

    conn = sqlite3.connect(f"file:{_meta_path()}?mode=ro", uri=True)
    try:
        return conn.execute(
            "SELECT task_name, last_run, last_status FROM sync_log WHERE task_name = ?",
            (label,),
        ).fetchone()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Attempt bookkeeping basics
# ---------------------------------------------------------------------------


def test_attempt_row_is_separate_from_the_freshness_row(fixture_db_dir):
    task_utils._mark_task_attempt("daily_ingest")
    attempt = _read(task_utils.attempt_label("daily_ingest"))
    assert attempt is not None
    assert attempt[2] == task_utils.ATTEMPT_STATUS
    # The freshness row must NOT be created by an attempt.
    assert _read("daily_ingest") is None


def test_attempt_row_never_reads_as_success():
    from myra_app.pipeline_control import PipelineControl

    assert (
        PipelineControl._normalise_sync_status("attempted", "2026-10-12T19:00:00")
        != "completed"
    )
    assert (
        PipelineControl._normalise_sync_status("attempted", "2026-10-12T19:00:00")
        == "no_new_data"
    )


def test_internal_attempt_rows_are_hidden_from_task_displays():
    assert task_utils.is_internal_sync_log_label("daily_ingest_attempt") is True
    assert task_utils.is_internal_sync_log_label("daily_ingest") is False
    assert task_utils.is_internal_sync_log_label("etf_sync") is False


# ---------------------------------------------------------------------------
# 4/5: both drivers share one cooldown
# ---------------------------------------------------------------------------


def test_executor_blocks_repeated_attempts_within_the_cooldown():
    """Case 4 — the executor must honour the shared cooldown."""
    spec = TASKS["daily-ingest"]
    assert spec.attempt_cooldown_minutes == COOLDOWN

    task_utils._mark_task_attempt("daily_ingest")
    now = _ist()
    # The row was written "now", so both drivers must refuse.
    assert task_utils._attempt_cooldown_elapsed("daily_ingest", COOLDOWN, now) is False
    assert ex.attempt_allowed(spec) is False
    assert wd._cooldown_elapsed(now) is False, "watchdog must use the same clock"


def test_tasks_without_a_cooldown_are_never_throttled():
    for name, spec in TASKS.items():
        if name == "daily-ingest":
            continue
        assert spec.attempt_cooldown_minutes is None, f"{name} unexpectedly throttled"
    assert ex.attempt_allowed(TASKS["etf-sync"]) is True
    assert ex.attempt_allowed(TASKS["institutional-sync"]) is True


def test_cooldown_expires_and_both_drivers_allow_again():
    """Case 7 — a previous attempt must not permanently block."""
    _age_row(_ist(), minutes=COOLDOWN + 1)
    now = _ist()
    assert task_utils._attempt_cooldown_elapsed("daily_ingest", COOLDOWN, now) is True
    assert ex.attempt_allowed(TASKS["daily-ingest"]) is True
    assert wd._cooldown_elapsed(now) is True


def test_cooldown_boundary_is_inclusive():
    _age_row(_ist(), minutes=COOLDOWN)
    assert (
        task_utils._attempt_cooldown_elapsed("daily_ingest", COOLDOWN, _ist()) is True
    )


# ---------------------------------------------------------------------------
# 6: a hard error cannot cause 60s hammering
# ---------------------------------------------------------------------------


def test_hard_error_writes_no_success_but_does_throttle(fixture_db_dir):
    """Case 6 — the defect itself: error path must still advance the clock."""
    import myra_app.eod2_sync as eod2_sync

    now = _ist()
    _age_row(now, minutes=0)  # pretend a real attempt just happened

    marks = []
    orig_mark, orig_attempt = ingest_mod._mark_task_run, ingest_mod._mark_task_attempt
    ingest_mod._mark_task_run = lambda *a, **k: marks.append(a)
    ingest_mod._mark_task_attempt = lambda *a, **k: None
    eod2_sync.sync_eod2_data = lambda: dict(
        rows_inserted=0,
        symbols_updated=0,
        skipped=0,
        rejected_rows=0,
        rejected_symbols=0,
        error="boom",
    )
    try:
        from myra_app import constants

        constants.USE_EOD2_DATA = True
        constants.ENABLE_DAILY_INGEST = True
        ctx = TaskContext(shutdown_event=threading.Event(), logger=ingest_mod.logger)
        ingest_mod.run(ctx, force=True)
    finally:
        ingest_mod._mark_task_run = orig_mark
        ingest_mod._mark_task_attempt = orig_attempt

    assert marks == [], "a hard error must not write a success marker"
    # And the attempt clock still blocks the next poll.
    assert ex.attempt_allowed(TASKS["daily-ingest"]) is False


def test_attempt_is_stamped_before_the_sync_runs(monkeypatch):
    """The stamp must land before any work, so a crash still throttles."""
    seen = []
    import myra_app.eod2_sync as eod2_sync
    from myra_app import constants

    monkeypatch.setattr(constants, "USE_EOD2_DATA", True)
    monkeypatch.setattr(constants, "ENABLE_DAILY_INGEST", True)
    monkeypatch.setattr(
        ingest_mod, "_mark_task_attempt", lambda t: seen.append(("attempt", t))
    )
    monkeypatch.setattr(ingest_mod, "_mark_task_run", lambda *a, **k: None)

    def _boom():
        seen.append(("sync", None))
        return dict(
            rows_inserted=0,
            symbols_updated=0,
            skipped=0,
            rejected_rows=0,
            rejected_symbols=0,
            error="boom",
        )

    monkeypatch.setattr(eod2_sync, "sync_eod2_data", _boom)

    ctx = TaskContext(shutdown_event=threading.Event(), logger=ingest_mod.logger)
    ingest_mod.run(ctx, force=True)

    assert seen[0] == ("attempt", "daily_ingest"), seen
    assert ("sync", None) in seen


# ---------------------------------------------------------------------------
# 8: success semantics unchanged
# ---------------------------------------------------------------------------


def test_successful_ingestion_still_marks_success_and_freshness(
    fixture_db_dir, monkeypatch
):
    """Case 8 / 11 — success path must be untouched by the attempt clock."""
    marks = []
    ingested = []
    hooks = []

    monkeypatch.setattr(
        ingest_mod,
        "_mark_task_run",
        lambda n, status="success", error_message=None: marks.append((n, status)),
    )
    monkeypatch.setattr(ingest_mod, "_mark_ingested_today", lambda: ingested.append(1))
    monkeypatch.setattr(
        ingest_mod, "enrich_corporate_actions", lambda *a, **k: hooks.append("ca")
    )
    import myra_app.portfolio_db as portfolio_db

    monkeypatch.setattr(portfolio_db, "auto_refresh_portfolio", lambda: {})

    import myra_app.eod2_sync as eod2_sync
    from myra_app import constants

    monkeypatch.setattr(constants, "USE_EOD2_DATA", True)
    monkeypatch.setattr(constants, "ENABLE_DAILY_INGEST", True)
    monkeypatch.setattr(
        eod2_sync,
        "sync_eod2_data",
        lambda: dict(
            rows_inserted=500,
            symbols_updated=100,
            skipped=0,
            rejected_rows=0,
            rejected_symbols=0,
            error=None,
        ),
    )

    ctx = TaskContext(shutdown_event=threading.Event(), logger=ingest_mod.logger)
    ingest_mod.run(ctx, force=True)

    assert ("daily_ingest", "success") in marks
    assert ingested == [1], "freshness must advance on real data"
    assert hooks == ["ca"], "success hooks must still run"


def test_no_new_data_keeps_freshness_and_advances_attempt_clock(
    fixture_db_dir, monkeypatch
):
    """no_new_data stays truthful and is retry-eligible after the cooldown."""
    marks = []
    monkeypatch.setattr(
        ingest_mod,
        "_mark_task_run",
        lambda n, status="success", error_message=None: marks.append((n, status)),
    )
    monkeypatch.setattr(
        ingest_mod,
        "_mark_ingested_today",
        lambda: pytest.fail("freshness must not advance"),
    )

    import myra_app.eod2_sync as eod2_sync
    from myra_app import constants

    monkeypatch.setattr(constants, "USE_EOD2_DATA", True)
    monkeypatch.setattr(constants, "ENABLE_DAILY_INGEST", True)
    monkeypatch.setattr(
        eod2_sync,
        "sync_eod2_data",
        lambda: dict(
            rows_inserted=0,
            symbols_updated=0,
            skipped=0,
            rejected_rows=0,
            rejected_symbols=0,
            error=None,
        ),
    )

    ctx = TaskContext(shutdown_event=threading.Event(), logger=ingest_mod.logger)
    ingest_mod.run(ctx, force=True)

    assert marks[-1] == ("daily_ingest", ingest_mod.NO_NEW_DATA)
    # Attempt clock advanced, so the next attempt is throttled...
    assert ex.attempt_allowed(TASKS["daily-ingest"]) is False
    # ...but only temporarily.
    _age_row(_ist(), minutes=COOLDOWN + 1)
    assert ex.attempt_allowed(TASKS["daily-ingest"]) is True


# ---------------------------------------------------------------------------
# 9: durability
# ---------------------------------------------------------------------------


def test_cooldown_survives_a_restart_via_persisted_state(fixture_db_dir):
    """Case 9 — the clock must come from the database, not process memory.

    Durability is demonstrated by inspecting *where* the value lives rather than
    by tearing down ``sys.modules`` — doing the latter leaves later tests holding
    a stale module object and silently poisons unrelated assertions.
    """
    task_utils._mark_task_attempt("daily_ingest")
    label = task_utils.attempt_label("daily_ingest")

    # The attempt timestamp is persisted in the existing sync_log table...
    row = _read(label)
    assert row is not None and row[1], "attempt timestamp must be persisted"

    # ...and nothing caches it in module state, so every check re-reads the DB.
    assert not hasattr(task_utils, "_last_attempt_at")
    assert not hasattr(task_utils, "_ATTEMPT_CACHE")
    assert (
        task_utils._attempt_cooldown_elapsed("daily_ingest", COOLDOWN, _ist()) is False
    )

    # A freshly started process would compute the same answer from the same row.
    _age_row(_ist(), minutes=COOLDOWN + 1)
    assert (
        task_utils._attempt_cooldown_elapsed("daily_ingest", COOLDOWN, _ist()) is True
    )


# ---------------------------------------------------------------------------
# 10: no duplicate concurrent ingestion
# ---------------------------------------------------------------------------


def test_concurrent_runs_cannot_overlap(monkeypatch):
    """Case 10 — executor and watchdog may race; only one may ingest."""
    inside = threading.Event()
    release = threading.Event()
    entered = []

    def _blocking(ctx, force=False):
        entered.append(1)
        inside.set()
        release.wait(timeout=5)

    monkeypatch.setattr(ingest_mod, "_run_ingest_locked", _blocking)

    ctx = TaskContext(shutdown_event=threading.Event(), logger=ingest_mod.logger)
    t1 = threading.Thread(target=ingest_mod.run, args=(ctx,))
    t1.start()
    assert inside.wait(timeout=5)

    # Second caller while the first is still running must be dropped.
    ingest_mod.run(ctx, force=True)
    assert len(entered) == 1, "a concurrent attempt must not run"

    release.set()
    t1.join(timeout=5)
    # After completion the lock is free again.
    ingest_mod.run(ctx, force=True)
    assert len(entered) == 2, "the lock must be released after a run"
    release.set()
