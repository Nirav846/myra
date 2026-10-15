"""Phase 10 WS2 — the automatic EOD recovery policy.

The recovery path answers two questions that the old watchdog conflated:
*should* we attempt, and *how often may we keep attempting the same thing*.

The contracts pinned here are the ones that keep it from becoming a
minute-by-minute data hammer:

* an official holiday or an unverifiable calendar never triggers a recovery;
* a confirmed session the database already holds never triggers a recovery;
* before the publication window it never triggers a recovery;
* the shared attempt cooldown spaces attempts out;
* and — the new bound — a hard per-input-state budget stops an endlessly
  incomplete source from being re-imported forever, while a genuinely new
  input (a late-arriving file) resets that budget.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import sqlite3

import pytest

from myra_app import eod_recovery as er
from myra_app import constants
from myra_app.librarian_core import LibrarianCore

MONDAY = dt.date(2026, 10, 12)
TUESDAY = dt.date(2026, 10, 13)

SOURCE_OK = {
    "available": True,
    "ready": True,
    "last_date": "2026-10-13",
    "complete": 10,
    "total_files": 10,
}


@pytest.fixture
def sealed_calendar(monkeypatch):
    """Pin the calendar so every verdict is decided by the fixture, not by eod2."""

    def _install(trading: bool = True):
        monkeypatch.setattr(
            "myra_app.tasks.watchdog._is_trading_day", lambda _d: trading
        )

    return _install


@pytest.fixture
def ready(monkeypatch):
    """Make the environment eligible by default; tests override one thing each."""
    monkeypatch.setattr("myra_app.utils.task_utils._get_last_run", lambda _label: None)
    monkeypatch.setattr(er, "budget_allows", lambda _fp: (True, {"attempts": 0}))
    monkeypatch.setattr(er, "budget_state", lambda _fp: {"attempts": 0})
    monkeypatch.setattr("myra_app.data_readiness.source_facts", lambda: dict(SOURCE_OK))
    monkeypatch.setattr(
        "myra_app.daily_ingestor.get_db_latest_date", lambda: "2026-10-12"
    )
    return monkeypatch


# ── fingerprint: what counts as "the same input"? ───────────────────────────


def test_same_input_yields_the_same_fingerprint():
    a = er.input_fingerprint("2026-10-13", SOURCE_OK)
    b = er.input_fingerprint("2026-10-13", dict(SOURCE_OK))
    assert a == b, "identical inputs must not reset the retry budget"


def test_a_new_session_changes_the_fingerprint():
    a = er.input_fingerprint("2026-10-13", SOURCE_OK)
    b = er.input_fingerprint("2026-10-14", SOURCE_OK)
    assert a != b


def test_a_late_arriving_file_changes_the_fingerprint():
    """The whole point: new input must re-open a budget spent on old input."""
    partial = {**SOURCE_OK, "complete": 9}
    late = {**SOURCE_OK, "complete": 10}
    assert er.input_fingerprint("2026-10-13", partial) != er.input_fingerprint(
        "2026-10-13", late
    )


def test_a_later_source_date_changes_the_fingerprint():
    a = er.input_fingerprint("2026-10-13", {**SOURCE_OK, "last_date": "2026-10-13"})
    b = er.input_fingerprint("2026-10-13", {**SOURCE_OK, "last_date": "2026-10-14"})
    assert a != b


def test_fingerprint_tolerates_an_empty_source():
    assert er.input_fingerprint(None, {}) == er.input_fingerprint(None, {})


# ── eligibility ─────────────────────────────────────────────────────────────


def test_a_confirmed_session_behind_the_db_is_eligible(ready, sealed_calendar):
    sealed_calendar(trading=True)
    out = er.evaluate(
        expected_date="2026-10-13", db_latest="2026-10-12", source=SOURCE_OK
    )
    assert out["eligible"] is True


def test_an_official_holiday_is_never_eligible(ready, sealed_calendar, monkeypatch):
    sealed_calendar(trading=False)
    monkeypatch.setattr(
        "myra_app.nse_calendar.classify",
        lambda _d: {"state": "closed", "verified": True, "reason": "NSE holiday: X"},
    )
    out = er.evaluate(
        expected_date="2026-10-13", db_latest="2026-10-12", source=SOURCE_OK
    )
    assert out["eligible"] is False
    assert out["reason"] == er.REASON_SKIPPED_HOLIDAY


def test_an_unverifiable_calendar_is_not_treated_as_a_holiday(
    ready, sealed_calendar, monkeypatch
):
    """Unknown must never be silently downgraded to "holiday"."""
    sealed_calendar(trading=False)
    monkeypatch.setattr(
        "myra_app.nse_calendar.classify",
        lambda _d: {"state": "unknown", "verified": False, "reason": "no list"},
    )
    out = er.evaluate(
        expected_date="2026-10-13", db_latest="2026-10-12", source=SOURCE_OK
    )
    assert out["eligible"] is False
    assert out["reason"] == er.REASON_SKIPPED_UNKNOWN


def test_a_db_already_at_the_expected_date_is_not_eligible(ready, sealed_calendar):
    sealed_calendar(trading=True)
    out = er.evaluate(
        expected_date="2026-10-13", db_latest="2026-10-13", source=SOURCE_OK
    )
    assert out["eligible"] is False
    assert out["reason"] == er.REASON_SKIPPED_READY


def test_a_db_ahead_of_the_expected_date_is_not_eligible(ready, sealed_calendar):
    sealed_calendar(trading=True)
    out = er.evaluate(
        expected_date="2026-10-13", db_latest="2026-10-14", source=SOURCE_OK
    )
    assert out["eligible"] is False


def test_before_the_publication_window_is_not_eligible(ready, sealed_calendar):
    """Waiting for today's bhavcopy is still correct when it hasn't landed.

    Here the source has *not* reached the expected session, so there is genuinely
    nothing to fetch yet and waiting is the right answer.
    """
    sealed_calendar(trading=True)
    behind = {**SOURCE_OK, "last_date": "2026-10-09", "complete": 9}
    out = er.evaluate(
        expected_date="2026-10-13",
        db_latest="2026-10-09",
        source=behind,
        after_publication_window=False,
    )
    assert out["eligible"] is False
    assert out["reason"] == er.REASON_SKIPPED_WINDOW


def test_before_the_publication_window_does_not_delay_a_published_session(
    ready, sealed_calendar
):
    """The regression behind the 2026-10-15 outage.

    The source already carried 2026-10-13 and 2026-10-14 while the database sat
    at 2026-10-12. Because the cutoff gate only asks "has *today's* bhavcopy
    landed?", it suppressed catch-up of sessions that were demonstrably already
    published - so nothing was ingested until an operator intervened.
    """
    sealed_calendar(trading=True)
    out = er.evaluate(
        expected_date="2026-10-13",
        db_latest="2026-10-12",
        source=SOURCE_OK,  # last_date == 2026-10-13: already published
        after_publication_window=False,  # 09:25 IST
    )
    assert (
        out["eligible"] is True
    ), "an already-published session must not wait for today's cutoff"


def test_the_cooldown_suppresses_a_repeat_attempt(ready, sealed_calendar, monkeypatch):
    sealed_calendar(trading=True)
    monkeypatch.setattr(
        "myra_app.utils.task_utils._get_last_run",
        lambda _label: dt.datetime.now(),
    )
    out = er.evaluate(
        expected_date="2026-10-13", db_latest="2026-10-12", source=SOURCE_OK
    )
    assert out["eligible"] is False
    assert out["reason"] == er.REASON_SKIPPED_COOLDOWN


def test_a_missing_expected_date_is_not_eligible(ready, sealed_calendar):
    sealed_calendar(trading=True)
    monkeypatch = ready
    monkeypatch.setattr(
        "myra_app.data_readiness._expected_date", lambda _now=None: None
    )
    out = er.evaluate(db_latest="2026-10-12", source=SOURCE_OK)
    assert out["eligible"] is False


def test_a_source_that_has_not_landed_yet_is_still_eligible(ready, sealed_calendar):
    """A missing file is not a reason to stop looking, only a reason to wait."""
    sealed_calendar(trading=True)
    empty = {
        "available": False,
        "ready": None,
        "last_date": None,
        "complete": 0,
        "total_files": 0,
    }
    out = er.evaluate(expected_date="2026-10-13", db_latest="2026-10-12", source=empty)
    assert out["eligible"] is True


# ── the retry budget ────────────────────────────────────────────────────────


def _clear_recovery_rows() -> None:
    """Recovery keys live in the shared fixture DB, so tests clear them."""
    path = os.path.join(constants.DB_DIR, LibrarianCore.DB_MAP["meta"])
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT)"
        )
        conn.execute(
            "DELETE FROM metadata WHERE key = ? OR key LIKE ?",
            (er.RECOVERY_STATUS_LABEL, f"{er.BUDGET_LABEL}:%"),
        )
        conn.commit()
    finally:
        conn.close()


def test_budget_starts_at_zero(fixture_db_dir):
    assert er.budget_state("fresh-fp")["attempts"] == 0


def test_an_attempt_spends_one_unit_of_budget(fixture_db_dir):
    er.note_attempt("fp-a", reason="catchup")
    er.note_attempt("fp-a", reason="catchup")
    assert er.budget_state("fp-a")["attempts"] == 2


def test_the_budget_is_per_input_state(fixture_db_dir):
    er.note_attempt("fp-a")
    assert (
        er.budget_state("fp-b")["attempts"] == 0
    ), "a new input state must start from a fresh budget"


def test_the_budget_stops_after_the_maximum(fixture_db_dir):
    for _ in range(er.MAX_ATTEMPTS_PER_INPUT):
        er.note_attempt("fp-c")
    allowed, state = er.budget_allows("fp-c")
    assert allowed is False
    assert state["attempts"] == er.MAX_ATTEMPTS_PER_INPUT


def test_an_exhausted_budget_suppresses_eligibility(
    fixture_db_dir, sealed_calendar, monkeypatch
):
    """The bound that actually stops an endless incomplete-source loop."""
    sealed_calendar(trading=True)
    monkeypatch.setattr("myra_app.utils.task_utils._get_last_run", lambda _label: None)
    # Spend the budget against the fingerprint evaluate() will actually compute.
    fp = er.input_fingerprint("2026-10-13", SOURCE_OK)
    for _ in range(er.MAX_ATTEMPTS_PER_INPUT):
        er.note_attempt(fp)

    out = er.evaluate(
        expected_date="2026-10-13",
        db_latest="2026-10-12",
        source=SOURCE_OK,
    )
    assert out["eligible"] is False
    assert out["reason"] == er.REASON_SKIPPED_BUDGET


def test_an_exhausted_budget_is_reset_by_a_late_arriving_file(
    fixture_db_dir, sealed_calendar, monkeypatch
):
    """A permanently-exhausted budget must never wedge recovery for good."""
    _clear_recovery_rows()
    sealed_calendar(trading=True)
    monkeypatch.setattr("myra_app.utils.task_utils._get_last_run", lambda _label: None)
    partial = {**SOURCE_OK, "complete": 9}
    stale_fp = er.input_fingerprint("2026-10-13", partial)
    for _ in range(er.MAX_ATTEMPTS_PER_INPUT):
        er.note_attempt(stale_fp)

    blocked = er.evaluate(
        expected_date="2026-10-13", db_latest="2026-10-12", source=partial
    )
    assert blocked["eligible"] is False, "the unchanged input stays suppressed"

    # One more file lands -> new fingerprint -> fresh budget.
    after = er.evaluate(
        expected_date="2026-10-13", db_latest="2026-10-12", source=SOURCE_OK
    )
    assert after["eligible"] is True, "a genuinely new input must recover"


def test_a_refund_gives_the_budget_back(fixture_db_dir):
    er.note_attempt("fp-e")
    er.note_attempt("fp-e")
    er.refund_attempt("fp-e")
    assert er.budget_state("fp-e")["attempts"] == 1


def test_a_refund_never_goes_negative(fixture_db_dir):
    er.refund_attempt("fp-f")
    assert er.budget_state("fp-f")["attempts"] == 0


def test_budget_records_when_it_was_first_and_last_used(fixture_db_dir):
    er.note_attempt("fp-g")
    first = er.budget_state("fp-g")["first"]
    assert first
    er.note_attempt("fp-g")
    assert er.budget_state("fp-g")["last"] >= first


def test_a_corrupt_budget_row_is_treated_as_zero_not_a_crash(fixture_db_dir):
    """A truncated write must not permanently wedge recovery."""
    path = os.path.join(constants.DB_DIR, LibrarianCore.DB_MAP["meta"])
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT)"
        )
        conn.execute(
            "INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)",
            (er._budget_key("fp-h"), "{not json"),
        )
        conn.commit()
    finally:
        conn.close()
    assert er.budget_state("fp-h")["attempts"] == 0


def test_budget_summary_reports_remaining_attempts(fixture_db_dir, monkeypatch):
    _clear_recovery_rows()
    monkeypatch.setattr(
        "myra_app.data_readiness._expected_date", lambda _now=None: "2026-10-13"
    )
    monkeypatch.setattr("myra_app.data_readiness.source_facts", lambda: dict(SOURCE_OK))
    summary = er.budget_summary()
    assert summary["remaining"] == er.MAX_ATTEMPTS_PER_INPUT
    assert summary["exhausted"] is False


# ── outcome recording and Mission Control contract ───────────────────────────


def test_record_outcome_round_trips(fixture_db_dir):
    assert (
        er.record_outcome(
            er.REASON_WAITING, detail="source incomplete", fingerprint="fp"
        )
        is True
    )
    out = er.last_outcome()
    assert out["reason"] == er.REASON_WAITING
    assert out["detail"] == "source incomplete"
    assert out["fingerprint"] == "fp"
    assert out["at"]


def test_last_outcome_is_empty_before_anything_is_recorded(fixture_db_dir):
    _clear_recovery_rows()
    assert er.last_outcome() == {}


def test_recording_an_outcome_replaces_the_previous_one(fixture_db_dir):
    er.record_outcome(er.REASON_WAITING)
    er.record_outcome(er.REASON_RECOVERED)
    assert er.last_outcome()["reason"] == er.REASON_RECOVERED


def test_record_outcome_survives_a_non_dict_payload(fixture_db_dir):
    """Malformed persisted status must not break the readiness panel."""
    path = os.path.join(constants.DB_DIR, LibrarianCore.DB_MAP["meta"])
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT)"
        )
        conn.execute(
            "INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)",
            (er.RECOVERY_STATUS_LABEL, json.dumps(["not", "a", "dict"])),
        )
        conn.commit()
    finally:
        conn.close()
    assert er.last_outcome() == {}


def test_record_outcome_truncates_a_runaway_detail(fixture_db_dir):
    er.record_outcome(er.REASON_FAILED, detail="x" * 5000)
    assert len(er.last_outcome()["detail"]) <= 400


def test_recovery_appears_in_the_readiness_contract(fixture_db_dir):
    """Mission Control must surface the watchdog's last decision."""
    from myra_app import data_readiness as dr

    er.record_outcome(er.REASON_SKIPPED_HOLIDAY, detail="Gandhi Jayanti")
    payload = dr.assess()
    assert payload["recovery"]["reason"] == er.REASON_SKIPPED_HOLIDAY
    assert payload["recovery"]["detail"] == "Gandhi Jayanti"


def test_readiness_fingerprint_changes_when_recovery_changes(fixture_db_dir):
    """A recovery transition must actually reach the UI."""
    from myra_app import data_readiness as dr

    first = dr.fingerprint(dr.assess())
    er.record_outcome(er.REASON_RECOVERED)
    second = dr.fingerprint(dr.assess())
    assert first != second


def test_readiness_fingerprint_is_stable_when_nothing_changes(fixture_db_dir):
    from myra_app import data_readiness as dr

    first = dr.fingerprint(dr.assess())
    second = dr.fingerprint(dr.assess())
    assert first == second


def test_every_reason_code_has_a_stable_name():
    """Reason strings are persisted and rendered; they must not drift silently."""
    codes = {v for k, v in vars(er).items() if k.startswith("REASON_")}
    assert len(codes) == 12
    assert all(isinstance(c, str) and c for c in codes)
