"""Regression tests for the watchdog's trading-date and retry-gating logic.

Bugs fixed (see docs/WATCHDOG_SCHEDULING_FIX.md):
  1. Staleness was decided by a calendar-day diff, so a midnight date rollover
     declared the DB stale before the bhavcopy could exist, and weekends /
     holidays counted as missing days even though Friday's data was current.
  2. ``hour >= 18 and minute >= 30`` wrongly excluded 19:00–19:29 (and every
     :00–:29 window from 19:00 onward).
  3. Catch-up called ``ingest.run(force=True)``, bypassing the weekend and
     after-close guards.
  4. ``stale_catchup`` was marked successful unconditionally, even when the
     catch-up established no freshness.

Time and calendar are mocked; the database is never touched. No ingestion runs.
"""

from __future__ import annotations

import datetime as dt
import threading

import pytest

from myra_app.tasks import watchdog as wd
from myra_app.tasks.context import TaskContext
from myra_app.utils import task_utils

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))

# A known Friday, used to build the weekend/holiday cases below.
FRIDAY = dt.date(2026, 10, 9)
SATURDAY = FRIDAY + dt.timedelta(days=1)
SUNDAY = FRIDAY + dt.timedelta(days=2)
MONDAY = FRIDAY + dt.timedelta(days=3)
HOLIDAY = dt.date(2026, 10, 13)  # a Tuesday treated as a market holiday


def test_anchor_dates_really_are_the_weekdays_the_tests_assume():
    assert FRIDAY.weekday() == 4, "FRIDAY must be a Friday"
    assert SATURDAY.weekday() == 5, "SATURDAY must be a Saturday"
    assert SUNDAY.weekday() == 6, "SUNDAY must be a Sunday"
    assert MONDAY.weekday() == 0, "MONDAY must be a Monday"
    assert HOLIDAY.weekday() == 1, "HOLIDAY must be a Tuesday"


def _ist(day: dt.date, hour: int = 12, minute: int = 0) -> dt.datetime:
    return dt.datetime(day.year, day.month, day.day, hour, minute, tzinfo=IST)


def _weekday_only(day: dt.date) -> bool:
    """Weekdays are trading days; weekends are not."""
    return day.weekday() < 5


class DBState:
    """Mutable stand-in for the stored data date, so a run can advance it."""

    def __init__(self, latest):
        self.latest = latest

    def __call__(self):
        return self.latest


@pytest.fixture
def env(monkeypatch):
    """Wire the watchdog's time / calendar / DB-truth seams."""

    def configure(
        now,
        trading_days=None,
        db_latest="2026-10-09",
        last_attempt=None,
    ):
        trading_days = trading_days or _weekday_only
        monkeypatch.setattr(wd, "now_ist", lambda: now)
        monkeypatch.setattr(wd, "_is_trading_day", trading_days)
        db = DBState(db_latest)
        monkeypatch.setattr(wd, "_db_latest_date", db)
        # The cooldown now lives in task_utils and is keyed on the durable
        # attempt row, so patch the seam it actually reads.
        monkeypatch.setattr(task_utils, "_get_last_run", lambda _label: last_attempt)
        return db

    return configure


@pytest.fixture
def ctx():
    return TaskContext(shutdown_event=threading.Event(), logger=wd.logger)


class Recorder:
    """Stands in for tasks.ingest.run and records how it was called.

    ``advance_to`` simulates the stored data date moving forward, which is the
    only thing that may earn a stale_catchup marker.
    """

    def __init__(self, db=None, advance_to=None):
        self.calls = []
        self._db = db
        self._advance_to = advance_to

    def __call__(self, ctx_arg, **kwargs):
        self.calls.append(kwargs)
        if self._advance_to is not None:
            self._db.latest = self._advance_to

    @property
    def forced(self):
        return any(kwargs.get("force") for kwargs in self.calls)


# ---------------------------------------------------------------------------
# 1. Expected trading date — weekends and holidays
# ---------------------------------------------------------------------------


def test_saturday_with_friday_data_is_not_stale(env):
    """Case: Saturday evening, Friday is still the expected trading date."""
    env(_ist(SATURDAY, 19, 0), db_latest=FRIDAY.isoformat())
    assert wd._expected_trading_date() == FRIDAY
    assert wd._is_db_stale() is False


