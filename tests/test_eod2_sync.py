"""EOD2 CSV ingestion: readiness, per-symbol cutoff, idempotency, isolation.

The active production ingest path (``constants.USE_EOD2_DATA = True``) had no
test coverage before Phase 8. Two defects were fixed and are locked here:

* **Per-symbol cutoff (WS4).** The cutoff used to be a single global
  ``MAX(date)`` over the whole table. With the manual EOD workflow — where the
  operator copies CSVs in over time — a half-copied folder advances the global
  cutoff with the first ready file, and every symbol still catching up is then
  filtered out *forever*. Each symbol now advances only its own cutoff, so the
  stragglers land on a later run.
* **Input readiness (WS4).** ``meta.json`` and the per-file dates are inspected
  before ingest, so a partially-copied or still-being-written source set is
  reported rather than silently treated as a complete session.

Every test runs on the fixture databases via ``fixture_db_dir``; nothing here
touches a live database.
"""

import json
import os

import pytest

import myra_app.constants as constants
import myra_app.eod2_sync as eod2

pytestmark = pytest.mark.usefixtures("fixture_db_dir")

OLD = "2026-10-12"
NEW = "2026-10-13"

_HEADER = "Date,Open,High,Low,Close,Volume,Series,TOTAL_TRADES,QTY_PER_TRADE,DLV_QTY"


def _csv(date_value, close=100.0, volume=1000, delivery=""):
    """One-row eod2 CSV in the real on-disk format."""
    return f"{_HEADER}\n" f"{date_value},100,101,99,{close},{volume},EQ,,,{delivery}\n"


def _write_csv(directory, name, content, *, mtime_offset=3600.0):
    """Write ``name`` with an mtime far enough in the past to look 'settled'.

    Files written "now" are legitimately treated as still-being-copied, so tests
    that want a stable file must age it.
    """
    import time

    path = os.path.join(directory, name)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(content)
    stamp = time.time() - mtime_offset
    os.utime(path, (stamp, stamp))
    return path


def _source(tmp_path, n_symbols, last_date, *, meta=True, meta_date=None):
    """Build an eod2 source folder with ``n_symbols`` CSVs at ``last_date``."""
    daily = tmp_path / "daily"
    daily.mkdir(exist_ok=True)
    for i in range(n_symbols):
        _write_csv(str(daily), "sym%02d.csv" % i, _csv(last_date))
    if meta:
        payload = {
            "lastUpdate": f"{meta_date or last_date}T00:00:00+05:30",
            "DLV_PENDING_DATES": [],
        }
        with open(tmp_path / "meta.json", "w", encoding="utf-8") as handle:
            json.dump(payload, handle)
    return str(daily)


@pytest.fixture
def technical_db(fixture_db_dir):
    """The isolated technical DB the sync will write to."""
    from myra_app.librarian_core import LibrarianCore

    return os.path.join(fixture_db_dir, LibrarianCore.DB_MAP["technical"])


def _seed(technical_db, symbols, date_value=OLD):
    import sqlite3

    conn = sqlite3.connect(technical_db)
    try:
        for sym in symbols:
            conn.execute(
                "INSERT OR IGNORE INTO technical_data "
                "(symbol, date, open, high, low, close, volume) "
                "VALUES (?, ?, 100, 101, 99, 100, 1000)",
                (sym, date_value),
            )
        conn.commit()
    finally:
        conn.close()


def _fresh_db(tmp_path, name="tech.db"):
    """A private technical_data DB carrying exactly the columns the sync writes.

    Built from ``eod2_sync._INSERT_COLS`` so it cannot drift from the INSERT the
    sync actually performs, plus ``ingestion_rejects`` because the sync mirrors
    rejected rows there.
    """
    import sqlite3

    path = str(tmp_path / name)
    cols = list(eod2._INSERT_COLS)
    ddl = ", ".join(
        f"{c} REAL" if c not in ("symbol", "date", "delivery_source") else f"{c} TEXT"
        for c in cols
    )
    conn = sqlite3.connect(path)
    try:
        conn.execute(f"CREATE TABLE technical_data ({ddl}, PRIMARY KEY(symbol, date))")
        conn.execute(
            "CREATE TABLE ingestion_rejects (id INTEGER PRIMARY KEY, "
            "symbol TEXT, date TEXT, reason TEXT, raw_values TEXT, "
            "timestamp TEXT DEFAULT (datetime('now')))"
        )
        conn.commit()
    finally:
        conn.close()
    return path


