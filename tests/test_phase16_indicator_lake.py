"""Phase 16: the indicator-lake contract — probe, atomic write, producer.

``data/indicators/precomputed`` is the artifact the CLI/turbo screening path
reads. It was empty in production while readiness kept reporting ``ready``, and
three properties made that both possible and invisible:

* listing a directory says nothing about whether its contents are usable;
* a half-written artifact loads as if it were a healthy one;
* the producer raised out of a *read* path instead of returning the empty frame
  its callers were already written to handle.

``constants.DATA_DIR`` is redirected in every test here, so nothing can read or
write the real lake.
"""

import os
import threading
from datetime import datetime, timedelta
from types import SimpleNamespace

import pandas as pd
import pytest

import myra_app.constants as constants
import myra_app.indicator_lake as lake
from myra_app.librarian_intelligence import LibrarianIntelligenceMixin

DATE = "2026-01-02"


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    """Redirect DATA_DIR so the real lake is untouchable from this module."""
    dest = tmp_path / "data"
    dest.mkdir()
    monkeypatch.setattr(constants, "DATA_DIR", str(dest))
    return str(dest)


def _future_date() -> str:
    """A reference date no artifact can be newer than."""
    return (datetime.now() + timedelta(days=1)).date().isoformat()


def _frame(symbol: str = "AAA", rows: int = 3) -> pd.DataFrame:
    """A minimal artifact that satisfies the consumer contract."""
    frame = pd.DataFrame(
        {
            "symbol": [symbol] * rows,
            "close": [100.0] * rows,
            "rsi": [55.0] * rows,
        },
        index=pd.to_datetime(["2026-01-0%d" % (i + 1) for i in range(rows)]),
    )
    frame.index.name = "date"
    return frame


def _write(symbol: str, *, mtime: float = None, frame: pd.DataFrame = None) -> str:
    directory = lake.lake_dir()
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, f"{symbol}.parquet")
    (frame if frame is not None else _frame(symbol)).to_parquet(
        path, compression="snappy", index=True
    )
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def _ohlcv(rows: int = 260) -> pd.DataFrame:
    """Synthetic OHLCV in the shape ``get_ohlcv`` supplies."""
    index = pd.bdate_range("2024-01-01", periods=rows, name="date")
    close = pd.Series([100.0 + i * 0.4 for i in range(rows)], index=index)
    return pd.DataFrame(
        {
            "Open": close - 1.0,
            "High": close + 2.0,
            "Low": close - 2.0,
            "Close": close,
            "Volume": pd.Series([100000.0] * rows, index=index),
            "Delivery_qty": pd.Series([45000.0] * rows, index=index),
        },
        index=index,
    )


class _FakeLibrarian(LibrarianIntelligenceMixin):
    """The smallest object the intelligence mixin will accept.

    ``get_ohlcv`` hands back synthetic history so the *real* per-symbol
    indicator maths runs; an empty ``prices`` mapping models a symbol with no
    stored history. Nothing here stubs the computation under test.
    """

    read_only = False

    def __init__(self, prices, indicators=None, reference_date=DATE):
        from types import SimpleNamespace

        self._prices = dict(prices)
        self.loader = SimpleNamespace(indicators=indicators or _manager())
        self.reference_date = reference_date

    def get_active_universe(self):
        return list(self._prices)

    def get_max_price_date(self):
        return self.reference_date

    def get_ohlcv(self, symbol):
        return self._prices.get(symbol)


def _manager():
    from myra_app.data_loader import IndicatorManager

    return IndicatorManager()


# ── 1. Availability is not validity ─────────────────────────────────────────


def test_absent_lake_reads_missing_and_is_not_created(data_dir):
    status = lake.status(reference_date=DATE)

    assert status["present"] is False
    assert status["count"] == 0
    assert status["reason"] == "missing"
    assert status["stale"] is False
    # A probe that materialised the directory it is checking for would turn
    # "no lake" into "an empty lake directory", which is how the real problem
    # stayed hidden.
    assert not os.path.exists(lake.lake_dir())


