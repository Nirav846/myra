"""Screener.in enricher: bounded concurrency, retries, rate limiting, safe writes.

No test here touches screener.in or any real database. Network access is faked
by monkeypatching the module's request path, and every DB is a temporary
fixture-DB copy obtained through ``fixture_db_dir``.

Locks in the Phase 8 behaviour:
  * bounded worker count (never one thread per symbol)
  * a *global* request pacer, so N workers do not mean N times the request rate
  * duplicate concurrent runs are refused, not doubled
  * a partial fetch never NULLs a previously valid metric
  * per-symbol commit, so progress survives an interruption
  * truthful summary + sync_log status for a degraded run
"""

import os
import sqlite3
import threading
import time

import pytest
import requests

import myra_app.constants as constants
from myra_app.db.enrichers import screener_enricher as se

pytestmark = pytest.mark.usefixtures("fixture_db_dir")


@pytest.fixture
def val_db(fixture_db_dir):
    """The isolated valuation sidecar with the two tables the enricher uses.

    The fixture DB directory is session-scoped, so this resets the tables these
    tests write to instead of assuming a pristine database.
    """
    from myra_app.librarian_core import LibrarianCore

    path = os.path.join(fixture_db_dir, LibrarianCore.DB_MAP["valuation"])
    conn = sqlite3.connect(path, timeout=30)
    try:
        conn.execute("PRAGMA busy_timeout = 30000")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS screener_fundamentals ("
            "symbol TEXT PRIMARY KEY, pbv REAL, roce REAL, last_updated TEXT)"
        )
        conn.execute("DELETE FROM screener_fundamentals")
        conn.commit()
    finally:
        conn.close()
    return path


def _add_fundamentals(val_db, symbols, market_cap=1_000_000.0):
    conn = sqlite3.connect(val_db, timeout=30)
    try:
        for sym in symbols:
            conn.execute(
                "INSERT OR IGNORE INTO fundamentals (symbol, market_cap) VALUES (?, ?)",
                (sym, market_cap),
            )
        conn.commit()
    finally:
        conn.close()


def _only(monkeypatch, symbols):
    """Make the enricher consider exactly ``symbols``.

    The shared fixture ``fundamentals`` table has its own rows with a non-null
    market_cap, so without this the run would also process fixture symbols and a
    small ``limit`` could slice away the symbol under test.
    """
    monkeypatch.setattr(se, "_select_symbols", lambda: list(symbols), raising=False)


def _screener_row(val_db, symbol):
    conn = sqlite3.connect(f"file:{val_db}?mode=ro", uri=True)
    try:
        return conn.execute(
            "SELECT pbv, roce FROM screener_fundamentals WHERE symbol = ?",
            (symbol,),
        ).fetchone()
    finally:
        conn.close()


class _FakeResponse:
    def __init__(self, status_code=200, payload=None, text="", headers=None):
        self.status_code = status_code
        self._payload = payload
        self.text = text
        self.headers = headers or {}

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


def _chart_payload(value):
    return {"datasets": [{"values": [[0, value]]}]}


# ---------------------------------------------------------------------------
# 1. Bounded concurrency + global pacing
# ---------------------------------------------------------------------------


def test_workers_are_bounded_and_concurrency_is_observed(val_db, monkeypatch, tmp_path):
    """Concurrency must be real but capped, and must not exceed the worker count."""
    symbols = [f"SYM{i:02d}" for i in range(12)]
    _add_fundamentals(val_db, symbols)

    active = {"now": 0, "max": 0}
    lock = threading.Lock()

    def fake_fetch(symbol, pacer):
        with lock:
            active["now"] += 1
            active["max"] = max(active["max"], active["now"])
        time.sleep(0.05)  # simulate network latency
        try:
            with lock:
                active["now"] -= 1
            return {"pbv": 1.0, "roce": 2.0}
        finally:
            pass

    monkeypatch.setattr(se, "_fetch_symbol_data", fake_fetch)
    monkeypatch.setattr(se, "REQUEST_INTERVAL_SECONDS", 0.0)
    monkeypatch.setattr(se, "_record_screener_status", lambda _s: None)

    summary = se.enrich_screener_fundamentals(
        force=True, max_workers=3, limit=len(symbols)
    )

    assert summary["updated"] == 12, summary
    # Never more than the configured worker count in flight.
    assert active["max"] <= 3, f"observed {active['max']} concurrent workers"
    # ... and it really did overlap (would be 1 if strictly sequential).
    assert active["max"] > 1, "no concurrency observed; the win would be lost"

    # The summary counter is not evidence on its own: assert the rows are
    # actually in the database, so a write that silently matches nothing (e.g. an
    # UPDATE against a sparse table) cannot pass this test.
    conn = sqlite3.connect(f"file:{val_db}?mode=ro", uri=True)
    try:
        stored = conn.execute("SELECT COUNT(*) FROM screener_fundamentals").fetchone()[
            0
        ]
    finally:
        conn.close()
    assert stored == 12, f"only {stored}/12 symbols were actually stored"


