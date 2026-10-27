"""Phase 51: Bottom Hunter columnar window.

The scan used to ask ``_get_tech_data`` (→ ``rows_for_symbol``) for a twelve
column tuple list per symbol, wrap it in a DataFrame, re-parse the date strings
and sort, then slice the last 20 rows. That per-symbol construction was ~23 s of
a ~48 s profiled run. This suite pins the accessor that replaces it:

* ``_ColumnarWindow.frame`` must carry the same values, column order and row
  order as the pre-change tuple path;
* ``scan()`` must emit the same candidates, ordering and fingerprint;
* absent / empty / short symbols, absent columns and unsorted frames keep their
  previous behaviour;
* returned frames own their data, and an empty bulk slice never falls back to
  one SQL query per symbol.

Expectations come from the fixture geometry, never from the window internals.
"""

from __future__ import annotations

import hashlib

import numpy as np
import pandas as pd
import pytest

import myra_app.strategies.bottom_hunter as bh
from myra_app.db.bulk_loader import COLUMNS_12, rows_for_symbol
from myra_app.strategies.bottom_hunter import BottomHunter, _ColumnarWindow

AS_ON = "2026-10-23"
LOOKBACK_DAYS = 260
MIN_DATE = f"{(pd.Timestamp(AS_ON) - pd.Timedelta(days=LOOKBACK_DAYS + 30)):%Y-%m-%d}"

MCAP = 1e9  # 100 Cr
FF_PCT = 40.0


# ---------------------------------------------------------------------------
# Fixtures: explicit up/down geometry, so the goldens are derived by hand
# ---------------------------------------------------------------------------


def _bars(n=200, up_delivery=20.0, down_delivery=10.0, high_52w=120.0, low_52w=80.0):
    """``n`` alternating up/down bars ending on AS_ON.

    Even rows are up days (close 101 > open 99), odd rows are down days
    (close 99 < open 101), so the last 20 rows always hold both sides and
    delivery absorption is ``up_delivery - down_delivery``.
    """
    dates = pd.date_range(end=AS_ON, periods=n, freq="D")
    rows = []
    for i, day in enumerate(dates):
        up = i % 2 == 0
        rows.append(
            (
                day.date().isoformat(),
                99.0 if up else 101.0,  # open
                102.0,  # high
                98.0,  # low
                101.0 if up else 99.0,  # close
                200_000,  # volume → 2 Cr turnover
                50_000,  # delivery
                up_delivery if up else down_delivery,  # delivery_pct
                0.5,  # nifty_outperformance_score
                None,  # sma_50
                high_52w,
                low_52w,
            )
        )
    return rows


def _frame(rows, columns=COLUMNS_12):
    return pd.DataFrame(list(rows), columns=list(columns))


def _bulk():
    return {
        "AAA": _frame(_bars()),
        "BBB": _frame(_bars(up_delivery=30.0, down_delivery=5.0)),
        "SHORT": _frame(_bars(n=10)),
        "EMPTY": _frame([]),
    }


def _universe(symbols):
    return [(symbol, MCAP, FF_PCT) for symbol in symbols]


def _tuple_frame(rows):
    """The pre-change scan built its frame exactly like this."""
    if not rows:
        return pd.DataFrame(columns=list(COLUMNS_12))
    if len(rows[0]) >= 12:
        df = pd.DataFrame(rows, columns=list(COLUMNS_12))
    else:
        df = pd.DataFrame(rows, columns=list(COLUMNS_12)[:9])
        df["sma_50"] = None
        df["high_52w"] = None
        df["low_52w"] = None
    df["date"] = pd.to_datetime(df["date"])
    return df.sort_values("date").reset_index(drop=True)


def _object_frame(df):
    """Object view with one null spelling, so NaN-vs-None never warns."""
    return df.astype(object).where(df.notna(), None)


def _window(bulk=None):
    return _ColumnarWindow.from_frames(
        bulk if bulk is not None else _bulk(), COLUMNS_12
    )


def _fingerprint(df: pd.DataFrame) -> str:
    if df is None or df.empty:
        return "empty"
    frame = df.astype(str).reset_index(drop=True)
    return hashlib.sha256(
        pd.util.hash_pandas_object(frame, index=False).to_numpy().tobytes()
    ).hexdigest()[:16]


