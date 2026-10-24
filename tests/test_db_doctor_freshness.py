"""Phase 47 — DB doctor freshness and price-quality reporting.

Two defects this pins down, both confirmed against the live ``doctor_runs``
history:

1. Staleness was measured in **calendar** days. ``doctor_runs`` id 88 ran on
   Sunday 2026-10-18 against data ending Friday 2026-10-16 — the newest
   possible row — and still reported ``[CRITICAL] Database is STALE - 2 days``.
   Every weekend, Monday and post-holiday morning produced the same false
   critical. Freshness must count *trading sessions*, and must say so when the
   trading calendar cannot be consulted instead of reporting the data fresh.

2. The zero/negative-close warning gave no scope. The live 225 rows are three
   non-equity/defunct series (two index pseudo-symbols with no ``fundamentals``
   row, one delisted name) ending 2026-04-23 — a permanent historical residue
   that read identically to live price corruption. The warning must name the
   affected symbol count and date span, and must still detect (never modify)
   the rows.

All fixtures are temporary databases. No production DB is opened and nothing is
written outside ``tmp_path``.
"""

import sqlite3
import sys
from datetime import datetime, timedelta, timezone

import pytest

from tools.db_doctor import DbDoctor, sessions_behind

IST = timezone(timedelta(hours=5, minutes=30))


def _ist(year, month, day, hour=10, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=IST)


def _weekday_calendar(holidays=()):
    """Weekday calendar with optional named closures, shaped like
    ``myra_app.daily_ingestor.is_trading_day`` (datetime -> bool)."""
    closed = set(holidays)

    def is_trading_day(dt):
        d = dt.date() if isinstance(dt, datetime) else dt
        return d.weekday() < 5 and d not in closed

    return is_trading_day


@pytest.fixture
def weekday_calendar(monkeypatch):
    """Install a Mon-Fri calendar (no holidays) for the module under test."""

    def install(holidays=()):
        import myra_app.daily_ingestor as ingestor

        monkeypatch.setattr(
            ingestor, "is_trading_day", _weekday_calendar(holidays), raising=False
        )

    return install


# --------------------------------------------------------------------------
# Trading-session freshness
# --------------------------------------------------------------------------


def test_friday_data_on_sunday_is_not_stale(weekday_calendar):
    """The exact false CRITICAL from doctor_runs id 88.

    Friday 2026-10-16 was the last session; on Sunday 2026-10-18 nothing is
    missing, so the calendar-day rule's "2 days behind" must become 0 sessions.
    """
    weekday_calendar()
    expected, behind, note = sessions_behind("2026-10-16", _ist(2026, 10, 18, 16))
    assert (expected, behind, note) == ("2026-10-16", 0, "")


def test_friday_data_on_monday_morning_is_not_stale(weekday_calendar):
    """Before the 18:30 IST bhavcopy cutoff, Monday's session is not expected."""
    weekday_calendar()
    expected, behind, _ = sessions_behind("2026-10-16", _ist(2026, 10, 19, 10))
    assert expected == "2026-10-16"
    assert behind == 0


def test_friday_data_on_monday_after_close_is_one_session_behind(weekday_calendar):
    """After the cutoff Monday is expected, so Friday data is exactly 1 session
    behind — a warning, not the calendar rule's CRITICAL."""
    weekday_calendar()
    expected, behind, _ = sessions_behind("2026-10-16", _ist(2026, 10, 19, 19))
    assert expected == "2026-10-19"
    assert behind == 1


def test_holiday_gap_is_not_counted(weekday_calendar):
    """2026-10-20 was a named NSE closure (Dussehra).

    Monday 10-19 data on Wednesday 10-21 after close is **one** session behind —
    only Wednesday's own session, not the holiday — so the calendar rule's
    CRITICAL "2 days behind" drops to a single-session WARNING.
    """
    weekday_calendar(holidays=[datetime(2026, 10, 20).date()])
    expected, behind, _ = sessions_behind("2026-10-19", _ist(2026, 10, 21, 19))
    assert expected == "2026-10-21"
    assert behind == 1


def test_genuinely_stale_data_counts_sessions(weekday_calendar):
    """Real staleness still trips: 10-13 data on Friday 10-23 after close has
    missed 14, 15, 16, 19, 20, 21, 22 and 23 — eight sessions."""
    weekday_calendar()
    expected, behind, _ = sessions_behind("2026-10-13", _ist(2026, 10, 23, 19))
    assert expected == "2026-10-23"
    assert behind == 8