def test_worker_count_is_clamped_to_a_safe_range(val_db, monkeypatch):
    """An absurd worker count must be clamped, not honoured."""
    symbols = [f"S{i}" for i in range(3)]
    _add_fundamentals(val_db, symbols)
    monkeypatch.setattr(
        se, "_fetch_symbol_data", lambda s, p: {"pbv": 1.0, "roce": 1.0}
    )
    monkeypatch.setattr(se, "REQUEST_INTERVAL_SECONDS", 0.0)
    monkeypatch.setattr(se, "_record_screener_status", lambda _s: None)

    seen = {}
    original = se.ThreadPoolExecutor

    def spy(max_workers=None, **kwargs):
        seen["max_workers"] = max_workers
        return original(max_workers=max_workers, **kwargs)

    monkeypatch.setattr(se, "ThreadPoolExecutor", spy)
    se.enrich_screener_fundamentals(force=True, max_workers=999, limit=3)
    assert seen["max_workers"] <= 16, seen


def test_request_pacer_serialises_across_threads():
    """The pacer must space requests globally, not per thread."""
    pacer = se._RequestPacer(0.05)
    starts = []
    lock = threading.Lock()

    def worker():
        for _ in range(3):
            pacer.wait()
            with lock:
                starts.append(time.monotonic())

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    starts.sort()
    # 12 requests at >=0.05s spacing; allow jitter but require the aggregate
    # rate to be bounded rather than 4 threads bursting at once.
    total_span = starts[-1] - starts[0]
    assert total_span >= 0.05 * 10, f"pacer not enforced globally: span={total_span}"


def test_pacer_penalise_pushes_gate_out():
    pacer = se._RequestPacer(0.0)
    pacer.wait()
    pacer.penalise(0.5)
    t0 = time.monotonic()
    pacer.wait()
    assert time.monotonic() - t0 >= 0.4, "429 cool-down was not honoured"


# ---------------------------------------------------------------------------
# 2. Duplicate-run prevention
# ---------------------------------------------------------------------------


def test_second_concurrent_run_is_refused(val_db, monkeypatch):
    """An overlapping run must refuse, not double-fetch."""
    _add_fundamentals(val_db, ["AAA"])
    monkeypatch.setattr(se, "REQUEST_INTERVAL_SECONDS", 0.0)
    monkeypatch.setattr(se, "_record_screener_status", lambda _s: None)

    entered = threading.Event()
    release = threading.Event()

    def slow_fetch(symbol, pacer):
        entered.set()
        release.wait(timeout=5)
        return {"pbv": 1.0, "roce": 1.0}

    monkeypatch.setattr(se, "_fetch_symbol_data", slow_fetch)

    result = {}

    def run():
        result["first"] = se.enrich_screener_fundamentals(force=True, limit=1)

    t = threading.Thread(target=run)
    t.start()
    assert entered.wait(timeout=5), "first run never started"

    second = se.enrich_screener_fundamentals(force=True, limit=1)
    release.set()
    t.join(timeout=5)

    assert second["reason"] == "already_running", second
    assert result["first"]["updated"] == 1


# ---------------------------------------------------------------------------
# 3. Never clobber a valid metric with a failed fetch (WS3)
# ---------------------------------------------------------------------------


def test_new_symbol_is_inserted_not_only_updated(val_db, monkeypatch):
    """Regression: screener_fundamentals is sparse, so the write must INSERT.

    The original statement was INSERT OR REPLACE; a naive "fix" to a plain
    fill-only UPDATE would match no row for a symbol seen for the first time and
    silently store nothing.
    """
    _only(monkeypatch, ["NEWSYM"])
    monkeypatch.setattr(se, "REQUEST_INTERVAL_SECONDS", 0.0)
    monkeypatch.setattr(se, "_record_screener_status", lambda _s: None)
    monkeypatch.setattr(
        se, "_fetch_symbol_data", lambda s, p: {"pbv": 1.5, "roce": 2.5}
    )

    se.enrich_screener_fundamentals(force=True, limit=1)

    assert _screener_row(val_db, "NEWSYM") == (1.5, 2.5), "new symbol was not inserted"


