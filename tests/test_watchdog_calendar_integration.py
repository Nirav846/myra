"""Integration regression for the real market_calendar integration.

The 35 earlier watchdog tests mocked ``_is_trading_day`` and therefore passed
while production ingestion was broken: ``market_calendar`` classifies ~23 recent
dates as "Likely holiday (zero rows)" even though ``technical_data`` holds
~3,200 EOD rows for them. Trusting that made ``_expected_trading_date()`` walk
back past the real latest session and report the database permanently fresh.

These tests build REAL SQLite calendars and technical databases in a temp dir and
point the watchdog at them. Nothing here touches production: every connection
goes through an isolated path injected below.
"""

from __future__ import annotations

import datetime as dt
import os
import sqlite3
import threading

import pytest

import myra_app.constants as constants
import myra_app.daily_ingestor as di
from myra_app.librarian_core import LibrarianCore
from myra_app.tasks import watchdog as wd
from myra_app.tasks.context import TaskContext

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))

FRIDAY = dt.date(2026, 10, 9)
SATURDAY = FRIDAY + dt.timedelta(days=1)
MONDAY = FRIDAY + dt.timedelta(days=3)


def _ist(day, hour=19, minute=0):
    return dt.datetime(day.year, day.month, day.day, hour, minute, tzinfo=IST)


@pytest.fixture(autouse=True)
def _no_module_db_dir_leak():
    """Fail closed if this module ever leaves a module-level DB_DIR redirected.

    ``daily_ingestor`` copies ``DB_DIR`` into its own namespace at import time.
    If it were imported for the first time from inside ``real_calendar`` -- i.e.
    *after* ``constants.DB_DIR`` had already been pointed at this test's temp
    directory -- its global would be initialised from that temp path, and
    monkeypatch's restore would then write the temp path back permanently.

    The symptom was order-dependent and invisible in the default alphabetical
    run: ``daily_ingestor.DB_DIR`` stayed pointed at a deleted temp directory, so
    any later file using the ``fixture_db_dir`` fixture aborted with
    ``TEST DB ISOLATION BREACH`` from ``tests/conftest.py::_assert_isolated``.

    Autouse fixtures are set up before explicitly requested ones and finalised
    after them, so this captures the live value first and asserts it back *after*
    monkeypatch has undone its patches.
    """
    before = di.DB_DIR
    yield
    assert di.DB_DIR == before, (
        f"module-level DB_DIR leaked out of {before} -> {di.DB_DIR}; "
        "a later test would resolve the database outside its sandbox"
    )


@pytest.fixture
def real_calendar(monkeypatch, tmp_path):
    """A real calendar DB + real technical_data DB, injected into the watchdog."""
    db_dir = tmp_path / "dbs"
    db_dir.mkdir()

    cal_path = db_dir / "myra_calendar.db"
    cal = sqlite3.connect(cal_path)
    cal.execute(
        "CREATE TABLE market_calendar (date TEXT PRIMARY KEY, "
        "is_trading_day INTEGER, holiday_name TEXT)"
    )
    cal.commit()
    cal.close()

    tech_path = db_dir / "myra_technical.db"
    tech = sqlite3.connect(tech_path)
    tech.execute("CREATE TABLE technical_data (symbol TEXT, date TEXT)")
    tech.execute("CREATE INDEX idx_td_date ON technical_data(date)")
    tech.commit()
    tech.close()

    monkeypatch.setattr(constants, "DB_DIR", str(db_dir))
    monkeypatch.setitem(LibrarianCore.DB_MAP, "calendar", "myra_calendar.db")
    monkeypatch.setitem(LibrarianCore.DB_MAP, "technical", "myra_technical.db")

    # `di` is imported at module scope on purpose: importing it here would
    # capture the already-redirected constants.DB_DIR into its own global.
    monkeypatch.setattr(di, "DB_DIR", str(db_dir), raising=False)
    monkeypatch.setattr(di, "LibrarianCore", LibrarianCore, raising=False)

    class Harness:
        pass

    Harness.cal_path = cal_path
    Harness.tech_path = tech_path

    def _calendar(date, is_trading_day, name=""):
        c = sqlite3.connect(Harness.cal_path)
        c.execute(
            "INSERT OR REPLACE INTO market_calendar VALUES (?, ?, ?)",
            (date.isoformat(), is_trading_day, name),
        )
        c.commit()
        c.close()

    def _eod_rows(date, n=3200):
        t = sqlite3.connect(Harness.tech_path)
        t.executemany(
            "INSERT INTO technical_data VALUES (?, ?)",
            [(f"SYM{i}", date.isoformat()) for i in range(n)],
        )
        t.commit()
        t.close()

    Harness.calendar = staticmethod(_calendar)
    Harness.eod_rows = staticmethod(_eod_rows)
    return Harness


