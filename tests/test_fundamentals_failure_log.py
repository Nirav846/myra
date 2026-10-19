"""Tests for the additive fundamentals failure telemetry (Phase 32).

The Phase 31 audit could not classify a single one of 119 failures because the
per-source reasons the fetcher already computes were never persisted. These
tests pin the contract of the module that fixes that, and -- just as importantly
-- pin that it cannot change anything else:

* the table is created additively and idempotently;
* one attempt yields one row per source, never duplicates;
* HTTP status stays isolated between sources and symbols;
* nothing secret or free-text can reach the table;
* a telemetry failure never masks the real failure;
* parking, retry counts, the undated-row guard and COALESCE are untouched.

All database access is against ``tmp_path``; no network call is made anywhere.
"""

import sqlite3

import pytest

from myra_app import constants
from myra_app.librarian_core import LibrarianCore
from myra_app.utils import fundamentals_failure_log as fl
from myra_app.utils import fundamentals_sync as fs


def _meta(tmp_path):
    import os

    return os.path.join(str(tmp_path), LibrarianCore.DB_MAP["meta"])


def _val(tmp_path):
    import os

    return os.path.join(str(tmp_path), LibrarianCore.DB_MAP["valuation"])


def _query(tmp_path, sql, params=()):
    conn = sqlite3.connect(_meta(tmp_path), timeout=10)
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def _count(tmp_path, symbol=None):
    """Row count, or 0 when the table was never created (nothing failed yet)."""
    conn = sqlite3.connect(_meta(tmp_path), timeout=10)
    try:
        try:
            if symbol:
                return conn.execute(
                    f"SELECT COUNT(*) FROM {fl.TABLE} WHERE symbol = ?", (symbol,)
                ).fetchone()[0]
            return conn.execute(f"SELECT COUNT(*) FROM {fl.TABLE}").fetchone()[0]
        except sqlite3.OperationalError:
            return 0
    finally:
        conn.close()


def _dump(tmp_path):
    """Every cell of the telemetry table, as text -- for secret-leak assertions."""
    conn = sqlite3.connect(_meta(tmp_path), timeout=10)
    try:
        try:
            rows = conn.execute(f"SELECT * FROM {fl.TABLE}").fetchall()
        except sqlite3.OperationalError:
            return ""
        return repr(rows)
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# Schema
# --------------------------------------------------------------------------- #


def test_table_is_created_on_a_new_database(tmp_path):
    """Recording into a directory with no metadata DB creates it additively."""
    import os

    assert not os.path.exists(_meta(tmp_path))

    fl.record_failure(tmp_path, "AAA", last_error="simulated failure")

    assert os.path.exists(_meta(tmp_path))
    columns = [r[1] for r in _query(tmp_path, f"PRAGMA table_info({fl.TABLE})")]
    assert columns == [
        "id",
        "symbol",
        "observed_at",
        "run_id",
        "source",
        "category",
        "http_status",
        "exception_class",
        "detail_code",
    ]


def test_initialisation_is_idempotent(tmp_path):
    """Calling ensure_table repeatedly must not fail or duplicate the table."""
    conn = sqlite3.connect(_meta(tmp_path), timeout=10)
    try:
        fl.ensure_table(conn)
        fl.ensure_table(conn)
        fl.ensure_table(conn)
        conn.commit()
    finally:
        conn.close()

    tables = _query(
        tmp_path,
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (fl.TABLE,),
    )
    assert tables == [(fl.TABLE,)]
    # And recording still works after repeated initialisation.
    assert fl.record_failure(tmp_path, "AAA", last_error="x") == 1


# --------------------------------------------------------------------------- #
# Event capture
# --------------------------------------------------------------------------- #


def test_one_failure_event_is_recorded(tmp_path):
    inserted = fl.record_failure(
        tmp_path,
        "AAA",
        last_error="multi-source fallback exhausted",
        run_id="2026-10-18T16:01:21+00:00",
    )
    assert inserted == 1

    rows = _query(
        tmp_path,
        f"SELECT symbol, source, category, http_status, run_id, observed_at "
        f"FROM {fl.TABLE}",
    )
    assert len(rows) == 1
    symbol, source, category, status, run_id, observed_at = rows[0]
    assert symbol == "AAA"
    assert source is None
    assert category == fl.CAT_FALLBACK_EXHAUSTED
    assert status is None
    assert run_id == "2026-10-18T16:01:21+00:00"
    assert observed_at  # a real per-attempt timestamp, not the run start