def test_partial_fetch_does_not_null_an_existing_metric(val_db, monkeypatch):
    """PBV resolves, ROCE does not -> the stored ROCE must survive."""
    _add_fundamentals(val_db, ["AAA"])
    _only(monkeypatch, ["AAA"])
    conn = sqlite3.connect(val_db, timeout=30)
    conn.execute(
        "INSERT INTO screener_fundamentals (symbol, pbv, roce, last_updated) "
        "VALUES ('AAA', 9.9, 7.7, '2020-01-01T00:00:00')"
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(se, "REQUEST_INTERVAL_SECONDS", 0.0)
    monkeypatch.setattr(se, "_record_screener_status", lambda _s: None)
    # ROCE missing from the fetch result entirely.
    monkeypatch.setattr(se, "_fetch_symbol_data", lambda s, p: {"pbv": 3.3})

    se.enrich_screener_fundamentals(force=True, limit=1)

    pbv, roce = _screener_row(val_db, "AAA")
    assert pbv == 3.3, "resolved metric was not written"
    assert roce == 7.7, "a failed metric was clobbered to NULL"


def test_empty_fetch_leaves_row_untouched(val_db, monkeypatch):
    _add_fundamentals(val_db, ["AAA"])
    conn = sqlite3.connect(val_db, timeout=30)
    conn.execute(
        "INSERT INTO screener_fundamentals (symbol, pbv, roce, last_updated) "
        "VALUES ('AAA', 9.9, 7.7, '2020-01-01T00:00:00')"
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(se, "REQUEST_INTERVAL_SECONDS", 0.0)
    monkeypatch.setattr(se, "_record_screener_status", lambda _s: None)
    monkeypatch.setattr(se, "_fetch_symbol_data", lambda s, p: {})

    summary = se.enrich_screener_fundamentals(force=True, limit=1)
    assert summary["updated"] == 0
    assert summary["failed"] == 1
    assert _screener_row(val_db, "AAA") == (9.9, 7.7)


# ---------------------------------------------------------------------------
# 4. Per-symbol commit => progress survives interruption
# ---------------------------------------------------------------------------


def test_progress_is_committed_per_symbol(val_db, monkeypatch):
    """A mid-run abort must not discard the symbols already written.

    Each successful symbol is committed as it completes (not batched to the
    end), so the ones that finished before the abort are durable.
    """
    symbols = [f"S{i:02d}" for i in range(10)]
    _add_fundamentals(val_db, symbols)
    _only(monkeypatch, symbols)
    monkeypatch.setattr(se, "REQUEST_INTERVAL_SECONDS", 0.0)
    monkeypatch.setattr(se, "_record_screener_status", lambda _s: None)

    written = []
    lock = threading.Lock()

    def maybe_abort(symbol, pacer):
        with lock:
            written.append(symbol)
            count = len(written)
        if count == 5:
            # Simulate the process being torn down mid-run.
            raise SystemExit("simulated operator abort")
        return {"pbv": 1.0, "roce": 1.0}

    monkeypatch.setattr(se, "_fetch_symbol_data", maybe_abort)

    with pytest.raises(SystemExit):
        se.enrich_screener_fundamentals(force=True, limit=10)

    conn = sqlite3.connect(f"file:{val_db}?mode=ro", uri=True)
    try:
        stored = conn.execute("SELECT COUNT(*) FROM screener_fundamentals").fetchone()[
            0
        ]
    finally:
        conn.close()
    assert stored >= 1, "per-symbol progress was lost on abort"


# ---------------------------------------------------------------------------
# 5. Retry / rate-limit policy (WS3)
# ---------------------------------------------------------------------------


class _FakeSession:
    """Replays a scripted list of responses/exceptions, recording call count."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = 0
        self.headers = {}
        self.thread_ids = set()

    def get(self, url, timeout=None):
        self.calls += 1
        self.thread_ids.add(threading.get_ident())
        item = self.script.pop(0) if self.script else _FakeResponse(200)
        if isinstance(item, Exception):
            raise item
        return item

    def close(self):
        pass


def test_transient_timeout_is_retried_then_succeeds(monkeypatch):
    session = _FakeSession(
        [
            requests.Timeout("boom"),
            _FakeResponse(200, text="ok"),
        ]
    )
    monkeypatch.setattr(se, "RETRY_BASE_DELAY_SECONDS", 0.01)
    monkeypatch.setattr(se, "RETRY_MAX_DELAY_SECONDS", 0.02)
    pacer = se._RequestPacer(0.0)
    resp = se._request_with_retry("GET", "http://x", pacer=pacer, session=session)
    assert resp.status_code == 200
    assert session.calls == 2, "transient failure was not retried"


def test_retry_is_bounded_then_raises(monkeypatch):
    session = _FakeSession([requests.Timeout("t")] * 10)
    monkeypatch.setattr(se, "RETRY_BASE_DELAY_SECONDS", 0.01)
    monkeypatch.setattr(se, "RETRY_MAX_DELAY_SECONDS", 0.02)
    pacer = se._RequestPacer(0.0)
    with pytest.raises(requests.RequestException):
        se._request_with_retry("GET", "http://x", pacer=pacer, session=session)
    assert session.calls == se.MAX_ATTEMPTS, "retries were not bounded"


def test_404_is_permanent_and_not_retried(monkeypatch):
    session = _FakeSession([_FakeResponse(404)] * 5)
    monkeypatch.setattr(se, "RETRY_BASE_DELAY_SECONDS", 0.01)
    pacer = se._RequestPacer(0.0)
    resp = se._request_with_retry("GET", "http://x", pacer=pacer, session=session)
    assert resp.status_code == 404
    assert session.calls == 1, "a permanent 404 was retried"


def test_429_is_retried_and_penalises_the_shared_pacer(monkeypatch):
    session = _FakeSession(
        [
            _FakeResponse(429, headers={"Retry-After": "0.2"}),
            _FakeResponse(200, text="ok"),
        ]
    )
    monkeypatch.setattr(se, "RETRY_BASE_DELAY_SECONDS", 0.01)
    pacer = se._RequestPacer(0.0)
    t0 = time.monotonic()
    resp = se._request_with_retry("GET", "http://x", pacer=pacer, session=session)
    assert resp.status_code == 200
    # The Retry-After hint must have throttled the *shared* gate.
    assert time.monotonic() - t0 >= 0.15, "Retry-After was ignored"


def test_5xx_is_retried(monkeypatch):
    session = _FakeSession([_FakeResponse(503), _FakeResponse(200, text="ok")])
    monkeypatch.setattr(se, "RETRY_BASE_DELAY_SECONDS", 0.01)
    pacer = se._RequestPacer(0.0)
    resp = se._request_with_retry("GET", "http://x", pacer=pacer, session=session)
    assert resp.status_code == 200
    assert session.calls == 2


# ---------------------------------------------------------------------------
# 6. Truthful partial-success reporting (WS3/WS5)
# ---------------------------------------------------------------------------


def test_summary_reports_partial_success(val_db, monkeypatch):
    symbols = [f"S{i}" for i in range(4)]
    _add_fundamentals(val_db, symbols)
    _only(monkeypatch, symbols)
    monkeypatch.setattr(se, "REQUEST_INTERVAL_SECONDS", 0.0)
    monkeypatch.setattr(se, "_record_screener_status", lambda _s: None)
    monkeypatch.setattr(
        se,
        "_fetch_symbol_data",
        lambda s, p: {"pbv": 1.0} if s in ("S0", "S1") else {},
    )
    summary = se.enrich_screener_fundamentals(force=True, limit=4)
    assert summary["updated"] == 2
    assert summary["failed"] == 2
    assert summary["partial"] is True
    assert summary["total"] == 4


def test_status_recorded_as_partial(val_db, monkeypatch):
    _add_fundamentals(val_db, ["S0", "S1"])
    _only(monkeypatch, ["S0", "S1"])
    monkeypatch.setattr(se, "REQUEST_INTERVAL_SECONDS", 0.0)
    monkeypatch.setattr(
        se, "_fetch_symbol_data", lambda s, p: {"pbv": 1.0} if s == "S0" else {}
    )
    real_recorder = se._record_screener_status
    recorded = {}
    monkeypatch.setattr(
        se,
        "_record_screener_status",
        lambda s: recorded.setdefault("status", s),
    )
    se.enrich_screener_fundamentals(force=True, limit=2)
    assert recorded["status"]["partial"] is True
    # The run really did produce a degraded summary, not a clean success.
    assert recorded["status"]["updated"] == 1
    assert recorded["status"]["failed"] == 1


def test_partial_status_maps_to_sync_log_vocabulary(monkeypatch):
    """A degraded run must reach sync_log as 'partial', never as success."""
    from myra_app.utils import task_utils

    seen = {}
    monkeypatch.setattr(
        task_utils,
        "_mark_task_run",
        lambda label, status=None, error_message=None: seen.update(
            label=label, status=status, msg=error_message
        ),
    )
    se._record_screener_status(
        {"partial": True, "updated": 2, "failed": 1, "skipped": 0, "total": 3}
    )
    assert seen["label"] == "screener_enrich"
    assert seen["status"] == "partial"
    assert "updated=2" in seen["msg"] and "failed=1" in seen["msg"]


def test_total_failure_status_is_failed_not_success(monkeypatch):
    from myra_app.utils import task_utils

    seen = {}
    monkeypatch.setattr(
        task_utils,
        "_mark_task_run",
        lambda label, status=None, error_message=None: seen.update(status=status),
    )
    se._record_screener_status(
        {"partial": False, "updated": 0, "failed": 5, "skipped": 0, "total": 5}
    )
    assert seen["status"] == "failed", "a total failure was recorded as success"


def test_total_failure_is_not_reported_as_success(val_db, monkeypatch):
    _add_fundamentals(val_db, ["S0"])
    monkeypatch.setattr(se, "REQUEST_INTERVAL_SECONDS", 0.0)
    monkeypatch.setattr(se, "_record_screener_status", lambda _s: None)
    monkeypatch.setattr(se, "_fetch_symbol_data", lambda s, p: {})
    summary = se.enrich_screener_fundamentals(force=True, limit=1)
    assert summary["updated"] == 0
    assert summary["partial"] is False
    assert summary["failed"] == 1


# ---------------------------------------------------------------------------
# 7. Cache / source gating / isolation
# ---------------------------------------------------------------------------


def test_cached_symbols_are_skipped(val_db, monkeypatch):
    from datetime import datetime

    _add_fundamentals(val_db, ["AAA"])
    _only(monkeypatch, ["AAA"])
    conn = sqlite3.connect(val_db, timeout=30)
    conn.execute(
        "INSERT INTO screener_fundamentals (symbol, pbv, roce, last_updated) "
        "VALUES ('AAA', 1.0, 1.0, ?)",
        (datetime.now().isoformat(),),
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(se, "SCREENER_ENRICH_AUTO_ENABLED", True)
    monkeypatch.setattr(se, "REQUEST_INTERVAL_SECONDS", 0.0)
    monkeypatch.setattr(se, "_record_screener_status", lambda _s: None)
    called = []
    monkeypatch.setattr(se, "_fetch_symbol_data", lambda s, p: called.append(s) or {})
    summary = se.enrich_screener_fundamentals(force=False, limit=10)
    assert summary["total"] == 0
    assert summary["skipped"] == 1
    assert called == [], "a cached symbol was re-fetched"


def test_disabled_source_is_a_clean_noop(val_db, monkeypatch):
    monkeypatch.setattr(se, "_source_enabled", lambda name: False)
    summary = se.enrich_screener_fundamentals(force=True)
    assert summary["reason"] == "source_disabled"
    assert summary["total"] == 0


def test_auto_disabled_is_a_clean_noop(val_db, monkeypatch):
    monkeypatch.setattr(se, "SCREENER_ENRICH_AUTO_ENABLED", False)
    summary = se.enrich_screener_fundamentals(force=False)
    assert summary["reason"] == "auto_run_disabled"


def test_enricher_writes_only_to_the_redirected_database(fixture_db_dir):
    """Module-level DB_DIR must track the redirect, never the live sidecar."""
    assert se.DB_DIR == fixture_db_dir


def test_company_id_parsing_and_metric_extraction(monkeypatch):
    """Pure parsing: no network, just response handling."""
    html = '<html><div data-company-id="1234"></div></html>'
    monkeypatch.setattr(
        se, "_request_with_retry", lambda *a, **k: _FakeResponse(200, text=html)
    )
    assert se._get_company_id("AAA", se._RequestPacer(0.0)) == "1234"

    monkeypatch.setattr(
        se,
        "_request_with_retry",
        lambda *a, **k: _FakeResponse(200, payload=_chart_payload(4.5)),
    )
    assert se._fetch_metric("1234", "Price to book value", se._RequestPacer(0.0)) == 4.5


def test_metric_missing_or_malformed_returns_none(monkeypatch):
    pacer = se._RequestPacer(0.0)
    monkeypatch.setattr(
        se,
        "_request_with_retry",
        lambda *a, **k: _FakeResponse(200, payload={"datasets": []}),
    )
    assert se._fetch_metric("1", "q", pacer) is None

    monkeypatch.setattr(
        se,
        "_request_with_retry",
        lambda *a, **k: _FakeResponse(200, payload=None, text="<html>"),
    )
    assert se._fetch_metric("1", "q", pacer) is None
