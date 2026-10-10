"""Phase 2 tests — source adapters + cost-aware enrichment orchestration.

All network access is replaced by injected fetchers; the cache is pointed at a
temporary DB so no live database is ever touched.
"""

from __future__ import annotations

import sqlite3
import threading

import pytest

from myra_app import enrichment_sources as es
from myra_app.data_sources.cache import TtlCache


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _cache(tmp_path) -> TtlCache:
    return TtlCache(db_path=str(tmp_path / "network_cache.db"))


def _patch_sources(monkeypatch, *, yf, nse=None, scr=None):
    monkeypatch.setitem(es.SOURCE_FETCHERS, es.YFINANCE, yf)
    monkeypatch.setitem(es.SOURCE_FETCHERS, es.NSE_SHAREHOLDING, nse or (lambda s: {}))
    monkeypatch.setitem(es.SOURCE_FETCHERS, es.SCREENER, scr or (lambda s: {}))


_COLUMNS = (
    "symbol TEXT PRIMARY KEY, market_cap REAL, shares_outstanding REAL, "
    "free_float_pct REAL, free_float_market_cap REAL, free_float_shares REAL, "
    "promoter_holding_pct REAL, public_holding_pct REAL, insider_holding_pct REAL, "
    "sector TEXT, industry TEXT, pe REAL, roe REAL, book_value REAL, "
    "dividend_yield REAL, eps REAL, last_updated TEXT, last_fundamental_update TEXT"
)


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute(f"CREATE TABLE fundamentals ({_COLUMNS})")
    conn.execute(
        "INSERT INTO fundamentals (symbol, market_cap, roe) VALUES ('TEST', 999.0, 7.0)"
    )
    conn.commit()
    return conn


# --------------------------------------------------------------------------- #
# derived free float
# --------------------------------------------------------------------------- #
def test_yfinance_fetch_converts_fractions_to_percent(monkeypatch):
    from myra_app.fetchers import full_fundamentals as ff

    monkeypatch.setattr(ff, "YFINANCE_AVAILABLE", True)
    monkeypatch.setattr(
        ff,
        "fetch_yfinance_data",
        lambda s: {
            "market_cap": 1000.0,
            "roe": 0.4557,  # fraction
            "held_percent_insiders": 0.7179,  # fraction
            "sector": "Technology",
        },
    )
    out = es.yfinance_fetch("TCS")
    assert out[es.ROE] == pytest.approx(45.57)
    assert out[es.INSIDER] == pytest.approx(71.79)
    assert out[es.MCAP] == 1000.0


def test_screener_fetch_converts_crore_to_rupees(monkeypatch):
    from myra_app.fetchers import full_fundamentals as ff

    monkeypatch.setattr(ff, "SCRAPLING_AVAILABLE", True)
    monkeypatch.setattr(
        ff,
        "fetch_screener_snapshot",
        lambda s: {
            "market_cap_crore": 1772762.0,
            "roe": 8.91,
            "roce": 10.3,
            "shareholding": {"promoters": 50.48},
        },
    )
    out = es.screener_fetch("RELIANCE")
    assert out[es.MCAP] == pytest.approx(1772762.0 * 1e7)
    assert out[es.ROE] == 8.91
    assert out[es.PROMOTER] == 50.48


def test_derive_free_float_from_public():
    values = es.derive_free_float(
        {es.PUBLIC: 49.52, es.SHARES: 1000.0, es.MCAP: 1_000_000.0}
    )
    assert values[es.FREE_FLOAT_PCT] == pytest.approx(49.52)
    assert values[es.FREE_FLOAT_SHARES] == pytest.approx(495.2)
    assert values[es.FREE_FLOAT_MCAP] == pytest.approx(495200.0)


def test_derive_free_float_from_promoter_only():
    values = es.derive_free_float({es.PROMOTER: 50.48})
    assert values[es.FREE_FLOAT_PCT] == pytest.approx(49.52)


def test_derive_free_float_never_overwrites_present_values():
    values = es.derive_free_float(
        {
            es.PUBLIC: 49.52,
            es.FREE_FLOAT_PCT: 10.0,
            es.FREE_FLOAT_SHARES: 5.0,
            es.FREE_FLOAT_MCAP: 7.0,
        }
    )
    assert values[es.FREE_FLOAT_PCT] == 10.0
    assert values[es.FREE_FLOAT_SHARES] == 5.0
    assert values[es.FREE_FLOAT_MCAP] == 7.0


def test_derive_free_float_rejects_out_of_range():
    values = es.derive_free_float({es.PUBLIC: 150.0})
    assert es.FREE_FLOAT_PCT not in values


