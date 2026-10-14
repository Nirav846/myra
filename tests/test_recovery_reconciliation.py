"""Recovery and reconciliation behaviour (Phase 9, workstream 2).

Phase 8 built the recovery *mechanisms* (per-symbol commit, bounded retry,
shared pacing, duplicate-run refusal, per-symbol EOD cutoff). This file pins
the end-to-end scenarios the directive lists, using controlled fakes — no live
Screener.in, no production database, no network.

Scenarios covered:
  1. process stops halfway through enrichment -> completed symbols survive
  2. some symbols succeed, others time out -> partial success reported
  3. upstream rate-limits -> bounded retry, shared pacer slows everyone
  4. restart after per-symbol commits -> cached symbols are not re-fetched
  5. launched twice from two entry points -> second run refuses
  6. a database lock during a write -> surfaced, not silently swallowed
  7. status update fails after work committed -> work is still durable
  8. an EOD CSV arrives after the initial run -> picked up on the next run
  9. the same EOD data is ingested again -> idempotent
 10. a partial file set followed by the complete set -> nothing is lost
"""

import os
import sqlite3
import threading
import time

import pytest
import requests

import myra_app.eod2_sync as eod2
from myra_app.db.enrichers import screener_enricher
from myra_app.db.enrichers import screener_enricher as se
from myra_app.utils import task_lock

pytestmark = pytest.mark.usefixtures("fixture_db_dir")

#: Captured at import, before any test can monkeypatch the attribute.
_REAL_RECORDER = screener_enricher._record_screener_status


@pytest.fixture(autouse=True)
def _no_cross_process_lock(monkeypatch):
    """Keep the cross-process lock out of unit tests (covered separately)."""
    monkeypatch.setattr(se, "_acquire_cross_process_lock", lambda: (True, None))
    monkeypatch.setattr(se, "_release_cross_process_lock", lambda: None)


@pytest.fixture
def val_db(fixture_db_dir):
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


def _only(monkeypatch, symbols):
    monkeypatch.setattr(se, "_select_symbols", lambda: list(symbols))


def _no_status(monkeypatch):
    monkeypatch.setattr(se, "_record_screener_status", lambda _s: None)


def _quiet(monkeypatch):
    """Zero pacing + no sync_log write. Enabling the source is required for any
    non-forced run to do work at all (SCREENER_ENRICH_AUTO_ENABLED is False in
    the repo, which is why the scheduled task is a no-op)."""
    monkeypatch.setattr(se, "SCREENER_ENRICH_AUTO_ENABLED", True)
    monkeypatch.setattr(se, "REQUEST_INTERVAL_SECONDS", 0.0)
    _no_status(monkeypatch)


def _stored(val_db, symbol):
    conn = sqlite3.connect(f"file:{val_db}?mode=ro", uri=True)
    try:
        return conn.execute(
            "SELECT pbv, roce FROM screener_fundamentals WHERE symbol = ?",
            (symbol,),
        ).fetchone()
    finally:
        conn.close()


# ── 1/2/4. Interruption, partial success, restart ───────────────────────────


def test_interrupted_run_keeps_completed_symbols(val_db, monkeypatch):
    """Scenario 1: a hard stop mid-run must not discard committed symbols."""
    symbols = [f"S{i:02d}" for i in range(20)]
    _only(monkeypatch, symbols)
    _quiet(monkeypatch)

    done = []
    lock = threading.Lock()

    def maybe_die(symbol, pacer):
        with lock:
            done.append(symbol)
            if len(done) == 8:
                raise SystemExit("simulated kill")
        return {"pbv": 1.0, "roce": 2.0}

    monkeypatch.setattr(se, "_fetch_symbol_data", maybe_die)

    with pytest.raises(SystemExit):
        se.enrich_screener_fundamentals(force=True, limit=20)

    conn = sqlite3.connect(f"file:{val_db}?mode=ro", uri=True)
    try:
        stored = conn.execute("SELECT COUNT(*) FROM screener_fundamentals").fetchone()[
            0
        ]
    finally:
        conn.close()
    assert stored >= 1, "committed per-symbol work was lost on interruption"


