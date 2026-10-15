"""Regression tests for daily-ingest freshness outcome handling.

Defect: ``myra_app/tasks/ingest.py`` recorded ``success`` in ``sync_log`` and
advanced ``metadata.last_sync_date`` even when zero rows were inserted, so a
database that was a day stale reported itself as freshly ingested.

Outcomes now:
  * ``rows_inserted > 0``           -> success marker + last_sync_date advanced
  * ``rows_inserted == 0``, no error -> ``no_new_data`` (throttled, not fresh)
  * ``error``                        -> established failure behaviour, no markers

Everything runs against the isolated temporary database supplied by the
``fixture_db_dir`` fixture. No live database, no network, no real ingestion.
"""

from __future__ import annotations

import sqlite3
import threading

import pytest

from myra_app.tasks import ingest as ingest_mod
from myra_app.tasks.context import TaskContext

pytestmark = pytest.mark.usefixtures("fixture_db_dir")


@pytest.fixture
def ctx():
    """A TaskContext that never signals shutdown."""
    return TaskContext(shutdown_event=threading.Event(), logger=ingest_mod.logger)


@pytest.fixture
def synced(monkeypatch):
    """Record every marker/hook call made by ingest.run()."""
    calls = {"mark_run": [], "mark_ingested": 0, "hooks": []}

    monkeypatch.setattr(
        ingest_mod,
        "_mark_task_run",
        lambda name, status="success", error_message=None: calls["mark_run"].append(
            (name, status, error_message)
        ),
    )
    monkeypatch.setattr(
        ingest_mod,
        "_mark_ingested_today",
        lambda: calls.__setitem__("mark_ingested", calls["mark_ingested"] + 1),
    )
    return calls


def _stub_eod2(monkeypatch, result):
    """Patch the EOD2 sync + every downstream hook; return the hook recorder."""
    import myra_app.eod2_sync as eod2_sync

    monkeypatch.setattr(eod2_sync, "sync_eod2_data", lambda: result)

    import myra_app.fundamental_sync as fundamental_sync

    class FakeFundamentalSync:
        def _compute_market_cap_from_prices(self):
            raise AssertionError("market-cap hook must not run without new data")

    monkeypatch.setattr(
        fundamental_sync, "FundamentalSync", FakeFundamentalSync, raising=False
    )

    import myra_app.portfolio_db as portfolio_db

    def _no_portfolio():
        raise AssertionError("portfolio hook must not run without new data")

    monkeypatch.setattr(portfolio_db, "auto_refresh_portfolio", _no_portfolio)

    # ingest.py imported enrich_corporate_actions at module import time.
    monkeypatch.setattr(
        ingest_mod,
        "enrich_corporate_actions",
        lambda *a, **k: pytest.fail(
            "corporate-actions hook must not run without new data"
        ),
    )
    return result


NO_ROWS = {
    "rows_inserted": 0,
    "symbols_updated": 0,
    "skipped": 0,
    "rejected_rows": 0,
    "rejected_symbols": 0,
    "error": None,
}


#: A run that actually ingested something - the positive path.
NEW_ROWS = dict(NO_ROWS, rows_inserted=3184, symbols_updated=1200)


def _run_eod2(ctx, monkeypatch, result, force: bool = True):
    from myra_app import constants

    monkeypatch.setattr(constants, "USE_EOD2_DATA", True)
    monkeypatch.setattr(constants, "ENABLE_DAILY_INGEST", True)
    ingest_mod.run(ctx, force=force)


# ---------------------------------------------------------------------------
# 1-4: zero rows, no error
# ---------------------------------------------------------------------------


def test_zero_rows_records_no_new_data(ctx, monkeypatch, synced):
    """Case 1 — the reported defect: 0 rows must not be recorded as success."""
    _stub_eod2(monkeypatch, dict(NO_ROWS))
    _run_eod2(ctx, monkeypatch, NO_ROWS)

    assert synced["mark_run"], "an outcome must still be recorded for throttling"
    name, status, _msg = synced["mark_run"][-1]
    assert name == "daily_ingest"
    assert status == ingest_mod.NO_NEW_DATA
    assert status != "success"


def test_zero_rows_does_not_advance_last_sync_date(ctx, monkeypatch, synced):
    """Case 2 — freshness marker must not move."""
    _stub_eod2(monkeypatch, dict(NO_ROWS))
    _run_eod2(ctx, monkeypatch, NO_ROWS)
    assert synced["mark_ingested"] == 0


def test_zero_rows_writes_no_success_marker(ctx, monkeypatch, synced):
    """Case 3 — no 'success' anywhere in the recorded outcomes."""
    _stub_eod2(monkeypatch, dict(NO_ROWS))
    _run_eod2(ctx, monkeypatch, NO_ROWS)
    statuses = [status for _n, status, _m in synced["mark_run"]]
    assert "success" not in statuses