def test_sunday_with_friday_data_is_not_stale(env):
    env(_ist(SUNDAY, 20, 0), db_latest=FRIDAY.isoformat())
    assert wd._expected_trading_date() == FRIDAY
    assert wd._is_db_stale() is False


def test_saturday_midnight_no_longer_looks_stale(env):
    """The reported defect: 00:00 Saturday used to read as 1 day stale."""
    env(_ist(SATURDAY, 0, 0), db_latest=FRIDAY.isoformat())
    assert wd._is_db_stale() is False, "date rollover must not imply staleness"


def test_market_holiday_with_prior_data_is_not_stale(env):
    """Case: an ordinary market holiday; the previous trading day is current."""
    trading = lambda d: _weekday_only(d) and d != HOLIDAY  # noqa: E731
    env(
        _ist(HOLIDAY, 19, 30),
        trading_days=trading,
        db_latest=(HOLIDAY - dt.timedelta(days=1)).isoformat(),
    )
    assert wd._expected_trading_date() == HOLIDAY - dt.timedelta(days=1)
    assert wd._is_db_stale() is False


def test_genuinely_stale_on_eligible_trading_day(env):
    """Case: real staleness on a trading day after the close still triggers."""
    env(_ist(MONDAY, 19, 0), db_latest=FRIDAY.isoformat())
    assert wd._expected_trading_date() == MONDAY
    assert wd._is_db_stale() is True


def test_before_publication_window_expected_is_previous_trading_day(env):
    """Before 18:30 today's file cannot exist, so today is not expected yet."""
    env(_ist(MONDAY, 10, 0), db_latest=FRIDAY.isoformat())
    assert wd._expected_trading_date() == FRIDAY
    assert wd._is_db_stale() is False


def test_empty_database_is_stale(env):
    env(_ist(MONDAY, 19, 0), db_latest=None)
    assert wd._is_db_stale() is True


# ---------------------------------------------------------------------------
# 2. The 18:30 threshold
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "hour,minute,expected",
    [
        (9, 0, False),
        (18, 0, False),
        (18, 29, False),
        (18, 30, True),  # exactly the threshold
        (18, 31, True),
        (19, 0, True),  # the window the old condition wrongly excluded
        (19, 29, True),
        (19, 30, True),
        (20, 0, True),
        (23, 59, True),
    ],
)
def test_publication_window_threshold(env, hour, minute, expected):
    env(_ist(MONDAY, hour, minute))
    assert wd._after_publication_window() is expected


# ---------------------------------------------------------------------------
# 3-5. Trigger decisions, marker semantics, no force
# ---------------------------------------------------------------------------


def test_fresh_database_does_not_trigger_ingestion(env, ctx):
    env(_ist(MONDAY, 19, 0), db_latest=MONDAY.isoformat(), last_attempt=None)
    rec = Recorder()
    assert wd._maybe_trigger_ingest(ctx, _ist(MONDAY, 19, 0), rec) is None
    assert rec.calls == []


def test_before_publication_window_does_not_trigger(env, ctx):
    env(_ist(MONDAY, 12, 0), db_latest=FRIDAY.isoformat())
    rec = Recorder()
    assert wd._maybe_trigger_ingest(ctx, _ist(MONDAY, 12, 0), rec) is None
    assert rec.calls == []


def test_stale_after_close_triggers_without_force(env, ctx, monkeypatch):
    """Case: catch-up must not bypass ingest's own guards."""
    db = env(_ist(MONDAY, 19, 0), db_latest=FRIDAY.isoformat())
    marks = []
    monkeypatch.setattr(wd, "_mark_task_run", lambda *a, **k: marks.append(a))

    rec = Recorder(db, advance_to=MONDAY.isoformat())
    outcome = wd._maybe_trigger_ingest(ctx, _ist(MONDAY, 19, 0), rec)

    assert outcome == "success"
    assert len(rec.calls) == 1
    assert not rec.forced, "ingest must keep ownership of its weekend/close guards"


def test_unavailable_source_does_not_mark_stale_catchup(env, ctx, monkeypatch):
    """Cases: empty/unavailable source and a no-op must not fake freshness."""
    env(_ist(MONDAY, 19, 0), db_latest=FRIDAY.isoformat())
    marks = []
    monkeypatch.setattr(wd, "_mark_task_run", lambda *a, **k: marks.append(a))

    rec = Recorder()
    outcome = wd._maybe_trigger_ingest(ctx, _ist(MONDAY, 19, 0), rec)

    assert outcome == "no_new_data"
    assert marks == [], "no stale_catchup success marker without real progress"