def _legacy_get_tech_df(bulk):
    """Pre-change accessor: tuples → DataFrame → datetime → sort."""

    def impl(symbol, min_date, max_date=None):
        rows = rows_for_symbol(bulk, symbol, COLUMNS_12, min_date, max_date or AS_ON)
        return _tuple_frame(rows)

    return impl


def _scan(monkeypatch, bulk, *, legacy=False, symbols=None):
    scanner = BottomHunter(lookback_days=LOOKBACK_DAYS)
    symbols = list(bulk) if symbols is None else symbols
    monkeypatch.setattr(scanner, "_get_universe", lambda: _universe(symbols))
    monkeypatch.setattr(bh, "load_ohlcv_for_universe", lambda *a, **k: dict(bulk))
    # Sector/quality lookups hit an empty in-memory DB and are swallowed.
    monkeypatch.setattr(scanner, "_db_path", lambda key: ":memory:")
    monkeypatch.setattr(bh, "compute_sector_momentum_tiers", lambda *a, **k: {})
    if legacy:
        monkeypatch.setattr(scanner, "_get_tech_df", _legacy_get_tech_df(bulk))
    return scanner, scanner.scan(as_on_date=AS_ON)


# ---------------------------------------------------------------------------
# Window: frame parity
# ---------------------------------------------------------------------------


def test_frame_values_match_the_tuple_path_for_every_symbol():
    bulk = _bulk()
    window = _window(bulk)
    for symbol in ("AAA", "BBB", "SHORT"):
        got = window.frame(symbol, MIN_DATE, AS_ON)
        want = _tuple_frame(rows_for_symbol(bulk, symbol, COLUMNS_12, MIN_DATE, AS_ON))
        assert got is not None
        assert list(got.columns) == list(COLUMNS_12)
        assert len(got) == len(want)
        pd.testing.assert_frame_equal(
            _object_frame(got).reset_index(drop=True),
            _object_frame(want).reset_index(drop=True),
        )


def test_frame_dtypes_are_native_and_dates_are_timestamps():
    df = _window().frame("AAA", MIN_DATE, AS_ON)

    assert df["date"].dtype == pd.to_datetime(pd.Series(["2026-01-02"])).dtype
    assert df["close"].dtype == np.float64
    assert df["high"].dtype == np.float64
    assert df["low"].dtype == np.float64
    assert df["delivery"].dtype == np.int64
    assert df["volume"].dtype == np.int64
    assert isinstance(df.index, pd.RangeIndex)


def test_rows_stay_in_chronological_snapshot_order():
    df = _window().frame("AAA", MIN_DATE, AS_ON)

    assert df["date"].is_monotonic_increasing
    assert len(set(df["date"])) == len(df)
    assert df["date"].iloc[-1] == pd.Timestamp(AS_ON)


def test_narrow_window_matches_the_tuple_path():
    bulk = _bulk()
    window = _window(bulk)
    narrow = (pd.Timestamp(AS_ON) - pd.Timedelta(days=5)).date().isoformat()

    for symbol in ("AAA", "BBB"):
        got = window.frame(symbol, narrow, AS_ON)
        want = _tuple_frame(rows_for_symbol(bulk, symbol, COLUMNS_12, narrow, AS_ON))
        assert 0 < len(got) == len(want) < len(bulk[symbol])
        pd.testing.assert_frame_equal(
            _object_frame(got).reset_index(drop=True),
            _object_frame(want).reset_index(drop=True),
        )


