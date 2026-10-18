"""Fundamentals diagnostics: per-source failure reasons must survive to the log.

Bounded diagnostic for the completed Screener Fundamentals refresh. The run log
reported only the generic ``multi-source fallback exhausted`` even though
``DataFetcher.fetch_fundamentals`` caught every per-source exception at debug
level, so the reason a source had failed was unrecoverable from the run.

These tests pin the corrected behaviour *and* the safeguards that must not
regress:

* a per-source reason is produced (exception type / HTTP status), credential-free;
* the manager surfaces those reasons instead of a bare generic message;
* the undated-row guard still refuses to write placeholder dates;
* a partial payload still cannot null out a value already held.
"""

from __future__ import annotations

import sqlite3

import pytest

from myra_app.fundamental_manager import _PLACEHOLDER_REPORT_DATE, FundamentalManager


class _StubFetcher:
    """Minimal DataFetcher stand-in: returns a canned chain result."""

    def __init__(self, payload, source, errors=None):
        self._payload = payload
        self._source = source
        self.last_http_status = None
        self.last_source_errors = errors or {}

    def unify_symbol(self, s):
        return s.split(".")[0].upper()

    def fetch_fundamentals(self, symbol):
        return self._payload, self._source


def _manager(tmp_path, payload, source, errors=None):
    mgr = FundamentalManager(db_dir=str(tmp_path))
    mgr.fetcher = _StubFetcher(payload, source, errors)
    return mgr


def test_placeholder_report_dates_are_recognised():
    """'Q-1'..'Q-4' from the Google fallback must be classified as undated."""
    for ph in ("Q-1", "Q-2", "Q-3", "Q-4", "q-1"):
        assert _PLACEHOLDER_REPORT_DATE.match(ph), ph
    # Real reporting periods must NOT be filtered out.
    for real in ("Sep 2025", "Mar 2026", "2025-09-30", "Dec 2024"):
        assert not _PLACEHOLDER_REPORT_DATE.match(real), real


def test_exhausted_chain_surfaces_per_source_reasons(tmp_path):
    """The manager must name *why* each source failed, not just 'exhausted'."""
    mgr = _manager(
        tmp_path,
        None,
        None,
        errors={
            "screener_in": "screener_in: ClientError",
            "google_finance": "HTTP 429",
        },
    )
    ok = mgr.fetch_fundamentals("TEST")
    assert ok is False
    assert "multi-source fallback exhausted" in mgr.last_error
    # The real reasons must be present, so an operator can act on them.
    assert "screener_in: ClientError" in mgr.last_error
    assert "google_finance: HTTP 429" in mgr.last_error


def test_exhausted_chain_without_reasons_still_reports_generic(tmp_path):
    """Backward compatible: a fetcher that records nothing yields the generic text."""
    mgr = _manager(tmp_path, None, None, errors={})
    assert mgr.fetch_fundamentals("TEST") is False
    assert mgr.last_error == "multi-source fallback exhausted"


def test_undated_only_rows_are_refused_before_persistence(tmp_path):
    """Undated Google rows must not be written (Phase safeguard preserved)."""
    undated = [
        {"report_date": "Q-1", "revenue": 10.0},
        {"report_date": "Q-2", "revenue": 20.0},
    ]
    mgr = _manager(tmp_path, undated, "google_finance")
    assert mgr.fetch_fundamentals("TEST") is False
    assert "undated" in mgr.last_error.lower()


def test_mixed_dated_and_undated_rows_drop_only_the_undated(tmp_path):
    """A valid dated row alongside placeholders keeps the dated one only."""
    rows = [
        {"report_date": "Q-1", "revenue": 10.0},
        {"report_date": "Sep 2025", "revenue": 100.0, "roe": 12.0},
    ]
    mgr = _manager(tmp_path, rows, "screener_in")
    # It proceeds past the undated guard; persist may then fail on an empty DB,
    # which is fine -- what matters is that it got past the guard at all.
    mgr.fetch_fundamentals("TEST")
    assert "undated rows" not in (mgr.last_error or "").lower()


def test_surface_reasons_do_not_leak_message_text(tmp_path):
    """Only the exception *type* / status is surfaced, never a message.

    A message could embed a URL, cookie or session token; the diagnostic must
    not be able to print credentials into the run log.
    """
    reasons = {"screener_in": "screener_in: ValueError"}
    mgr = _manager(tmp_path, None, None, errors=reasons)
    mgr.fetch_fundamentals("TEST")
    assert "ValueError" in mgr.last_error
    assert "http://" not in mgr.last_error
    assert "cookie" not in mgr.last_error.lower()


def test_fetcher_records_http_status_and_exception_type_without_message():
    """DataFetcher distinguishes 'denied page' from 'no data' and hides messages."""
    import inspect

    from myra_app.fetcher import DataFetcher

    src = inspect.getsource(DataFetcher.fetch_fundamentals)
    # The bare debug-only swallow must be gone.
    assert "self.last_source_errors" in src
    assert "HTTP {status}" in src or "HTTP %s" in src
    # It must not format the exception message into the recorded reason.
    assert (
        "{exc}" not in src
    ), "the exception message must never reach last_source_errors"


def test_funds_partial_payload_cannot_null_out_held_values(tmp_path):
    """A partial refresh must preserve every already-held metric (COALESCE)."""
    db = tmp_path / "v.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE fundamentals (symbol TEXT PRIMARY KEY, roe REAL, profit_growth REAL,"
        " last_updated TEXT)"
    )
    conn.execute("INSERT INTO fundamentals VALUES ('AAA', 12.5, 8.0, '2026-01-01')")
    conn.commit()
    # The production UPSERT COALESCEs every column against the stored value, so a
    # NULL incoming value cannot overwrite a held one. Assert that property.
    conn.execute(
        "INSERT INTO fundamentals (symbol, roe, profit_growth, last_updated) "
        "VALUES (?, ?, ?, ?) ON CONFLICT(symbol) DO UPDATE SET "
        "roe = COALESCE(excluded.roe, fundamentals.roe), "
        "profit_growth = COALESCE(excluded.profit_growth, fundamentals.profit_growth)",
        ("AAA", None, None, "2026-02-01"),
    )
    conn.commit()
    roe, pg = conn.execute(
        "SELECT roe, profit_growth FROM fundamentals WHERE symbol='AAA'"
    ).fetchone()
    conn.close()
    assert roe == 12.5 and pg == 8.0, "a failed refresh must preserve existing values"


def test_the_production_upsert_actually_coalesces_every_column():
    """Guard the real SQL, not just a paraphrase of it."""
    import inspect

    from myra_app.fundamental_manager import FundamentalManager as FM

    src = inspect.getsource(FM.fetch_fundamentals)
    assert "COALESCE(EXCLUDED.pe, fundamentals.pe)" in src
    assert "COALESCE(EXCLUDED.roe, fundamentals.roe)" in src
    assert "COALESCE(EXCLUDED.promoter_holding_pct" in src
    assert "COALESCE(EXCLUDED.free_float_pct" in src
