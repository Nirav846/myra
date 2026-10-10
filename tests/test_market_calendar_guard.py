"""Regression tests for the market_calendar zero-row heuristic.

Two defects motivated this file:

* **Writer** — the legacy ingest path wrote ``is_trading_day = 0`` with
  holiday_name ``'Likely holiday (zero rows)'`` whenever an attempt inserted zero
  rows, using ``INSERT OR REPLACE``. A zero-row insert is not proof the exchange
  was closed (it can mean duplicates, an empty source, or a parse failure), and
  REPLACE let that inference overwrite a correct ``is_trading_day = 1`` record.

* **Reader** — because :func:`calculate_missing_dates` and the other calendar
  consumers trust ``market_calendar`` verbatim, one such row permanently excluded
  a real session from retry while EOD2 went on to load its data.

Every test builds REAL SQLite databases in ``tmp_path`` and injects the path via
``constants.DB_DIR``. Nothing here touches production. Assertions run through the
real public functions — ``_is_trading_day`` is never mocked, because mocking it is
precisely what let the original bug reach production.
"""

from __future__ import annotations

import datetime as dt
import os
import sqlite3
import sys

import pytest

import myra_app.constants as constants
import myra_app.daily_ingestor as di
from myra_app.librarian_core import LibrarianCore

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))

#: Friday. Flagged as a likely holiday by the legacy writer in production while
#: holding ~3,200 EOD rows.
FRIDAY = dt.date(2026, 10, 9)
THURSDAY = dt.date(2026, 10, 8)
NEXT_MONDAY = dt.date(2026, 10, 12)
NEXT_FRIDAY = dt.date(2026, 10, 16)

HEURISTIC = di.HEURISTIC_HOLIDAY_REASON


@pytest.fixture(autouse=True)
def _close_cached_connections():
    """Release the module-level read-only connection cache around each test.

    ``daily_ingestor`` caches read-only SQLite handles per resolved path. On
    Windows an open handle blocks ``tmp_path`` cleanup, so tests must not leak.
    """
    yield
    for conn in list(di._RO_CONNS.values()):
        try:
            conn.close()
        except sqlite3.Error:
            pass
    di._RO_CONNS.clear()


@pytest.fixture
def cal(monkeypatch, tmp_path):
    """A real market_calendar + technical_data pair, injected by path."""
    db_dir = tmp_path / "dbs"
    db_dir.mkdir()

    cal_path = db_dir / "myra_calendar.db"
    conn = sqlite3.connect(cal_path)
    # Mirrors schema/calendar.sql exactly. `session_type` matters: without it
    # validate_calendar_date() raises and silently falls through to "ingest
    # anyway", which would make the bhavcopy assertions vacuous.
    conn.execute(
        "CREATE TABLE market_calendar ("
        "date TEXT PRIMARY KEY, is_trading_day INTEGER NOT NULL, "
        "holiday_name TEXT, session_type TEXT)"
    )
    conn.commit()
    conn.close()

    tech_path = db_dir / "myra_technical.db"
    conn = sqlite3.connect(tech_path)
    conn.execute("CREATE TABLE technical_data (symbol TEXT, date TEXT)")
    conn.execute("CREATE INDEX idx_td_date ON technical_data(date)")
    conn.commit()
    conn.close()

    monkeypatch.setattr(constants, "DB_DIR", str(db_dir))
    monkeypatch.setitem(LibrarianCore.DB_MAP, "calendar", "myra_calendar.db")
    monkeypatch.setitem(LibrarianCore.DB_MAP, "technical", "myra_technical.db")
    monkeypatch.setattr(di, "LibrarianCore", LibrarianCore, raising=False)

    # Consumers do `from myra_app.constants import DB_DIR` at import time, which
    # snapshots the value into their own module namespace. Patching constants
    # alone leaves ingest_bhavcopy/backtest_engine resolving the live directory.
    # Redirect *any* module whose DB_DIR is not already this temp dir: a module
    # first imported by an earlier test would otherwise still hold that test's
    # (now stale) directory, and the assertion below would read the wrong file.
    for name, module in list(sys.modules.items()):
        if module is None:
            continue
        value = getattr(module, "DB_DIR", None)
        if isinstance(value, str) and os.path.abspath(value) != str(db_dir):
            monkeypatch.setattr(module, "DB_DIR", str(db_dir), raising=False)

    class Harness:
        pass

    Harness.cal_path = cal_path
    Harness.tech_path = tech_path

    def _record(date, is_trading_day, name=""):
        c = sqlite3.connect(cal_path)
        c.execute(
            "INSERT OR REPLACE INTO market_calendar "
            "(date, is_trading_day, holiday_name, session_type) "
            "VALUES (?, ?, ?, NULL)",
            (date.isoformat(), is_trading_day, name),
        )
        c.commit()
        c.close()

    def _flag_heuristic(date):
        """Reproduce the corrupt production row: flagged, zero rows absent."""
        _record(date, 0, HEURISTIC)

    def _eod_rows(date, n=3200):
        t = sqlite3.connect(tech_path)
        t.executemany(
            "INSERT INTO technical_data VALUES (?, ?)",
            [(f"SYM{i}", date.isoformat()) for i in range(n)],
        )
        t.commit()
        t.close()

    def _read(date):
        c = sqlite3.connect(cal_path)
        row = c.execute(
            "SELECT is_trading_day, holiday_name FROM market_calendar WHERE date = ?",
            (date.isoformat(),),
        ).fetchone()
        c.close()
        return row

    def _count():
        c = sqlite3.connect(cal_path)
        n = c.execute("SELECT COUNT(*) FROM market_calendar").fetchone()[0]
        c.close()
        return n

    Harness.record = staticmethod(_record)
    Harness.flag_heuristic = staticmethod(_flag_heuristic)
    Harness.eod_rows = staticmethod(_eod_rows)
    Harness.read = staticmethod(_read)
    Harness.count = staticmethod(_count)
    return Harness