def test_a_column_absent_from_one_symbol_keeps_its_position():
    """Positional safety for a drifted schema.

    The window carries the bulk column's dtype (float64 + NaN for the symbol
    that lacked the column); the old per-symbol tuple path inferred ``object``
    because every value it saw was ``None``. Both mean "missing" to the scanner,
    which only asks ``pd.notna`` — asserted here rather than assumed.
    """
    bulk = _bulk()
    bulk["DRIFT"] = bulk["AAA"].drop(columns=["high_52w"])
    window = _window(bulk)

    df = window.frame("DRIFT", MIN_DATE, AS_ON)
    assert list(df.columns) == list(COLUMNS_12)
    assert df["high_52w"].isna().all()
    assert df["low_52w"].notna().all()

    want = rows_for_symbol(bulk, "DRIFT", COLUMNS_12, MIN_DATE, AS_ON)
    assert want[0][10] is None  # high_52w padded, low_52w keeps index 11
    assert want[0][11] == 80.0
    assert df["low_52w"].tolist() == [row[11] for row in want]
    # The scanner's only use of the column: missing → fall back to max(high).
    assert not pd.notna(df["high_52w"].iloc[-1])


def test_a_column_absent_from_every_symbol_is_an_object_column():
    bulk = {symbol: df.drop(columns=["high_52w"]) for symbol, df in _bulk().items()}
    window = _window(bulk)

    df = window.frame("AAA", MIN_DATE, AS_ON)
    assert list(df.columns) == list(COLUMNS_12)
    assert df["high_52w"].dtype == object
    assert df["high_52w"].isna().all()
    want = _tuple_frame(rows_for_symbol(bulk, "AAA", COLUMNS_12, MIN_DATE, AS_ON))
    assert want["high_52w"].dtype == object
    pd.testing.assert_frame_equal(
        _object_frame(df).reset_index(drop=True),
        _object_frame(want).reset_index(drop=True),
    )


def test_absent_empty_and_unknown_symbols_serve_nothing():
    window = _window()

    assert window.frame("GONE", MIN_DATE, AS_ON) is None
    # "EMPTY" exists in the snapshot but owns no rows: same as absent.
    assert window.frame("EMPTY", MIN_DATE, AS_ON) is None
    assert (
        _ColumnarWindow.from_frames({}, COLUMNS_12).frame("AAA", MIN_DATE, AS_ON)
        is None
    )


def test_a_window_outside_the_data_returns_no_frame():
    window = _window()
    before = (pd.Timestamp(MIN_DATE) - pd.Timedelta(days=400)).date().isoformat()
    earlier = (pd.Timestamp(before) + pd.Timedelta(days=10)).date().isoformat()
    assert window.frame("AAA", before, earlier) is None


def test_returned_frames_own_their_data():
    window = _window()
    first = window.frame("AAA", MIN_DATE, AS_ON)
    baseline = first["close"].tolist()

    assert not np.shares_memory(first["close"].to_numpy(), window.arrays["close"])
    first.loc[:, "close"] = -1.0
    first.loc[:, "date"] = pd.Timestamp("1999-01-01")

    again = window.frame("AAA", MIN_DATE, AS_ON)
    assert again["close"].tolist() == baseline
    assert again["date"].iloc[-1] == pd.Timestamp(AS_ON)
    # A sibling symbol was never reachable through the first frame.
    assert window.frame("BBB", MIN_DATE, AS_ON)["close"].iloc[-1] == 99.0


# ---------------------------------------------------------------------------
# Scanner: identical candidates, lifecycle, fallback
# ---------------------------------------------------------------------------


def test_scan_matches_the_legacy_tuple_path(monkeypatch):
    bulk = _bulk()
    _, expected = _scan(monkeypatch, bulk, legacy=True)
    _, actual = _scan(monkeypatch, bulk)

    assert len(actual) == len(expected) > 0
    assert actual["symbol"].tolist() == expected["symbol"].tolist()
    assert _fingerprint(actual) == _fingerprint(expected)
    pd.testing.assert_frame_equal(actual, expected)


def test_scan_releases_the_bulk_dictionary_and_keeps_the_window(monkeypatch):
    scanner, result = _scan(monkeypatch, _bulk())

    assert not result.empty
    assert scanner._columnar is not None
    assert scanner._bulk_data is None
    # Every non-empty frame is reachable, including the too-short symbol: the
    # length filter belongs to the scan, not the window.
    assert set(scanner._columnar.spans) == {"AAA", "BBB", "SHORT"}


def test_scan_is_repeatable_and_rebuilds_the_window(monkeypatch):
    scanner, first = _scan(monkeypatch, _bulk())
    original = scanner._columnar
    second = scanner.scan(as_on_date=AS_ON)

    assert _fingerprint(first) == _fingerprint(second)
    assert scanner._columnar is not original
    assert scanner._bulk_data is None