def test_zero_rows_does_not_run_post_ingest_hooks(ctx, monkeypatch, synced):
    """Case 4 — the _stub_eod2 helpers raise if any hook runs."""
    _stub_eod2(monkeypatch, dict(NO_ROWS))
    _run_eod2(ctx, monkeypatch, NO_ROWS)
    assert synced["mark_ingested"] == 0


def test_no_new_data_message_is_truthful_and_non_speculative(ctx, monkeypatch, synced):
    """Must describe the observation, not assert a cause."""
    _stub_eod2(monkeypatch, dict(NO_ROWS))
    _run_eod2(ctx, monkeypatch, NO_ROWS)
    _name, _status, msg = synced["mark_run"][-1]
    low = msg.lower()
    assert "0 rows inserted" in low
    assert "freshness not established" in low
    for speculation in (
        "not yet available",
        "unavailable",
        "not published",
        "upstream down",
        "failed to download",
    ):
        assert speculation not in low, f"message speculates about cause: {speculation}"


# ---------------------------------------------------------------------------
# 5: positive row count -> normal success path
# ---------------------------------------------------------------------------


def test_positive_rows_takes_the_success_path(ctx, monkeypatch, synced):
    _stub_eod2(monkeypatch, dict(NO_ROWS, rows_inserted=3184, symbols_updated=1200))
    _run_eod2(ctx, monkeypatch, NO_ROWS)

    assert synced["mark_ingested"] == 1
    assert ("daily_ingest", "success", None) in synced["mark_run"]
    assert not any(s == ingest_mod.NO_NEW_DATA for _n, s, _m in synced["mark_run"])


def test_success_path_runs_post_ingest_hooks(ctx, monkeypatch, synced):
    """Hooks must still run when data really landed."""
    ran = {"mcap": False, "portfolio": False, "ca": False}

    import myra_app.eod2_sync as eod2_sync

    monkeypatch.setattr(
        eod2_sync,
        "sync_eod2_data",
        lambda: dict(NO_ROWS, rows_inserted=500, symbols_updated=100),
    )

    import myra_app.fundamental_sync as fundamental_sync

    class FakeFundamentalSync:
        def _compute_market_cap_from_prices(self):
            ran["mcap"] = True

    monkeypatch.setattr(
        fundamental_sync, "FundamentalSync", FakeFundamentalSync, raising=False
    )
    import myra_app.portfolio_db as portfolio_db

    monkeypatch.setattr(
        portfolio_db,
        "auto_refresh_portfolio",
        lambda: ran.__setitem__("portfolio", True),
    )
    monkeypatch.setattr(
        ingest_mod,
        "enrich_corporate_actions",
        lambda *a, **k: ran.__setitem__("ca", True),
    )

    _run_eod2(ctx, monkeypatch, NO_ROWS)
    assert ran == {"mcap": True, "portfolio": True, "ca": True}


# ---------------------------------------------------------------------------
# 6: genuine failure
# ---------------------------------------------------------------------------


def test_sync_error_records_no_success_or_freshness(ctx, monkeypatch, synced):
    """Case 6 — established failure behaviour preserved."""
    import myra_app.eod2_sync as eod2_sync

    monkeypatch.setattr(
        eod2_sync,
        "sync_eod2_data",
        lambda: dict(NO_ROWS, error="eod2_data/daily/ folder not found"),
    )
    _run_eod2(ctx, monkeypatch, NO_ROWS)

    assert synced["mark_ingested"] == 0
    assert all(s != "success" for _n, s, _m in synced["mark_run"])


def test_sync_error_leaves_task_eligible_for_retry(ctx, monkeypatch, synced):
    """Failure must not burn the day's attempt allowance."""
    import myra_app.eod2_sync as eod2_sync

    monkeypatch.setattr(
        eod2_sync,
        "sync_eod2_data",
        lambda: dict(NO_ROWS, error="eod2_data/daily/ folder not found"),
    )
    _run_eod2(ctx, monkeypatch, NO_ROWS)
    assert synced["mark_run"] == [], "error path must not write a throttle marker"


# ---------------------------------------------------------------------------
# 7: no 60s hot loop
# ---------------------------------------------------------------------------


def test_no_new_data_throttles_via_last_run_not_status(fixture_db_dir, monkeypatch):
    """Case 7 — the executor's due check reads last_run only.

    ``_is_due`` (``myra_app/utils/task_utils.py:96``) selects on ``last_run``
    and ignores ``last_status``. Writing a ``no_new_data`` row must therefore
    throttle the task exactly like a success row would.
    """
    from myra_app.utils.task_utils import _is_task_due, _mark_task_run

    assert _is_task_due("freshness_probe", 1) is True, "no row -> due"

    # Exactly what _record_no_new_data writes.
    _mark_task_run(
        "freshness_probe",
        status=ingest_mod.NO_NEW_DATA,
        error_message="0 rows inserted",
    )

    assert (
        _is_task_due("freshness_probe", 1) is False
    ), "a no_new_data marker must throttle the retry loop"