def test_heuristic_holiday_with_eod_rows_is_a_trading_day(real_calendar):
    """Defect A core: the calendar says holiday, the database says otherwise."""
    real_calendar.calendar(FRIDAY, 0, "Likely holiday (zero rows)")
    real_calendar.eod_rows(FRIDAY)

    assert (
        wd._is_trading_day(FRIDAY) is True
    ), "a date holding valid EOD data must not be rejected as a holiday"


def test_heuristic_holiday_without_eod_rows_stays_a_holiday(real_calendar):
    """The heuristic is only overridden by contrary evidence."""
    real_calendar.calendar(MONDAY, 0, "Likely holiday (zero rows)")
    assert wd._is_trading_day(MONDAY) is False


def test_named_holiday_is_never_overridden(real_calendar):
    """A real exchange holiday stays authoritative even if rows exist."""
    real_calendar.calendar(MONDAY, 0, "Gandhi Jayanti")
    real_calendar.eod_rows(MONDAY)
    assert (
        wd._is_trading_day(MONDAY) is False
    ), "a named holiday must not be overridden by DB rows"


def test_expected_date_does_not_fall_back_when_calendar_is_wrong(
    real_calendar, monkeypatch
):
    """Regression: expected date used to collapse ~15 days into the past."""
    # Reproduce the production shape: every recent date flagged heuristically,
    # each of which genuinely holds EOD data.
    for offset in range(0, 12):
        day = FRIDAY - dt.timedelta(days=offset)
        if day.weekday() >= 5:
            continue
        real_calendar.calendar(day, 0, "Likely holiday (zero rows)")
        real_calendar.eod_rows(day)

    now = _ist(MONDAY, 19, 0)
    monkeypatch.setattr(wd, "now_ist", lambda: now)

    # Monday after close: today is expected (it carries no calendar row, so it
    # reads as a normal trading day). The regression would collapse this to a
    # date around 2026-09-24 because every earlier session looked like a holiday.
    expected = wd._expected_trading_date(now)
    assert expected == MONDAY, f"expected date collapsed to {expected}"
    assert expected > dt.date(2026, 9, 24)


def test_genuinely_stale_db_is_detected_through_the_real_calendar(
    real_calendar, monkeypatch
):
    """A DB one real trading session behind must still read as stale."""
    for offset in range(0, 12):
        day = FRIDAY - dt.timedelta(days=offset)
        if day.weekday() >= 5:
            continue
        real_calendar.calendar(day, 0, "Likely holiday (zero rows)")
        real_calendar.eod_rows(day)

    thursday = FRIDAY - dt.timedelta(days=1)
    now = _ist(MONDAY, 19, 0)
    monkeypatch.setattr(wd, "now_ist", lambda: now)
    monkeypatch.setattr(wd, "_db_latest_date", lambda: thursday.isoformat())

    assert wd._expected_trading_date(now) == MONDAY
    assert wd._is_db_stale(now) is True, "a genuinely stale DB must be detected"
    assert wd._freshness_established(now) is False


def test_fresh_db_is_not_stale_through_the_real_calendar(real_calendar, monkeypatch):
    for offset in range(0, 12):
        day = FRIDAY - dt.timedelta(days=offset)
        if day.weekday() >= 5:
            continue
        real_calendar.calendar(day, 0, "Likely holiday (zero rows)")
        real_calendar.eod_rows(day)

    now = _ist(SATURDAY, 19, 0)
    monkeypatch.setattr(wd, "now_ist", lambda: now)
    monkeypatch.setattr(wd, "_db_latest_date", lambda: FRIDAY.isoformat())

    assert wd._expected_trading_date(now) == FRIDAY
    assert wd._is_db_stale(now) is False


def test_watchdog_end_to_end_with_real_calendar_triggers_ingestion(
    real_calendar, monkeypatch
):
    """The decision path, using the real calendar adapter, must fire."""
    for offset in range(0, 12):
        day = FRIDAY - dt.timedelta(days=offset)
        if day.weekday() >= 5:
            continue
        real_calendar.calendar(day, 0, "Likely holiday (zero rows)")
        real_calendar.eod_rows(day)

    thursday = FRIDAY - dt.timedelta(days=1)
    now = _ist(MONDAY, 19, 0)
    monkeypatch.setattr(wd, "now_ist", lambda: now)
    monkeypatch.setattr(wd, "_db_latest_date", lambda: thursday.isoformat())
    monkeypatch.setattr(wd, "_cooldown_elapsed", lambda *_a, **_k: True)
    monkeypatch.setattr(wd, "_mark_task_run", lambda *a, **k: None)

    calls = []

    def _recorder(ctx_arg, **kwargs):
        calls.append(kwargs)

    ctx = TaskContext(shutdown_event=threading.Event(), logger=wd.logger)
    outcome = wd._maybe_trigger_ingest(ctx, now, _recorder)

    assert outcome == "no_new_data"  # the stub ingested nothing
    assert len(calls) == 1, "watchdog must actually attempt ingestion"
    assert not calls[0].get("force"), "no force=True"
