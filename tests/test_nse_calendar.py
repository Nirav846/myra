"""Phase 10 WS1 — the authoritative NSE trading calendar.

The regression these tests exist to prevent is the one that motivated the
phase: a day with zero ingested rows was written to `market_calendar` as
``"Likely holiday (zero rows)"``, permanently marking a perfectly ordinary
session as a holiday. Missing data is not evidence of closure, so the
classifier must answer from the official publication and must say
``unknown`` when it genuinely cannot tell.

Every test pins the official source in-process, so none of them reads the real
`eod2` meta.json and none can drift when that file is refreshed.
"""

from __future__ import annotations

import datetime as dt
import os
import sqlite3

import pytest

from myra_app import nse_calendar as nc
from myra_app import constants
from myra_app.librarian_core import LibrarianCore

# The published 2026 NSE CM calendar, reduced to the dates that matter for these
# contracts: one genuine holiday, and one *special session* that falls on a
# Sunday (2026-02-01), which a naive "weekend => closed" rule gets wrong.
FAKE_META = {
    "year": 2026,
    "data_version": "3.4",
    "holidays": {
        "2026-01-26": "Republic Day",
        "2026-10-02": "Mahatma Gandhi Jayanti",
    },
    "special_sessions": ["2026-02-01T00:00:00"],
    "special_sessions_last_update": "2026-10-14",
}


@pytest.fixture
def official(monkeypatch):
    """Pin a small, deterministic official calendar."""

    def _install(meta=None):
        payload = FAKE_META if meta is None else meta
        monkeypatch.setattr(nc, "load_official_meta", lambda: payload)
        year = payload.get("year")
        monkeypatch.setattr(nc, "verified_years", lambda: [int(year)] if year else [])
        return payload

    return _install


def _day(iso: str) -> dt.date:
    return dt.datetime.strptime(iso, "%Y-%m-%d").date()


# ── classifier ──────────────────────────────────────────────────────────────


def test_plain_weekday_in_a_verified_year_is_a_session(official):
    """The core fix: a weekday we have a list for is a trading day, full stop."""
    official()
    verdict = nc.classify(_day("2026-10-13"))
    assert verdict["state"] == nc.SESSION_TRADING
    assert verdict["verified"] is True


def test_official_holiday_is_closed_and_named(official):
    official()
    verdict = nc.classify(_day("2026-10-02"))
    assert verdict["state"] == nc.SESSION_CLOSED
    assert verdict["verified"] is True
    assert "Gandhi" in verdict["reason"]


def test_weekend_is_closed_but_still_verified(official):
    official()
    verdict = nc.classify(_day("2026-10-10"))  # Saturday
    assert verdict["state"] == nc.SESSION_CLOSED
    assert verdict["verified"] is True


def test_special_session_on_a_weekend_wins_over_the_weekend_rule(official):
    """2026-02-01 is a Sunday with a published special session."""
    official()
    verdict = nc.classify(_day("2026-02-01"))
    assert verdict["state"] == nc.SESSION_SPECIAL
    assert verdict["verified"] is True


def test_special_session_wins_over_an_official_holiday_on_the_same_day(official):
    """Precedence is explicit: a published live session beats the holiday list."""
    official(
        {
            **FAKE_META,
            "holidays": {**FAKE_META["holidays"], "2026-02-01": "Imposed holiday"},
            "special_sessions": ["2026-02-01T00:00:00"],
        }
    )
    verdict = nc.classify(_day("2026-02-01"))
    assert verdict["state"] == nc.SESSION_SPECIAL


def test_year_with_no_published_list_is_unknown_not_closed(official):
    """Never guess: an unpublished year must not be assumed to be a holiday."""
    official()
    verdict = nc.classify(_day("2027-01-26"))
    assert verdict["state"] == nc.SESSION_UNKNOWN
    assert verdict["verified"] is False


def test_historical_year_with_no_published_list_is_unknown(official):
    official()
    verdict = nc.classify(_day("2015-08-15"))
    assert verdict["state"] == nc.SESSION_UNKNOWN


