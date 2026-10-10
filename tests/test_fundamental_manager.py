"""Regression tests for FundamentalManager sidecar resolution and write safety.

The production incident this pins down
--------------------------------------
``FundamentalManager`` resolved its sidecars as ``<db_dir>/meta.db`` and
``<db_dir>/valuation.db``. Those are *not* MYRA sidecars -- the real files are
``myra_metadata.db`` / ``myra_valuation.db`` per ``LibrarianCore.DB_MAP``, and
the stray ``valuation.db`` was an empty 4 KB file with zero tables.

Every enrichment therefore failed at ``INSERT ... quarterly_results`` with
``OperationalError: no such table: quarterly_results``, which a bare
``except Exception: return False`` converted into ``False``. The caller printed
that as "multi-source fallback exhausted", so 1632 symbols were reported as
network failures while every Screener.in fetch had in fact succeeded.
"""

import os
import sqlite3

import pytest

from myra_app import constants
from myra_app.fundamental_manager import FundamentalManager
from myra_app.librarian_core import LibrarianCore

_VAL_SCHEMA = """
CREATE TABLE quarterly_results (
    symbol TEXT, report_date TEXT, period_end TEXT, revenue REAL,
    net_profit REAL, eps REAL, opm_pct REAL,
    PRIMARY KEY (symbol, report_date)
);
CREATE TABLE fundamentals (
    symbol TEXT PRIMARY KEY, pe REAL, roe REAL, eps REAL, book_value REAL,
    market_cap REAL, sector TEXT, industry TEXT, promoter_holding_pct REAL,
    public_holding_pct REAL, free_float_pct REAL, free_float_market_cap REAL,
    last_updated TEXT
);
"""


@pytest.fixture
def db_dir(tmp_path):
    """A throwaway DB directory holding the real sidecar filenames."""
    val = tmp_path / LibrarianCore.DB_MAP["valuation"]
    meta = tmp_path / LibrarianCore.DB_MAP["meta"]
    conn = sqlite3.connect(str(val))
    conn.executescript(_VAL_SCHEMA)
    conn.commit()
    conn.close()
    conn = sqlite3.connect(str(meta))
    conn.execute(
        "CREATE TABLE symbols_master ("
        "symbol TEXT PRIMARY KEY, last_fundamental_update TEXT)"
    )
    conn.commit()
    conn.close()
    return str(tmp_path)


class _FakeFetcher:
    """Stands in for DataFetcher so the tests never touch the network."""

    def __init__(self, rows):
        self.rows = rows

    def fetch_fundamentals(self, symbol):
        return (self.rows, "screener_in")


def _row(**over):
    base = {
        "report_date": "2026-06",
        "period_end": "Jun 2026",
        "revenue": 100.0,
        "net_profit": 12.0,
        "eps": 3.0,
        "opm_pct": 12.0,
        "stock_pe": 22.5,
        "roe": 17.0,
        "book_value": 90.0,
        "market_cap": 1.5e10,
        "sector": "Energy",
    }
    base.update(over)
    return base


def test_sidecars_resolve_through_db_map(db_dir):
    """The bare 'meta.db'/'valuation.db' names are the root cause; the manager
    must resolve the real MYRA sidecar filenames."""
    mgr = FundamentalManager(db_dir=db_dir)

    assert os.path.basename(mgr.val_db) == LibrarianCore.DB_MAP["valuation"]
    assert os.path.basename(mgr.val_db) == "myra_valuation.db"
    assert os.path.basename(mgr.meta_db) == LibrarianCore.DB_MAP["meta"]
    assert os.path.basename(mgr.meta_db) == "myra_metadata.db"


def test_default_db_dir_follows_constants(monkeypatch, tmp_path):
    """No db_dir must resolve to constants.DB_DIR -- not <repo-root>/db, which
    does not exist. Read at call time so the test-isolation fixture that
    rebinds constants.DB_DIR also redirects this class."""
    monkeypatch.setattr(constants, "DB_DIR", str(tmp_path))
    mgr = FundamentalManager()

    assert mgr.db_dir == str(tmp_path)
    assert mgr.val_db == os.path.join(str(tmp_path), "myra_valuation.db")


