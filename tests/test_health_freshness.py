"""Trading-session freshness must not treat weekends/holidays as staleness.

Regression for a user-visible defect: ``/api/data-health`` reported
``days_behind`` as *calendar* days, and the UI turned red at >=3. Every Monday
(and every post-holiday morning) therefore showed a false "stale" alarm while
the newest possible row was already loaded. ``trading_day_freshness`` counts
missed exchange sessions instead.
"""

import datetime as dt
from datetime import timezone, timedelta

from myra_web.utils import trading_day_freshness

IST = timezone(timedelta(hours=5, minutes=30))


def _ist(y, m, d, hh=10, mm=0):
    return dt.datetime(y, m, d, hh, mm, tzinfo=IST)


def _trading_days(holidays=()):
    holidays = set(holidays)

    def fn(day: dt.date) -> bool:
        return day.weekday() < 5 and day.isoformat() not in holidays

    return fn


# 2026-10-12 is a Monday, 2026-10-09 the preceding Friday (see env date
# 2026-10-13 = Tuesday).
def test_monday_with_friday_data_is_not_behind():
    expected, behind = trading_day_freshness(
        "2026-10-09", _ist(2026, 10, 12, 10), _trading_days()
    )
    assert expected == "2026-10-09"
    assert behind == 0


def test_one_session_behind_on_tuesday():
    expected, behind = trading_day_freshness(
        "2026-10-09", _ist(2026, 10, 13, 10), _trading_days()
    )
    assert expected == "2026-10-12"
    assert behind == 1


def test_tuesday_after_close_counts_mondays_session():
    expected, behind = trading_day_freshness(
        "2026-10-09", _ist(2026, 10, 13, 19), _trading_days()
    )
    assert expected == "2026-10-13"
    assert behind == 2


def test_holiday_monday_is_skipped_entirely():
    # 2026-10-19 is a Monday holiday; Friday 10-16 data is current on Monday.
    fn = _trading_days(holidays={"2026-10-19"})
    expected, behind = trading_day_freshness("2026-10-16", _ist(2026, 10, 19, 10), fn)
    assert expected == "2026-10-16"
    assert behind == 0

    # ...and still current the next morning (Tuesday), before that session.
    expected, behind = trading_day_freshness("2026-10-16", _ist(2026, 10, 20, 10), fn)
    assert expected == "2026-10-16"
    assert behind == 0


def test_genuinely_stale_counts_every_session():
    expected, behind = trading_day_freshness(
        "2026-10-02", _ist(2026, 10, 13, 19), _trading_days()
    )
    assert expected == "2026-10-13"
    assert behind == 7


def test_session_ready_boundary_changes_expected_day():
    # Same day, one minute apart: before 18:30 today is not yet expected.
    before = trading_day_freshness(
        "2026-10-09", _ist(2026, 10, 12, 18, 29), _trading_days()
    )
    after = trading_day_freshness(
        "2026-10-09", _ist(2026, 10, 12, 18, 30), _trading_days()
    )
    assert before == ("2026-10-09", 0)
    assert after == ("2026-10-12", 1)


def test_missing_or_invalid_latest_is_reported_as_unknown():
    assert trading_day_freshness(None, _ist(2026, 10, 13, 19), _trading_days()) == (
        None,
        None,
    )
    assert trading_day_freshness(
        "garbage", _ist(2026, 10, 13, 19), _trading_days()
    ) == (
        None,
        None,
    )


def test_no_trading_day_in_lookback_is_unknown():
    # A calendar with no sessions at all cannot produce a freshness figure.
    result = trading_day_freshness(
        "2026-10-09", _ist(2026, 10, 13, 19), lambda d: False
    )
    assert result == (None, None)
