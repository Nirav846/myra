"""Tests for the Phase 6 v2.5 ranking-input sources.

These lock the *definitions* (20 trading sessions, delivery **percentage** not
quantity, never fabricate a value) and the opt-in default that keeps the
production ordering path unchanged.
"""

from __future__ import annotations

import sqlite3

import pytest

from myra_app import ranking_inputs
from myra_app.ranking_inputs import (
    attach_ranking_inputs,
    compute_fundamental_inputs,
    compute_ranking_inputs,
    ranking_inputs_enabled,
)


def _sessions(symbol, values, start_day=1):
    """Rows ``(symbol, date, volume, delivery, delivery_pct)`` for one symbol.

    ``values`` is a list of ``(volume, delivery, delivery_pct)`` tuples, oldest
    first, one per consecutive trading session.
    """
    rows = []
    for offset, (volume, delivery, delivery_pct) in enumerate(values):
        day = start_day + offset
        rows.append((symbol, f"2025-03-{day:02d}", volume, delivery, delivery_pct))
    return rows


@pytest.fixture
def tech_db(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "tech.db"))
    conn.execute(
        "CREATE TABLE technical_data (symbol TEXT, date TEXT, volume INTEGER, "
        "delivery REAL, delivery_pct REAL)"
    )

    rows = []
    # FULL: exactly 20 sessions, complete data.
    rows += _sessions("FULL", [(1000 + 10 * i, 100.0, 10.0) for i in range(20)])
    # SHORT: only 10 sessions -> no 20-session mean may be emitted.
    rows += _sessions("SHORT", [(500.0, 50.0, 5.0) for _ in range(10)])
    # DERIVED: 20 sessions, delivery_pct NULL, delivery/volume present -> derive.
    rows += _sessions("DERIVED", [(200.0, 40.0, None) for _ in range(20)])
    # NODATA: 20 sessions but delivery_pct and delivery both NULL.
    rows += _sessions("NODATA", [(300.0, None, None) for _ in range(20)])
    conn.executemany("INSERT INTO technical_data VALUES (?, ?, ?, ?, ?)", rows)
    conn.commit()
    yield conn
    conn.close()


def test_full_window_means(tech_db):
    out = compute_ranking_inputs(tech_db, ["FULL"])
    row = out["FULL"]
    assert row["sessions"] == 20
    assert row["volume_obs"] == 20
    assert row["delivery_obs"] == 20
    # mean of 1000, 1010, ... 1190 == 1095
    assert row["avg_volume_20d"] == pytest.approx(1095.0)
    assert row["avg_delivery_20d"] == pytest.approx(10.0)


def test_short_history_emits_no_metric(tech_db):
    row = compute_ranking_inputs(tech_db, ["SHORT"])["SHORT"]
    assert row["sessions"] == 10
    assert row["avg_volume_20d"] is None
    assert row["avg_delivery_20d"] is None


def test_delivery_derived_from_quantity_over_volume(tech_db):
    """A missing percentage is derived, never treated as zero."""
    row = compute_ranking_inputs(tech_db, ["DERIVED"])["DERIVED"]
    assert row["avg_delivery_20d"] == pytest.approx(40.0 / 200.0 * 100.0)
    assert row["delivery_obs"] == 20


def test_all_null_delivery_is_absent_not_zero(tech_db):
    row = compute_ranking_inputs(tech_db, ["NODATA"])["NODATA"]
    assert row["avg_volume_20d"] == pytest.approx(300.0)
    assert row["avg_delivery_20d"] is None
    assert row["delivery_obs"] == 0


def test_as_of_date_excludes_future_sessions(tmp_path):
    """A historical scan must not average sessions after its as-of date."""
    conn = sqlite3.connect(str(tmp_path / "tech.db"))
    conn.execute(
        "CREATE TABLE technical_data (symbol TEXT, date TEXT, volume INTEGER, "
        "delivery REAL, delivery_pct REAL)"
    )
    conn.executemany(
        "INSERT INTO technical_data VALUES (?, ?, ?, ?, ?)",
        _sessions("X", [(100.0, 10.0, 10.0) for _ in range(15)])
        + _sessions("X", [(999.0, 90.0, 90.0) for _ in range(10)], start_day=16),
    )
    conn.commit()

    # Cut off before the 10 inflated future rows -> only 15 sessions -> absent.
    cut = compute_ranking_inputs(conn, ["X"], as_of_date="2025-03-15")["X"]
    assert cut["sessions"] == 15
    assert cut["avg_volume_20d"] is None

    # Full history -> the (higher) 20 most recent sessions are averaged.
    full = compute_ranking_inputs(conn, ["X"])["X"]
    assert full["sessions"] == 20
    assert full["avg_volume_20d"] > 500  # dominated by the 999.0 sessions
    conn.close()


