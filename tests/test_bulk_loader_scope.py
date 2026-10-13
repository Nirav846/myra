"""Batch-scoped shared-snapshot tests for the bulk OHLCV loader.

These exercise ``myra_app.db.bulk_loader.batch_snapshot_scope`` against a small
synthetic ``technical_data`` DB, so the default run never touches the live
2 GB sidecar:

* a batch's overlapping ``load_ohlcv_for_universe`` calls collapse to one read;
* anything served from the snapshot is identical to a direct DB read;
* requests outside the loaded window widen it and stay correct + bounded;
* callers outside a scope (concurrent standalone scans) are untouched;
* load failures propagate unchanged and the snapshot is released at the
  boundary, so a later batch re-reads updated source data.

``TestLiveParity`` is the only test that touches the real DB; it skips when the
table has no rows rather than pretending the parity assertion passed.
"""

import os
import sqlite3
from datetime import datetime, timedelta

import pandas as pd
import pytest

from myra_app.constants import DB_DIR
from myra_app.db import bulk_loader
from myra_app.librarian_core import LibrarianCore

AS_ON = "2026-06-30"
_SYMBOLS = ("AAA", "BBB", "CCC")
_ALL_COLUMNS = (
    "symbol",
    "date",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "delivery",
    "delivery_pct",
    "nifty_outperformance_score",
    "sma_50",
    "high_52w",
    "low_52w",
    "swing_low",
)


def _build_db(path: str) -> None:
    """Create a miniature technical DB with the real base schema."""
    dates = pd.date_range("2025-06-01", AS_ON, freq="D").strftime("%Y-%m-%d").tolist()
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE technical_data ("
            "symbol TEXT, date TEXT, open REAL, high REAL, low REAL, close REAL, "
            "volume REAL, delivery REAL, delivery_pct REAL, "
            "nifty_outperformance_score REAL, sma_50 REAL, high_52w REAL, "
            "low_52w REAL, swing_low REAL)"
        )
        rows = []
        for si, sym in enumerate(_SYMBOLS):
            for di, day in enumerate(dates):
                base = 100.0 + si * 10 + di * 0.5
                rows.append(
                    (
                        sym,
                        day,
                        base,
                        base + 1.0,
                        base - 1.0,
                        base + 0.2,
                        1000.0 + di,
                        100.0 + di,
                        50.0 + di,
                        # NaN in a float column must survive the snapshot slice.
                        None if di % 7 == 0 else 1.5,
                        base,
                        base + 5.0,
                        base - 5.0,
                        # Entirely NULL for CCC: a narrow query infers object for
                        # the whole column, the wide snapshot infers float64.
                        None if sym == "CCC" else base - 2.0,
                    )
                )
        conn.executemany(
            "INSERT INTO technical_data ("
            + ", ".join(_ALL_COLUMNS)
            + ") VALUES ("
            + ", ".join("?" for _ in _ALL_COLUMNS)
            + ")",
            rows,
        )
        conn.commit()


@pytest.fixture()
def tech_db(tmp_path, monkeypatch):
    path = tmp_path / "myra_technical.db"
    _build_db(str(path))
    monkeypatch.setattr(bulk_loader, "_tech_db_path", lambda: str(path))
    return str(path)


@pytest.fixture()
def reads(monkeypatch):
    """Count real DB reads by wrapping the unshared read path."""
    counter: list[int] = []
    original = bulk_loader._read_bulk

    def counting(start_date, end_date, symbols):
        counter.append(1)
        return original(start_date, end_date, symbols)

    monkeypatch.setattr(bulk_loader, "_read_bulk", counting)
    return counter


def _direct(start, end, symbols=None):
    return bulk_loader._read_bulk(start, end, symbols)


def _assert_equal(actual, expected):
    assert set(actual) == set(expected)
    for symbol, frame in expected.items():
        pd.testing.assert_frame_equal(actual[symbol], frame)


# --- load de-duplication ---------------------------------------------------


def test_prewarm_serves_all_overlapping_scanners_from_one_read(tech_db, reads):
    with bulk_loader.batch_snapshot_scope(load_all_symbols=True) as scope:
        scope.prewarm("2025-06-01", AS_ON)
        # Different windows and different symbol filters, all inside the
        # pre-warmed union -- exactly the confluence batch's access pattern.
        bulk_loader.load_ohlcv_for_universe("2026-05-16", AS_ON, ["AAA"])
        bulk_loader.load_ohlcv_for_universe("2026-04-01", AS_ON)
        bulk_loader.load_ohlcv_for_universe("2025-07-01", AS_ON, ["BBB", "CCC"])
        assert len(reads) == 1