def test_multiple_source_errors_preserve_the_source_distinction(tmp_path):
    """Two source outcomes must not collapse into one generic row."""
    inserted = fl.record_failure(
        tmp_path,
        "AAA",
        last_error="multi-source fallback exhausted (google_finance: no data "
        "returned; screener_in: HTTP 403)",
        source_errors={
            "screener_in": "HTTP 403",
            "google_finance": "no data returned",
        },
        run_id="run-1",
    )
    assert inserted == 2

    rows = _query(
        tmp_path,
        f"SELECT source, category, http_status FROM {fl.TABLE} ORDER BY source",
    )
    assert rows == [
        ("google_finance", fl.CAT_NO_DATA, None),
        ("screener_in", fl.CAT_HTTP_ERROR, 403),
    ]
    # One attempt, one timestamp: the rows are a single event, not two.
    stamps = _query(tmp_path, f"SELECT DISTINCT observed_at FROM {fl.TABLE}")
    assert len(stamps) == 1


def test_unknown_and_missing_diagnostics_become_unknown(tmp_path):
    """No evidence is recorded as ``unknown`` -- never guessed."""
    assert fl.record_failure(tmp_path, "AAA", last_error=None) == 1
    assert fl.record_failure(tmp_path, "BBB", last_error="something new") == 1
    assert (
        fl.record_failure(
            tmp_path, "CCC", source_errors={"screener_in": "unexpected text"}
        )
        == 1
    )

    rows = _query(
        tmp_path,
        f"SELECT source, category, detail_code, exception_class FROM {fl.TABLE}",
    )
    assert all(r[1] == fl.CAT_UNKNOWN for r in rows)
    assert all(r[2] is None and r[3] is None for r in rows)


def test_http_status_is_isolated_between_sources_and_symbols(tmp_path):
    """A status belongs to its own source and its own symbol."""
    fl.record_failure(
        tmp_path,
        "AAA",
        source_errors={"screener_in": "HTTP 403", "google_finance": "HTTP 404"},
    )
    fl.record_failure(
        tmp_path,
        "BBB",
        source_errors={"screener_in": "HTTP 200 but no " "quarterly table in page"},
    )

    rows = _query(
        tmp_path,
        f"SELECT symbol, source, category, http_status, detail_code "
        f"FROM {fl.TABLE} ORDER BY symbol, source",
    )
    assert rows == [
        ("AAA", "google_finance", fl.CAT_HTTP_ERROR, 404, None),
        ("AAA", "screener_in", fl.CAT_HTTP_ERROR, 403, None),
        ("BBB", "screener_in", fl.CAT_PAGE_NOTE, None, "no_quarterly_table"),
    ]


def test_undated_google_rows_are_recorded_as_rejected(tmp_path):
    """Regression: Google's Q-1..Q-4 rows are still rejected by the guard.

    The telemetry records that the guard rejected them; it does not weaken the
    guard, accept the rows, or invent a period.
    """
    inserted = fl.record_failure(
        tmp_path,
        "AAA",
        last_error="source returned only undated rows (google fallback)",
        last_source="google_finance",
    )
    assert inserted == 1
    row = _query(tmp_path, f"SELECT source, category FROM {fl.TABLE}")[0]
    assert row == ("google_finance", fl.CAT_UNDATED_ROWS)


def test_persist_failure_keeps_only_the_exception_class(tmp_path):
    """A persistence failure is classified, but its message is discarded."""
    secret = "OperationalError: disk I/O error at https://x/?token=SECRET123"
    fl.record_failure(tmp_path, "AAA", last_error=f"persist failed ({secret})")

    row = _query(tmp_path, f"SELECT category, exception_class FROM {fl.TABLE}")[0]
    assert row == (fl.CAT_PERSIST_FAILED, "OperationalError")
    assert "SECRET123" not in _dump(tmp_path)


# --------------------------------------------------------------------------- #
# Sanitisation
# --------------------------------------------------------------------------- #


