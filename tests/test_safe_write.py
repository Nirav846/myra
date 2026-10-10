"""Phase 0 tests — safe (non-clobbering) write helpers."""

from __future__ import annotations

import sqlite3

from myra_app.safe_write import sanitize_metrics, update_fill_only, upsert_row


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE fundamentals ("
        "symbol TEXT PRIMARY KEY, market_cap REAL, sector TEXT, roe REAL, "
        "shares_outstanding REAL, free_float_pct REAL)"
    )
    conn.execute(
        "INSERT INTO fundamentals (symbol, market_cap, sector, roe) "
        "VALUES ('X', 100.0, 'Energy', 8.9)"
    )
    conn.commit()
    return conn


def test_update_fill_only_skips_none_values():
    conn = _conn()
    # A partial fetch that returns None must NOT erase existing values.
    update_fill_only(
        conn, "fundamentals", "symbol", "X", {"market_cap": None, "sector": None}
    )
    row = conn.execute(
        "SELECT market_cap, sector, roe FROM fundamentals WHERE symbol='X'"
    ).fetchone()
    assert row == (100.0, "Energy", 8.9)


def test_update_fill_only_writes_present_values():
    conn = _conn()
    n = update_fill_only(conn, "fundamentals", "symbol", "X", {"roe": 9.5})
    assert n == 1
    assert (
        conn.execute("SELECT roe FROM fundamentals WHERE symbol='X'").fetchone()[0]
        == 9.5
    )


def test_zero_guarded_does_not_overwrite_with_zero():
    conn = _conn()
    update_fill_only(
        conn,
        "fundamentals",
        "symbol",
        "X",
        {"market_cap": 0.0},
        zero_guarded=frozenset({"market_cap"}),
    )
    assert (
        conn.execute("SELECT market_cap FROM fundamentals WHERE symbol='X'").fetchone()[
            0
        ]
        == 100.0
    )


def test_upsert_row_preserves_existing_on_null():
    conn = _conn()
    upsert_row(
        conn,
        "fundamentals",
        "symbol",
        {"symbol": "X", "market_cap": None, "roe": 10.0, "sector": None},
    )
    row = conn.execute(
        "SELECT market_cap, sector, roe FROM fundamentals WHERE symbol='X'"
    ).fetchone()
    assert row == (100.0, "Energy", 10.0)


def test_upsert_row_inserts_new_symbol():
    conn = _conn()
    upsert_row(conn, "fundamentals", "symbol", {"symbol": "Y", "roe": 12.0})
    assert (
        conn.execute("SELECT roe FROM fundamentals WHERE symbol='Y'").fetchone()[0]
        == 12.0
    )


def test_sanitize_metrics_drops_out_of_range_free_float():
    out = sanitize_metrics({"free_float_pct": 150.0, "free_float_market_cap": 1e9})
    assert "free_float_pct" not in out
    assert "free_float_market_cap" not in out
    # A valid value is preserved.
    assert sanitize_metrics({"free_float_pct": 45.0})["free_float_pct"] == 45.0