def test_absent_official_source_is_unknown_everywhere(official):
    """If we cannot read the publication we admit ignorance rather than lie."""
    official({"year": None, "holidays": {}, "special_sessions": []})
    monkey_verdict = nc.classify(_day("2026-10-13"))
    assert monkey_verdict["state"] == nc.SESSION_UNKNOWN


# ── is_expected_trading_day ─────────────────────────────────────────────────


def test_is_expected_trading_day_agrees_with_classify_for_a_session(official):
    official()
    assert nc.is_expected_trading_day(_day("2026-10-13")) is True


def test_is_expected_trading_day_is_false_for_a_real_holiday(official):
    official()
    assert nc.is_expected_trading_day(_day("2026-10-02")) is False


def test_is_expected_trading_day_is_conservative_when_unverified(official):
    """An unverifiable date must not trigger ingestion."""
    official()
    assert nc.is_expected_trading_day(_day("2027-01-26")) is False


# ── meta parsing ────────────────────────────────────────────────────────────


def test_holiday_keys_parse_from_the_published_shape(monkeypatch):
    monkeypatch.setattr(
        nc,
        "load_official_meta",
        lambda: {
            "year": 2026,
            # The publication keys holidays by "DD-MON-YYYY".
            "holidays": {"26-Jan-2026": "Republic Day", "02-Oct-2026": "Gandhi"},
            "special_sessions": ["2026-02-01T00:00:00"],
        },
    )
    holidays = nc.official_holidays_for_year(2026)
    assert holidays[_day("2026-01-26")] == "Republic Day"
    assert holidays[_day("2026-10-02")] == "Gandhi"


def test_malformed_holiday_key_is_ignored_not_raised(monkeypatch):
    """A refresh of the upstream file must never crash classification."""
    monkeypatch.setattr(
        nc,
        "load_official_meta",
        lambda: {
            "year": 2026,
            "holidays": {"not-a-date": "???"},
            "special_sessions": [],
        },
    )
    assert nc.official_holidays_for_year(2026) == {}


def test_malformed_special_session_entry_is_ignored(monkeypatch):
    monkeypatch.setattr(
        nc,
        "load_official_meta",
        lambda: {"year": 2026, "holidays": {}, "special_sessions": ["garbage"]},
    )
    assert nc.official_special_sessions() == {}


def test_special_sessions_carry_their_descriptions(monkeypatch):
    monkeypatch.setattr(
        nc,
        "load_official_meta",
        lambda: {
            "year": 2026,
            "holidays": {},
            "special_sessions": {"2026-02-01": "Muhurat trading session"},
        },
    )
    assert (
        nc.official_special_sessions()[_day("2026-02-01")] == "Muhurat trading session"
    )


def test_missing_meta_file_yields_empty_holidays(monkeypatch):
    monkeypatch.setattr(nc, "load_official_meta", lambda: None)
    assert nc.official_holidays_for_year(2026) == {}
    assert nc.verified_years() == []


# ── reconciliation safety ───────────────────────────────────────────────────


def test_reconciliation_touches_only_heuristic_rows(fixture_db_dir, official):
    """Curated and named records must survive the repair untouched."""
    official()
    _seed_calendar()
    conn = sqlite3.connect(_cal_path())
    try:
        conn.execute(
            "INSERT INTO market_calendar (date, is_trading_day, holiday_name, session_type)"
            " VALUES ('2026-03-03', 0, 'Holi', 'holiday')"
        )
        conn.commit()
        summary = nc.reconcile_heuristic_records(dry_run=True)
        # Only the two heuristic rows are examined; the curated "Holi" row is
        # not a repair candidate and is not even counted.
        assert summary["examined"] == 2
        assert all(u["date"] != "2026-03-03" for u in summary["updates_preview"])
        row = conn.execute(
            "SELECT holiday_name FROM market_calendar WHERE date = '2026-03-03'"
        ).fetchone()
        assert row[0] == "Holi", "a curated holiday must never be rewritten"
    finally:
        conn.close()