def test_fetch_fundamentals_writes_to_the_real_sidecar(db_dir):
    """The end-to-end behaviour the incident broke: a successful fetch must
    return True and land rows in the real valuation sidecar."""
    mgr = FundamentalManager(db_dir=db_dir)
    mgr.set_fetcher(_FakeFetcher([_row()]))

    assert mgr.fetch_fundamentals("RELIANCE") is True

    conn = sqlite3.connect(mgr.val_db)
    fundamentals = conn.execute(
        "SELECT symbol, pe, roe, market_cap, sector FROM fundamentals"
    ).fetchall()
    n_quarterly = conn.execute("SELECT COUNT(*) FROM quarterly_results").fetchone()[0]
    conn.close()

    assert fundamentals == [("RELIANCE", 22.5, 17.0, 1.5e10, "Energy")]
    assert n_quarterly == 1


def test_fetch_fundamentals_sets_sync_marker(db_dir):
    mgr = FundamentalManager(db_dir=db_dir)
    conn = sqlite3.connect(mgr.meta_db)
    conn.execute("INSERT INTO symbols_master VALUES ('RELIANCE', '1900-01-01')")
    conn.commit()
    conn.close()

    mgr.set_fetcher(_FakeFetcher([_row()]))
    assert mgr.fetch_fundamentals("RELIANCE") is True

    conn = sqlite3.connect(mgr.meta_db)
    marker = conn.execute(
        "SELECT last_fundamental_update FROM symbols_master WHERE symbol = 'RELIANCE'"
    ).fetchone()[0]
    conn.close()
    assert marker != "1900-01-01"


def test_partial_payload_never_nulls_an_existing_metric(db_dir):
    """Invariant: a failed/partial fetch must not overwrite a valid metric.

    The UPSERT used a bare `EXCLUDED.pe` (and roe/eps/book_value), so a payload
    missing those keys would null out real values across the whole table.
    """
    mgr = FundamentalManager(db_dir=db_dir)
    conn = sqlite3.connect(mgr.val_db)
    conn.execute(
        "INSERT INTO fundamentals (symbol, pe, roe, book_value, sector) "
        "VALUES ('TESTCO', 25.0, 18.0, 55.0, 'Existing')"
    )
    conn.commit()
    conn.close()

    # Payload carries market_cap + eps but no pe / roe / book_value / sector.
    mgr.set_fetcher(
        _FakeFetcher(
            [
                _row(
                    pe=None,
                    stock_pe=None,
                    roe=None,
                    book_value=None,
                    sector=None,
                    market_cap=9.9e9,
                )
            ]
        )
    )
    assert mgr.fetch_fundamentals("TESTCO") is True

    conn = sqlite3.connect(mgr.val_db)
    pe, roe, bv, sector, mcap = conn.execute(
        "SELECT pe, roe, book_value, sector, market_cap FROM fundamentals "
        "WHERE symbol = 'TESTCO'"
    ).fetchone()
    conn.close()

    assert pe == 25.0, "valid pe was nulled by a partial payload"
    assert roe == 18.0
    assert bv == 55.0
    assert sector == "Existing"
    # A value that WAS supplied still wins.
    assert mcap == pytest.approx(9.9e9)


def test_missing_sidecar_schema_is_logged_not_swallowed(db_dir, caplog, tmp_path):
    """A write failure must be visible, not silently reported as a source
    failure -- that mislabelling is what hid this bug in the first place."""
    from myra_app import fundamental_manager as fm

    mgr = FundamentalManager(db_dir=db_dir)
    # Point at a DB that exists but has no schema at all.
    empty = tmp_path / "empty.db"
    sqlite3.connect(str(empty)).close()
    mgr.val_db = str(empty)
    mgr.set_fetcher(_FakeFetcher([_row()]))

    with caplog.at_level("ERROR"):
        assert mgr.fetch_fundamentals("RELIANCE") is False
    assert "failed to persist fundamentals" in caplog.text
    # The caller must be able to report the real cause, not blame the sources.
    assert "no such table" in mgr.last_error
    assert "multi-source" not in mgr.last_error


