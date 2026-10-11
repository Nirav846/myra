"""End-to-end tests for the resumable, cancel/pause-aware ``sync_fundamentals``.

The operator Stop used to be a no-op for this task (it ran every queued symbol
to completion and left the UI on "cancelling"), and a hard kill lost all
progress because the cursor was memory-only. These tests pin the new contract:

* cancellation sets ``cancelled`` and leaves the run resumable;
* failures park a symbol after ``PARK_AFTER_FAILURES`` and it is skipped on the
  next run instead of being retried pointlessly;
* a rate-limited source aborts the whole pass instead of counting every symbol
  as a failure;
* a resumed run skips symbols that were fixed before the interruption.
"""

import sqlite3
import threading
from datetime import datetime, timezone

import pytest

from myra_app import constants
from myra_app.librarian_core import LibrarianCore
from myra_app.utils import fundamentals_sync as fs


class _FakeFundamentalManager:
    """Drop-in for FundamentalManager: writes on success, ignores the fetcher."""

    def __init__(self, db_dir, outcome, failure_status=None):
        self._db_dir = db_dir
        self._outcome = outcome
        self.failure_status = failure_status
        self.last_error = None
        self.last_source = None
        self.last_http_status = None

    def set_fetcher(self, fetcher):
        pass

    def fetch_fundamentals(self, symbol):
        if self._outcome(symbol):
            conn = sqlite3.connect(_val(self._db_dir), timeout=10)
            conn.execute(
                "UPDATE fundamentals SET sector='fixed', pe=10, roe=15, "
                "market_cap=100 WHERE symbol=?",
                (symbol,),
            )
            conn.commit()
            conn.close()
            self.last_source = "screener_in"
            self.last_http_status = 200
            self.last_error = None
            return True
        self.last_source = None
        self.last_http_status = self.failure_status
        self.last_error = "simulated failure"
        return False


def _val(db_dir):
    import os

    return os.path.join(db_dir, LibrarianCore.DB_MAP["valuation"])


def _meta(db_dir):
    import os

    return os.path.join(db_dir, LibrarianCore.DB_MAP["meta"])


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    """Point constants.DB_DIR at a throwaway dir with real sidecar filenames."""
    monkeypatch.setattr(constants, "DB_DIR", str(tmp_path))
    meta_conn = sqlite3.connect(_meta(tmp_path))
    meta_conn.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT)")
    meta_conn.commit()
    meta_conn.close()
    conn = sqlite3.connect(_val(tmp_path))
    conn.execute(
        "CREATE TABLE fundamentals ("
        "symbol TEXT PRIMARY KEY, sector TEXT, pe REAL, roe REAL, market_cap REAL)"
    )
    conn.commit()
    conn.close()
    return tmp_path


def _seed(tmp_path, symbols):
    conn = sqlite3.connect(_val(tmp_path), timeout=10)
    for symbol in symbols:
        conn.execute(
            "INSERT OR IGNORE INTO fundamentals (symbol) VALUES (?)", (symbol,)
        )
    conn.commit()
    conn.close()


def _patch_pipeline(monkeypatch, fake):
    monkeypatch.setattr(fs, "FundamentalManager", lambda **_: fake)
    monkeypatch.setattr(fs, "DataFetcher", lambda: None)
    monkeypatch.setattr(fs, "_backoff_sleep", lambda seconds, cancel_event: None)


def _load_progress(tmp_path):
    conn = sqlite3.connect(_meta(tmp_path))
    try:
        row = conn.execute(
            "SELECT value FROM metadata WHERE key = ?", (fs.PROGRESS_KEY,)
        ).fetchone()
        import json

        return json.loads(row[0]) if row and row[0] else {}
    finally:
        conn.close()


def test_cancel_before_start_leaves_run_resumable(isolated_db, monkeypatch):
    _seed(isolated_db, ["AAA", "BBB", "CCC"])
    fake = _FakeFundamentalManager(str(isolated_db), lambda s: True)
    _patch_pipeline(monkeypatch, fake)

    cancel = threading.Event()
    cancel.set()
    summary = fs.sync_fundamentals(force=True, cancel_event=cancel)

    assert summary["cancelled"] is True
    assert summary["updated"] == 0
    progress = _load_progress(isolated_db)
    assert progress["status"] == "in_progress"  # never stamped complete


def test_resume_skips_symbols_already_fixed(isolated_db, monkeypatch):
    symbols = ["AAA", "BBB", "CCC", "DDD"]
    _seed(isolated_db, symbols)
    fake = _FakeFundamentalManager(str(isolated_db), lambda s: True)
    _patch_pipeline(monkeypatch, fake)

    cancel = threading.Event()

    def stop_after_first(done, total, info):
        if done == 1:
            cancel.set()

    first = fs.sync_fundamentals(
        force=True, cancel_event=cancel, progress_cb=stop_after_first
    )
    assert first["cancelled"] is True
    assert first["updated"] == 1
    assert first["queued"] == 4

    # Second run: AAA is already fixed, so it must not be re-queued.
    second = fs.sync_fundamentals(force=True)
    assert second["cancelled"] is False
    assert second["updated"] == 3
    assert second["queued"] == 3
    assert _load_progress(isolated_db)["status"] == "complete"


def test_symbols_parked_after_repeated_failures(isolated_db, monkeypatch):
    symbols = ["F1", "F2", "F3", "F4", "F5"]
    _seed(isolated_db, symbols)
    fake = _FakeFundamentalManager(
        str(isolated_db), lambda s: False, failure_status=404
    )
    _patch_pipeline(monkeypatch, fake)
    # The per-failure backoff would make this test take minutes; the parking
    # bookkeeping is what we are exercising, not the sleep.
    monkeypatch.setattr(fs, "_backoff_sleep", lambda *a, **k: None)

    for run in range(fs.PARK_AFTER_FAILURES):
        result = fs.sync_fundamentals(force=True)
        assert result["failed"] == len(symbols)
        assert result["cancelled"] is False
        # Still short of the threshold: the next pass must retry them.
        if run + 1 < fs.PARK_AFTER_FAILURES:
            assert result["parked"] == 0

    park_conn = sqlite3.connect(_meta(isolated_db))
    state = fs.load_park_state(park_conn)
    park_conn.close()
    for symbol in symbols:
        assert fs.is_parked(state, symbol, datetime.now(timezone.utc))

    # The fake never writes on failure, so rows are still "needing fundamentals"
    # — the park gate is what keeps the second pass from retrying them.
    second = fs.sync_fundamentals(force=True)
    assert second["queued"] == 0
    assert second["parked"] == 5


def test_rate_limit_aborts_pass_not_the_symbols(isolated_db, monkeypatch):
    symbols = ["R1", "R2", "R3", "R4"]
    _seed(isolated_db, symbols)
    fake = _FakeFundamentalManager(
        str(isolated_db), lambda s: False, failure_status=429
    )
    _patch_pipeline(monkeypatch, fake)

    summary = fs.sync_fundamentals(force=True)
    assert summary["throttled"] == fs.RATE_LIMIT_ABORT_AFTER
    assert summary["failed"] == 0  # 429 is the source's fault, not the symbol's
    # Run aborted early: remaining symbols are simply untouched.
    assert summary["queued"] == 4
    assert _load_progress(isolated_db)["status"] == "in_progress"
