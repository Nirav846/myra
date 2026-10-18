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
    """Regression guard for the near-miss this phase hit, and for Phase 10.

    ``eod2_sync`` used to bind ``DB_DIR`` by value at import, so a caller that
    patched only ``myra_app.constants.DB_DIR`` still wrote to the *live*
    database. It now resolves ``DB_DIR`` at call time, so the returned path
    follows the redirect rather than a stale snapshot.
    """
    assert constants.DB_DIR == fixture_db_dir
    resolved = eod2._get_db_path()
    assert resolved.startswith(
        os.path.abspath(fixture_db_dir)
    ), f"eod2 resolves its database outside the redirect: {resolved}"


# ---------------------------------------------------------------------------
# 6. Phase 20 (defect D1): derived columns must roll over the symbol's history
# ---------------------------------------------------------------------------


def _seed_history(technical_db, symbol, n_rows=60, end_date=OLD):
    """Store ``n_rows`` daily sessions for ``symbol`` ending at ``end_date``.

    Closes ascend 100.. so every rolling statistic over the series is a value
    the new session's own price can never reproduce. Delivery is held at 50% of
    volume so the 60-day delivery mean is likewise distinct.
    """
    import sqlite3

    import pandas as pd

    dates = pd.bdate_range(end=end_date, periods=n_rows).strftime("%Y-%m-%d")
    conn = sqlite3.connect(technical_db)
    try:
        for i, d in enumerate(dates):
            close = 100.0 + i
            conn.execute(
                "INSERT OR IGNORE INTO technical_data "
                "(symbol, date, open, high, low, close, volume, delivery) "
                "VALUES (?, ?, ?, ?, ?, ?, 1000, 500)",
                (symbol, d, close, close + 5, close - 5, close),
            )
        conn.commit()
    finally:
        conn.close()
    return dates


def test_one_new_session_rolls_over_history_not_the_new_row(tmp_path, monkeypatch):
    """A one-row daily sync must compute a real SMA / 52-week / 60-day series.

    Phase 20 defect D1. ``_compute_derived`` used to be handed only the rows
    newer than the per-symbol cutoff, so a normal one-session sync took a
    ``len(df) < 2`` branch that stored the raw session values in place of every
    rolling column: ``sma_50 == close``, ``high_52w == high``,
    ``low_52w == low``, ``delivery_ma_60 == delivery_pct``. That happened for
    the *whole universe* on 12 sessions in 2026-08..2026-10, because the
    enrichment repair only ever touches the latest date.

    The new session here is priced far below its own 60-session history and
    delivers far less, so every rolling column has a value the new row's own
    fields cannot match.
    """
    import sqlite3

    # A private DB: the shared fixture accumulates rows from other tests, and a
    # symbol already stored at NEW would leave this run with nothing to insert.
    technical_db = _fresh_db(tmp_path)
    history = _seed_history(technical_db, "SYM00", n_rows=60, end_date=OLD)
    assert len(history) == 60
    # The history ends at OLD, so the per-symbol cutoff is OLD and the CSV below
    # contributes exactly one new row -- the production shape.
    daily = tmp_path / "daily"
    daily.mkdir()
    _write_csv(str(daily), "sym00.csv", _csv(NEW, close=97, delivery=600))

    monkeypatch.setattr(eod2, "_get_db_path", lambda: technical_db)
    monkeypatch.setattr(eod2, "_find_eod2_daily", lambda: daily)
    monkeypatch.setattr(eod2, "_get_meta_latest", lambda _d: NEW)
    # The enrichment repair is out of scope here: it only ever writes the latest
    # date, so it cannot be what saves a session that is not the latest one.
    monkeypatch.setattr(eod2, "_run_enrichment", lambda _conn: None)

    result = eod2.sync_eod2_data()
    assert result["rows_inserted"] == 1, result

    conn = sqlite3.connect(f"file:{technical_db}?mode=ro", uri=True)
    try:
        row = conn.execute(
            "SELECT date, high, low, close, volume, delivery FROM technical_data"
            " WHERE symbol='SYM00' ORDER BY date",
        ).fetchall()
        stored = conn.execute(
            "SELECT close, sma_50, sma_200, high, high_52w, low, low_52w,"
            " delivery_pct, delivery_ma_60 FROM technical_data"
            " WHERE symbol='SYM00' AND date=?",
            (NEW,),
        ).fetchone()
    finally:
        conn.close()
    assert len(row) == 61, f"expected 61 sessions, got {len(row)}"
    assert stored is not None, "the new session was not stored"
    (
        close,
        sma_50,
        sma_200,
        high,
        high_52w,
        low,
        low_52w,
        d_pct,
        dm60,
    ) = stored

    # The fabricated-value shape, asserted against directly: none of the rolling
    # columns may equal the session's own field.
    assert sma_50 != close, f"sma_50 was stored as the session close ({sma_50})"
    assert high_52w != high, f"high_52w was stored as the session high ({high_52w})"
    assert low_52w != low, f"low_52w was stored as the session low ({low_52w})"
    assert dm60 != d_pct, f"delivery_ma_60 was stored as delivery_pct ({dm60})"
    assert sma_200 is not None, "sma_200 was left NULL by the sync"

    # Recompute the rolling series independently, over the same 61 sessions the
    # sync saw, and require the stored values to match.
    import pandas as pd

    frame = pd.DataFrame(
        row, columns=["date", "high", "low", "close", "volume", "delivery"]
    )
    vol = frame["volume"].replace(0, pd.NA).astype("float64")
    d_pct_series = (frame["delivery"] / vol * 100).fillna(0)
    expected = {
        "sma_50": frame["close"].rolling(50, min_periods=1).mean().iloc[-1],
        "sma_200": frame["close"].rolling(200, min_periods=1).mean().iloc[-1],
        "high_52w": frame["high"].rolling(252, min_periods=1).max().iloc[-1],
        "low_52w": frame["low"].rolling(252, min_periods=1).min().iloc[-1],
        "delivery_ma_60": d_pct_series.rolling(60, min_periods=1).mean().iloc[-1],
    }
    got = {
        "sma_50": sma_50,
        "sma_200": sma_200,
        "high_52w": high_52w,
        "low_52w": low_52w,
        "delivery_ma_60": dm60,
    }
    for col, want in expected.items():
        assert got[col] == pytest.approx(want, rel=1e-9), f"{col}: {got[col]} != {want}"


