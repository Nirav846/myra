"""Tests for the append-only point-in-time archive.

Guarantees under test:
  * The valuation DB is never written (mtime unchanged, opened read-only).
  * Re-running the same day is a no-op (idempotent, no duplicate rows).
  * An existing snapshot is never replaced by a nullier one.
  * Raw traction files are stored verbatim and deduped on SHA-256.
  * The archive lives in its own DB, not in myra_valuation.db.
"""

import json
import os
import sqlite3

import pytest

from myra_app import point_in_time_archive as pit
from myra_app.librarian_core import LibrarianCore

_FUNDAMENTALS_DDL = """
CREATE TABLE fundamentals (
    symbol TEXT PRIMARY KEY,
    pe REAL,
    roe REAL,
    market_cap REAL,
    sector TEXT,
    last_updated TEXT,
    source TEXT
)
"""


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """Temp DB_DIR with a real-shaped valuation DB, and a temp raw dir."""
    db_dir = tmp_path / "db"
    db_dir.mkdir()
    raw_dir = tmp_path / "archive" / "raw"

    val = db_dir / LibrarianCore.DB_MAP["valuation"]
    conn = sqlite3.connect(str(val))
    conn.execute(_FUNDAMENTALS_DDL)
    conn.executemany(
        "INSERT INTO fundamentals (symbol, pe, roe, market_cap, sector, "
        "last_updated, source) VALUES (?,?,?,?,?,?,?)",
        [
            ("AAA", 20.0, 0.15, 1e10, "Energy", "2026-09-01", "nse"),
            ("BBB", 30.0, 0.10, 2e10, "IT", "2026-09-01", "nse"),
            ("CCC", None, None, 3e9, "Pharma", "2026-09-01", "ms"),
        ],
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(pit, "DB_DIR", str(db_dir))
    monkeypatch.setattr(pit, "ARCHIVE_RAW_DIR", str(raw_dir))
    return {"db_dir": db_dir, "val": val, "raw_dir": raw_dir}


def _archive_rows(sandbox):
    conn = sqlite3.connect(str(sandbox["db_dir"] / "myra_archive.db"))
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute("SELECT * FROM fundamentals_snapshots")]
    conn.close()
    return rows


# ── fundamentals snapshots ──────────────────────────────────────────────────