def test_scan_skips_short_history_symbols(monkeypatch):
    _, result = _scan(monkeypatch, _bulk())

    assert "SHORT" not in result["symbol"].tolist()
    assert "EMPTY" not in result["symbol"].tolist()


def test_scan_skips_a_symbol_absent_from_the_slice(monkeypatch):
    bulk = _bulk()
    scanner, result = _scan(monkeypatch, bulk, symbols=["AAA", "NOT_IN_SLICE"])

    assert result["symbol"].tolist() == ["AAA"]


def test_scan_returns_an_empty_frame_when_the_slice_is_empty(monkeypatch):
    scanner, result = _scan(monkeypatch, {}, symbols=["AAA"])

    assert result.empty
    assert scanner._columnar is None
    assert scanner._bulk_data == {}


def test_empty_bulk_slice_never_reaches_per_symbol_sql(monkeypatch):
    scanner = BottomHunter(lookback_days=LOOKBACK_DAYS)
    scanner._bulk_data = {}
    scanner._columnar = None

    def _boom(key):
        raise AssertionError(f"per-symbol SQL path reached for {key!r}")

    monkeypatch.setattr(scanner, "_db_path", _boom)

    assert scanner._get_tech_data("AAA", MIN_DATE, AS_ON) == []
    assert scanner._get_tech_df("AAA", MIN_DATE, AS_ON).empty


def test_accessor_without_a_window_uses_the_bulk_dictionary(monkeypatch):
    """``_bulk_data`` set by a caller keeps working (pre-change behaviour)."""
    bulk = _bulk()
    scanner = BottomHunter(lookback_days=LOOKBACK_DAYS)
    scanner._columnar = None
    scanner._bulk_data = bulk

    df = scanner._get_tech_df("AAA", MIN_DATE, AS_ON)
    want = _tuple_frame(rows_for_symbol(bulk, "AAA", COLUMNS_12, MIN_DATE, AS_ON))
    pd.testing.assert_frame_equal(
        _object_frame(df).reset_index(drop=True),
        _object_frame(want).reset_index(drop=True),
    )


def test_accessor_serves_an_empty_frame_for_an_absent_symbol():
    scanner = BottomHunter(lookback_days=LOOKBACK_DAYS)
    scanner._columnar = _window()

    df = scanner._get_tech_df("GONE", MIN_DATE, AS_ON)
    assert df.empty
    assert list(df.columns) == list(COLUMNS_12)


def test_accessor_sorts_a_frame_that_is_not_monotonic():
    bulk = _bulk()
    bulk["UNSORTED"] = bulk["AAA"].iloc[::-1].reset_index(drop=True)
    scanner = BottomHunter(lookback_days=LOOKBACK_DAYS)
    scanner._columnar = _window(bulk)

    df = scanner._get_tech_df("UNSORTED", MIN_DATE, AS_ON)
    assert df["date"].is_monotonic_increasing
    assert (
        df["close"].tolist()
        == _window().frame("AAA", MIN_DATE, AS_ON)["close"].tolist()
    )


def test_accessor_keeps_the_three_argument_call_shape():
    """The accessor is called positionally as ``(symbol, min_date, max_date)``."""
    scanner = BottomHunter(lookback_days=LOOKBACK_DAYS)
    scanner._columnar = _window()

    df = scanner._get_tech_df("AAA", MIN_DATE, AS_ON)
    assert len(df) > 0


@pytest.mark.parametrize("symbol", ["AAA", "BBB"])
def test_scan_candidate_fields_are_unchanged(monkeypatch, symbol):
    bulk = _bulk()
    _, expected = _scan(monkeypatch, bulk, legacy=True)
    _, actual = _scan(monkeypatch, bulk)

    want = expected.set_index("symbol").loc[symbol]
    got = actual.set_index("symbol").loc[symbol]
    for column in expected.columns:
        if column == "symbol":
            continue
        if isinstance(want[column], float) and pd.isna(want[column]):
            assert pd.isna(got[column])
        else:
            assert got[column] == want[column]
