"""Tests for the Cross-Buy scanner API route.

Focus is the response contract the frontend depends on: 0-1 ratio values, the
pre-limit `total` vs capped `returned` split, and filters being applied in SQL
(so size/tag filters do not silently truncate the result set).
"""

import os
import sqlite3

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from myra_app import cross_buy_processor as proc
from myra_web.routes import cross_buy as cb_route

DDL_FCB = """
CREATE TABLE fund_cross_buy (
    symbol TEXT,
    month TEXT,
    total_funds INTEGER,
    large_funds INTEGER,
    mid_funds INTEGER,
    small_funds INTEGER,
    multi_funds INTEGER,
    other_funds INTEGER,
    cross_buy_ratio REAL,
    signal_tag TEXT,
    PRIMARY KEY (symbol, month)
)
"""

DDL_FUNDAMENTALS = """
CREATE TABLE fundamentals (
    symbol TEXT PRIMARY KEY,
    market_cap REAL,
    sector TEXT
)
"""


def _seed(conn):
    """3 months of data. 2026-08 is the newest.

    LARGE_A/LARGE_B have high ratios but are buried behind LOW rows in the
    default ratio DESC ordering, so a post-LIMIT category filter would drop them.
    """
    rows = [
        # symbol,      month,   tot, lg, md, sm, mu, ot, ratio, tag
        ("LARGE_A", "2026-08", 12, 2, 4, 3, 2, 1, 0.90, "STRONG_CROSS_BUY"),
        ("LARGE_B", "2026-08", 9, 1, 3, 2, 2, 1, 0.85, "STRONG_CROSS_BUY"),
        ("MID_A", "2026-08", 8, 2, 2, 2, 1, 1, 0.50, "MIXED"),
        ("SMALL_A", "2026-08", 4, 1, 1, 1, 1, 0, 0.40, "MIXED"),
        ("STYLE_A", "2026-08", 6, 6, 0, 0, 0, 0, 0.00, "STYLE_CONCENTRATED"),
        ("NOFUND_A", "2026-08", 3, 1, 1, 1, 0, 0, 0.66, "CROSS_BUY"),
        ("LARGE_A", "2026-07", 10, 2, 3, 3, 1, 1, 0.80, "CROSS_BUY"),
        ("LOW_JUNK", "2026-07", 2, 1, 1, 0, 0, 0, 0.10, "MIXED"),
    ]
    conn.executemany(
        "INSERT INTO fund_cross_buy (symbol, month, total_funds, large_funds,"
        " mid_funds, small_funds, multi_funds, other_funds, cross_buy_ratio,"
        " signal_tag) VALUES (?,?,?,?,?,?,?,?,?,?)",
        rows,
    )
    conn.executemany(
        "INSERT INTO fundamentals (symbol, market_cap, sector) VALUES (?,?,?)",
        [
            ("LARGE_A", 3.0e11, "Financials"),
            ("LARGE_B", 2.5e11, "Energy"),
            ("MID_A", 8.0e10, "Pharma"),
            ("SMALL_A", 1.0e10, "Defence"),
            ("STYLE_A", 4.0e11, "IT"),
            ("LOW_JUNK", 1.5e11, "Auto"),
            # NOFUND_A deliberately absent -> LEFT JOIN must not drop it.
        ],
    )
    conn.commit()


def _client():
    """Minimal app mounting only this router (keeps the real server out of tests)."""
    app = FastAPI()
    app.include_router(cb_route.router)
    return TestClient(app)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(cb_route, "DB_DIR", str(tmp_path))
    db_path = os.path.join(str(tmp_path), "myra_valuation.db")
    conn = sqlite3.connect(db_path)
    conn.execute(DDL_FCB)
    conn.execute(DDL_FUNDAMENTALS)
    _seed(conn)
    conn.close()
    return _client()


def _get(client, **params):
    r = client.get("/api/cross-buy/scanner", params=params)
    assert r.status_code == 200, r.text
    return r.json()


def test_ratio_is_zero_to_one_fraction(client):
    """Frontend renders the ratio with MiniBarCell(max=1); 0-1 is required."""
    d = _get(client, month="2026-08")
    by_sym = {s["symbol"]: s for s in d["stocks"]}
    assert by_sym["LARGE_A"]["cross_buy_ratio"] == pytest.approx(0.90)
    assert by_sym["STYLE_A"]["cross_buy_ratio"] == pytest.approx(0.0)
    assert all(0.0 <= s["cross_buy_ratio"] <= 1.0 for s in d["stocks"])