def _max_dates(technical_db):
    import sqlite3

    conn = sqlite3.connect(f"file:{technical_db}?mode=ro", uri=True)
    try:
        return dict(
            conn.execute(
                "SELECT symbol, MAX(date) FROM technical_data GROUP BY symbol"
            ).fetchall()
        )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 1. The defect: a partially-copied file set used to lose the rest of the day
# ---------------------------------------------------------------------------


def test_partial_file_set_is_not_lost_on_a_later_run(tmp_path, monkeypatch):
    """The whole point of the per-symbol cutoff.

    A half-copied folder must ingest the ready symbols now and pick the rest up
    on the next run. With the old global cutoff the second run inserted nothing.
    """
    daily = _source(tmp_path, 10, OLD, meta=False)
    # 3 of 10 files have been copied to the new session; the rest are still OLD.
    for i in range(3):
        _write_csv(daily, "sym%02d.csv" % i, _csv(NEW))
    with open(tmp_path / "meta.json", "w", encoding="utf-8") as handle:
        json.dump(
            {"lastUpdate": f"{NEW}T00:00:00+05:30", "DLV_PENDING_DATES": []}, handle
        )

    technical_db = _fresh_db(tmp_path)
    _seed(technical_db, ["SYM%02d" % i for i in range(10)])
    monkeypatch.setattr(eod2, "_get_db_path", lambda: technical_db)
    monkeypatch.setattr(eod2, "_find_eod2_daily", lambda: daily)
    monkeypatch.setattr(eod2, "_get_meta_latest", lambda _d: NEW)
    monkeypatch.setattr(eod2, "_run_enrichment", lambda _conn: None)

    first = eod2.sync_eod2_data()
    assert first["rows_inserted"] == 3, first
    assert first["readiness"]["ready"] is False, "a half-copied set must be reported"

    # The remaining files finish copying.
    for i in range(3, 10):
        _write_csv(daily, "sym%02d.csv" % i, _csv(NEW))

    second = eod2.sync_eod2_data()
    assert second["rows_inserted"] == 7, second

    per_symbol = _max_dates(technical_db)
    at_new = sum(1 for value in per_symbol.values() if value == NEW)
    assert at_new == 10, f"partial set lost {10 - at_new} symbols: {per_symbol}"


def test_full_file_set_ingests_everything_in_one_pass(tmp_path, monkeypatch):
    daily = _source(tmp_path, 10, NEW, meta=False)
    technical_db = _fresh_db(tmp_path)
    _seed(technical_db, ["SYM%02d" % i for i in range(10)])
    monkeypatch.setattr(eod2, "_get_db_path", lambda: technical_db)
    monkeypatch.setattr(eod2, "_find_eod2_daily", lambda: daily)
    monkeypatch.setattr(eod2, "_get_meta_latest", lambda _d: NEW)
    monkeypatch.setattr(eod2, "_run_enrichment", lambda _conn: None)

    result = eod2.sync_eod2_data()
    assert result["rows_inserted"] == 10, result
    assert result["readiness"]["ready"] is True, result["readiness"]
    assert set(_max_dates(technical_db).values()) == {NEW}


# ---------------------------------------------------------------------------
# 2. Idempotency / safe reruns
# ---------------------------------------------------------------------------


def test_rerun_inserts_nothing_new(tmp_path, monkeypatch):
    daily = _source(tmp_path, 5, NEW, meta=False)
    technical_db = _fresh_db(tmp_path)
    _seed(technical_db, ["SYM%02d" % i for i in range(5)])
    monkeypatch.setattr(eod2, "_get_db_path", lambda: technical_db)
    monkeypatch.setattr(eod2, "_find_eod2_daily", lambda: daily)
    monkeypatch.setattr(eod2, "_get_meta_latest", lambda _d: NEW)
    monkeypatch.setattr(eod2, "_run_enrichment", lambda _conn: None)

    first = eod2.sync_eod2_data()
    assert first["rows_inserted"] == 5
    snapshot = _max_dates(technical_db)

    second = eod2.sync_eod2_data()
    assert second["rows_inserted"] == 0, second
    assert _max_dates(technical_db) == snapshot, "a rerun mutated stored data"