# ---------------------------------------------------------------------------
# 8: a later eligible attempt can still ingest
# ---------------------------------------------------------------------------


def test_later_attempt_after_no_op_can_succeed(
    ctx, fixture_db_dir, monkeypatch, synced
):
    """Case 8 — a no-op must not permanently block real ingestion."""
    import myra_app.eod2_sync as eod2_sync
    from myra_app import constants

    monkeypatch.setattr(constants, "USE_EOD2_DATA", True)
    monkeypatch.setattr(constants, "ENABLE_DAILY_INGEST", True)

    # First attempt: nothing available.
    monkeypatch.setattr(eod2_sync, "sync_eod2_data", lambda: dict(NO_ROWS))
    _stub_eod2(monkeypatch, dict(NO_ROWS))
    ingest_mod.run(ctx, force=True)
    assert synced["mark_run"][-1][1] == ingest_mod.NO_NEW_DATA

    # Later attempt: data is now available.
    import myra_app.fundamental_sync as fundamental_sync

    class FakeFundamentalSync:
        def _compute_market_cap_from_prices(self):
            return None

    monkeypatch.setattr(
        fundamental_sync, "FundamentalSync", FakeFundamentalSync, raising=False
    )
    import myra_app.portfolio_db as portfolio_db

    monkeypatch.setattr(portfolio_db, "auto_refresh_portfolio", lambda: {})
    monkeypatch.setattr(ingest_mod, "enrich_corporate_actions", lambda *a, **k: None)
    monkeypatch.setattr(
        eod2_sync,
        "sync_eod2_data",
        lambda: dict(NO_ROWS, rows_inserted=3210, symbols_updated=1195),
    )

    synced["mark_run"].clear()
    synced["mark_ingested"] = 0
    ingest_mod.run(ctx, force=True)

    assert synced["mark_ingested"] == 1, "a later attempt must be able to mark success"
    assert ("daily_ingest", "success", None) in synced["mark_run"]


# ---------------------------------------------------------------------------
# 9: persisted outcome / status mapping
# ---------------------------------------------------------------------------


def test_no_new_data_is_not_normalised_as_completed():
    """Case 9 — pipeline_control must not render a stale DB as healthy."""
    from myra_app.pipeline_control import PipelineControl

    assert (
        PipelineControl._normalise_sync_status("no_new_data", "2026-10-10T00:00:15")
        == "no_new_data"
    )
    assert (
        PipelineControl._normalise_sync_status("NO_NEW_DATA", "2026-10-10T00:00:15")
        == "no_new_data"
    )
    # Regression guard: it must never fall through to the success mapping.
    assert (
        PipelineControl._normalise_sync_status("no_new_data", "2026-10-10T00:00:15")
        != "completed"
    )