def test_empty_directory_is_missing_not_ok(data_dir):
    os.makedirs(lake.lake_dir())

    status = lake.status(reference_date=DATE)

    assert status["present"] is False
    assert status["count"] == 0
    assert status["reason"] == "missing"


def test_valid_artifacts_read_ok(data_dir):
    _write("AAA")
    _write("BBB")

    status = lake.status(reference_date=DATE)

    assert status["present"] is True
    assert status["count"] == 2
    assert status["valid"] is True
    assert status["reason"] == "ok"
    assert status["sampled"] == 2


def test_lake_newer_than_the_reference_is_not_stale(data_dir):
    _write("AAA", mtime=1_700_000_000.0)

    status = lake.status(reference_date="2020-01-01")

    assert status["stale"] is False
    assert status["reason"] == "ok"
    assert status["stale_vs"] == "2020-01-01"


def test_lake_older_than_the_stored_session_reads_stale(data_dir):
    """A stale lake loads perfectly well and would rank on old prices."""
    _write("AAA", mtime=1_600_000_000.0)

    status = lake.status(reference_date=_future_date())

    assert status["stale"] is True
    assert status["reason"] == "stale"
    assert status["valid"] is False
    assert status["freshest"]


def test_a_file_where_the_directory_belongs_reads_missing(data_dir):
    directory = lake.lake_dir()
    os.makedirs(os.path.dirname(directory), exist_ok=True)
    with open(directory, "w", encoding="utf-8") as handle:
        handle.write("not a directory")

    status = lake.status()

    assert status["present"] is False
    assert status["reason"] == "missing"


# ── 2. Existence is not validity ────────────────────────────────────────────


def test_corrupt_artifact_is_invalid_not_ok(data_dir):
    directory = lake.lake_dir()
    os.makedirs(directory)
    with open(os.path.join(directory, "AAA.parquet"), "wb") as handle:
        handle.write(b"definitely not parquet")

    status = lake.status()

    assert status["count"] == 1
    assert status["present"] is True
    assert status["reason"] == "invalid_artifact"
    assert status["valid"] is False
    assert status["invalid"][0]["file"] == "AAA.parquet"


def test_artifact_without_a_symbol_column_is_invalid(data_dir):
    frame = pd.DataFrame({"close": [1.0]}, index=pd.to_datetime(["2026-01-02"]))
    frame.index.name = "date"
    _write("AAA", frame=frame)

    assert lake.status()["reason"] == "invalid_artifact"


def test_artifact_without_a_date_bearing_index_is_invalid(data_dir):
    """``precompute_indicators`` needs ``reset_index()`` to yield ``date``."""
    frame = pd.DataFrame({"symbol": ["AAA"], "close": [1.0]})
    _write("AAA", frame=frame)

    assert lake.status()["reason"] == "invalid_artifact"


def test_probe_opens_only_a_bounded_sample(data_dir):
    for i in range(5):
        _write(f"S{i}")

    status = lake.status(sample_limit=2)

    assert status["count"] == 5
    assert status["sampled"] == 2


def test_freshness_needs_a_reference_date(data_dir):
    """No reference means "cannot prove it is fresh", never a hopeful True."""
    _write("AAA")

    assert lake.is_fresh("AAA", None) is False
    assert lake.is_fresh("AAA", "1999-01-01") is True
    assert lake.is_fresh("MISSING", "1999-01-01") is False


def test_paths_are_pure_arithmetic(data_dir):
    """Resolving a path must not create the lake as a side effect."""
    assert lake.lake_dir() == os.path.join(data_dir, "indicators", "precomputed")
    assert not os.path.exists(lake.indicators_root())
    assert lake.list_artifacts() == []
    assert lake.count() == 0
    assert lake.artifact_mtime("AAA") is None


# ── 3. Writes are atomic and reads never create ─────────────────────────────


def test_save_leaves_only_the_artifact(data_dir):
    manager = _manager()
    manager.save_indicators("precomputed", "AAA", _frame("AAA"))

    assert sorted(os.listdir(lake.lake_dir())) == ["AAA.parquet"]
    assert lake.status()["reason"] == "ok"