def test_successful_ingestion_marks_stale_catchup(env, ctx, monkeypatch):
    """Case: real data advance earns the marker."""
    db = env(_ist(MONDAY, 19, 0), db_latest=FRIDAY.isoformat())
    marks = []
    monkeypatch.setattr(wd, "_mark_task_run", lambda *a, **k: marks.append(a))

    rec = Recorder(db, advance_to=MONDAY.isoformat())
    outcome = wd._maybe_trigger_ingest(ctx, _ist(MONDAY, 19, 0), rec)

    assert outcome == "success"
    assert marks == [("stale_catchup",)]


# ---------------------------------------------------------------------------
# 6-7. Cooldown / retry eligibility after no_new_data
# ---------------------------------------------------------------------------


def test_cooldown_blocks_immediate_retry_after_no_new_data(env, ctx, monkeypatch):
    """Case: a previous no-op must not cause repeated high-frequency attempts."""
    recent = _ist(MONDAY, 19, 0) - dt.timedelta(minutes=5)
    env(_ist(MONDAY, 19, 0), db_latest=FRIDAY.isoformat(), last_attempt=recent)
    monkeypatch.setattr(wd, "_mark_task_run", lambda *a, **k: None)

    rec = Recorder()
    outcome = wd._maybe_trigger_ingest(ctx, _ist(MONDAY, 19, 0), rec)

    assert outcome is None, "cooldown must suppress a retry"
    assert rec.calls == [], "no attempt within the cooldown window"


def test_retry_is_eligible_once_the_cooldown_expires(env, ctx, monkeypatch):
    """Case: a previous no_new_data must not permanently block ingestion."""
    long_ago = _ist(MONDAY, 19, 0) - dt.timedelta(minutes=90)
    db = env(_ist(MONDAY, 19, 0), db_latest=FRIDAY.isoformat(), last_attempt=long_ago)
    marks = []
    monkeypatch.setattr(wd, "_mark_task_run", lambda *a, **k: marks.append(a))

    rec = Recorder(db, advance_to=MONDAY.isoformat())
    outcome = wd._maybe_trigger_ingest(ctx, _ist(MONDAY, 19, 0), rec)

    assert outcome == "success"
    assert marks == [("stale_catchup",)]


def test_cooldown_is_inclusive_of_its_boundary(env):
    exactly = _ist(MONDAY, 19, 0) - dt.timedelta(
        minutes=wd.STALE_RETRY_COOLDOWN_MINUTES
    )
    now = env(_ist(MONDAY, 19, 0), last_attempt=exactly)
    assert wd._cooldown_elapsed(now) is True


def test_naive_last_attempt_timestamp_is_tolerated(env):
    """sync_log rows written without an offset must not crash the comparison."""
    naive = dt.datetime(2026, 10, 9, 18, 0)
    now = env(_ist(MONDAY, 19, 0), last_attempt=naive)
    assert wd._cooldown_elapsed(now) is True


def test_no_attempt_history_allows_immediate_action(env):
    now = env(_ist(MONDAY, 19, 0), last_attempt=None)
    assert wd._cooldown_elapsed(now) is True


# ---------------------------------------------------------------------------
# 8. Startup catch-up (background_orchestrator)
# ---------------------------------------------------------------------------


def test_startup_catchup_skips_when_already_current(monkeypatch):
    import myra_app.background_orchestrator as bo
    import myra_app.tasks.ingest as ing
    import myra_app.tasks.watchdog as w

    monkeypatch.setattr(w, "now_ist", lambda: _ist(MONDAY, 19, 0))
    monkeypatch.setattr(w, "_is_trading_day", _weekday_only)
    monkeypatch.setattr(w, "_db_latest_date", lambda: MONDAY.isoformat())

    rec = Recorder()
    monkeypatch.setattr(ing, "run", rec)
    assert bo._startup_ingest_catchup() == "skipped"
    assert rec.calls == []