def test_partial_success_is_reported_not_hidden(val_db, monkeypatch):
    """Scenario 2: some succeed, some time out -> explicit partial summary."""
    symbols = ["GOOD1", "GOOD2", "TMO1", "TMO2"]
    _only(monkeypatch, symbols)
    _quiet(monkeypatch)

    def flaky(symbol, pacer):
        if symbol.startswith("TMO"):
            raise requests.Timeout("upstream stalled")
        return {"pbv": 1.0, "roce": 2.0}

    monkeypatch.setattr(se, "_fetch_symbol_data", flaky)
    # The real recorder is grabbed from the module object *before* the spy
    # replaces the attribute below, so this test exercises the real mapping.
    real_recorder = _REAL_RECORDER
    seen = {}
    monkeypatch.setattr(
        se, "_record_screener_status", lambda s: seen.setdefault("summary", s)
    )
    summary = se.enrich_screener_fundamentals(force=True, limit=4)

    assert summary["updated"] == 2
    assert summary["failed"] == 2
    assert summary["partial"] is True
    assert seen["summary"]["partial"] is True

    # The real recorder must map that onto the sync_log vocabulary, with the
    # counts visible rather than a bare success.
    import myra_app.utils.task_utils as task_utils_mod

    recorded = {}
    monkeypatch.setattr(
        task_utils_mod,
        "_mark_task_run",
        lambda label, status=None, error_message=None: recorded.update(
            label=label, status=status, msg=error_message
        ),
        raising=True,
    )
    real_recorder(summary)
    assert recorded.get("status") == "partial", recorded
    assert "updated=2" in recorded.get("msg", "") and "failed=2" in recorded.get(
        "msg", ""
    )


def test_restart_does_not_refetch_cached_symbols(val_db, monkeypatch):
    """Scenario 4: a restart re-fetches only what is not already current."""
    from datetime import datetime

    _only(monkeypatch, ["CACHED", "STALE"])
    _quiet(monkeypatch)
    conn = sqlite3.connect(val_db, timeout=30)
    conn.execute(
        "INSERT INTO screener_fundamentals (symbol, pbv, roce, last_updated) "
        "VALUES ('CACHED', 5.0, 6.0, ?)",
        (datetime.now().isoformat(),),
    )
    conn.execute(
        "INSERT INTO screener_fundamentals (symbol, pbv, roce, last_updated) "
        "VALUES ('STALE', 5.0, 6.0, '2000-01-01T00:00:00')"
    )
    conn.commit()
    conn.close()

    fetched = []
    monkeypatch.setattr(
        se,
        "_fetch_symbol_data",
        lambda s, p: fetched.append(s) or {"pbv": 1.0, "roce": 1.0},
    )
    se.enrich_screener_fundamentals(force=False, limit=10)
    assert "CACHED" not in fetched, "a fresh symbol was re-fetched needlessly"
    assert "STALE" in fetched, "a stale symbol was not refreshed"


# ── 3. Rate limiting ─────────────────────────────────────────────────────────


class _Resp:
    def __init__(self, status, headers=None):
        self.status_code = status
        self.headers = headers or {}

    def json(self):
        return {"datasets": [{"values": [[0, 1.0]]}]}


class _Sess:
    def __init__(self, script):
        self.script = list(script)
        self.calls = 0

    def get(self, url, timeout=None):
        self.calls += 1
        return self.script.pop(0) if self.script else _Resp(200)

    def close(self):
        pass