def test_full_snapshot_serves_filtered_requests_without_reload(tech_db, reads):
    with bulk_loader.batch_snapshot_scope(load_all_symbols=True) as scope:
        scope.prewarm("2025-06-01", AS_ON)
        everything = bulk_loader.load_ohlcv_for_universe("2025-06-01", AS_ON)
        single = bulk_loader.load_ohlcv_for_universe("2025-06-01", AS_ON, ["BBB"])
        assert len(reads) == 1
    assert set(everything) == set(_SYMBOLS)
    assert set(single) == {"BBB"}


def test_partial_snapshot_widens_symbols_as_needed(tech_db, reads):
    with bulk_loader.batch_snapshot_scope(load_all_symbols=False) as scope:
        one = bulk_loader.load_ohlcv_for_universe("2025-06-01", AS_ON, ["AAA"])
        assert len(reads) == 1
        assert set(scope.snapshot.data) == {"AAA"}
        pair = bulk_loader.load_ohlcv_for_universe("2025-06-01", AS_ON, ["AAA", "BBB"])
        # The snapshot only held AAA, so BBB forces exactly one widened reload.
        assert len(reads) == 2
        assert set(scope.snapshot.data) == {"AAA", "BBB"}
    _assert_equal(one, _direct("2025-06-01", AS_ON, ["AAA"]))
    _assert_equal(pair, _direct("2025-06-01", AS_ON, ["AAA", "BBB"]))


def test_request_outside_prewarm_widens_dates_and_matches(tech_db, reads):
    with bulk_loader.batch_snapshot_scope(load_all_symbols=True) as scope:
        scope.prewarm("2026-05-01", AS_ON)
        bulk_loader.load_ohlcv_for_universe("2026-05-10", AS_ON, ["AAA"])
        assert len(reads) == 1
        wider = bulk_loader.load_ohlcv_for_universe("2025-07-01", AS_ON)
        assert len(reads) == 2
    _assert_equal(wider, _direct("2025-07-01", AS_ON))
    assert wider["AAA"]["date"].min() >= "2025-07-01"


# --- correctness ----------------------------------------------------------


def test_snapshot_output_is_identical_to_a_direct_read(tech_db):
    cases = [
        ("2026-05-16", AS_ON, ["AAA"]),
        ("2026-04-01", AS_ON, None),
        ("2025-07-01", AS_ON, ["BBB", "CCC"]),
    ]
    with bulk_loader.batch_snapshot_scope(load_all_symbols=True) as scope:
        scope.prewarm("2025-06-01", AS_ON)
        for start, end, symbols in cases:
            _assert_equal(
                bulk_loader.load_ohlcv_for_universe(start, end, symbols),
                _direct(start, end, symbols),
            )


def test_narrow_request_returns_only_the_requested_window(tech_db):
    with bulk_loader.batch_snapshot_scope() as scope:
        scope.prewarm("2025-06-01", AS_ON)
        out = bulk_loader.load_ohlcv_for_universe("2026-06-01", "2026-06-10", ["AAA"])
    frame = out["AAA"]
    assert frame["date"].min() >= "2026-06-01"
    assert frame["date"].max() <= "2026-06-10"
    assert len(frame) == 10


def test_unknown_symbol_returns_empty_within_scope(tech_db):
    with bulk_loader.batch_snapshot_scope() as scope:
        scope.prewarm("2025-06-01", AS_ON)
        assert bulk_loader.load_ohlcv_for_universe("2025-06-01", AS_ON, ["ZZZ"]) == {}


def test_all_null_column_dtype_matches_a_direct_read(tech_db):
    # CCC's swing_low is NULL for every row, so a direct read of just CCC infers
    # object/None for that column while the wider snapshot is float64. The slice
    # must reproduce the narrow query, or scanners would see a different dtype.
    with bulk_loader.batch_snapshot_scope() as scope:
        scope.prewarm("2025-06-01", AS_ON)
        out = bulk_loader.load_ohlcv_for_universe("2025-06-01", AS_ON, ["CCC"])
    _assert_equal(out, _direct("2025-06-01", AS_ON, ["CCC"]))
    assert out["CCC"]["swing_low"].dtype == object
    assert out["CCC"]["swing_low"].isna().all()


