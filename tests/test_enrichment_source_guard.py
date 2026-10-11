"""Integration test: enrich_symbol honours the source-ownership guard."""

import sqlite3
from datetime import datetime, timedelta, timezone

from myra_app import source_priority as sp
from myra_app import enrichment_sources as es

# Columns _WRITABLE touches (keep the fixture table narrow but real).
_FUNDAMENTALS_SQL = """
CREATE TABLE fundamentals (
    symbol TEXT PRIMARY KEY,
    pe REAL, roe REAL, eps REAL, book_value REAL, market_cap REAL,
    shares_outstanding REAL, sector TEXT, industry TEXT,
    promoter_holding_pct REAL, public_holding_pct REAL,
    free_float_pct REAL, free_float_market_cap REAL, free_float_shares REAL,
    insider_holding_pct REAL, dividend_yield REAL, last_updated TEXT
)
"""


def _conn():
    conn = sqlite3.connect(":memory:")
    conn.executescript(_FUNDAMENTALS_SQL)
    sp.ensure_table(conn)
    return conn


def _fake_resolve(values, provenance):
    def resolve(symbol, want, **kwargs):
        return dict(values), dict(provenance)

    return resolve


def _claim_for(conn, symbol, source):
    row = conn.execute(
        "SELECT last_success_at FROM fundamental_source_freshness "
        "WHERE symbol = ? AND source = ?",
        (symbol, source),
    ).fetchone()
    return row[0] if row else None


def _now():
    return datetime.now(timezone.utc)


def test_fresh_screener_claim_blocks_yfinance_owned_fields(monkeypatch):
    conn = _conn()
    conn.execute("INSERT INTO fundamentals (symbol) VALUES ('RELIANCE')")
    sp.record_source_success("RELIANCE", "screener_in", conn=conn)
    values = {
        "pe": 22.5,
        "roe": 17.0,
        "shares_outstanding": 1_000_000_000,
        "market_cap": 1.5e12,
    }
    provenance = {
        "pe": "yfinance",
        "roe": "yfinance",
        "shares_outstanding": "yfinance",
        "market_cap": "yfinance",
    }
    monkeypatch.setattr(es, "resolve_symbol", _fake_resolve(values, provenance))

    report = es.enrich_symbol("RELIANCE", conn, dry_run=False)

    assert report["written"] == 1
    row = conn.execute(
        "SELECT pe, roe, shares_outstanding, market_cap FROM fundamentals "
        "WHERE symbol = 'RELIANCE'"
    ).fetchone()
    # pe/roe/market_cap are Screener-owned and vetoed (stay NULL).
    assert row[0] is None and row[1] is None and row[3] is None
    # shares_outstanding is not Screener-owned and flows through.
    assert row[2] == 1_000_000_000
    # The writing source is recorded as the new owner of what it did write.
    assert _claim_for(conn, "RELIANCE", "yfinance") is not None


def test_all_owned_fields_blocked_reports_veto(monkeypatch):
    conn = _conn()
    conn.execute("INSERT INTO fundamentals (symbol) VALUES ('RELIANCE')")
    sp.record_source_success("RELIANCE", "screener_in", conn=conn)
    values = {"pe": 22.5, "roe": 17.0, "market_cap": 1.5e12}
    provenance = {k: "yfinance" for k in values}
    monkeypatch.setattr(es, "resolve_symbol", _fake_resolve(values, provenance))

    report = es.enrich_symbol("RELIANCE", conn, dry_run=False)

    assert report["written"] == 0
    assert sorted(report["blocked_by_source_priority"]) == [
        "market_cap",
        "pe",
        "roe",
    ]
    assert _claim_for(conn, "RELIANCE", "yfinance") is None


def test_stale_screener_claim_allows_takeover(monkeypatch):
    conn = _conn()
    conn.execute("INSERT INTO fundamentals (symbol) VALUES ('RELIANCE')")
    old = (_now() - timedelta(days=90)).isoformat()
    conn.execute(
        "INSERT OR REPLACE INTO fundamental_source_freshness (symbol, source, last_success_at) "
        "VALUES (?, ?, ?)",
        ("RELIANCE", "screener_in", old),
    )
    monkeypatch.setattr(sp, "takeover_days", lambda: 60)
    values = {"pe": 22.5, "roe": 17.0}
    provenance = {"pe": "yfinance", "roe": "yfinance"}
    monkeypatch.setattr(es, "resolve_symbol", _fake_resolve(values, provenance))

    report = es.enrich_symbol("RELIANCE", conn, dry_run=False)
    assert report["written"] == 1
    row = conn.execute(
        "SELECT pe, roe FROM fundamentals WHERE symbol = 'RELIANCE'"
    ).fetchone()
    assert row == (22.5, 17.0)