def test_startup_catchup_defers_before_publication_window(monkeypatch):
    """A restart before 18:30 with a genuinely behind DB must not ingest."""
    import myra_app.background_orchestrator as bo
    import myra_app.tasks.ingest as ing
    import myra_app.tasks.watchdog as w

    monday_morning = _ist(MONDAY, 9, 0)
    monkeypatch.setattr(w, "now_ist", lambda: monday_morning)
    monkeypatch.setattr(w, "_is_trading_day", _weekday_only)
    # Expected trading date at 09:00 Monday is FRIDAY, so the DB must be behind
    # Friday for a catch-up to even be considered.
    db = DBState((FRIDAY - dt.timedelta(days=1)).isoformat())
    monkeypatch.setattr(w, "_db_latest_date", db)

    rec = Recorder(db)
    monkeypatch.setattr(ing, "run", rec)
    assert bo._startup_ingest_catchup() == "deferred"
    assert rec.calls == [], "a restart before 18:30 must not ingest"


def test_startup_catchup_skips_when_previous_data_is_current(monkeypatch):
    """09:00 Monday with Friday loaded is already current — no action."""
    import myra_app.background_orchestrator as bo
    import myra_app.tasks.ingest as ing
    import myra_app.tasks.watchdog as w

    monkeypatch.setattr(w, "now_ist", lambda: _ist(MONDAY, 9, 0))
    monkeypatch.setattr(w, "_is_trading_day", _weekday_only)
    monkeypatch.setattr(w, "_db_latest_date", lambda: FRIDAY.isoformat())

    rec = Recorder()
    monkeypatch.setattr(ing, "run", rec)
    assert bo._startup_ingest_catchup() == "skipped"
    assert rec.calls == []


def test_startup_catchup_does_not_mark_when_no_progress(monkeypatch):
    import myra_app.background_orchestrator as bo
    import myra_app.tasks.ingest as ing
    import myra_app.tasks.watchdog as w

    monkeypatch.setattr(w, "now_ist", lambda: _ist(MONDAY, 19, 0))
    monkeypatch.setattr(w, "_is_trading_day", _weekday_only)
    db = DBState(FRIDAY.isoformat())
    monkeypatch.setattr(w, "_db_latest_date", db)

    marks = []
    monkeypatch.setattr(bo, "_mark_task_run", lambda *a, **k: marks.append(a))

    rec = Recorder(db)
    monkeypatch.setattr(ing, "run", rec)

    assert bo._startup_ingest_catchup() == "no_new_data"
    assert marks == [], "no freshness marker without an advance"
    assert rec.calls and not rec.forced


def test_startup_catchup_marks_only_on_real_advance(monkeypatch):
    import myra_app.background_orchestrator as bo
    import myra_app.tasks.ingest as ing
    import myra_app.tasks.watchdog as w

    monkeypatch.setattr(w, "now_ist", lambda: _ist(MONDAY, 19, 0))
    monkeypatch.setattr(w, "_is_trading_day", _weekday_only)
    db = DBState(FRIDAY.isoformat())
    monkeypatch.setattr(w, "_db_latest_date", db)

    marks = []
    monkeypatch.setattr(bo, "_mark_task_run", lambda *a, **k: marks.append(a))

    rec = Recorder(db, advance_to=MONDAY.isoformat())
    monkeypatch.setattr(ing, "run", rec)

    assert bo._startup_ingest_catchup() == "success"
    assert marks == [("stale_catchup",)]


# ---------------------------------------------------------------------------
# 9. Real contract of the ingest task (not "did not raise")
# ---------------------------------------------------------------------------


def test_ingest_run_is_called_without_force_by_every_catchup_path(
    env, ctx, monkeypatch
):
    """No catch-up path may pass force=True."""
    db = env(_ist(MONDAY, 19, 0), db_latest=FRIDAY.isoformat())
    monkeypatch.setattr(wd, "_mark_task_run", lambda *a, **k: None)

    rec = Recorder(db, advance_to=MONDAY.isoformat())
    wd._maybe_trigger_ingest(ctx, _ist(MONDAY, 19, 0), rec)
    assert rec.calls == [{}], "call must carry no arguments at all"


def test_is_trading_day_delegates_to_the_calendar(monkeypatch):
    """The holiday-aware calendar is actually used, not a weekday shortcut."""
    import myra_app.daily_ingestor as di

    seen = []

    def fake_is_trading_day(dt_arg):
        seen.append(dt_arg)
        return dt_arg.date() == FRIDAY

    monkeypatch.setattr(di, "is_trading_day", fake_is_trading_day)

    assert wd._is_trading_day(FRIDAY) is True
    assert wd._is_trading_day(SATURDAY) is False
    assert len(seen) == 2, "must consult the shared trading-calendar helper"
