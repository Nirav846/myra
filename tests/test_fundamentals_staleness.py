"""Tests for fundamentals API staleness signalling.

The six fields in `FROZEN_SOURCE_FIELDS` (pb, roe, quick_ratio, revenue_growth,
earnings_growth, payout_ratio) lost their only writer when the Upstox
fundamentals write was removed in upstox_fetcher commit d2f0de6. No live source
populates any of them today, so the route serves real-but-frozen values and now
flags them as stale. See docs/DB_CONTEXT.md for the measurements.

The flag is deliberately age-based rather than hardcoded, so these tests pin
both directions: old -> stale, recently refreshed -> not stale.
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from myra_web.routes import fundamentals as fundamentals_route
from myra_web.routes.fundamentals import (
    FROZEN_SOURCE_FIELDS,
    STALENESS_MAX_AGE_DAYS,
    _staleness_flags,
    get_live_fundamentals,
)

NOW = datetime(2026, 9, 29, 12, 0, 0, tzinfo=timezone.utc)


def _iso(dt):
    return dt.isoformat()


def _old():
    return _iso(NOW - timedelta(days=STALENESS_MAX_AGE_DAYS + 1))


def _fresh():
    return _iso(NOW - timedelta(days=1))


# --- unit level: _staleness_flags -----------------------------------------


def test_old_value_is_flagged_stale():
    merged = {"pb": 12.4, "roe": 18.2}
    flags = _staleness_flags(merged, {"last_updated": _old()}, now=NOW)

    assert flags["pb_stale"] is True
    assert flags["roe_stale"] is True


def test_recently_refreshed_value_is_not_flagged_stale():
    """The whole point of an age-based flag: a real writer makes it self-clear."""
    merged = {"pb": 12.4, "roe": 18.2}
    flags = _staleness_flags(merged, {"last_updated": _fresh()}, now=NOW)

    assert flags["pb_stale"] is False
    assert flags["roe_stale"] is False


def test_exactly_at_threshold_is_not_stale():
    """Boundary: only *older than* the threshold counts as stale."""
    edge = _iso(NOW - timedelta(days=STALENESS_MAX_AGE_DAYS))
    flags = _staleness_flags({"pb": 1.0}, {"last_updated": edge}, now=NOW)

    assert flags["pb_stale"] is False


def test_null_value_is_never_stale():
    """Nothing is being served, so there is nothing misleading to flag."""
    merged = {"pb": None, "roe": None, "quick_ratio": None}
    flags = _staleness_flags(merged, {"last_updated": _old()}, now=NOW)

    assert flags["pb_stale"] is False
    assert flags["roe_stale"] is False
    assert flags["quick_ratio_stale"] is False


def test_zero_value_is_still_evaluated():
    """0 is a real value, not missing data - it must not be skipped as falsy."""
    flags = _staleness_flags({"pb": 0}, {"last_updated": _old()}, now=NOW)

    assert flags["pb_stale"] is True


def test_missing_timestamp_is_treated_as_stale():
    """Several legacy rows have last_updated IS NULL; we cannot claim currency."""
    flags = _staleness_flags({"pb": 3.2}, {"last_updated": None}, now=NOW)

    assert flags["pb_stale"] is True


def test_unparseable_timestamp_is_treated_as_stale():
    flags = _staleness_flags({"pb": 3.2}, {"last_updated": "not-a-date"}, now=NOW)

    assert flags["pb_stale"] is True


def test_every_frozen_field_gets_a_flag():
    merged = {f: 1.0 for f in FROZEN_SOURCE_FIELDS}
    flags = _staleness_flags(merged, {"last_updated": _old()}, now=NOW)

    for field in FROZEN_SOURCE_FIELDS:
        assert f"{field}_stale" in flags


@pytest.mark.parametrize(
    "raw", ["2026-09-28T10:00:00+00:00", "2026-09-28T10:00:00", "2026-09-28"]
)
def test_timestamp_formats_in_the_table_are_understood(raw):
    """The table holds offset-aware, naive and bare-date timestamps."""
    assert (
        _staleness_flags({"pb": 1.0}, {"last_updated": raw}, now=NOW)["pb_stale"]
        is False
    )


def test_falls_back_to_last_fundamental_update():
    """last_updated can be absent while last_fundamental_update is populated."""
    fresh = _staleness_flags(
        {"pb": 1.0},
        {"last_updated": None, "last_fundamental_update": _fresh()},
        now=NOW,
    )
    assert fresh["pb_stale"] is False

    stale = _staleness_flags(
        {"pb": 1.0}, {"last_updated": None, "last_fundamental_update": _old()}, now=NOW
    )
    assert stale["pb_stale"] is True


# --- route level: the flags reach the response ---------------------------


@pytest.fixture
def valuation_db(tmp_path, monkeypatch):
    """Temp valuation DB plus a patched DB_DIR, and no live `screener` CLI call."""
    path = os.path.join(str(tmp_path), "myra_valuation.db")
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE fundamentals (
            symbol TEXT PRIMARY KEY,
            priceToBook REAL,
            returnOnEquity REAL,
            quickRatio REAL,
            payoutRatio REAL,
            revenueGrowth REAL,
            earningsGrowth REAL,
            source_ms TEXT,
            last_updated TEXT
        )
        """
    )
    conn.commit()
    conn.close()

    # The route binds DB_DIR at import time, so patch the route module directly.
    monkeypatch.setattr(fundamentals_route, "DB_DIR", str(tmp_path))

    def _no_cli(*args, **kwargs):
        raise FileNotFoundError("screener CLI not available in tests")

    monkeypatch.setattr(fundamentals_route.subprocess, "run", _no_cli)
    return path