def test_older_history_backfill_does_not_regress_a_symbol(tmp_path, monkeypatch):
    """A file carrying older dates must not delete or rewind newer stored rows."""
    daily = _source(tmp_path, 3, OLD, meta=False)
    technical_db = _fresh_db(tmp_path)
    _seed(technical_db, ["SYM00", "SYM01", "SYM02"], date_value=NEW)
    monkeypatch.setattr(eod2, "_get_db_path", lambda: technical_db)
    monkeypatch.setattr(eod2, "_find_eod2_daily", lambda: daily)
    monkeypatch.setattr(eod2, "_get_meta_latest", lambda _d: NEW)
    monkeypatch.setattr(eod2, "_run_enrichment", lambda _conn: None)

    result = eod2.sync_eod2_data()
    assert result["rows_inserted"] == 0
    assert set(_max_dates(technical_db).values()) == {NEW}


# ---------------------------------------------------------------------------
# 3. Readiness assessment
# ---------------------------------------------------------------------------


def test_readiness_flags_a_half_copied_set(tmp_path):
    daily = _source(tmp_path, 10, OLD, meta=False)
    for i in range(2):  # only 2 of 10 advanced
        _write_csv(daily, "sym%02d.csv" % i, _csv(NEW))

    report = eod2.assess_source_readiness(daily, OLD)
    assert report["ready"] is False
    assert "partially copied" in report["reason"]
    assert report["last_date"] == NEW
    assert report["complete"] == 2
    assert report["total_files"] == 10


def test_readiness_accepts_a_complete_set(tmp_path):
    daily = _source(tmp_path, 10, NEW)
    report = eod2.assess_source_readiness(daily, OLD)
    assert report["ready"] is True
    assert report["complete"] == 10


def test_readiness_rejects_delivery_pending_source(tmp_path):
    daily = _source(tmp_path, 10, NEW, meta=False)
    with open(tmp_path / "meta.json", "w", encoding="utf-8") as handle:
        json.dump(
            {"lastUpdate": f"{NEW}T00:00:00+05:30", "DLV_PENDING_DATES": [NEW]},
            handle,
        )
    report = eod2.assess_source_readiness(daily, OLD)
    assert report["ready"] is False
    assert "delivery data pending" in report["reason"]
    assert report["delivery_pending"] == [NEW]


def test_readiness_rejects_files_still_being_written(tmp_path):
    """A file whose mtime is "now" is mid-copy and must not be trusted."""
    import time

    daily = _source(tmp_path, 10, NEW)
    path = os.path.join(daily, "sym00.csv")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(_csv(NEW))
    os.utime(path, (time.time(), time.time()))

    report = eod2.assess_source_readiness(daily, OLD, now=time.time())
    assert report["ready"] is False
    assert "still being written" in report["reason"]
    assert report["unstable_files"] == 1


def test_stability_check_flags_a_fresh_file(tmp_path):
    import time

    path = str(tmp_path / "x.csv")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("a")
    assert eod2._is_file_stable(path) is False
    old = time.time() - 600
    os.utime(path, (old, old))
    assert eod2._is_file_stable(path) is True
    assert eod2._is_file_stable(str(tmp_path / "missing.csv")) is False


def test_readiness_handles_an_empty_source(tmp_path):
    daily = tmp_path / "daily"
    daily.mkdir()
    report = eod2.assess_source_readiness(str(daily), OLD)
    assert report["ready"] is False
    assert report["last_date"] is None


def test_readiness_reports_source_not_newer(tmp_path):
    daily = _source(tmp_path, 4, OLD)
    report = eod2.assess_source_readiness(daily, NEW)
    assert report["ready"] is True
    assert "not newer" in report["reason"]


# ---------------------------------------------------------------------------
# 4. Malformed / empty input is reported, never silently damaging
# ---------------------------------------------------------------------------