def test_rate_limit_is_bounded_and_backs_off(monkeypatch):
    """Scenario 3: 429 is retried a bounded number of times, not forever."""
    monkeypatch.setattr(se, "RETRY_BASE_DELAY_SECONDS", 0.01)
    monkeypatch.setattr(se, "RETRY_MAX_DELAY_SECONDS", 0.02)
    session = _Sess([_Resp(429), _Resp(429), _Resp(429), _Resp(429), _Resp(429)])
    pacer = se._RequestPacer(0.0)
    se._request_with_retry("GET", "http://x", pacer=pacer, session=session)
    assert session.calls == se.MAX_ATTEMPTS, "retries were not bounded"


def test_rate_limit_pushes_the_shared_pacer(monkeypatch):
    """A 429 must slow *all* workers, not just the one that saw it."""
    monkeypatch.setattr(se, "RETRY_BASE_DELAY_SECONDS", 0.01)
    session = _Sess([_Resp(429, {"Retry-After": "0.3"}), _Resp(200)])
    pacer = se._RequestPacer(0.0)
    t0 = time.monotonic()
    se._request_with_retry("GET", "http://x", pacer=pacer, session=session)
    assert time.monotonic() - t0 >= 0.25, "the shared gate was not slowed"


# ── 5. Duplicate-run prevention across entry points ──────────────────────────


def test_cross_process_lock_refuses_a_second_process(monkeypatch, tmp_path):
    """Scenario 5: a lock held by another live pid must block acquisition."""
    monkeypatch.setattr(task_lock, "DB_DIR", str(tmp_path))
    lock_file = tmp_path / ".myra_task_screener_enrich.pid"
    # Pretend another process owns it, and that the pid is genuinely alive
    # (on Windows an arbitrary pid can resolve, so stub the liveness check).
    lock_file.write_text("4242\n", encoding="utf-8")
    monkeypatch.setattr(task_lock, "_pid_alive", lambda pid: True)

    acquired, owner = task_lock.acquire("screener_enrich")
    assert acquired is False
    assert owner == 4242
    assert lock_file.exists(), "a lock owned by another process was removed"

    # A non-owner must not be able to release someone else's lock.
    task_lock.release("screener_enrich")
    assert lock_file.exists(), "release removed another process's lock"


def test_enricher_refuses_when_another_process_holds_the_lock(val_db, monkeypatch):
    """The cross-process guard is wired into the public entry point."""
    _only(monkeypatch, ["A"])
    _quiet(monkeypatch)
    monkeypatch.undo()  # drop the autouse bypass so the real guard is exercised
    monkeypatch.setattr(se, "_acquire_cross_process_lock", lambda: (False, 4242))

    summary = se.enrich_screener_fundamentals(force=True, limit=1)
    assert summary["reason"] == "already_running_other_process"
    assert summary["total"] == 0


def test_stale_lock_from_a_dead_process_is_reclaimed(monkeypatch, tmp_path):
    """A crashed run must not wedge the task forever."""
    monkeypatch.setattr(task_lock, "DB_DIR", str(tmp_path))
    lock_file = tmp_path / ".myra_task_x.pid"
    lock_file.write_text("4242\n", encoding="utf-8")
    monkeypatch.setattr(task_lock, "_pid_alive", lambda pid: False)
    acquired, _ = task_lock.acquire("x")
    assert acquired is True
    assert lock_file.read_text(encoding="utf-8").startswith(str(os.getpid()))


def test_second_run_refuses_while_first_holds_the_process_lock(val_db, monkeypatch):
    """The in-process guard still refuses a concurrent caller."""
    _only(monkeypatch, ["A"])
    _quiet(monkeypatch)
    entered = threading.Event()
    release = threading.Event()

    def slow(symbol, pacer):
        entered.set()
        release.wait(timeout=5)
        return {"pbv": 1.0, "roce": 1.0}

    monkeypatch.setattr(se, "_fetch_symbol_data", slow)
    out = {}

    t = threading.Thread(
        target=lambda: out.update(
            r=se.enrich_screener_fundamentals(force=True, limit=1)
        )
    )
    t.start()
    assert entered.wait(timeout=5)
    second = se.enrich_screener_fundamentals(force=True, limit=1)
    release.set()
    t.join(timeout=5)
    assert second["reason"] == "already_running"