def test_source_exhaustion_is_reported_as_such(db_dir):
    """The mirror image: a genuine source failure must still say so."""
    mgr = FundamentalManager(db_dir=db_dir)

    class _Empty:
        def fetch_fundamentals(self, symbol):
            return (None, None)

    mgr.set_fetcher(_Empty())
    assert mgr.fetch_fundamentals("RELIANCE") is False
    assert mgr.last_error == "multi-source fallback exhausted"


def test_undated_google_rows_are_never_written(db_dir):
    """`_fetch_google` labels rows "Q-1".."Q-4". Consumers sort quarterly_results
    by `report_date DESC` lexicographically, where "Q-1" outranks a real
    "Sep 2025" row. Undated rows must be dropped, not written."""
    mgr = FundamentalManager(db_dir=db_dir)
    mgr.set_fetcher(
        _FakeFetcher(
            [
                {"report_date": "Q-1", "revenue": 1.0, "net_profit": 1.0},
                {"report_date": "Q-2", "revenue": 2.0, "net_profit": 2.0},
            ]
        )
    )
    assert mgr.fetch_fundamentals("RELIANCE") is False
    assert "undated" in mgr.last_error

    conn = sqlite3.connect(mgr.val_db)
    assert conn.execute("SELECT COUNT(*) FROM quarterly_results").fetchone()[0] == 0
    conn.close()


def test_dated_rows_survive_alongside_undated_ones(db_dir):
    """Mixed payloads keep the real rows and drop only the placeholders."""
    mgr = FundamentalManager(db_dir=db_dir)
    mgr.set_fetcher(_FakeFetcher([{"report_date": "Q-1", "revenue": 9.0}, _row()]))
    assert mgr.fetch_fundamentals("RELIANCE") is True

    conn = sqlite3.connect(mgr.val_db)
    dates = [
        r[0]
        for r in conn.execute(
            "SELECT report_date FROM quarterly_results ORDER BY report_date"
        ).fetchall()
    ]
    conn.close()
    assert dates == ["2026-06"]


# ── queue filtering: index/benchmark rows ────────────────────────────────────


def test_index_rows_are_skipped_but_real_tickers_survive():
    """Indices (NIFTY 100, INDIA VIX, G-SEC benchmarks) have no fundamentals page
    anywhere. Every index row contains whitespace and no NSE/BSE equity ticker
    does -- notably 'BSE' is a real listed company and must be kept."""
    from myra_app.utils.fundamentals_sync import partition_index_rows

    equities, indices = partition_index_rows(
        [
            "BSE",
            "NIFTY 100",
            "INDIA VIX",
            "NIFTY 11-15 YR G-SEC INDEX",
            "NIFTY EV & NEW AGE AUTOMOTIVE",
            "RELIANCE",
            "NAMOEWASTE_SME",
            "M&M",
        ]
    )

    assert equities == ["BSE", "RELIANCE", "NAMOEWASTE_SME", "M&M"]
    assert indices == [
        "NIFTY 100",
        "INDIA VIX",
        "NIFTY 11-15 YR G-SEC INDEX",
        "NIFTY EV & NEW AGE AUTOMOTIVE",
    ]


# ── unify_symbol: SME-exchange ticker normalisation ─────────────────────────


def test_unify_symbol_strips_sme_suffix():
    """MYRA stores SME tickers as '<TICKER>_SME'; the generic '_'->'&' rule
    turned 'NAMOEWASTE_SME' into 'NAMOEWASTE&SME', which no source has."""
    from myra_app.fetcher import DataFetcher

    unify = DataFetcher.unify_symbol
    assert unify(None, "NAMOEWASTE_SME") == "NAMOEWASTE"
    assert unify(None, "NEOCHEM-SME") == "NEOCHEM"
    assert unify(None, "MUKKA_SME") == "MUKKA"


def test_unify_symbol_preserves_existing_behaviour():
    from myra_app.fetcher import DataFetcher

    unify = DataFetcher.unify_symbol
    # Underscores still map to ampersands once the SME marker is gone.
    assert unify(None, "M_M") == "M&M"
    assert unify(None, "NSE:RELIANCE") == "RELIANCE"
    assert unify(None, "TCS.NS") == "TCS"
    assert unify(None, "  hcl  ") == "HCL"
    assert unify(None, "") == ""