def test_first_ever_ingest_has_no_history_to_roll_over(tmp_path, monkeypatch):
    """A symbol with no stored rows still ingests; nothing is fabricated."""
    technical_db = _fresh_db(tmp_path)
    daily = _source(tmp_path, 1, NEW, meta=False)

    monkeypatch.setattr(eod2, "_get_db_path", lambda: technical_db)
    monkeypatch.setattr(eod2, "_find_eod2_daily", lambda: daily)
    monkeypatch.setattr(eod2, "_get_meta_latest", lambda _d: NEW)
    monkeypatch.setattr(eod2, "_run_enrichment", lambda _conn: None)

    result = eod2.sync_eod2_data()
    assert result["rows_inserted"] == 1, result
    per_symbol = _max_dates(technical_db)
    assert per_symbol["SYM00"] == NEW


def test_load_history_tail_returns_oldest_first_and_honours_the_limit(tmp_path):
    technical_db = _fresh_db(tmp_path)

    import sqlite3

    conn = sqlite3.connect(technical_db)
    try:
        # Nothing stored yet, so there is no history to roll over.
        assert eod2._load_history_tail(conn, "SYM00") is None
        assert eod2._load_history_tail(conn, "NOSUCH") is None
    finally:
        conn.close()

    _seed_history(technical_db, "SYM00", n_rows=40, end_date=OLD)

    conn = sqlite3.connect(f"file:{technical_db}?mode=ro", uri=True)
    try:
        tail = eod2._load_history_tail(conn, "SYM00", limit=10)
        assert tail is not None and len(tail) == 10, tail
        assert tail["date"].is_monotonic_increasing, "history tail is not oldest-first"
        assert tail["date"].iloc[-1] == OLD, tail["date"].iloc[-1]
    finally:
        conn.close()


def test_a_narrower_table_degrades_loudly_instead_of_losing_the_session(
    tmp_path, monkeypatch, caplog
):
    """A table missing a written column must still store the session's OHLCV.

    ``technical_data`` is normally the live table, which carries every column.
    A narrower table makes every INSERT raise ``no column named ...``; the
    per-symbol handler counts that as one skipped symbol, so the sync would
    report a completed run having stored *nothing* for the whole universe.
    The absent columns are dropped with a warning instead.
    """
    import logging
    import sqlite3

    technical_db = str(tmp_path / "narrow.db")
    conn = sqlite3.connect(technical_db)
    try:
        # Deliberately missing the Phase 20 addition.
        conn.execute(
            "CREATE TABLE technical_data (symbol TEXT, date TEXT, open REAL, "
            "high REAL, low REAL, close REAL, volume REAL, delivery REAL, "
            "trades REAL, delivery_pct REAL, delivery_ratio REAL, "
            "delivery_source TEXT, sma_50 REAL, high_52w REAL, low_52w REAL, "
            "delivery_ma_60 REAL, PRIMARY KEY(symbol, date))"
        )
        conn.execute(
            "CREATE TABLE ingestion_rejects (id INTEGER PRIMARY KEY, symbol TEXT, "
            "date TEXT, reason TEXT, raw_values TEXT)"
        )
        conn.commit()
    finally:
        conn.close()

    daily = tmp_path / "daily"
    daily.mkdir()
    _write_csv(str(daily), "sym00.csv", _csv(NEW))

    monkeypatch.setattr(eod2, "_get_db_path", lambda: technical_db)
    monkeypatch.setattr(eod2, "_find_eod2_daily", lambda: str(daily))
    monkeypatch.setattr(eod2, "_get_meta_latest", lambda _d: NEW)
    monkeypatch.setattr(eod2, "_run_enrichment", lambda _conn: None)

    with caplog.at_level(logging.WARNING, logger="myra_app.eod2_sync"):
        result = eod2.sync_eod2_data()

    assert result["rows_inserted"] == 1, result
    assert "sma_200" in caplog.text, "the dropped column was not reported"

    conn = sqlite3.connect(f"file:{technical_db}?mode=ro", uri=True)
    try:
        row = conn.execute(
            "SELECT close, sma_50 FROM technical_data WHERE symbol='SYM00' AND date=?",
            (NEW,),
        ).fetchone()
    finally:
        conn.close()
    assert row is not None, "the session was lost when a column was missing"
    assert row[0] == 100.0