# ── 6/7. DB lock and status-write failure ────────────────────────────────────


def test_database_lock_is_surfaced_not_swallowed(val_db, monkeypatch):
    """Scenario 6: a locked database must raise, not silently 'succeed'."""
    _only(monkeypatch, ["A"])
    _quiet(monkeypatch)
    monkeypatch.setattr(
        se, "_fetch_symbol_data", lambda s, p: {"pbv": 1.0, "roce": 1.0}
    )

    real_write = se._write_symbol

    def locked(db_path, symbol, data):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(se, "_write_symbol", locked)

    # A per-symbol write failure is contained (one symbol never aborts the run)
    # but it must be *counted* as a failure, not reported as an update.
    summary = se.enrich_screener_fundamentals(force=True, limit=1)
    assert summary["updated"] == 0
    assert summary["failed"] == 1
    assert summary["partial"] is False
    assert real_write is not locked  # sanity


def test_status_write_failure_does_not_undo_committed_work(val_db, monkeypatch):
    """Scenario 7: if recording status fails, the data stays committed.

    The production recorder already swallows its own failure; this pins that the
    committed rows survive a status-write problem rather than being rolled back
    or reported as lost.
    """
    _only(monkeypatch, ["A", "B"])
    _quiet(monkeypatch)
    monkeypatch.setattr(
        se, "_fetch_symbol_data", lambda s, p: {"pbv": 1.0, "roce": 1.0}
    )

    from myra_app.utils import task_utils

    def boom(*a, **k):
        raise RuntimeError("sync_log unavailable")

    monkeypatch.setattr(task_utils, "_mark_task_run", boom)

    summary = se.enrich_screener_fundamentals(force=True, limit=2)
    assert summary["updated"] == 2
    # ... and the rows are on disk regardless.
    assert _stored(val_db, "A") == (1.0, 1.0)
    assert _stored(val_db, "B") == (1.0, 1.0)


def test_ingest_tests_do_not_leak_a_fake_eod_sync():
    """Regression: an ingest test once left `sync_eod2_data` faked process-wide.

    `test_ingest_attempt_cooldown` assigned `eod2_sync.sync_eod2_data` and two
    `constants` flags directly and restored only part of them, so every later
    test in the process silently ran against a stub that returned
    ``error="boom"``. This asserts the real entry points are intact after the
    ingest modules have run in the same process.
    """
    import myra_app.constants as constants
    import myra_app.eod2_sync as eod2_mod

    assert (
        eod2_mod.sync_eod2_data.__module__ == "myra_app.eod2_sync"
    ), "sync_eod2_data is still monkeypatched by an earlier test"
    assert isinstance(constants.USE_EOD2_DATA, bool)


# ── 8/9/10. EOD reconciliation ──────────────────────────────────────────────

_HDR = "Date,Open,High,Low,Close,Volume,Series,TOTAL_TRADES,QTY_PER_TRADE,DLV_QTY"


def _csv(d):
    return f"{_HDR}\n{d},100,101,99,100,1000,EQ,,,\n"