def test_total_is_prelimit_and_returned_is_capped(client):
    """total lets the UI say "N of M"; returned is what actually rendered."""
    d = _get(client, month="2026-08", limit=2)
    assert len(d["stocks"]) == 2
    assert d["returned"] == 2
    assert d["total"] == 6


def test_category_filter_applied_before_limit(client):
    """Regression: category was filtered post-LIMIT, hiding qualifying rows.

    SMALL_A (ratio 0.40) falls outside the global top-2 by ratio. Filtering
    after LIMIT returned zero rows; filtering in SQL returns it correctly.
    """
    d = _get(client, month="2026-08", stock_category="Small", limit=2)
    assert [s["symbol"] for s in d["stocks"]] == ["SMALL_A"]
    assert d["total"] == 1


def test_category_filter_returns_full_bucket(client):
    """Large = LARGE_A + LARGE_B + STYLE_A (STYLE_A has a 4e11 mcap)."""
    d = _get(client, month="2026-08", stock_category="Large", limit=100)
    assert {s["symbol"] for s in d["stocks"]} == {"LARGE_A", "LARGE_B", "STYLE_A"}
    assert all(s["stock_category"] == "Large" for s in d["stocks"])
    assert d["total"] == 3


def test_category_filter_total_counts_all_matches(client):
    """The pre-limit total must respect the category filter too."""
    d = _get(client, month="2026-08", stock_category="Small", limit=100)
    assert [s["symbol"] for s in d["stocks"]] == ["SMALL_A"]
    assert d["total"] == 1


def test_symbol_without_fundamentals_survives_left_join(client):
    d = _get(client, month="2026-08", limit=100)
    by_sym = {s["symbol"]: s for s in d["stocks"]}
    assert "NOFUND_A" in by_sym
    assert by_sym["NOFUND_A"]["stock_category"] == "Unknown"
    assert by_sym["NOFUND_A"]["market_cap"] is None
    assert by_sym["NOFUND_A"]["sector"] is None


def test_unknown_category_is_filterable(client):
    d = _get(client, month="2026-08", stock_category="Unknown", limit=100)
    assert [s["symbol"] for s in d["stocks"]] == ["NOFUND_A"]
    assert d["total"] == 1


def test_category_filter_is_case_insensitive(client):
    assert _get(client, month="2026-08", stock_category="large")["total"] == 3


def test_signal_tag_filter(client):
    d = _get(client, month="2026-08", signal_tag="STRONG_CROSS_BUY", limit=100)
    assert {s["symbol"] for s in d["stocks"]} == {"LARGE_A", "LARGE_B"}
    assert d["total"] == 2


def test_min_ratio_and_min_funds_filters_compose(client):
    d = _get(client, month="2026-08", min_cross_buy_ratio=0.6, limit=100)
    assert {s["symbol"] for s in d["stocks"]} == {"LARGE_A", "LARGE_B", "NOFUND_A"}
    assert d["total"] == 3

    d = _get(client, month="2026-08", min_total_funds=10, limit=100)
    assert {s["symbol"] for s in d["stocks"]} == {"LARGE_A"}
    assert d["total"] == 1


def test_month_defaults_to_newest(client):
    d = _get(client)
    assert d["month"] == "2026-08"
    assert all(s["month"] == "2026-08" for s in d["stocks"])


def test_rows_ordered_by_ratio_then_funds(client):
    d = _get(client, month="2026-08", limit=100)
    ratios = [s["cross_buy_ratio"] for s in d["stocks"]]
    assert ratios == sorted(ratios, reverse=True)


def test_combined_filters(client):
    d = _get(
        client,
        month="2026-08",
        stock_category="Large",
        min_cross_buy_ratio=0.9,
        limit=100,
    )
    assert [s["symbol"] for s in d["stocks"]] == ["LARGE_A"]
    assert d["total"] == 1


def test_months_endpoint_newest_first(client):
    r = client.get("/api/cross-buy/months")
    assert r.status_code == 200
    assert r.json()["months"] == ["2026-08", "2026-07"]