def test_no_new_data_persists_with_its_status_and_message(fixture_db_dir):
    """The persisted row keeps status + diagnostic text for the UI."""
    from myra_app.utils.task_utils import _mark_task_run

    _mark_task_run(
        "persisted_probe",
        status=ingest_mod.NO_NEW_DATA,
        error_message="0 rows inserted; data freshness not established",
    )

    path = f"file:{fixture_db_dir}/myra_metadata.db?mode=ro"
    conn = sqlite3.connect(path, uri=True)
    try:
        row = conn.execute(
            "SELECT last_status, error_message FROM sync_log WHERE task_name = ?",
            ("persisted_probe",),
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row[0] == ingest_mod.NO_NEW_DATA
    assert "freshness not established" in row[1]


# ---------------------------------------------------------------------------
# 9: the "data not yet available" gate must not block published sessions
#
# Regression: on 2026-10-15 at 09:2x IST the source already carried 2026-10-13
# and 2026-10-14, the database was stuck at 2026-10-12, and both the cutoff gate
# and the weekend gate said "not yet" - so the sessions were never ingested and
# nothing recovered. These guards are about *today's* bhavcopy only; they must
# not suppress catch-up of sessions upstream has already published.
# ---------------------------------------------------------------------------


def _pin_backlog(monkeypatch, backlog: bool):
    monkeypatch.setattr(ingest_mod, "_has_publishable_backlog", lambda: backlog)


def test_backlog_probe_is_true_when_the_source_is_ahead():
    from myra_app.tasks.ingest import _has_publishable_backlog

    monkey = pytest.MonkeyPatch()
    monkey.setattr(
        "myra_app.data_readiness.source_facts",
        lambda: {"last_date": "2026-10-14"},
    )
    monkey.setattr("myra_app.daily_ingestor.get_db_latest_date", lambda: "2026-10-12")
    try:
        assert _has_publishable_backlog() is True
    finally:
        monkey.undo()


def test_backlog_probe_is_false_when_we_are_already_current():
    from myra_app.tasks.ingest import _has_publishable_backlog

    monkey = pytest.MonkeyPatch()
    monkey.setattr(
        "myra_app.data_readiness.source_facts",
        lambda: {"last_date": "2026-10-14"},
    )
    monkey.setattr("myra_app.daily_ingestor.get_db_latest_date", lambda: "2026-10-14")
    try:
        assert _has_publishable_backlog() is False
    finally:
        monkey.undo()


def test_backlog_probe_is_false_when_the_source_is_unknown():
    from myra_app.tasks.ingest import _has_publishable_backlog

    monkey = pytest.MonkeyPatch()
    monkey.setattr("myra_app.data_readiness.source_facts", lambda: {"last_date": None})
    try:
        assert _has_publishable_backlog() is False
    finally:
        monkey.undo()


def test_backlog_probe_does_not_raise_when_the_probe_itself_fails():
    """A probe failure must degrade to 'no backlog', not blow up the task."""
    from myra_app.tasks.ingest import _has_publishable_backlog

    monkey = pytest.MonkeyPatch()

    def _boom():
        raise RuntimeError("probe exploded")

    monkey.setattr("myra_app.data_readiness.source_facts", _boom)
    try:
        assert _has_publishable_backlog() is False
    finally:
        monkey.undo()


def test_before_cutoff_runs_catch_up_when_a_backlog_exists(ctx, monkeypatch, synced):
    """The core fix: a published backlog must be ingested before 18:00 IST."""
    import datetime as dt

    _pin_backlog(monkeypatch, True)
    morning = dt.datetime(
        2026, 10, 15, 9, 25, tzinfo=dt.timezone(dt.timedelta(hours=5, minutes=30))
    )
    monkeypatch.setattr(ingest_mod, "now_ist", lambda: morning)
    _stub_eod2(monkeypatch, NEW_ROWS)
    _run_eod2(ctx, monkeypatch, NEW_ROWS, force=False)
    assert synced["mark_run"], "catch-up must not be suppressed by the clock"


def test_before_cutoff_still_skips_when_there_is_no_backlog(ctx, monkeypatch, synced):
    import datetime as dt

    _pin_backlog(monkeypatch, False)
    morning = dt.datetime(
        2026, 10, 15, 9, 25, tzinfo=dt.timezone(dt.timedelta(hours=5, minutes=30))
    )
    monkeypatch.setattr(ingest_mod, "now_ist", lambda: morning)
    _stub_eod2(monkeypatch, NEW_ROWS)
    _run_eod2(ctx, monkeypatch, NEW_ROWS, force=False)
    assert synced["mark_run"] == [], "waiting for today's bhavcopy is still correct"


def test_saturday_morning_still_ingests_friday_catch_up(ctx, monkeypatch, synced):
    """The weekend guard must not swallow Friday's published session."""
    import datetime as dt

    assert dt.date(2026, 10, 17).weekday() == 5, "fixture must be a Saturday"
    _pin_backlog(monkeypatch, True)
    saturday = dt.datetime(
        2026, 10, 17, 9, 30, tzinfo=dt.timezone(dt.timedelta(hours=5, minutes=30))
    )
    monkeypatch.setattr(ingest_mod, "now_ist", lambda: saturday)
    _stub_eod2(monkeypatch, NEW_ROWS)
    _run_eod2(ctx, monkeypatch, NEW_ROWS, force=False)
    assert synced["mark_run"], "a weekend must not block already-published data"


def test_saturday_with_no_backlog_is_still_skipped(ctx, monkeypatch, synced):
    import datetime as dt

    _pin_backlog(monkeypatch, False)
    saturday = dt.datetime(
        2026, 10, 17, 9, 30, tzinfo=dt.timezone(dt.timedelta(hours=5, minutes=30))
    )
    monkeypatch.setattr(ingest_mod, "now_ist", lambda: saturday)
    _stub_eod2(monkeypatch, NEW_ROWS)
    _run_eod2(ctx, monkeypatch, NEW_ROWS, force=False)
    assert synced["mark_run"] == []


def test_force_still_bypasses_every_guard(ctx, monkeypatch, synced):
    _pin_backlog(monkeypatch, False)
    _stub_eod2(monkeypatch, NEW_ROWS)
    _run_eod2(ctx, monkeypatch, NEW_ROWS, force=True)
    assert synced["mark_run"], "force must remain an unconditional override"
