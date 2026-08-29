"""
Tests for the point-in-time fundamentals-history system (Part B).

Covers:
- fundamentals_history table creation (columns, composite PK, idempotency)
- schema_registry parity entry for the table
- backfill insert path (INSERT OR REPLACE idempotency)
- monthly-snapshot collapsing
- WyckoffAutomaton point-in-time mcap resolution: as-of lookup, current-
  snapshot fallback (with the warning path), None when nothing is found,
  universe-filtered bulk load, and the guarantee that Spring scores are
  identical whether or not history rows exist (scoring integration deferred).

No network access and no real-DB access — everything runs on temp DBs.
"""

import logging
import os
import sqlite3

import pandas as pd
import pytest

import myra_app.strategies.wyckoff_automaton as wy_mod
from myra_app.backfill_fundamentals import (
    create_fundamentals_history_table,
    insert_history_rows,
    monthly_snapshot,
)
from myra_app.strategies.wyckoff_automaton import WyckoffAutomaton

WY_LOGGER = "myra_app.strategies.wyckoff_automaton"

HISTORY_ROWS = [
    ("AAA", "2025-01-31", 5.0e9, None, None, "yfinance_daily"),
    ("AAA", "2025-02-28", 5.5e9, None, None, "yfinance_daily"),
    ("AAA", "2025-03-31", 6.0e9, None, None, "yfinance_daily"),
    ("BBB", "2025-03-31", 3.0e9, None, None, "yfinance_daily"),
]


@pytest.fixture
def history_db(tmp_path, monkeypatch):
    """Temp valuation DB (fundamentals_history + current fundamentals snapshot)
    with DB_DIR redirected so the scanner resolves against it."""
    monkeypatch.setattr(wy_mod, "DB_DIR", str(tmp_path))
    db_path = os.path.join(str(tmp_path), "myra_valuation.db")
    conn = sqlite3.connect(db_path)
    try:
        create_fundamentals_history_table(conn)
        conn.executemany(
            "INSERT OR REPLACE INTO fundamentals_history "
            "(symbol, date, market_cap, free_float_mcap, free_float_pct, source) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            HISTORY_ROWS,
        )
        conn.execute(
            "CREATE TABLE fundamentals (symbol TEXT PRIMARY KEY, market_cap REAL,"
            " date TEXT)"
        )
        conn.executemany(
            "INSERT INTO fundamentals (symbol, market_cap, date) VALUES (?, ?, ?)",
            [
                ("AAA", 9.0e9, "2026-08-24"),
                ("BBB", 3.5e9, "2026-08-24"),
                ("CCC", 2.0e9, "2026-08-24"),
            ],
        )
        conn.commit()
    finally:
        conn.close()
    return db_path


# ---------------------------------------------------------------------------
# Table creation / registry parity
# ---------------------------------------------------------------------------


def test_create_table_schema_and_composite_pk(tmp_path):
    db = os.path.join(str(tmp_path), "t.db")
    conn = sqlite3.connect(db)
    try:
        create_fundamentals_history_table(conn)
        conn.execute("CREATE TABLE fundamentals (symbol TEXT PRIMARY KEY)")
        cols = {
            r[1]: r[2]
            for r in conn.execute("PRAGMA table_info(fundamentals_history)").fetchall()
        }
        assert cols == {
            "symbol": "TEXT",
            "date": "TEXT",
            "market_cap": "REAL",
            "free_float_mcap": "REAL",
            "free_float_pct": "REAL",
            "source": "TEXT",
        }
        # Composite PK (symbol, date) — in declaration order.
        pk_cols = [
            r[1]
            for r in conn.execute("PRAGMA table_info(fundamentals_history)").fetchall()
            if r[5] > 0
        ]
        assert pk_cols == ["symbol", "date"]
        # The PK auto-index covers the as-of lookup (symbol, date <= x).
        index_names = [
            r[1]
            for r in conn.execute("PRAGMA index_list(fundamentals_history)").fetchall()
        ]
        assert any("sqlite_autoindex" in name for name in index_names)
    finally:
        conn.close()


def test_create_table_idempotent(tmp_path):
    db = os.path.join(str(tmp_path), "t.db")
    conn = sqlite3.connect(db)
    try:
        create_fundamentals_history_table(conn)
        create_fundamentals_history_table(conn)  # second call must be a no-op
        n = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table'"
            " AND name='fundamentals_history'"
        ).fetchone()[0]
        assert n == 1
    finally:
        conn.close()


def test_schema_registry_knows_fundamentals_history():
    from myra_app.schema_registry import SchemaRegistry

    entry = SchemaRegistry.TABLES["fundamentals_history"]
    assert entry["db"] == "valuation"
    assert entry["primary_key"] == "(symbol, date)"
    assert set(entry["columns"]) == {
        "symbol",
        "date",
        "market_cap",
        "free_float_mcap",
        "free_float_pct",
        "source",
    }