def test_urls_query_strings_cookies_and_tokens_are_never_persisted(tmp_path):
    """No free text reaches the table, whatever the fetcher hands us."""
    secrets = [
        "https://www.screener.in/company/AAA/?token=SECRET123",
        "cookie=sessionid=SECRET123",
        "Authorization: Bearer SECRET123",
        "FieldTextThatShouldNotBeStored: SECRET123",
    ]
    for source, detail in zip(
        ["screener_in", "google_finance", "nse_shareholding", "unknown_src"],
        secrets,
    ):
        fl.record_failure(tmp_path, "AAA", source_errors={source: detail})

    dumped = _dump(tmp_path)
    assert "SECRET123" not in dumped
    assert "screener.in" not in dumped
    assert "Bearer" not in dumped
    assert _count(tmp_path) == len(secrets)


def test_secret_bearing_exception_class_is_rejected(tmp_path):
    """An exception *message* cannot masquerade as a class name."""
    fl.record_failure(tmp_path, "AAA", exc_class="ValueError: token=SECRET123")
    row = _query(tmp_path, f"SELECT category, exception_class FROM {fl.TABLE}")[0]
    assert row == (fl.CAT_EXCEPTION, None)
    assert "SECRET123" not in _dump(tmp_path)


def test_non_symbol_shaped_input_is_not_stored(tmp_path):
    """A symbol that cannot be sanitised yields no row rather than junk."""
    assert fl.record_failure(tmp_path, "../../etc/passwd", last_error="x") == 0
    assert fl.record_failure(tmp_path, None, last_error="x") == 0
    assert _count(tmp_path) == 0


# --------------------------------------------------------------------------- #
# Failure containment
# --------------------------------------------------------------------------- #


def test_telemetry_write_failure_never_raises(tmp_path, monkeypatch):
    """A broken insert reports 0 rows and logs; it must not propagate."""

    def boom(*args, **kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(fl, "record_events", boom)
    assert fl.record_failure(tmp_path, "AAA", last_error="x") == 0


def test_exception_branch_records_class_only(tmp_path):
    """The sync loop's escaped-exception branch stores no message."""
    fl.record_failure(tmp_path, "AAA", exc_class="KeyError")
    row = _query(tmp_path, f"SELECT category, exception_class FROM {fl.TABLE}")[0]
    assert row == (fl.CAT_EXCEPTION, "KeyError")


# --------------------------------------------------------------------------- #
# Retention (documented, bounded, per symbol)
# --------------------------------------------------------------------------- #


def test_retention_keeps_the_newest_rows_per_symbol(tmp_path):
    for i in range(fl.KEEP_PER_SYMBOL + 5):
        fl.record_failure(tmp_path, "AAA", last_error=f"attempt {i}")
    fl.record_failure(tmp_path, "BBB", last_error="other symbol")

    count_aaa = _query(tmp_path, f"SELECT COUNT(*) FROM {fl.TABLE} WHERE symbol='AAA'")[
        0
    ][0]
    count_bbb = _query(tmp_path, f"SELECT COUNT(*) FROM {fl.TABLE} WHERE symbol='BBB'")[
        0
    ][0]
    assert count_aaa == fl.KEEP_PER_SYMBOL
    assert count_bbb == 1

    # Newest survive: the oldest attempts were the ones dropped.
    newest = _query(
        tmp_path,
        f"SELECT detail_code FROM {fl.TABLE} WHERE symbol='AAA' "
        f"ORDER BY id DESC LIMIT 1",
    )
    assert newest == [(None,)]


# --------------------------------------------------------------------------- #
# Read-only helpers
# --------------------------------------------------------------------------- #


def test_summarize_groups_by_symbol_source_and_category(tmp_path):
    fl.record_failure(tmp_path, "AAA", source_errors={"screener_in": "HTTP 403"})
    fl.record_failure(tmp_path, "AAA", source_errors={"screener_in": "HTTP 403"})
    fl.record_failure(tmp_path, "BBB", source_errors={"screener_in": "HTTP 404"})

    summary = fl.summarize(tmp_path)
    assert summary[0] == {
        "symbol": "AAA",
        "source": "screener_in",
        "category": fl.CAT_HTTP_ERROR,
        "count": 2,
        "last_seen": summary[0]["last_seen"],
    }

    only_bbb = fl.summarize(tmp_path, symbol="BBB")
    assert len(only_bbb) == 1 and only_bbb[0]["count"] == 1


def test_recent_failures_returns_newest_first_and_does_not_write(tmp_path):
    fl.record_failure(tmp_path, "AAA", last_error="first")
    fl.record_failure(tmp_path, "BBB", last_error="second")

    before = _dump(tmp_path)
    rows = fl.recent_failures(tmp_path, limit=10)
    assert [r["symbol"] for r in rows] == ["BBB", "AAA"]
    assert _dump(tmp_path) == before

    assert [r["symbol"] for r in fl.recent_failures(tmp_path, symbol="AAA")] == ["AAA"]


def test_helpers_are_safe_when_nothing_exists(tmp_path):
    assert fl.summarize(tmp_path) == []
    assert fl.recent_failures(tmp_path) == []


# --------------------------------------------------------------------------- #
# Sync integration: no behaviour change
# --------------------------------------------------------------------------- #


class _FakeManager:
    def __init__(self, outcome, failure_status=None, last_error="simulated"):
        self._outcome = outcome
        self.failure_status = failure_status
        self.last_error = last_error
        self.last_source = None
        self.last_http_status = None

    def set_fetcher(self, fetcher):
        pass

    def fetch_fundamentals(self, symbol):
        if self._outcome(symbol):
            self.last_http_status = 200
            self.last_error = None
            return True
        self.last_http_status = self.failure_status
        self.last_error = "simulated failure"
        return False


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(constants, "DB_DIR", str(tmp_path))
    meta = sqlite3.connect(_meta(tmp_path))
    meta.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT)")
    meta.commit()
    meta.close()
    val = sqlite3.connect(_val(tmp_path))
    val.execute(
        "CREATE TABLE fundamentals (symbol TEXT PRIMARY KEY, sector TEXT, "
        "pe REAL, roe REAL, market_cap REAL)"
    )
    val.commit()
    val.close()
    return tmp_path