def test_failed_write_leaves_neither_target_nor_scratch(data_dir, monkeypatch):
    """A half-written artifact must never be loadable as a healthy one."""
    manager = _manager()

    def _boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(pd.DataFrame, "to_parquet", _boom)

    with pytest.raises(OSError):
        manager.save_indicators("precomputed", "AAA", _frame("AAA"))

    assert os.listdir(lake.lake_dir()) == []
    assert not os.path.exists(lake.symbol_path("precomputed", "AAA"))


def test_reading_indicators_never_creates_the_lake(data_dir):
    manager = _manager()

    loaded = manager.load_indicators("precomputed", "AAA")

    assert loaded.empty
    assert not os.path.exists(lake.lake_dir())


def test_empty_frame_is_not_written(data_dir):
    manager = _manager()
    manager.save_indicators("precomputed", "AAA", pd.DataFrame())

    assert not os.path.exists(lake.lake_dir())


# ── 4. The producer/consumer contract ───────────────────────────────────────


def test_precompute_returns_the_empty_frame_its_callers_expect(data_dir):
    """An unbuilt lake is an empty result, not an exception out of a read path.

    This used to raise ``AssertionError`` from ``precompute_indicators``, which
    unwound through ``load_universe`` and turned "no lake" into an opaque crash
    instead of the empty frame both callers already handle.
    """
    librarian = _FakeLibrarian({"AAA": None, "BBB": None})

    result = librarian.precompute_indicators()

    assert isinstance(result, pd.DataFrame)
    assert result.empty


def test_precompute_reads_back_what_the_producer_saved(data_dir):
    manager = _manager()
    for symbol in ("AAA", "BBB"):
        manager.save_indicators("precomputed", symbol, _frame(symbol))
    librarian = _FakeLibrarian({"AAA": None, "BBB": None}, indicators=manager)

    result = librarian.precompute_indicators()

    assert sorted(result["symbol"].unique()) == ["AAA", "BBB"]
    assert list(result.index.names) == ["symbol", "date"]


def test_the_real_indicator_maths_produces_an_artifact_the_probe_accepts(data_dir):
    """End-to-end: real computation → file → probe → consumer.

    The producer had never actually run, so this is the test that proves the
    whole chain works with the real code rather than a replaced stub.
    """
    librarian = _FakeLibrarian({"AAA": _ohlcv()})

    summary = librarian.build_indicator_lake(force=True, reference_date=DATE)

    assert summary["built"] == 1, summary
    assert summary["failed"] == 0, summary
    assert lake.status(reference_date="1999-01-01")["reason"] == "ok"

    loaded = librarian.precompute_indicators()
    assert list(loaded["symbol"].unique()) == ["AAA"]
    for column in ("sma20", "rsi", "atr20", "vcp", "delivery_pct"):
        assert column in loaded.columns, column


def test_history_too_short_is_skipped_not_zeroed(data_dir):
    """No history is counted, never written as zeroed indicators."""
    librarian = _FakeLibrarian({"AAA": _ohlcv(rows=10)})

    summary = librarian.build_indicator_lake(force=True, reference_date=DATE)

    assert summary["built"] == 0
    assert summary["skipped_no_data"] == 1
    assert not os.path.exists(lake.lake_dir())


# ── 5. The build is bounded, resumable and never fatal ──────────────────────


def test_build_writes_every_symbol_with_history(data_dir):
    librarian = _FakeLibrarian({"AAA": _ohlcv(), "BBB": _ohlcv()})

    summary = librarian.build_indicator_lake(force=True, reference_date=DATE)

    assert summary["requested"] == 2
    assert summary["built"] == 2
    assert summary["failed"] == 0
    assert lake.count() == 2