def test_absent_symbol_in_window_is_omitted_like_direct_read(tech_db):
    # CCC has no rows before 2025-06-01; a window entirely before its data must
    # omit the key rather than emit an empty frame.
    with bulk_loader.batch_snapshot_scope() as scope:
        scope.prewarm("2025-06-01", AS_ON)
        out = bulk_loader.load_ohlcv_for_universe("2025-01-01", "2025-01-31")
    assert out == _direct("2025-01-01", "2025-01-31") == {}


def test_returned_frames_do_not_alias_the_snapshot(tech_db):
    with bulk_loader.batch_snapshot_scope() as scope:
        scope.prewarm("2025-06-01", AS_ON)
        first = bulk_loader.load_ohlcv_for_universe("2025-06-01", AS_ON, ["AAA"])
        first["AAA"].loc[:, "close"] = -1.0
        first["AAA"].drop(first["AAA"].index, inplace=True)
        first.pop("AAA")
        second = bulk_loader.load_ohlcv_for_universe("2025-06-01", AS_ON, ["AAA"])
        third = bulk_loader.load_ohlcv_for_universe("2025-06-01", AS_ON, ["AAA"])
    assert second["AAA"] is not third["AAA"]
    _assert_equal(second, _direct("2025-06-01", AS_ON, ["AAA"]))


# --- lifecycle / errors ---------------------------------------------------


def test_outside_a_scope_every_call_hits_the_db(tech_db, reads):
    bulk_loader.load_ohlcv_for_universe("2026-05-16", AS_ON, ["AAA"])
    bulk_loader.load_ohlcv_for_universe("2026-05-16", AS_ON, ["AAA"])
    assert len(reads) == 2


def test_snapshot_is_released_when_the_scope_exits(tech_db, reads):
    with bulk_loader.batch_snapshot_scope() as scope:
        scope.prewarm("2025-06-01", AS_ON)
        bulk_loader.load_ohlcv_for_universe("2026-05-01", AS_ON)
    assert len(reads) == 1
    assert getattr(bulk_loader._local, "scope", None) is None
    bulk_loader.load_ohlcv_for_universe("2026-05-01", AS_ON)
    assert len(reads) == 2


def test_snapshot_is_released_even_when_the_body_raises(tech_db, reads):
    with pytest.raises(RuntimeError):
        with bulk_loader.batch_snapshot_scope() as scope:
            scope.prewarm("2025-06-01", AS_ON)
            raise RuntimeError("boom")
    assert getattr(bulk_loader._local, "scope", None) is None
    bulk_loader.load_ohlcv_for_universe("2026-05-01", AS_ON)
    assert len(reads) == 2


def test_nested_scope_restores_the_outer_snapshot(tech_db, reads):
    with bulk_loader.batch_snapshot_scope() as outer:
        outer.prewarm("2025-06-01", AS_ON)
        with bulk_loader.batch_snapshot_scope(load_all_symbols=False):
            bulk_loader.load_ohlcv_for_universe("2026-05-01", AS_ON, ["AAA"])
        before = len(reads)
        bulk_loader.load_ohlcv_for_universe("2026-05-01", AS_ON, ["AAA"])
        assert len(reads) == before  # served from the restored outer snapshot


def test_load_failure_propagates_and_keeps_the_previous_snapshot(tech_db, monkeypatch):
    with bulk_loader.batch_snapshot_scope() as scope:
        scope.prewarm("2025-06-01", AS_ON)

        def boom(start_date, end_date, symbols):
            raise sqlite3.OperationalError("db is locked")

        monkeypatch.setattr(bulk_loader, "_read_bulk", boom)
        with pytest.raises(sqlite3.OperationalError):
            bulk_loader.load_ohlcv_for_universe("2020-01-01", AS_ON)
        # The failed widen must not have published a partial snapshot.
        assert scope.snapshot.start == "2025-06-01"
        assert scope.snapshot.end == AS_ON

        # A covered request still works off the intact snapshot.
        assert set(
            bulk_loader.load_ohlcv_for_universe("2025-06-01", AS_ON, ["AAA"])
        ) == {"AAA"}


def test_new_batch_observes_updated_source_data(tech_db):
    with bulk_loader.batch_snapshot_scope() as scope:
        scope.prewarm("2025-06-01", AS_ON)
        before = bulk_loader.load_ohlcv_for_universe("2025-06-01", AS_ON, ["AAA"])

    with sqlite3.connect(tech_db) as conn:
        conn.execute(
            "INSERT INTO technical_data (symbol, date, close) "
            "VALUES ('AAA', '2026-07-01', 999.0)"
        )
        conn.commit()

    with bulk_loader.batch_snapshot_scope() as scope:
        scope.prewarm("2025-06-01", "2026-07-01")
        after = bulk_loader.load_ohlcv_for_universe("2025-06-01", "2026-07-01", ["AAA"])
    assert "2026-07-01" not in set(before["AAA"]["date"])
    assert "2026-07-01" in set(after["AAA"]["date"])