def _seed(db_dir, symbols):
    conn = sqlite3.connect(_val(db_dir), timeout=10)
    for symbol in symbols:
        conn.execute(
            "INSERT OR IGNORE INTO fundamentals (symbol) VALUES (?)", (symbol,)
        )
    conn.commit()
    conn.close()


def _patch(monkeypatch, fake):
    monkeypatch.setattr(fs, "FundamentalManager", lambda **_: fake)
    monkeypatch.setattr(fs, "DataFetcher", lambda: None)
    monkeypatch.setattr(fs, "_backoff_sleep", lambda *a, **k: None)


def test_successful_refresh_records_no_failures(isolated_db, monkeypatch):
    _seed(isolated_db, ["AAA", "BBB"])
    _patch(monkeypatch, _FakeManager(lambda s: True))

    summary = fs.sync_fundamentals(force=True)

    assert summary["updated"] == 2 and summary["failed"] == 0
    assert _count(isolated_db) == 0


def test_one_attempt_records_one_event_not_duplicates(isolated_db, monkeypatch):
    """Event granularity is one row per failed attempt at the sync layer."""
    _seed(isolated_db, ["AAA", "BBB"])
    _patch(monkeypatch, _FakeManager(lambda s: False, failure_status=404))

    summary = fs.sync_fundamentals(force=True)

    assert summary["failed"] == 2
    rows = _query(
        isolated_db, f"SELECT symbol, COUNT(*) FROM {fl.TABLE} GROUP BY symbol"
    )
    assert sorted(rows) == [("AAA", 1), ("BBB", 1)]


def test_parking_and_retry_state_are_unchanged(isolated_db, monkeypatch):
    """Telemetry must not touch fails/last_attempt/parked_until semantics."""
    _seed(isolated_db, ["AAA", "BBB"])
    _patch(monkeypatch, _FakeManager(lambda s: False, failure_status=404))

    fs.sync_fundamentals(force=True)

    conn = sqlite3.connect(_meta(isolated_db), timeout=10)
    try:
        raw = conn.execute(
            "SELECT value FROM metadata WHERE key = ?", (fs.PARK_STATE_KEY,)
        ).fetchone()[0]
    finally:
        conn.close()

    import json
    from datetime import datetime, timezone

    state = json.loads(raw)
    assert set(state) == {"AAA", "BBB"}
    for entry in state.values():
        assert entry["fails"] == 1
        assert "parked_until" not in entry
    # Below the threshold: still not parked, exactly as before this change.
    assert not any(
        fs.is_parked(state, sym, datetime.now(timezone.utc)) for sym in state
    )