def _at_noon(day: dt.date) -> dt.datetime:
    return dt.datetime(day.year, day.month, day.day, 12, 0)


def _ist(day, hour=19, minute=0) -> dt.datetime:
    return dt.datetime(day.year, day.month, day.day, hour, minute, tzinfo=IST)


# ─── Writer: option (b), the zero-row guard ───────────────────────────────────


def test_writer_never_overwrites_correct_trading_day(cal):
    """A zero-row attempt must not clobber a correct is_trading_day = 1 record."""
    cal.record(FRIDAY, 1, None)

    assert di._mark_calendar_unproven(_at_noon(FRIDAY), db_before=0) is False
    assert cal.read(FRIDAY) == (1, None)


def test_writer_will_not_flag_a_data_bearing_date(cal):
    """EOD rows exist for the date, so a zero-row insert proves nothing."""
    cal.eod_rows(FRIDAY, n=50)

    # db_before == 0 only because the caller counted a different slice; the
    # authoritative evidence is the presence of rows.
    assert di._mark_calendar_unproven(_at_noon(FRIDAY), db_before=0) is False
    assert cal.read(FRIDAY) is None

    # And when db_before itself reports pre-existing rows, it is also refused.
    assert di._mark_calendar_unproven(_at_noon(FRIDAY), db_before=50) is False
    assert cal.read(FRIDAY) is None


def test_zero_row_result_is_not_definitive_evidence(cal):
    """An unproven date is recorded as an inference and stays recoverable.

    The writer may still annotate the date so the gap is visible, but the record
    must be recognisable as a heuristic rather than a confirmed closure — and the
    reader must flip to "trading day" the moment real data appears, with no
    further calendar write.
    """
    assert di._mark_calendar_unproven(_at_noon(FRIDAY), db_before=0) is True

    flag, reason = cal.read(FRIDAY)
    assert flag == 0
    assert reason == HEURISTIC  # an inference, not a named exchange holiday

    # Nothing contradicts it yet -> conservative existing behaviour holds.
    assert di.is_trading_day(_at_noon(FRIDAY)) is False

    # Data arrives (EOD2 loaded the session). The reader recovers on its own,
    # with no calendar rewrite.
    cal.eod_rows(FRIDAY, n=5)
    assert di.is_trading_day(_at_noon(FRIDAY)) is True
    assert cal.read(FRIDAY) == (0, HEURISTIC)  # untouched


def test_writer_preserves_named_holidays_and_weekends(cal):
    """A curated holiday or weekend record is never rewritten."""
    named = dt.date(2026, 10, 2)  # Gandhi Jayanti
    saturday = dt.date(2026, 10, 3)
    cal.record(named, 0, "Gandhi Jayanti")
    cal.record(saturday, 0, "Weekend")

    assert di._mark_calendar_unproven(_at_noon(named), db_before=0) is False
    assert di._mark_calendar_unproven(_at_noon(saturday), db_before=0) is False

    assert cal.read(named) == (0, "Gandhi Jayanti")
    assert cal.read(saturday) == (0, "Weekend")