def test_empty_and_malformed_files_are_counted_not_stored(tmp_path, monkeypatch):
    daily = _source(tmp_path, 3, NEW, meta=False)
    _write_csv(daily, "empty.csv", "")
    _write_csv(daily, "garbage.csv", "this is not,a valid\ncsv,at all\n")

    technical_db = _fresh_db(tmp_path)
    _seed(technical_db, ["SYM00", "SYM01", "SYM02"])
    monkeypatch.setattr(eod2, "_get_db_path", lambda: technical_db)
    monkeypatch.setattr(eod2, "_find_eod2_daily", lambda: daily)
    monkeypatch.setattr(eod2, "_get_meta_latest", lambda _d: NEW)
    monkeypatch.setattr(eod2, "_run_enrichment", lambda _conn: None)

    result = eod2.sync_eod2_data()
    assert result["rows_inserted"] == 3, result
    assert result["skipped"] == 2, result
    per_symbol = _max_dates(technical_db)
    assert "EMPTY" not in per_symbol
    assert "GARBAGE" not in per_symbol


def test_zero_close_row_is_rejected_not_stored(tmp_path, monkeypatch, technical_db):
    """Audit C1 behaviour must survive: a close<=0 row never reaches storage."""
    daily = _source(tmp_path, 2, NEW, meta=False)
    _write_csv(daily, "sym01.csv", _csv(NEW, close=0))

    monkeypatch.setattr(eod2, "_get_db_path", lambda: technical_db)
    monkeypatch.setattr(eod2, "_find_eod2_daily", lambda: daily)
    monkeypatch.setattr(eod2, "_get_meta_latest", lambda _d: NEW)
    monkeypatch.setattr(eod2, "_run_enrichment", lambda _conn: None)

    result = eod2.sync_eod2_data()
    assert result["rejected_rows"] >= 1, result

    import sqlite3

    conn = sqlite3.connect(f"file:{technical_db}?mode=ro", uri=True)
    try:
        stored = conn.execute(
            "SELECT COUNT(*) FROM technical_data WHERE symbol='SYM01' AND date=?",
            (NEW,),
        ).fetchone()[0]
        assert stored == 0, "a close<=0 row was written to technical_data"
    finally:
        conn.close()


def test_duplicate_dates_in_one_file_are_collapsed(tmp_path, monkeypatch, technical_db):
    """A file repeating the same session date must not create duplicate rows."""
    daily = tmp_path / "daily"
    daily.mkdir()
    _write_csv(
        str(daily),
        "sym00.csv",
        _HEADER
        + f"\n{NEW},100,101,99,100,1000,EQ,,,\n{NEW},100,101,99,100,1000,EQ,,,\n",
    )
    monkeypatch.setattr(eod2, "_get_db_path", lambda: technical_db)
    monkeypatch.setattr(eod2, "_find_eod2_daily", lambda: str(daily))
    monkeypatch.setattr(eod2, "_get_meta_latest", lambda _d: NEW)
    monkeypatch.setattr(eod2, "_run_enrichment", lambda _conn: None)

    eod2.sync_eod2_data()

    import sqlite3

    conn = sqlite3.connect(f"file:{technical_db}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT COUNT(*) FROM technical_data WHERE symbol='SYM00' AND date=?",
            (NEW,),
        ).fetchone()[0]
    finally:
        conn.close()
    assert rows == 1, f"duplicate session rows stored: {rows}"


# ---------------------------------------------------------------------------
# 5. Isolation: the sync must resolve its DB through DB_DIR
# ---------------------------------------------------------------------------


def test_sync_writes_only_to_the_redirected_database(fixture_db_dir, monkeypatch):
    """Regression guard for the near-miss this phase hit.

    ``eod2_sync`` binds ``DB_DIR`` by value at import, so a caller that patches
    only ``myra_app.constants.DB_DIR`` still writes to the *live* database. The
    real path is resolved through the module-level binding, which is what
    ``fixture_db_dir`` rebinds.
    """
    assert constants.DB_DIR == fixture_db_dir
    assert eod2.DB_DIR == fixture_db_dir, (
        "eod2_sync.DB_DIR must track the redirected DB_DIR, otherwise the sync "
        "writes to the live technical database"
    )