def test_snapshots_written_to_separate_archive_db(sandbox):
    res = pit.archive_fundamentals(as_of_date="2026-09-28")

    assert res["success"] is True
    assert res["source_rows"] == 3
    assert res["rows_archived"] == 3
    assert res["rows_unchanged"] == 0

    assert os.path.exists(str(sandbox["db_dir"] / "myra_archive.db"))
    # Nothing new in the valuation DB.
    val_tables = {
        r[0]
        for r in sqlite3.connect(str(sandbox["val"])).execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    assert "fundamentals_snapshots" not in val_tables


def test_valuation_db_never_written(sandbox):
    before = os.path.getmtime(str(sandbox["val"]))
    pit.archive_fundamentals(as_of_date="2026-09-28")
    after = os.path.getmtime(str(sandbox["val"]))
    assert before == after


def test_rerun_same_day_is_idempotent(sandbox):
    first = pit.archive_fundamentals(as_of_date="2026-09-28")
    assert first["rows_archived"] == 3

    second = pit.archive_fundamentals(as_of_date="2026-09-28")
    assert second["rows_archived"] == 0
    assert second["rows_unchanged"] == 3
    assert second["success"] is True

    rows = _archive_rows(sandbox)
    assert len(rows) == 3
    assert {r["as_of_date"] for r in rows} == {"2026-09-28"}


def test_new_day_appends_without_touching_history(sandbox):
    pit.archive_fundamentals(as_of_date="2026-09-28")

    # Simulate the rolling snapshot advancing: CCC gains real values.
    conn = sqlite3.connect(str(sandbox["val"]))
    conn.execute("UPDATE fundamentals SET pe=5.0, roe=0.2 WHERE symbol='CCC'")
    conn.commit()
    conn.close()

    second = pit.archive_fundamentals(as_of_date="2026-09-29")
    assert second["rows_archived"] == 3

    rows = _archive_rows(sandbox)
    assert len(rows) == 6
    by = {(r["symbol"], r["as_of_date"]): json.loads(r["row_json"]) for r in rows}
    # History is immutable...
    assert by[("CCC", "2026-09-28")]["pe"] is None
    # ...and the new day captured the improvement.
    assert by[("CCC", "2026-09-29")]["pe"] == 5.0


def test_null_richer_row_never_clobbers_existing(sandbox):
    """A later same-day run cannot downgrade a snapshot to nulls."""
    pit.archive_fundamentals(as_of_date="2026-09-28")
    good = {r["symbol"]: r for r in _archive_rows(sandbox)}

    # Wipe values in the source, then re-run the SAME day.
    conn = sqlite3.connect(str(sandbox["val"]))
    conn.execute("UPDATE fundamentals SET pe=NULL, roe=NULL, market_cap=NULL")
    conn.commit()
    conn.close()

    res = pit.archive_fundamentals(as_of_date="2026-09-28")
    assert res["rows_archived"] == 0
    assert res["rows_unchanged"] == 3

    after = {r["symbol"]: r for r in _archive_rows(sandbox)}
    for sym in good:
        assert json.loads(after[sym]["row_json"]) == json.loads(good[sym]["row_json"])


def test_row_json_is_lossless(sandbox):
    """The archived row must reproduce the source row exactly."""
    pit.archive_fundamentals(as_of_date="2026-09-28")
    rows = {r["symbol"]: r for r in _archive_rows(sandbox)}

    conn = sqlite3.connect(str(sandbox["val"]))
    conn.row_factory = sqlite3.Row
    src = {r["symbol"]: dict(r) for r in conn.execute("SELECT * FROM fundamentals")}
    conn.close()

    for sym, src_row in src.items():
        assert json.loads(rows[sym]["row_json"]) == src_row


# ── raw traction capture ────────────────────────────────────────────────────


class _Resp:
    def __init__(self, content):
        self.content = content

    def raise_for_status(self):
        return None


def test_raw_file_stored_verbatim_and_manifest_recorded(sandbox, monkeypatch):
    blob = json.dumps({"stocks": [{"nse": "AAA", "score": 1.5}]}).encode()

    monkeypatch.setattr(
        pit.requests,
        "get",
        lambda url, timeout=30: _Resp(blob),
    )

    res = pit.archive_traction_raw(fetched_at="2026-09-28", months=["2026-07"])
    assert res["success"] is True
    assert res["files_archived"] == 1
    assert res["files_unchanged"] == 0

    day_dir = os.path.join(str(sandbox["raw_dir"]), "2026-09-28")
    raw_path = os.path.join(day_dir, "july_traction.json")
    assert os.path.exists(raw_path)
    # Byte-for-byte, no re-serialisation.
    with open(raw_path, "rb") as fh:
        assert fh.read() == blob

    conn = sqlite3.connect(str(sandbox["db_dir"] / "myra_archive.db"))
    conn.row_factory = sqlite3.Row
    row = dict(conn.execute("SELECT * FROM raw_traction_archive").fetchone())
    conn.close()
    assert row["source_month"] == "2026-07"
    assert row["symbol_count"] == 1
    assert row["byte_size"] == len(blob)
    assert row["fetched_at"] == "2026-09-28"
    assert row["raw_path"].endswith("july_traction.json")


def test_raw_rerun_same_content_is_skipped(sandbox, monkeypatch):
    blob = json.dumps({"stocks": [{"nse": "AAA", "score": 1.5}]}).encode()
    monkeypatch.setattr(pit.requests, "get", lambda url, timeout=30: _Resp(blob))

    first = pit.archive_traction_raw(fetched_at="2026-09-28", months=["2026-07"])
    assert first["files_archived"] == 1

    second = pit.archive_traction_raw(fetched_at="2026-09-29", months=["2026-07"])
    assert second["files_archived"] == 0
    assert second["files_unchanged"] == 1

    conn = sqlite3.connect(str(sandbox["db_dir"] / "myra_archive.db"))
    n = conn.execute("SELECT COUNT(*) FROM raw_traction_archive").fetchone()[0]
    conn.close()
    assert n == 1


def test_raw_changed_content_is_appended(sandbox, monkeypatch):
    v1 = json.dumps({"stocks": [{"nse": "AAA", "score": 1.5}]}).encode()
    monkeypatch.setattr(pit.requests, "get", lambda url, timeout=30: _Resp(v1))
    pit.archive_traction_raw(fetched_at="2026-09-28", months=["2026-07"])

    v2 = json.dumps({"stocks": [{"nse": "AAA", "score": 9.9}]}).encode()
    monkeypatch.setattr(pit.requests, "get", lambda url, timeout=30: _Resp(v2))
    res = pit.archive_traction_raw(fetched_at="2026-09-29", months=["2026-07"])
    assert res["files_archived"] == 1

    conn = sqlite3.connect(str(sandbox["db_dir"] / "myra_archive.db"))
    n = conn.execute("SELECT COUNT(*) FROM raw_traction_archive").fetchone()[0]
    conn.close()
    assert n == 2


def test_raw_missing_month_reports_error(sandbox, monkeypatch):
    def boom(url, timeout=30):
        raise ConnectionError("upstream down")

    monkeypatch.setattr(pit.requests, "get", boom)

    res = pit.archive_traction_raw(fetched_at="2026-09-28", months=["2026-07"])
    assert res["success"] is False
    assert "upstream down" in res["error"]
    assert res["files_archived"] == 0


def test_count_symbols_schema_agnostic():
    assert pit._count_symbols([1, 2, 3]) == 3
    assert pit._count_symbols({"stocks": [1, 2]}) == 2
    assert pit._count_symbols({"other": 5}) is None
    assert pit._count_symbols("nonsense") is None


# ── combined entry point ────────────────────────────────────────────────────


def test_run_archive_returns_both(sandbox, monkeypatch):
    blob = json.dumps({"stocks": []}).encode()
    monkeypatch.setattr(pit.requests, "get", lambda url, timeout=30: _Resp(blob))

    res = pit.run_archive(as_of_date="2026-09-28")
    assert res["fundamentals"]["success"] is True
    assert res["raw"]["success"] is True