def test_repeated_attempts_leave_existing_records_intact(cal):
    """Successive zero-row attempts never damage an established record."""
    cal.record(FRIDAY, 1, None)

    for _ in range(3):
        di._mark_calendar_unproven(_at_noon(FRIDAY), db_before=0)

    assert cal.read(FRIDAY) == (1, None)
    assert cal.count() == 1


def test_calendar_write_is_idempotent(cal):
    """Re-running the write adds no rows and changes no values."""
    assert di._mark_calendar_unproven(_at_noon(FRIDAY), db_before=0) is True
    first = cal.read(FRIDAY)

    for _ in range(3):
        assert di._mark_calendar_unproven(_at_noon(FRIDAY), db_before=0) is False

    assert cal.read(FRIDAY) == first
    assert cal.count() == 1


# ─── Reader: option (c), the shared database-truth override ───────────────────


def test_flagged_session_with_eod_rows_is_a_trading_day(cal):
    """The production condition: flagged likely-holiday, ~3,200 EOD rows."""
    cal.flag_heuristic(FRIDAY)
    cal.eod_rows(FRIDAY)

    assert di.calendar_classification(FRIDAY.isoformat()) == (False, HEURISTIC)
    assert di.has_eod_evidence(FRIDAY.isoformat()) is True
    assert di.is_trading_day(_at_noon(FRIDAY)) is True


def test_flagged_session_without_eod_rows_stays_conservative(cal):
    """With no counter-evidence the existing conservative rule is unchanged."""
    cal.flag_heuristic(FRIDAY)

    assert di.is_trading_day(_at_noon(FRIDAY)) is False


def test_presence_of_rows_is_the_only_evidence_required(cal):
    """One row is enough — no arbitrary row-count threshold."""
    cal.flag_heuristic(FRIDAY)
    cal.eod_rows(FRIDAY, n=1)

    assert di.is_trading_day(_at_noon(FRIDAY)) is True


def test_named_holiday_and_weekend_are_not_overridden(cal):
    """Explicit closures stay authoritative even when rows happen to exist."""
    named = dt.date(2026, 10, 2)  # Gandhi Jayanti, also in NSE_HOLIDAYS_BASELINE
    saturday = dt.date(2026, 10, 3)

    cal.record(named, 0, "Gandhi Jayanti")
    cal.eod_rows(named, n=3200)
    cal.record(saturday, 0, "Weekend")
    cal.eod_rows(saturday, n=3200)

    assert di.is_trading_day(_at_noon(named)) is False
    assert di.is_trading_day(_at_noon(saturday)) is False


def test_unrecorded_weekday_is_a_trading_day(cal):
    """No calendar record at all keeps the permissive default."""
    assert di.is_trading_day(_at_noon(FRIDAY)) is True


def test_missing_dates_no_longer_excludes_flagged_session(cal):
    """calculate_missing_dates must retry a data-supported session."""
    cal.flag_heuristic(FRIDAY)
    cal.eod_rows(FRIDAY)

    missing = di.calculate_missing_dates(
        (FRIDAY - dt.timedelta(days=1)).isoformat(), _at_noon(NEXT_FRIDAY)
    )

    assert FRIDAY.isoformat() in missing
    assert NEXT_MONDAY.isoformat() in missing


def test_missing_dates_still_skips_weekends_and_holidays(cal):
    """The recovery must not turn every weekend into a work item."""
    cal.record(dt.date(2026, 10, 10), 0, "Weekend")
    cal.record(dt.date(2026, 10, 2), 0, "Gandhi Jayanti")

    missing = di.calculate_missing_dates("2026-10-01", _at_noon(NEXT_FRIDAY))

    assert "2026-10-02" not in missing
    assert "2026-10-03" not in missing
    assert "2026-10-10" not in missing


# ─── Consumers: the override must reach real callers ─────────────────────────