# --- the real batch wiring ------------------------------------------------

_RECORDED: list[tuple[str, int]] = []
_WINDOWS = {"A": (45, ["AAA"]), "B": (90, None), "C": (500, ["BBB", "CCC"])}


def _fake_scan(name):
    days_back, symbols = _WINDOWS[name]

    def scan(self, as_on_date=None):
        start = (
            datetime.strptime(as_on_date, "%Y-%m-%d") - timedelta(days=days_back)
        ).strftime("%Y-%m-%d")
        data = bulk_loader.load_ohlcv_for_universe(start, as_on_date, symbols)
        _RECORDED.append((name, sum(len(frame) for frame in data.values())))
        return [{"symbol": name}]

    return scan


class _FakeA:
    scan = _fake_scan("A")


class _FakeB:
    scan = _fake_scan("B")


class _FakeC:
    scan = _fake_scan("C")


def test_confluence_batch_shares_one_snapshot_across_scanners(
    tech_db, reads, monkeypatch, tmp_path
):
    import myra_web.confluence_batch as cb

    _RECORDED.clear()
    classes = {"fake:A": _FakeA, "fake:B": _FakeB, "fake:C": _FakeC}
    monkeypatch.setattr(
        cb,
        "CONFLUENCE_SCANNERS",
        [cb.ScannerSpec(name, f"fake:{name}") for name in ("A", "B", "C")],
    )
    monkeypatch.setattr(cb, "_resolve", lambda target: classes[target])
    monkeypatch.setattr(cb, "_get_latest_trading_day_before", lambda day: AS_ON)

    out = cb.run_confluence_batch(as_on_date=AS_ON, out_path=str(tmp_path / "s.json"))

    assert out["scanners_ok"] == 3
    assert out["scanners_failed"] == 0
    # Three scanners with overlapping windows, one shared DB read.
    assert len(reads) == 1

    # Every scanner still received exactly the rows a direct read would give it.
    expected = []
    for name in ("A", "B", "C"):
        days_back, symbols = _WINDOWS[name]
        start = (
            datetime.strptime(AS_ON, "%Y-%m-%d") - timedelta(days=days_back)
        ).strftime("%Y-%m-%d")
        data = bulk_loader.load_ohlcv_for_universe(start, AS_ON, symbols)
        expected.append((name, sum(len(frame) for frame in data.values())))
    assert _RECORDED == expected


# --- live-DB parity (skips when the table is empty, never silently passes) --

_LIVE_TECH_DB = os.path.join(DB_DIR, LibrarianCore.DB_MAP["technical"])


def _live_row_count() -> int:
    if not os.path.exists(_LIVE_TECH_DB):
        return 0
    try:
        with sqlite3.connect(f"file:{_LIVE_TECH_DB}?mode=ro", uri=True) as conn:
            return int(
                conn.execute("SELECT COUNT(*) FROM technical_data").fetchone()[0]
            )
    except sqlite3.Error:
        return 0


_LIVE_ROWS = _live_row_count()


@pytest.mark.skipif(_LIVE_ROWS == 0, reason="live technical DB has no rows")
class TestLiveParity:
    """Same assertions as above, against the real schema and real data."""

    def test_scope_output_matches_direct_read_on_real_data(self):
        end = bulk_loader.latest_ohlcv_date()
        assert end is not None
        start = (datetime.strptime(end, "%Y-%m-%d") - timedelta(days=120)).strftime(
            "%Y-%m-%d"
        )
        short = (datetime.strptime(end, "%Y-%m-%d") - timedelta(days=30)).strftime(
            "%Y-%m-%d"
        )

        with bulk_loader.batch_snapshot_scope(load_all_symbols=True) as scope:
            scope.prewarm(start, end)
            everything = bulk_loader.load_ohlcv_for_universe(short, end)
            assert everything, "live snapshot returned no rows"
            one_symbol = sorted(everything)[0]
            single = bulk_loader.load_ohlcv_for_universe(start, end, [one_symbol])

        _assert_equal(everything, _direct(short, end))
        _assert_equal(single, _direct(start, end, [one_symbol]))