def test_a_fresh_artifact_is_skipped(data_dir):
    librarian = _FakeLibrarian({"AAA": _ohlcv()})
    first = librarian.build_indicator_lake(force=True, reference_date=DATE)
    assert first["built"] == 1
    before = lake.artifact_mtime("AAA")

    second = librarian.build_indicator_lake(reference_date=DATE)

    assert second["skipped_fresh"] == 1
    assert second["built"] == 0
    assert lake.artifact_mtime("AAA") == before


def test_force_rebuilds_a_fresh_artifact(data_dir):
    librarian = _FakeLibrarian({"AAA": _ohlcv()})
    librarian.build_indicator_lake(force=True, reference_date=DATE)
    before = lake.artifact_mtime("AAA")

    summary = librarian.build_indicator_lake(force=True, reference_date=DATE)

    assert summary["built"] == 1
    assert summary["skipped_fresh"] == 0
    assert lake.artifact_mtime("AAA") >= before


def test_the_build_can_be_staged_in_bounded_runs(data_dir):
    symbols = [f"S{i}" for i in range(5)]
    librarian = _FakeLibrarian({s: _ohlcv() for s in symbols})

    summary = librarian.build_indicator_lake(
        force=True, reference_date=DATE, max_symbols=2
    )

    assert summary["requested"] == 2
    assert summary["built"] == 2
    assert lake.count() == 2


def test_one_bad_symbol_does_not_abort_the_build(data_dir):
    prices = {"BAD": _ohlcv(), "GOOD": _ohlcv()}
    librarian = _FakeLibrarian(prices)
    real = librarian._compute_symbol_indicators

    def _compute(symbol):
        if symbol == "BAD":
            raise ValueError("bad column dtype")
        return real(symbol)

    librarian._compute_symbol_indicators = _compute

    summary = librarian.build_indicator_lake(force=True, reference_date=DATE)

    assert summary["built"] == 1
    assert summary["failed"] == 1
    assert summary["failures"][0]["symbol"] == "BAD"
    assert "ValueError" in summary["failures"][0]["error"]
    assert lake.has_artifact("GOOD")
    assert not lake.has_artifact("BAD")


def test_progress_reporting_runs_against_the_real_logger(data_dir):
    """``progress=True`` used to raise ``NameError`` on the first symbol.

    ``myra_log`` was called without ever being imported, so the producer would
    have died immediately had it ever been reached. Nothing caught it because
    the only caller was itself unreachable.
    """
    librarian = _FakeLibrarian({"AAA": _ohlcv()})

    summary = librarian.build_indicator_lake(
        force=True, reference_date=DATE, progress=True
    )

    assert summary["built"] == 1


def test_cancellation_stops_the_build_and_says_so(data_dir):
    import threading

    symbols = [f"S{i}" for i in range(6)]
    librarian = _FakeLibrarian({s: _ohlcv() for s in symbols})
    cancel = threading.Event()
    real = librarian._compute_symbol_indicators

    def _compute(symbol):
        frame = real(symbol)
        if symbol == "S1":
            cancel.set()
        return frame

    librarian._compute_symbol_indicators = _compute

    summary = librarian.build_indicator_lake(
        force=True, reference_date=DATE, cancel_event=cancel
    )

    assert summary["cancelled"] is True
    assert summary["built"] < summary["requested"]


def test_a_read_only_librarian_builds_nothing(data_dir):
    librarian = _FakeLibrarian({"AAA": _ohlcv()})
    librarian.read_only = True

    summary = librarian.build_indicator_lake(force=True, reference_date=DATE)

    assert summary["reason"] == "read_only"
    assert summary["built"] == 0
    assert not os.path.exists(lake.lake_dir())


def test_update_indicator_history_is_a_full_rebuild(data_dir):
    """The legacy entry point still rebuilds unconditionally."""
    librarian = _FakeLibrarian({"AAA": _ohlcv()})
    librarian.build_indicator_lake(force=True, reference_date=DATE)
    before = lake.artifact_mtime("AAA")

    summary = librarian.update_indicator_history()

    assert summary["built"] == 1
    assert summary["skipped_fresh"] == 0
    assert lake.artifact_mtime("AAA") >= before