def _insert(path, symbol, last_updated, **values):
    cols = ["symbol", "last_updated", "source_ms"] + list(values)
    marks = ",".join("?" * len(cols))
    conn = sqlite3.connect(path)
    conn.execute(
        f"INSERT INTO fundamentals ({','.join(cols)}) VALUES ({marks})",
        [symbol, last_updated, "UPSTOX"] + list(values.values()),
    )
    conn.commit()
    conn.close()


def _call(symbol):
    return asyncio.run(get_live_fundamentals(symbol))


def test_route_marks_old_frozen_values_stale(valuation_db):
    """End-to-end: a frozen row comes back with values AND stale flags set."""
    _insert(
        valuation_db,
        "FROZEN",
        _iso(datetime.now(timezone.utc) - timedelta(days=90)),
        priceToBook=12.4,
        returnOnEquity=18.2,
        quickRatio=0.9,
    )

    funda = _call("FROZEN")["fundamentals"]

    # Values are still served - they are genuine historical data.
    assert funda["pb"] == 12.4
    assert funda["roe"] == 18.2
    assert funda["quick_ratio"] == 0.9
    # ...but they are disclosed as stale.
    assert funda["pb_stale"] is True
    assert funda["roe_stale"] is True
    assert funda["quick_ratio_stale"] is True
    assert funda["data_as_of"] is not None


def test_route_does_not_mark_recently_refreshed_values_stale(valuation_db):
    """The regression guard for the 'not hardcoded' requirement."""
    _insert(
        valuation_db,
        "FRESH",
        _iso(datetime.now(timezone.utc) - timedelta(days=1)),
        priceToBook=2.5,
        quickRatio=1.4,
    )

    funda = _call("FRESH")["fundamentals"]

    assert funda["pb"] == 2.5
    assert funda["pb_stale"] is False
    assert funda["quick_ratio_stale"] is False


def test_route_leaves_untimed_legacy_row_flagged_stale(valuation_db):
    """The payoutRatio rows are old Morningstar writes with last_updated NULL."""
    _insert(valuation_db, "NOTIMED", None, payoutRatio=22.0)

    funda = _call("NOTIMED")["fundamentals"]

    assert funda["payout_ratio"] == 22.0
    assert funda["payout_ratio_stale"] is True
    assert funda["data_as_of"] is None