def test_symbol_lookup_is_case_insensitive(tech_db):
    assert "FULL" in compute_ranking_inputs(tech_db, ["full"])


def test_empty_symbols_and_none_connection():
    assert compute_ranking_inputs(None, ["A"]) == {}
    assert compute_ranking_inputs(object(), []) == {}


def test_fundamental_inputs_pass_through(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "val.db"))
    conn.execute(
        "CREATE TABLE fundamentals (symbol TEXT, roe REAL, profit_growth REAL)"
    )
    conn.executemany(
        "INSERT INTO fundamentals VALUES (?, ?, ?)",
        [
            ("A", 18.5, 22.0),
            ("B", 0.0, 0.0),  # stored zeros are kept, not treated as absent
            ("C", None, 5.0),  # SQL NULL -> None
        ],
    )
    conn.commit()

    out = compute_fundamental_inputs(conn, ["a", "B", "C", "MISSING"])
    assert out["A"] == {"roe": 18.5, "ProfitGrowth": 22.0}
    assert out["B"] == {"roe": 0.0, "ProfitGrowth": 0.0}
    assert out["C"] == {"roe": None, "ProfitGrowth": 5.0}
    assert "MISSING" not in out
    conn.close()


def test_attach_is_pure_and_only_fills_present_values():
    results = [
        {"Stock": "aaa", "MYRA_Score_v25": 50.0, "ProfitGrowth": 0},
        {"Stock": "BBB", "MYRA_Score_v25": 40.0, "roe": 3.0},
    ]
    inputs = {
        "AAA": {"avg_volume_20d": 12345.0, "avg_delivery_20d": None, "roe": 11.0},
    }
    merged = attach_ranking_inputs(results, inputs)

    # Original rows are untouched (no shared mutation).
    assert results[0] == {"Stock": "aaa", "MYRA_Score_v25": 50.0, "ProfitGrowth": 0}
    assert results[1]["roe"] == 3.0

    # Case-insensitive alignment; absent/None metrics do not overwrite.
    assert merged[0]["avg_volume_20d"] == 12345.0
    assert merged[0]["roe"] == 11.0
    assert "avg_delivery_20d" not in merged[0]
    assert merged[0]["ProfitGrowth"] == 0  # untouched (not in inputs)
    assert merged[1] == results[1]  # no inputs for BBB


def test_explicit_flag_beats_env(monkeypatch):
    monkeypatch.setenv(ranking_inputs.ENV_FLAG, "1")
    assert ranking_inputs_enabled(None) is True
    assert ranking_inputs_enabled(False) is False  # explicit wins


def test_default_flag_is_off(monkeypatch):
    monkeypatch.delenv(ranking_inputs.ENV_FLAG, raising=False)
    assert ranking_inputs_enabled(None) is False
    assert ranking_inputs_enabled() is False


def test_env_flag_truthy_values(monkeypatch):
    for value in ["1", "true", "YES", "on"]:
        monkeypatch.setenv(ranking_inputs.ENV_FLAG, value)
        assert ranking_inputs_enabled(None) is True
    for value in ["0", "false", "", "no"]:
        monkeypatch.setenv(ranking_inputs.ENV_FLAG, value)
        assert ranking_inputs_enabled(None) is False


def test_populate_ranking_inputs_merges_into_funda_map(tmp_path, tech_db):
    """The load_universe helper writes real metrics into funda_map rows."""
    from myra_app.universe_loader import _populate_ranking_inputs

    val = sqlite3.connect(str(tmp_path / "val.db"))
    val.execute("CREATE TABLE fundamentals (symbol TEXT, roe REAL, profit_growth REAL)")
    val.execute("INSERT INTO fundamentals VALUES ('FULL', 21.0, 15.0)")
    val.commit()

    class FakeLib:
        _tech_conn = tech_db
        _val_conn = val

    funda_map = {
        "FULL": {"symbol": "FULL", "ROE": None, "ProfitGrowth": 0},
        "SHORT": {"symbol": "SHORT", "ROE": None, "ProfitGrowth": 0},
    }
    _populate_ranking_inputs(FakeLib(), ["FULL.NS", "SHORT"], None, funda_map)

    assert funda_map["FULL"]["avg_volume_20d"] == pytest.approx(1095.0)
    assert funda_map["FULL"]["avg_delivery_20d"] == pytest.approx(10.0)
    assert funda_map["FULL"]["roe"] == 21.0
    assert funda_map["FULL"]["ProfitGrowth"] == 15.0
    # Insufficient history -> the keys stay absent, not zero.
    assert "avg_volume_20d" not in funda_map["SHORT"]
    assert "roe" not in funda_map["SHORT"]
    val.close()