def test_schema_registry_validates_created_table(tmp_path):
    from myra_app.schema_registry import SchemaRegistry

    db = os.path.join(str(tmp_path), "t.db")
    conn = sqlite3.connect(db)
    try:
        create_fundamentals_history_table(conn)
        assert SchemaRegistry.validate_schema(conn, "fundamentals_history") is True
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Backfill insert path / monthly snapshots
# ---------------------------------------------------------------------------


def test_insert_rows_idempotent(tmp_path):
    db = os.path.join(str(tmp_path), "t.db")
    conn = sqlite3.connect(db)
    try:
        create_fundamentals_history_table(conn)
        rows = [("RELIANCE", "2025-01-31", 16.0e12, None, None, "yfinance_daily")]
        insert_history_rows(conn, rows)
        insert_history_rows(conn, rows)  # same (symbol, date) again
        n = conn.execute("SELECT COUNT(*) FROM fundamentals_history").fetchone()[0]
        assert n == 1
        # A different date for the same symbol adds a separate row.
        insert_history_rows(
            conn, [("RELIANCE", "2025-02-28", 17.0e12, None, None, "yfinance_daily")]
        )
        n = conn.execute("SELECT COUNT(*) FROM fundamentals_history").fetchone()[0]
        assert n == 2
    finally:
        conn.close()


def test_monthly_snapshot_keeps_last_trading_day():
    rows = [
        ("2025-01-06", 1.0, None, None, "s"),
        ("2025-01-31", 1.1, None, None, "s"),
        ("2025-02-03", 1.2, None, None, "s"),
        ("2025-02-28", 1.3, None, None, "s"),
        ("2025-03-31", 1.4, None, None, "s"),
    ]
    out = monthly_snapshot(rows)
    assert [r[0] for r in out] == ["2025-01-31", "2025-02-28", "2025-03-31"]


# ---------------------------------------------------------------------------
# Point-in-time resolution
# ---------------------------------------------------------------------------


def test_resolve_pit_mcap_as_of(history_db):
    scanner = WyckoffAutomaton()
    scanner._load_pit_history(["AAA"])
    # 2025-02-15: the 2025-02-28 snapshot did not exist yet → Jan snapshot.
    assert scanner._resolve_pit_mcap("AAA", "2025-02-15") == pytest.approx(5.0e9)
    # date <= as_on_date is inclusive.
    assert scanner._resolve_pit_mcap("AAA", "2025-02-28") == pytest.approx(5.5e9)
    assert scanner._resolve_pit_mcap("AAA", "2025-03-31") == pytest.approx(6.0e9)
    # Stale is allowed, future is not: latest history date <= requested date.
    assert scanner._resolve_pit_mcap("AAA", "2025-04-01") == pytest.approx(6.0e9)
    assert scanner._resolve_pit_mcap("AAA", "2025-01-31") == pytest.approx(5.0e9)


def test_resolve_falls_back_to_current_snapshot_with_warning(history_db, caplog):
    scanner = WyckoffAutomaton()
    scanner._load_pit_history(["AAA", "BBB", "CCC"])
    # "AAA" has history rows, but all are AFTER the requested date.
    with caplog.at_level(logging.WARNING, logger=WY_LOGGER):
        mcap = scanner._resolve_pit_mcap("AAA", "2024-12-31")
    assert mcap == pytest.approx(9.0e9)  # current fundamentals snapshot
    assert any("AAA" in r.message and "2024-12-31" in r.message for r in caplog.records)
    # "CCC" has no history rows at all inside the chunked load scope.
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger=WY_LOGGER):
        mcap_ccc = scanner._resolve_pit_mcap("CCC", "2025-06-01")
    assert mcap_ccc == pytest.approx(2.0e9)
    assert any("CCC" in r.message for r in caplog.records)


def test_resolve_returns_none_when_nothing_found(history_db):
    scanner = WyckoffAutomaton()
    scanner._load_pit_history(["AAA"])
    # "ZZZ" is neither in history nor in the current fundamentals snapshot.
    assert scanner._resolve_pit_mcap("ZZZ", "2025-06-01") is None


def test_resolve_returns_none_when_db_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(wy_mod, "DB_DIR", str(tmp_path))
    scanner = WyckoffAutomaton()
    assert scanner._resolve_pit_mcap("AAA", "2025-06-01") is None


def test_load_pit_history_filters_to_universe(history_db):
    scanner = WyckoffAutomaton()
    scanner._load_pit_history(["AAA"])  # BBB is in the table but not requested
    assert "AAA" in scanner._pit_history
    assert "BBB" not in scanner._pit_history