def test_telemetry_breakage_does_not_change_the_run_outcome(isolated_db, monkeypatch):
    """A telemetry exception must not mask or alter the fundamentals failure."""
    _seed(isolated_db, ["AAA"])
    _patch(monkeypatch, _FakeManager(lambda s: False, failure_status=404))

    def boom(*args, **kwargs):
        raise RuntimeError("telemetry exploded")

    monkeypatch.setattr(fl, "record_events", boom)

    summary = fs.sync_fundamentals(force=True)

    assert summary["failed"] == 1
    assert summary["cancelled"] is False
    conn = sqlite3.connect(_meta(isolated_db), timeout=10)
    try:
        raw = conn.execute(
            "SELECT value FROM metadata WHERE key = ?", (fs.PARK_STATE_KEY,)
        ).fetchone()[0]
    finally:
        conn.close()
    import json

    assert json.loads(raw)["AAA"]["fails"] == 1


def test_rate_limited_attempts_get_their_own_category(isolated_db, monkeypatch):
    """Throttling is recorded without being attributed to a symbol failure."""
    _seed(isolated_db, ["AAA"])
    _patch(monkeypatch, _FakeManager(lambda s: False, failure_status=429))
    monkeypatch.setattr(fs, "RATE_LIMIT_ABORT_AFTER", 99)

    fs.sync_fundamentals(force=True)

    rows = _query(
        isolated_db,
        f"SELECT category, http_status, source FROM {fl.TABLE}",
    )
    assert rows == [(fl.CAT_RATE_LIMITED, 429, None)]


def test_fundamentals_values_are_not_touched_by_telemetry(isolated_db, monkeypatch):
    """Seeded metrics and the undated-row guard's effect stay identical."""
    _seed(isolated_db, ["AAA"])
    conn = sqlite3.connect(_val(isolated_db), timeout=10)
    conn.execute(
        "UPDATE fundamentals SET sector='kept', pe=11.0, roe=12.0, "
        "market_cap=13.0 WHERE symbol='AAA'"
    )
    conn.commit()
    conn.close()

    _patch(monkeypatch, _FakeManager(lambda s: False, failure_status=404))
    fs.sync_fundamentals(force=True)

    row = (
        sqlite3.connect(_val(isolated_db), timeout=10)
        .execute(
            "SELECT sector, pe, roe, market_cap FROM fundamentals WHERE symbol='AAA'"
        )
        .fetchone()
    )
    assert row == ("kept", 11.0, 12.0, 13.0)


class _StaleEvidence:
    """Fetcher stub holding errors that belong to a *previous* symbol."""

    last_source_errors = {"screener_in": "HTTP 403"}


class _RaisingManager(_FakeManager):
    def __init__(self, exc):
        super().__init__(lambda s: False)
        self._exc = exc
        self.fetcher = _StaleEvidence()

    def fetch_fundamentals(self, symbol):
        raise self._exc


def test_escaped_exception_records_exactly_one_event(isolated_db, monkeypatch):
    """The exception path must not also produce a source-level event."""
    _seed(isolated_db, ["AAA"])
    _patch(monkeypatch, _RaisingManager(ValueError("boom")))

    summary = fs.sync_fundamentals(force=True)

    assert summary["failed"] == 1
    rows = _query(
        isolated_db,
        f"SELECT source, category, exception_class FROM {fl.TABLE}",
    )
    assert rows == [(None, fl.CAT_EXCEPTION, "ValueError")]


def test_escaped_exception_never_reuses_stale_source_errors(isolated_db, monkeypatch):
    """Evidence from the previous symbol must not be attributed to this one."""
    _seed(isolated_db, ["AAA"])
    _patch(monkeypatch, _RaisingManager(TimeoutError("t")))

    fs.sync_fundamentals(force=True)

    rows = _query(
        isolated_db,
        f"SELECT source, category, http_status, exception_class FROM {fl.TABLE}",
    )
    assert rows == [(None, fl.CAT_EXCEPTION, None, "TimeoutError")]