def test_watchdog_uses_the_shared_override(cal):
    """The watchdog delegates instead of keeping a private copy of the rule."""
    from myra_app.tasks import watchdog as wd

    cal.flag_heuristic(FRIDAY)
    cal.eod_rows(FRIDAY)
    cal.record(THURSDAY, 1, None)

    assert wd._is_trading_day(FRIDAY) is True
    # Monday morning, before the 18:30 cutoff. The last session that should hold
    # data is Friday -- only the shared override can recognise it as a session.
    assert wd._expected_trading_date(_ist(NEXT_MONDAY, 9, 0)) == FRIDAY


def test_watchdog_expected_date_for_genuinely_stale_database(cal):
    """With no counter-evidence the watchdog falls back to the prior session.

    Same calendar shape as the test above minus the EOD rows: Friday stays a
    non-session and the expected trading date walks back to Thursday.
    """
    from myra_app.tasks import watchdog as wd

    cal.flag_heuristic(FRIDAY)  # no EOD rows -> conservative, not a session
    cal.record(THURSDAY, 1, None)

    assert wd._is_trading_day(FRIDAY) is False
    assert wd._expected_trading_date(_ist(NEXT_MONDAY, 9, 0)) == THURSDAY


def test_backtest_date_filter_recovers_flagged_sessions(cal):
    """_trading_days must include a real session the calendar flagged."""
    from myra_app.backtest_engine import _trading_days

    cal.record(NEXT_MONDAY, 1, None)
    cal.flag_heuristic(FRIDAY)
    cal.eod_rows(FRIDAY)

    conn = sqlite3.connect(cal.cal_path)
    conn.execute("ATTACH DATABASE ? AS tech", (str(cal.tech_path),))
    try:
        days = _trading_days(conn, FRIDAY.isoformat(), NEXT_MONDAY.isoformat())
    finally:
        conn.close()

    assert days == [FRIDAY.isoformat(), NEXT_MONDAY.isoformat()]


def test_backtest_date_filter_leaves_real_holidays_out(cal):
    """Recovery must not pull a genuine holiday into the backtest window."""
    from myra_app.backtest_engine import _trading_days

    named = dt.date(2026, 10, 13)
    cal.record(NEXT_MONDAY, 1, None)
    cal.record(named, 0, "Ambedkar Jayanti")

    conn = sqlite3.connect(cal.cal_path)
    conn.execute("ATTACH DATABASE ? AS tech", (str(cal.tech_path),))
    try:
        days = _trading_days(conn, NEXT_MONDAY.isoformat(), named.isoformat())
    finally:
        conn.close()

    assert days == [NEXT_MONDAY.isoformat()]


def test_bhavcopy_ingestion_is_not_blocked_by_the_heuristic(cal):
    """The bhavcopy gate consults the shared authority before skipping."""
    from myra_app import ingest_bhavcopy
    from myra_app.librarian_core import LibrarianCore as _LC

    cal.flag_heuristic(FRIDAY)
    cal.eod_rows(FRIDAY)

    ok, session_type = ingest_bhavcopy.validate_calendar_date(FRIDAY.isoformat(), _LC())
    assert ok is True
    assert session_type is None


def test_bhavcopy_ingestion_still_skips_a_real_holiday(cal):
    """A genuine exchange holiday continues to block ingestion."""
    from myra_app import ingest_bhavcopy
    from myra_app.librarian_core import LibrarianCore as _LC

    named = dt.date(2026, 10, 13)
    cal.record(named, 0, "Ambedkar Jayanti")

    ok, session_type = ingest_bhavcopy.validate_calendar_date(named.isoformat(), _LC())
    assert ok is False
    assert session_type is None


def test_technical_audit_counts_recovered_sessions(cal):
    """The audit's trading-day count includes data-backed flagged sessions."""
    cal.record(NEXT_MONDAY, 1, None)
    cal.flag_heuristic(FRIDAY)
    cal.eod_rows(FRIDAY)

    import pandas as pd

    from myra_app.daily_ingestor import has_eod_evidence

    conn = sqlite3.connect(cal.cal_path)
    base = pd.read_sql(
        "SELECT COUNT(*) FROM market_calendar WHERE is_trading_day=1", conn
    ).iloc[0, 0]
    flagged = pd.read_sql(
        "SELECT date FROM market_calendar WHERE is_trading_day=0 "
        "AND holiday_name = ?",
        conn,
        params=(HEURISTIC,),
    )["date"].tolist()
    conn.close()

    assert base == 1
    assert sum(1 for d in flagged if has_eod_evidence(d)) == 1