def test_load_pit_history_chunked_across_var_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(wy_mod, "DB_DIR", str(tmp_path))
    db_path = os.path.join(str(tmp_path), "myra_valuation.db")
    conn = sqlite3.connect(db_path)
    try:
        create_fundamentals_history_table(conn)
        n = 901  # > the 900-var chunk boundary
        conn.executemany(
            "INSERT OR REPLACE INTO fundamentals_history "
            "(symbol, date, market_cap) VALUES (?, ?, ?)",
            [(f"S{i:04d}", "2025-03-31", float(i)) for i in range(n)],
        )
        conn.commit()
    finally:
        conn.close()
    scanner = WyckoffAutomaton()
    scanner._load_pit_history([f"S{i:04d}" for i in range(n)])
    assert len(scanner._pit_history) == n
    assert scanner._resolve_pit_mcap("S0900", "2025-04-01") == pytest.approx(900.0)


# ---------------------------------------------------------------------------
# Event integration — point_in_time_mcap, scoring untouched
# ---------------------------------------------------------------------------


def _spring_df():
    """Synthetic Spring (grab row 68, confirmation row 69, no equal-low zone),
    mirroring tests/test_wyckoff_spring_score.py::_build_wyckoff_df."""
    n = 70
    data = {
        "date": pd.date_range("2025-01-01", periods=n),
        "open": [103.5] * n,
        "high": [106.0] * n,
        "low": [100.0] * n,
        "close": [103.0] * n,
        "volume": [50000.0] * n,
        "delivery": [15000.0] * n,
        "delivery_pct": [30.0] * n,
        "swing_low": [float("nan")] * n,
        "nifty_outperformance_score": [0.0] * n,
        "sma_50": [102.0] * n,
        "high_52w": [130.0] * n,
        "low_52w": [95.0] * n,
    }
    df = pd.DataFrame(data)
    for idx, overrides in {
        68: {
            "low": 98.5,
            "high": 103.5,
            "close": 101.0,
            "open": 103.0,
            "volume": 70000.0,
            "delivery_pct": 60.0,
            "swing_low": 100.0,
        },
        69: {"low": 102.0, "high": 106.0, "close": 105.0, "open": 103.0},
    }.items():
        for col, val in overrides.items():
            df.at[idx, col] = val
    return df


def test_detect_events_pit_mcap_and_identical_scores(history_db):
    df = _spring_df()
    plain = WyckoffAutomaton()
    rich = WyckoffAutomaton()
    rich._load_pit_history(["TEST"])  # no TEST rows → empty history
    rich._pit_history["TEST"] = [
        ("2025-01-31", 5.0e9),
        ("2025-03-31", 6.0e9),  # AFTER the event date → must NOT be used
        ("2025-04-30", 6.5e9),
    ]

    e_plain = [
        e for e in plain._detect_events(df, symbol="TEST") if e["event"] == "Spring"
    ]
    e_rich = [
        e for e in rich._detect_events(df, symbol="TEST") if e["event"] == "Spring"
    ]
    assert len(e_plain) == 1 and len(e_rich) == 1

    # Scoring is byte-identical whether or not history exists (deferred).
    for field in (
        "spring_score",
        "quality",
        "grade",
        "equal_low_zone",
        "two_candle_confirm",
        "event_date",
        "lower_wick_ratio",
        "close_location",
        "grab_depth_pct",
    ):
        assert e_rich[0][field] == e_plain[0][field], field

    # Confirmed Spring is dated on row 69 (same str() format the repo's other
    # event-date tests use — "2025-03-11 00:00:00"): the pre-event snapshot
    # resolves; the 2025-03-31 snapshot would be a look-ahead and must NOT be
    # used (with the Timestamp-string as-of, its comparison still holds).
    assert e_rich[0]["event_date"] == str(df["date"].iloc[69])
    assert e_rich[0]["point_in_time_mcap"] == pytest.approx(5.0e9)
    # No history loaded → the field is None (never a bogus value).
    assert e_plain[0]["point_in_time_mcap"] is None


def test_detect_events_fallback_reads_fundamentals_snapshot(history_db, caplog):
    """Direct _detect_events call (no scan → no universe-seeded map): the
    lazy per-symbol fallback query must resolve from the current fundamentals
    snapshot and emit the missing-data warning."""
    df = _spring_df()
    scanner = WyckoffAutomaton()  # _pit_history / _current_mcap_map not seeded
    with caplog.at_level(logging.WARNING, logger=WY_LOGGER):
        events = scanner._detect_events(df, symbol="BBB")
    springs = [e for e in events if e["event"] == "Spring"]
    assert len(springs) == 1
    # BBB has no history rows → falls back to the latest fundamentals mcap.
    assert springs[0]["point_in_time_mcap"] == pytest.approx(3.5e9)
    assert any("BBB" in r.message for r in caplog.records)
    # The lazy lookup is memoized: a second call for the same symbol must not
    # re-query (and must not add another warning).
    caplog.clear()
    events2 = scanner._detect_events(df, symbol="BBB")
    springs2 = [e for e in events2 if e["event"] == "Spring"]
    assert springs2[0]["point_in_time_mcap"] == pytest.approx(3.5e9)
    assert not [r for r in caplog.records if "BBB" in r.message]