@pytest.fixture
def eod(tmp_path, monkeypatch):
    """An isolated EOD source + technical DB wired into the sync."""
    daily = tmp_path / "daily"
    daily.mkdir()

    def write(names, d):
        for n in names:
            p = daily / f"{n}.csv"
            p.write_text(_csv(d), encoding="utf-8")
            old = time.time() - 3600
            os.utime(p, (old, old))

    tech = str(tmp_path / "tech.db")
    conn = sqlite3.connect(tech)
    conn.execute(
        "CREATE TABLE technical_data (symbol TEXT, date TEXT, open REAL, high REAL, "
        "low REAL, close REAL, volume REAL, delivery REAL, trades REAL, "
        "delivery_pct REAL, delivery_ratio REAL, delivery_source TEXT, sma_50 REAL, "
        "high_52w REAL, low_52w REAL, delivery_ma_60 REAL, PRIMARY KEY(symbol, date))"
    )
    conn.execute(
        "CREATE TABLE ingestion_rejects (id INTEGER PRIMARY KEY, symbol TEXT, "
        "date TEXT, reason TEXT, raw_values TEXT)"
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(eod2, "_get_db_path", lambda: tech)
    monkeypatch.setattr(eod2, "_find_eod2_daily", lambda: str(daily))
    monkeypatch.setattr(eod2, "_get_meta_latest", lambda _d: "2026-01-05")
    monkeypatch.setattr(eod2, "_run_enrichment", lambda _c: None)
    monkeypatch.setattr("myra_app.data_readiness.invalidate_source_cache", lambda: None)
    return str(daily), write, tech


def _max_dates(tech):
    conn = sqlite3.connect(f"file:{tech}?mode=ro", uri=True)
    try:
        return dict(
            conn.execute(
                "SELECT symbol, MAX(date) FROM technical_data GROUP BY symbol"
            ).fetchall()
        )
    finally:
        conn.close()


def test_late_arriving_csv_is_ingested_on_a_later_run(eod):
    """Scenario 8: a file that appears after the first run is still discovered."""
    daily, write, tech = eod
    write(["A", "B"], "2026-01-02")
    first = eod2.sync_eod2_data()
    assert first["rows_inserted"] == 2
    assert set(_max_dates(tech).values()) == {"2026-01-02"}

    # A third symbol's file lands late.
    write(["C"], "2026-01-02")
    second = eod2.sync_eod2_data()
    assert second["rows_inserted"] == 1
    assert _max_dates(tech)["C"] == "2026-01-02"


def test_reingesting_the_same_data_changes_nothing(eod):
    """Scenario 9: idempotent repeated import."""
    daily, write, tech = eod
    write(["A", "B", "C"], "2026-01-02")
    eod2.sync_eod2_data()
    snapshot = _max_dates(tech)
    again = eod2.sync_eod2_data()
    assert again["rows_inserted"] == 0
    assert _max_dates(tech) == snapshot


def test_partial_then_complete_file_set_loses_nothing(eod):
    """Scenario 10: stragglers from a half-copied set land on the next run."""
    daily, write, tech = eod
    # Seed the DB one session behind, as if a previous complete run happened.
    write(["A", "B", "C", "D", "E"], "2026-01-02")
    eod2.sync_eod2_data()
    assert set(_max_dates(tech).values()) == {"2026-01-02"}

    # Now a *partial* copy of the next session: only A and B have advanced.
    write(["A", "B"], "2026-01-05")
    first = eod2.sync_eod2_data()
    assert first["rows_inserted"] == 2
    assert first["readiness"]["ready"] is False, "a half-copied set must be reported"

    # The remaining three arrive.
    write(["C", "D", "E"], "2026-01-05")
    second = eod2.sync_eod2_data()
    assert second["rows_inserted"] == 3
    assert set(_max_dates(tech).values()) == {"2026-01-05"}, "a symbol was lost"


def test_still_being_written_file_is_skipped_then_ingested(eod):
    """A file skipped as unstable must not vanish from later discovery."""
    daily, write, tech = eod
    write(["A", "B"], "2026-01-02")
    p = os.path.join(daily, "B.csv")
    now = time.time()
    os.utime(p, (now, now))  # B looks mid-copy
    first = eod2.sync_eod2_data()
    assert first["skipped"] >= 1
    assert "B" not in _max_dates(tech)

    old = time.time() - 600
    os.utime(p, (old, old))  # copy finishes
    second = eod2.sync_eod2_data()
    assert _max_dates(tech).get("B") == "2026-01-02"