# --------------------------------------------------------------------------- #
# resolution
# --------------------------------------------------------------------------- #
def test_resolve_symbol_provenance_and_derivation(monkeypatch, tmp_path):
    _patch_sources(
        monkeypatch,
        yf=lambda s: {es.MCAP: 100.0, es.SHARES: 10.0},
        nse=lambda s: {es.PROMOTER: 50.0, es.PUBLIC: 50.0},
        scr=lambda s: {es.ROE: 12.5, es.ROCE: 15.0},
    )
    values, provenance = es.resolve_symbol(
        "TEST",
        registry=es.default_registry(),
        cache=_cache(tmp_path),
        rate_limiters={},
    )
    assert values[es.MCAP] == 100.0
    assert provenance[es.MCAP] == es.YFINANCE
    assert provenance[es.ROE] == es.SCREENER
    assert provenance[es.PROMOTER] == es.NSE_SHAREHOLDING
    assert values[es.FREE_FLOAT_PCT] == pytest.approx(50.0)
    assert values[es.FREE_FLOAT_SHARES] == pytest.approx(5.0)
    assert values[es.FREE_FLOAT_MCAP] == pytest.approx(50.0)


def test_resolve_symbol_roe_falls_back_to_screener(monkeypatch, tmp_path):
    # yfinance returns values but no ROE -> resolver must reach Screener.
    _patch_sources(
        monkeypatch,
        yf=lambda s: {es.MCAP: 1.0},
        scr=lambda s: {es.ROE: 9.9},
    )
    values, provenance = es.resolve_symbol(
        "TEST",
        want=(es.ROE,),
        registry=es.default_registry(),
        cache=_cache(tmp_path),
        rate_limiters={},
    )
    assert values[es.ROE] == 9.9
    assert provenance[es.ROE] == es.SCREENER


# --------------------------------------------------------------------------- #
# writes
# --------------------------------------------------------------------------- #
def test_enrich_symbol_fill_only_null_protection(monkeypatch, tmp_path):
    # yfinance supplies shares but NO market cap -> existing mcap must survive.
    _patch_sources(monkeypatch, yf=lambda s: {es.SHARES: 5.0})
    conn = _conn()
    report = es.enrich_symbol(
        "TEST",
        conn,
        want=(es.MCAP, es.SHARES),
        dry_run=False,
        registry=es.default_registry(),
        cache=_cache(tmp_path),
        rate_limiters={},
    )
    assert report["written"] == 1
    mcap, shares = conn.execute(
        "SELECT market_cap, shares_outstanding FROM fundamentals WHERE symbol='TEST'"
    ).fetchone()
    assert mcap == 999.0  # not clobbered by the missing value
    assert shares == 5.0


def test_enrich_symbol_dry_run_writes_nothing(monkeypatch, tmp_path):
    _patch_sources(monkeypatch, yf=lambda s: {es.MCAP: 111.0, es.PE: 22.0})
    conn = _conn()
    report = es.enrich_symbol(
        "TEST",
        conn,
        want=(es.MCAP, es.PE),
        dry_run=True,
        registry=es.default_registry(),
        cache=_cache(tmp_path),
        rate_limiters={},
    )
    assert report["written"] == 0
    mcap, pe = conn.execute(
        "SELECT market_cap, pe FROM fundamentals WHERE symbol='TEST'"
    ).fetchone()
    assert mcap == 999.0
    assert pe is None


def test_enrich_symbol_valid_value_updates(monkeypatch, tmp_path):
    _patch_sources(monkeypatch, yf=lambda s: {es.MCAP: 111.0})
    conn = _conn()
    es.enrich_symbol(
        "TEST",
        conn,
        want=(es.MCAP,),
        dry_run=False,
        registry=es.default_registry(),
        cache=_cache(tmp_path),
        rate_limiters={},
    )
    (mcap,) = conn.execute(
        "SELECT market_cap FROM fundamentals WHERE symbol='TEST'"
    ).fetchone()
    assert mcap == 111.0


# --------------------------------------------------------------------------- #
# batch
# --------------------------------------------------------------------------- #
def test_enrich_batch_reports_counts(monkeypatch, tmp_path):
    _patch_sources(monkeypatch, yf=lambda s: {es.MCAP: 1.0, es.SHARES: 2.0})
    conn = sqlite3.connect(":memory:")
    conn.execute(f"CREATE TABLE fundamentals ({_COLUMNS})")
    for sym in ("A", "B", "C"):
        conn.execute("INSERT INTO fundamentals (symbol) VALUES (?)", (sym,))
    conn.commit()
    summary = es.enrich_batch(
        ["A", "B", "C"],
        conn,
        want=(es.MCAP, es.SHARES),
        dry_run=False,
        registry=es.default_registry(),
        cache=_cache(tmp_path),
        rate_limiters={},
    )
    assert summary == {
        "total": 3,
        "written": 3,
        "resolved": 3,
        "failed": 0,
        "dry_run": False,
    }


def test_enrich_batch_cancel_raises(monkeypatch, tmp_path):
    cancel = threading.Event()
    calls = {"n": 0}

    def yf(symbol):
        calls["n"] += 1
        if calls["n"] >= 1:
            cancel.set()
        return {es.MCAP: 1.0}

    _patch_sources(monkeypatch, yf=yf)

    from myra_app.feature_enrichment import EnrichmentCancelled

    with pytest.raises(EnrichmentCancelled):
        es.enrich_batch(
            ["A", "B", "C"],
            None,
            want=(es.MCAP,),
            dry_run=True,
            cancel_event=cancel,
            registry=es.default_registry(),
            cache=_cache(tmp_path),
            rate_limiters={},
        )