def test_dry_run_changes_nothing(fixture_db_dir, official):
    official()
    _seed_calendar()
    conn = sqlite3.connect(_cal_path())
    try:
        before = conn.execute(
            "SELECT date, is_trading_day FROM market_calendar ORDER BY date"
        ).fetchall()
        summary = nc.reconcile_heuristic_records(dry_run=True)
        assert summary["dry_run"] is True
        after = conn.execute(
            "SELECT date, is_trading_day FROM market_calendar ORDER BY date"
        ).fetchall()
        assert before == after
    finally:
        conn.close()


def test_apply_is_idempotent(fixture_db_dir, official):
    official()
    _seed_calendar()
    conn = sqlite3.connect(_cal_path())
    try:
        first = nc.reconcile_heuristic_records(dry_run=False)
        assert first["changed"] == 2
        second = nc.reconcile_heuristic_records(dry_run=False)
        assert second["changed"] == 0
    finally:
        conn.close()


def test_repair_promotes_a_real_session_and_never_deletes_a_row(
    fixture_db_dir, official
):
    official()
    _seed_calendar()
    conn = sqlite3.connect(_cal_path())
    try:
        total_before = conn.execute("SELECT COUNT(*) FROM market_calendar").fetchone()[
            0
        ]
        nc.reconcile_heuristic_records(dry_run=False)
        total_after = conn.execute("SELECT COUNT(*) FROM market_calendar").fetchone()[0]
        assert total_after == total_before, "reconciliation must never delete a row"
        promoted = conn.execute(
            "SELECT is_trading_day FROM market_calendar WHERE date = '2026-09-07'"
        ).fetchone()[0]
        assert promoted == 1, "a weekday wrongly called a holiday must be restored"
        confirmed = conn.execute(
            "SELECT is_trading_day FROM market_calendar WHERE date = '2026-10-02'"
        ).fetchone()[0]
        assert confirmed == 0, "a genuine official holiday stays closed"
    finally:
        conn.close()


def test_unverifiable_records_are_reported_and_left_alone(fixture_db_dir, official):
    official()
    _seed_calendar(include_historical=True)
    conn = sqlite3.connect(_cal_path())
    try:
        summary = nc.reconcile_heuristic_records(dry_run=False)
        assert summary["unclassified"], "an unverifiable date must be reported"
        assert summary["changed"] == 2
        row = conn.execute(
            "SELECT is_trading_day, holiday_name FROM market_calendar"
            " WHERE date = '2015-08-15'"
        ).fetchone()
        assert row[0] == 0 and row[1] == nc.HEURISTIC_REASON
    finally:
        conn.close()


def test_no_official_source_is_a_no_op(fixture_db_dir, monkeypatch):
    """With no publication we must repair nothing at all."""
    monkeypatch.setattr(nc, "load_official_meta", lambda: None)
    monkeypatch.setattr(nc, "verified_years", lambda: [])
    _seed_calendar()
    conn = sqlite3.connect(_cal_path())
    try:
        summary = nc.reconcile_heuristic_records(dry_run=False)
        assert summary["changed"] == 0
        assert summary["examined"] == 3
        assert len(summary["unclassified"]) == 3
    finally:
        conn.close()


# ── helpers ─────────────────────────────────────────────────────────────────


def _cal_path() -> str:
    return os.path.join(constants.DB_DIR, LibrarianCore.DB_MAP["calendar"])


def _seed_calendar(include_historical: bool = False) -> None:
    """Three heuristic rows: two 2026 dates we can judge, plus optionally 2015."""
    conn = sqlite3.connect(_cal_path())
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS market_calendar ("
            " date TEXT PRIMARY KEY, is_trading_day INTEGER NOT NULL,"
            " holiday_name TEXT, session_type TEXT)"
        )
        dates = ["2026-09-07", "2026-10-02"]
        if include_historical:
            dates.append("2015-08-15")
        conn.executemany(
            "INSERT OR REPLACE INTO market_calendar"
            " (date, is_trading_day, holiday_name, session_type)"
            " VALUES (?, 0, ?, 'holiday')",
            [(d, nc.HEURISTIC_REASON) for d in dates],
        )
        conn.commit()
    finally:
        conn.close()
