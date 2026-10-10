"""Tests for the Phase 2.3 fundamentals-enrich task module.

The task is split so its core (``enrich_symbols``) can be driven with an
in-memory connection and stub fetchers, and its gates can be exercised without
opening a live database.
"""

import datetime as dt
import logging
import sqlite3
import threading

import pytest

from myra_app import enrichment_sources as es
from myra_app.tasks import fundamentals_enrich as fe
from myra_app.tasks.context import TaskContext

FUNDAMENTALS_DDL = """
CREATE TABLE fundamentals (
    symbol TEXT PRIMARY KEY,
    market_cap REAL,
    shares_outstanding REAL,
    free_float_shares REAL,
    free_float_pct REAL,
    free_float_market_cap REAL,
    promoter_holding_pct REAL,
    public_holding_pct REAL,
    insider_holding_pct REAL,
    sector TEXT,
    industry TEXT,
    pe REAL,
    roe REAL,
    book_value REAL,
    dividend_yield REAL,
    eps REAL,
    last_updated TEXT,
    last_fundamental_update TEXT
)
"""


def _ctx():
    return TaskContext(shutdown_event=threading.Event(), logger=logging.getLogger("t"))


def _conn():
    conn = sqlite3.connect(":memory:")
    conn.execute(FUNDAMENTALS_DDL)
    return conn


def _stub_sources(monkeypatch, yfinance):
    """Replace every adapter with a stub so no network is touched."""
    monkeypatch.setitem(es.SOURCE_FETCHERS, es.YFINANCE, lambda symbol: yfinance)
    monkeypatch.setitem(es.SOURCE_FETCHERS, es.NSE_SHAREHOLDING, lambda symbol: {})
    monkeypatch.setitem(es.SOURCE_FETCHERS, es.SCREENER, lambda symbol: {})


@pytest.fixture
def cache(tmp_path):
    """Isolated, empty, on-disk cache.

    The production ``TtlCache`` is backed by the live ``myra_cache_network.db``,
    so using one here would (a) serve stale real values before the stubs run and
    (b) write to a live database. Point it at a temp file instead.
    """
    from myra_app.data_sources.cache import TtlCache

    return TtlCache(db_path=str(tmp_path / "network_cache.db"))


class TestEnrichSymbols:
    def test_fills_missing_columns(self, monkeypatch, cache):
        conn = _conn()
        conn.execute("INSERT INTO fundamentals (symbol) VALUES ('TCS')")
        _stub_sources(
            monkeypatch,
            {"market_cap": 1.0e12, "shares_outstanding": 3.6e9, "roe": 45.0},
        )
        summary = fe.enrich_symbols(conn, ["TCS"], cache=cache, with_health=False)
        row = conn.execute(
            "SELECT market_cap, roe, shares_outstanding FROM fundamentals "
            "WHERE symbol = 'TCS'"
        ).fetchone()
        assert row[0] == 1.0e12  # filled
        assert row[1] == 45.0  # filled
        assert row[2] == 3.6e9
        assert summary["written"] == 1
        assert summary["selected"] == 1

    def test_absent_value_never_clobbers_existing(self, monkeypatch, cache):
        """The invariant is 'never overwrite a valid metric with null/0/NA'."""
        conn = _conn()
        conn.execute("INSERT INTO fundamentals (symbol, roe) VALUES ('Z', 12.5)")
        _stub_sources(monkeypatch, {"market_cap": 9.0})  # no roe in the fetch
        fe.enrich_symbols(conn, ["Z"], cache=cache, with_health=False)
        roe = conn.execute(
            "SELECT roe FROM fundamentals WHERE symbol = 'Z'"
        ).fetchone()[0]
        assert roe == 12.5

    def test_zero_does_not_clobber_valid_market_cap(self, monkeypatch, cache):
        conn = _conn()
        conn.execute(
            "INSERT INTO fundamentals (symbol, market_cap) VALUES ('X', 500.0)"
        )
        _stub_sources(monkeypatch, {"market_cap": 0, "shares_outstanding": 10.0})
        fe.enrich_symbols(conn, ["X"], cache=cache, with_health=False)
        market_cap = conn.execute(
            "SELECT market_cap FROM fundamentals WHERE symbol = 'X'"
        ).fetchone()[0]
        assert market_cap == 500.0  # zero-guarded

    def test_stamps_last_updated_but_not_fundamental_update(self, monkeypatch, cache):
        conn = _conn()
        conn.execute("INSERT INTO fundamentals (symbol) VALUES ('Y')")
        _stub_sources(monkeypatch, {"market_cap": 100.0})
        fe.enrich_symbols(conn, ["Y"], cache=cache, with_health=False)
        row = conn.execute(
            "SELECT last_updated, last_fundamental_update FROM fundamentals "
            "WHERE symbol = 'Y'"
        ).fetchone()
        assert row[0] is not None
        assert row[1] is None  # must not suppress the shares backfill

    def test_failing_symbol_does_not_abort_batch(self, monkeypatch, cache):
        conn = _conn()
        conn.execute("INSERT INTO fundamentals (symbol) VALUES ('A')")
        conn.execute("INSERT INTO fundamentals (symbol) VALUES ('B')")

        def boom(symbol, conn, *args, **kwargs):
            if symbol == "A":
                raise RuntimeError("network exploded")
            return {"symbol": symbol, "values": {"market_cap": 7.0}, "written": 1}

        monkeypatch.setattr(es, "enrich_symbol", boom)
        summary = fe.enrich_symbols(conn, ["A", "B"], cache=cache, with_health=False)
        assert summary["failed"] == 1
        assert summary["written"] == 1


class TestRunGates:
    def test_disabled_writer_returns_before_selection(self, monkeypatch):
        monkeypatch.setattr(fe, "DISABLE_FUNDAMENTAL_WRITERS", True)
        monkeypatch.setattr(fe, "now_ist", lambda: dt.datetime(2026, 10, 9, 19, 0))

        def _never(*_a, **_k):
            pytest.fail("must not select symbols when writers are disabled")

        monkeypatch.setattr(fe, "_select_symbols", _never)
        fe.run(_ctx())  # must simply return

    def test_off_hours_returns_without_opening_db(self, monkeypatch):
        monkeypatch.setattr(fe, "DISABLE_FUNDAMENTAL_WRITERS", False)
        # Saturday -> off-hours regardless of the hour.
        monkeypatch.setattr(fe, "now_ist", lambda: dt.datetime(2026, 10, 10, 10, 0))

        def _never(*_a, **_k):
            pytest.fail("must not select symbols off-hours")

        monkeypatch.setattr(fe, "_select_symbols", _never)
        fe.run(_ctx())  # must simply return

    def test_before_close_weekday_returns(self, monkeypatch):
        monkeypatch.setattr(fe, "DISABLE_FUNDAMENTAL_WRITERS", False)
        # Friday 10:00 IST -> before the 18:00 gate.
        monkeypatch.setattr(fe, "now_ist", lambda: dt.datetime(2026, 10, 9, 10, 0))

        def _never(*_a, **_k):
            pytest.fail("must not select symbols before close")

        monkeypatch.setattr(fe, "_select_symbols", _never)
        fe.run(_ctx())