def test_empty_month_returns_zero_counts(client):
    d = _get(client, month="1999-01", limit=100)
    assert d == {"month": "1999-01", "stocks": [], "total": 0, "returned": 0}


def test_missing_db_returns_503(tmp_path, monkeypatch):
    monkeypatch.setattr(cb_route, "DB_DIR", str(tmp_path / "nope"))
    r = _client().get("/api/cross-buy/scanner", params={"month": "2026-08"})
    assert r.status_code == 503


# --- processor month discovery -------------------------------------------------
#
# DEFAULT_MONTHS was a hardcoded month list that silently drifted behind the data
# (newest month in the DB was 2026-08 while the constant stopped at 2026-07). It is
# now gone: months come from the holdings folder, falling back to the DB.


@pytest.fixture
def fcb_db(tmp_path, monkeypatch):
    """Valuation DB at a tmp path, wired into the processor module."""
    monkeypatch.setattr(proc, "VALUATION_DB_PATH", str(tmp_path / "myra_valuation.db"))
    return tmp_path / "myra_valuation.db"


def _make_fcb_db(path, months):
    conn = sqlite3.connect(str(path))
    conn.execute(DDL_FCB)
    conn.executemany(
        "INSERT INTO fund_cross_buy (symbol, month, cross_buy_ratio, signal_tag)"
        " VALUES (?,?,?,?)",
        [(f"SYM{i}", m, 0.5, "MIXED") for i, m in enumerate(months)],
    )
    conn.commit()
    conn.close()


def test_months_discovered_from_holdings_folder(tmp_path, monkeypatch):
    holdings = tmp_path / "temp_holdings"
    holdings.mkdir()
    for name in ("fund_large_08_26.csv", "fund_small_06_26.csv", "notes.txt"):
        (holdings / name).write_text("x")
    monkeypatch.setattr(proc, "RAW_HOLDINGS_DIR", holdings)
    monkeypatch.setattr(proc, "VALUATION_DB_PATH", str(tmp_path / "absent.db"))
    assert proc.detect_available_months() == ["2026-06", "2026-08"]


def test_empty_folder_falls_back_to_db_months(tmp_path, monkeypatch):
    """Degraded mode must refresh months that actually have data."""
    holdings = tmp_path / "empty"
    holdings.mkdir()
    db = tmp_path / "myra_valuation.db"
    _make_fcb_db(db, ["2026-07", "2026-08"])
    monkeypatch.setattr(proc, "RAW_HOLDINGS_DIR", holdings)
    monkeypatch.setattr(proc, "VALUATION_DB_PATH", str(db))
    assert proc.detect_available_months() == ["2026-07", "2026-08"]


def test_missing_folder_falls_back_to_db_months(tmp_path, monkeypatch):
    monkeypatch.setattr(proc, "RAW_HOLDINGS_DIR", tmp_path / "does_not_exist")
    db = tmp_path / "myra_valuation.db"
    _make_fcb_db(db, ["2026-08"])
    monkeypatch.setattr(proc, "VALUATION_DB_PATH", str(db))
    assert proc.detect_available_months() == ["2026-08"]


def test_no_sources_yields_empty_list_not_hardcoded_months(tmp_path, monkeypatch):
    """Empty folder + empty DB => nothing to do, not four invented months."""
    holdings = tmp_path / "empty"
    holdings.mkdir()
    monkeypatch.setattr(proc, "RAW_HOLDINGS_DIR", holdings)
    monkeypatch.setattr(proc, "VALUATION_DB_PATH", str(tmp_path / "absent.db"))
    assert proc.detect_available_months() == []


def test_backfill_with_no_months_is_not_a_crash(monkeypatch):
    """backfill_months must degrade to success=False, never raise.

    detect_available_months is stubbed so this never touches the production
    holdings folder, valuation DB, or the RupeeVest downloader subprocess.
    """
    monkeypatch.setattr(proc, "detect_available_months", lambda: [])
    summary = proc.backfill_months()
    assert summary["success"] is False
    assert summary["requested"] == 0
    assert summary["results"] == []


def test_default_months_constant_removed():
    assert not hasattr(proc, "DEFAULT_MONTHS")