def test_unparseable_latest_date_yields_no_verdict(weekday_calendar):
    weekday_calendar()
    expected, behind, note = sessions_behind("not-a-date", _ist(2026, 10, 23, 19))
    assert expected is None
    assert behind is None
    assert note  # caller is told why, and must fall back


def test_no_verdict_when_calendar_helper_declines(monkeypatch):
    """When ``trading_day_freshness`` cannot answer, the doctor must be told —
    ``behind=None`` — rather than being handed a fabricated 0."""

    def decline(*_args, **_kwargs):
        return None, None

    monkeypatch.setattr("myra_web.utils.trading_day_freshness", decline)
    expected, behind, note = sessions_behind("2026-10-16", _ist(2026, 10, 18, 16))
    assert expected is None
    assert behind is None
    assert "no verdict" in note


def test_unavailable_calendar_is_reported_not_assumed_fresh(monkeypatch):
    """An import failure must surface a note, never a silent 'up to date'."""
    monkeypatch.setitem(sys.modules, "myra_web.utils", None)
    expected, behind, note = sessions_behind("2026-10-16", _ist(2026, 10, 18, 16))
    assert expected is None
    assert behind is None
    assert "unavailable" in note


# --------------------------------------------------------------------------
# Zero/negative close reporting
# --------------------------------------------------------------------------


def _make_technical_db(path, rows):
    conn = sqlite3.connect(path)
    try:
        conn.execute("""
            CREATE TABLE technical_data (
                symbol TEXT,
                date TEXT,
                close REAL,
                volume REAL,
                delivery REAL,
                delivery_pct REAL,
                delivery_ratio REAL,
                delivery_source TEXT
            )
            """)
        conn.executemany(
            "INSERT INTO technical_data VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows
        )
        conn.commit()
    finally:
        conn.close()


def _row(symbol, date, close, volume=1000.0, delivery=None):
    return (symbol, date, close, volume, delivery, None, None, None)


def _run_quality(tmp_path, monkeypatch, rows, dry_run=True):
    db_path = tmp_path / "tech.db"
    _make_technical_db(db_path, rows)
    doctor = DbDoctor(dry_run=dry_run)
    monkeypatch.setattr(
        DbDoctor, "_get_connection", lambda self, key: sqlite3.connect(db_path)
    )
    doctor.check_technical_data_quality()
    return doctor, db_path


def test_bad_close_warning_reports_symbol_scope_and_span(tmp_path, monkeypatch, capsys):
    rows = [
        _row("NIFTY50 DIVIDEND POINTS", "2012-03-30", 0.0),
        _row("NIFTY50 DIVIDEND POINTS", "2026-04-23", 0.0),
        _row("ESSENTIA", "2019-11-11", 0.0),
        _row("RELIANCE", "2026-10-23", 2845.5),
    ]
    doctor, _ = _run_quality(tmp_path, monkeypatch, rows)
    out = capsys.readouterr().out

    assert "Rows with zero/negative close price: 3 across 2 symbol(s)" in out
    assert "(2012-03-30 .. 2026-04-23)" in out
    assert doctor.issues_found >= 1


def test_bad_close_detected_but_rows_never_modified(tmp_path, monkeypatch, capsys):
    """Detection must not delete, rewrite or quarantine the rows — the doctor is
    an auditor here, not a mutator."""
    rows = [_row("ESSENTIA", "2019-08-08", 0.0), _row("ESSENTIA", "2019-11-11", 0.0)]
    _, db_path = _run_quality(tmp_path, monkeypatch, rows, dry_run=False)

    conn = sqlite3.connect(db_path)
    try:
        survivors = conn.execute(
            "SELECT COUNT(*) FROM technical_data WHERE close <= 0"
        ).fetchone()[0]
        closes = {r[0] for r in conn.execute("SELECT close FROM technical_data")}
    finally:
        conn.close()

    assert survivors == 2
    assert closes == {0.0}


def test_clean_data_emits_no_bad_close_warning(tmp_path, monkeypatch, capsys):
    rows = [_row("RELIANCE", "2026-10-23", 2845.5)]
    _run_quality(tmp_path, monkeypatch, rows)
    out = capsys.readouterr().out
    assert "zero/negative close price" not in out


def test_missing_technical_db_skips_without_raising(monkeypatch, capsys):
    doctor = DbDoctor(dry_run=True)
    monkeypatch.setattr(DbDoctor, "_get_connection", lambda self, key: None)
    doctor.check_technical_data_quality()
    out = capsys.readouterr().out
    assert "[SKIP] Technical DB not found." in out
    assert doctor.issues_failed == 0
