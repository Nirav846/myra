"""Phase 46: Float Exhaustion columnar window.

The scan used to ask ``_get_tech_data`` (→ ``rows_for_symbol``) for a twelve
column tuple list per symbol, wrap it in a DataFrame, re-parse the date strings
and sort, then slice the last ``window_days`` rows. This suite pins the
accessor that replaces it:

* ``_ColumnarWindow.frame`` must carry the same values, the same column order
  and the same row order as the pre-change tuple path;
* ``scan()`` must emit the same candidates, ordering and fingerprint;
* absent / empty / short / whitespace symbols and absent columns keep their
  previous behaviour;
* returned frames own their data.

Expectations come from the fixture geometry, never from the window internals.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import myra_app.strategies.float_exhaustion_scanner as fes
from myra_app.db.bulk_loader import COLUMNS_12, rows_for_symbol
from myra_app.strategies.float_exhaustion_scanner import (
    FloatExhaustionScanner,
    _ColumnarWindow,
)

AS_ON = "2026-10-16"
WINDOW_DAYS = 20
MIN_DATE = f"{(pd.Timestamp(AS_ON) - pd.Timedelta(days=WINDOW_DAYS + 10)):%Y-%m-%d}"
MIN_ROWS = max(14, int(WINDOW_DAYS * 0.7) + 2)  # 16

MCAP = 1e9  # 100 Cr
FF_PCT = 40.0
PROMOTER_PCT = 50.0


# ---------------------------------------------------------------------------
# Fixtures: explicit delivery geometry, so the goldens are derived by hand
# ---------------------------------------------------------------------------


def _bars(n=45, delivery=50_000, high_52w=120.0, low_52w=80.0):
    """``n`` daily bars ending on AS_ON, flat price, constant delivery."""
    dates = pd.date_range(end=AS_ON, periods=n, freq="D")
    rows = []
    for day in dates:
        rows.append(
            (
                day.strftime("%Y-%m-%d"),
                99.5,  # open
                101.0,  # high
                99.0,  # low
                100.0,  # close
                200_000,  # volume
                delivery,
                25.0,  # delivery_pct
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
        "FLOAT": _frame(_bars()),
        "LOW": _frame(_bars(delivery=1_000)),
        "SHORT": _frame(_bars(n=10)),
        "EMPTY": _frame([]),
    }


def _universe(symbols):
    """(symbol, mcap, ff_pct, promoter_pct, raw_promoter_pct, raw_ff_pct)."""
    return [
        (symbol, MCAP, FF_PCT, PROMOTER_PCT, PROMOTER_PCT, FF_PCT) for symbol in symbols
    ]


def _fingerprint(candidates: list[dict]) -> str:
    if not candidates:
        return "empty"
    frame = pd.DataFrame(candidates)
    frame.columns = [str(c) for c in frame.columns]
    frame = frame.astype(str).sort_values(list(frame.columns)).reset_index(drop=True)
    return hashlib.sha256(
        pd.util.hash_pandas_object(frame, index=False).to_numpy().tobytes()
    ).hexdigest()[:16]


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


# ---------------------------------------------------------------------------
# Window: frame parity
# ---------------------------------------------------------------------------


def test_frame_values_match_the_tuple_path_for_every_symbol():
    bulk = _bulk()
    window = _window(bulk)
    for symbol in ("FLOAT", "LOW", "SHORT"):
        got = window.frame(symbol, MIN_DATE, AS_ON)
        want = _tuple_frame(rows_for_symbol(bulk, symbol, COLUMNS_12, MIN_DATE, AS_ON))
        assert got is not None
        assert list(got.columns) == list(COLUMNS_12)
        assert len(got) == len(want)
        # Values: same cells, same order (dtype is native in the window path).
        pd.testing.assert_frame_equal(
            got.astype(object).reset_index(drop=True),
            want.astype(object).reset_index(drop=True),
        )


def test_frame_dtypes_are_native_and_dates_are_timestamps():
    df = _window().frame("FLOAT", MIN_DATE, AS_ON)

    assert df["date"].dtype == pd.to_datetime(pd.Series(["2026-01-02"])).dtype
    assert df["close"].dtype == np.float64
    assert df["high"].dtype == np.float64
    assert df["low"].dtype == np.float64
    assert df["delivery"].dtype == np.int64
    assert df["volume"].dtype == np.int64
    assert isinstance(df.index, pd.RangeIndex)


def test_rows_stay_in_chronological_snapshot_order():
    df = _window().frame("FLOAT", MIN_DATE, AS_ON)

    assert df["date"].is_monotonic_increasing
    assert len(set(df["date"])) == len(df)
    assert df["date"].iloc[-1] == pd.Timestamp(AS_ON)


def test_narrow_window_matches_the_tuple_path():
    bulk = _bulk()
    window = _window(bulk)
    narrow = (pd.Timestamp(AS_ON) - pd.Timedelta(days=5)).strftime("%Y-%m-%d")

    for symbol in ("FLOAT", "LOW"):
        got = window.frame(symbol, narrow, AS_ON)
        want = _tuple_frame(rows_for_symbol(bulk, symbol, COLUMNS_12, narrow, AS_ON))
        assert 0 < len(got) == len(want) < len(bulk[symbol])
        pd.testing.assert_frame_equal(
            got.astype(object).reset_index(drop=True),
            want.astype(object).reset_index(drop=True),
        )


def test_a_column_absent_from_one_symbol_keeps_its_position():
    """Positional safety for a drifted schema.

    The window carries the bulk column's dtype (float64 + NaN for the symbol
    that lacked the column); the old per-symbol tuple path inferred ``object``
    because every value it saw was ``None``. Both mean "missing" to the scanner,
    which only asks ``pd.notna`` — asserted here rather than assumed.
    """
    bulk = _bulk()
    bulk["DRIFT"] = bulk["FLOAT"].drop(columns=["high_52w"])
    window = _window(bulk)

    df = window.frame("DRIFT", MIN_DATE, AS_ON)
    assert list(df.columns) == list(COLUMNS_12)
    assert df["high_52w"].isna().all()
    assert df["low_52w"].notna().all()
    assert df["close"].dtype == np.float64  # neighbours keep their values

    want = rows_for_symbol(bulk, "DRIFT", COLUMNS_12, MIN_DATE, AS_ON)
    assert want[0][10] is None  # high_52w padded, low_52w keeps index 11
    assert want[0][11] == 80.0
    assert df["close"].tolist() == [row[4] for row in want]
    assert df["low_52w"].tolist() == [row[11] for row in want]
    # The scanner's only use of the column: missing → fall back to max(high).
    assert not pd.notna(df["high_52w"].iloc[-1])
    assert not pd.notna(_tuple_frame(want)["high_52w"].iloc[-1])


def test_a_column_absent_from_every_symbol_is_an_object_column():
    bulk = {symbol: df.drop(columns=["high_52w"]) for symbol, df in _bulk().items()}
    window = _window(bulk)

    df = window.frame("FLOAT", MIN_DATE, AS_ON)
    assert list(df.columns) == list(COLUMNS_12)
    assert df["high_52w"].dtype == object
    assert df["high_52w"].isna().all()
    want = _tuple_frame(rows_for_symbol(bulk, "FLOAT", COLUMNS_12, MIN_DATE, AS_ON))
    assert want["high_52w"].dtype == object
    pd.testing.assert_frame_equal(
        _object_frame(df).reset_index(drop=True),
        _object_frame(want).reset_index(drop=True),
    )


def test_an_all_na_column_keeps_its_position():
    bulk = _bulk()
    bulk["NANCOL"] = bulk["FLOAT"].assign(sma_50=np.nan)
    window = _window(bulk)

    df = window.frame("NANCOL", MIN_DATE, AS_ON)
    assert list(df.columns) == list(COLUMNS_12)
    assert df["sma_50"].isna().all()
    assert df["high_52w"].notna().all()
    want = _tuple_frame(rows_for_symbol(bulk, "NANCOL", COLUMNS_12, MIN_DATE, AS_ON))
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
        _ColumnarWindow.from_frames({}, COLUMNS_12).frame("FLOAT", MIN_DATE, AS_ON)
        is None
    )


def test_a_window_outside_the_data_returns_no_frame():
    window = _window()
    before = (pd.Timestamp(MIN_DATE) - pd.Timedelta(days=400)).strftime("%Y-%m-%d")
    earlier = (pd.Timestamp(before) + pd.Timedelta(days=10)).strftime("%Y-%m-%d")
    assert window.frame("FLOAT", before, earlier) is None


def test_returned_frames_own_their_data():
    window = _window()
    first = window.frame("FLOAT", MIN_DATE, AS_ON)
    baseline = first["close"].tolist()

    assert not np.shares_memory(first["close"].to_numpy(), window.arrays["close"])
    first.loc[:, "close"] = -1.0
    first.loc[:, "date"] = pd.Timestamp("1999-01-01")

    again = window.frame("FLOAT", MIN_DATE, AS_ON)
    assert again["close"].tolist() == baseline
    assert again["date"].iloc[-1] == pd.Timestamp(AS_ON)
    # A sibling symbol was never reachable through the first frame.
    assert window.frame("LOW", MIN_DATE, AS_ON)["close"].iloc[-1] == 100.0


# ---------------------------------------------------------------------------
# Scanner: identical candidates, lifecycle, fallback
# ---------------------------------------------------------------------------


def _legacy_get_tech_df(bulk):
    """Pre-change accessor: tuples → DataFrame → datetime → sort."""

    def impl(symbol, min_date, max_date=None):
        rows = rows_for_symbol(bulk, symbol, COLUMNS_12, min_date, max_date or AS_ON)
        return _tuple_frame(rows)

    return impl


def _scan(monkeypatch, bulk, *, legacy=False, symbols=None):
    scanner = FloatExhaustionScanner(window_days=WINDOW_DAYS)
    symbols = list(bulk) if symbols is None else symbols
    monkeypatch.setattr(scanner, "_get_universe", lambda: _universe(symbols))
    monkeypatch.setattr(fes, "load_ohlcv_for_universe", lambda *a, **k: dict(bulk))
    monkeypatch.setattr(scanner, "_db_path", lambda key: ":memory:")
    if legacy:
        monkeypatch.setattr(scanner, "_get_tech_df", _legacy_get_tech_df(bulk))
    return scanner, scanner.scan(as_on_date=AS_ON)


def test_scan_matches_the_legacy_tuple_path(monkeypatch):
    bulk = _bulk()
    _, expected = _scan(monkeypatch, bulk, legacy=True)
    _, actual = _scan(monkeypatch, bulk)

    assert len(actual) == len(expected)
    assert [c["symbol"] for c in actual] == [c["symbol"] for c in expected]
    assert _fingerprint(actual) == _fingerprint(expected)
    for got, want in zip(actual, expected):
        assert got == want


def test_the_candidate_is_exactly_the_hand_computed_golden(monkeypatch):
    """Independent expectation: 20 x 50,000 delivery over a 4,000,000 float."""
    _, candidates = _scan(monkeypatch, _bulk())

    assert [c["symbol"] for c in candidates] == ["FLOAT"]
    assert candidates[0] == {
        "symbol": "FLOAT",
        "sector": "Unknown",
        "market_cap_cr": 100.0,
        "free_float_shares": 4_000_000,
        "cum_delivery_20d": 1_000_000,
        "float_util_pct": 25.0,
        "smart_float_ratio": 25.0,
        "absorption_rate": 1.0,
        "exhaustion_tier": "T2 HIGH",
        "close": 100.0,
        "wk52_pos": 50.0,
    }


def test_symbols_below_the_threshold_are_skipped(monkeypatch):
    _, candidates = _scan(monkeypatch, _bulk())

    assert "LOW" not in [c["symbol"] for c in candidates]


def test_short_and_absent_symbols_are_skipped_without_aborting(monkeypatch):
    bulk = _bulk()
    _, candidates = _scan(
        monkeypatch, bulk, symbols=["FLOAT", "SHORT", "EMPTY", "GONE"]
    )

    assert [c["symbol"] for c in candidates] == ["FLOAT"]


def test_a_symbol_without_float_or_promoter_data_is_skipped(monkeypatch):
    scanner = FloatExhaustionScanner(window_days=WINDOW_DAYS)
    monkeypatch.setattr(
        scanner, "_get_universe", lambda: [("FLOAT", MCAP, FF_PCT, 0.0, None, None)]
    )
    monkeypatch.setattr(fes, "load_ohlcv_for_universe", lambda *a, **k: _bulk())
    monkeypatch.setattr(scanner, "_db_path", lambda key: ":memory:")

    assert scanner.scan(as_on_date=AS_ON) == []


def test_a_whitespace_padded_symbol_is_still_scanned(monkeypatch):
    bulk = {"FLOAT": _frame(_bars())}
    _, candidates = _scan(monkeypatch, bulk, symbols=["  FLOAT  "])

    assert [c["symbol"] for c in candidates] == ["FLOAT"]


def test_an_empty_universe_returns_no_candidates(monkeypatch):
    scanner = FloatExhaustionScanner(window_days=WINDOW_DAYS)
    monkeypatch.setattr(scanner, "_get_universe", lambda: [])
    monkeypatch.setattr(scanner, "_db_path", lambda key: ":memory:")

    assert scanner.scan(as_on_date=AS_ON) == []


def test_repeated_scans_are_stable(monkeypatch):
    scanner, first = _scan(monkeypatch, _bulk())
    second = scanner.scan(as_on_date=AS_ON)

    assert _fingerprint(first) == _fingerprint(second)


def test_the_scan_releases_the_bulk_mapping(monkeypatch):
    scanner, _ = _scan(monkeypatch, _bulk())

    assert scanner._columnar is not None
    assert scanner._bulk_data is None


def test_an_empty_bulk_load_keeps_the_empty_mapping(monkeypatch):
    scanner = FloatExhaustionScanner(window_days=WINDOW_DAYS)
    monkeypatch.setattr(
        scanner,
        "_get_universe",
        lambda: [("FLOAT", MCAP, FF_PCT, PROMOTER_PCT, PROMOTER_PCT, FF_PCT)],
    )
    monkeypatch.setattr(fes, "load_ohlcv_for_universe", lambda *a, **k: {})
    monkeypatch.setattr(scanner, "_db_path", lambda key: ":memory:")

    assert scanner.scan(as_on_date=AS_ON) == []
    assert scanner._columnar is None
    assert scanner._bulk_data == {}


# ---------------------------------------------------------------------------
# Accessor: no-snapshot fallback and progress contract
# ---------------------------------------------------------------------------


def test_accessor_falls_back_to_the_tuple_path_without_a_window():
    bulk = _bulk()
    scanner = FloatExhaustionScanner(window_days=WINDOW_DAYS)
    scanner._columnar = None
    scanner._bulk_data = bulk

    got = scanner._get_tech_df("FLOAT", MIN_DATE, AS_ON)
    want = _tuple_frame(rows_for_symbol(bulk, "FLOAT", COLUMNS_12, MIN_DATE, AS_ON))
    pd.testing.assert_frame_equal(
        got.astype(object).reset_index(drop=True),
        want.astype(object).reset_index(drop=True),
    )


def test_accessor_pads_a_nine_column_fallback_frame():
    scanner = FloatExhaustionScanner(window_days=WINDOW_DAYS)
    scanner._columnar = None
    rows = [tuple(r[:9]) for r in _bars()]

    scanner._get_tech_data = lambda symbol, min_date, max_date=None: rows
    df = scanner._get_tech_df("OLD", MIN_DATE, AS_ON)

    assert list(df.columns) == list(COLUMNS_12)
    assert df["sma_50"].isna().all()
    assert df["high_52w"].isna().all()
    assert df["low_52w"].isna().all()


def test_accessor_serves_an_empty_frame_for_an_absent_symbol():
    scanner = FloatExhaustionScanner(window_days=WINDOW_DAYS)
    scanner._columnar = _window()

    df = scanner._get_tech_df("GONE", MIN_DATE, AS_ON)
    assert df.empty
    assert list(df.columns) == list(COLUMNS_12)


def test_accessor_sorts_a_frame_that_is_not_monotonic():
    bulk = _bulk()
    bulk["UNSORTED"] = bulk["FLOAT"].iloc[::-1].reset_index(drop=True)
    scanner = FloatExhaustionScanner(window_days=WINDOW_DAYS)
    scanner._columnar = _window(bulk)

    df = scanner._get_tech_df("UNSORTED", MIN_DATE, AS_ON)
    assert df["date"].is_monotonic_increasing
    assert (
        df["close"].tolist()
        == _window().frame("FLOAT", MIN_DATE, AS_ON)["close"].tolist()
    )


def test_progress_hook_is_the_frame_accessor_the_loop_calls():
    source = Path("myra_web/routes/scanners.py").read_text(encoding="utf-8")
    block = source.split('register_scanner(\n    "float-exhaustion"', 1)[1]
    assert 'progress_attr="_get_tech_df"' in block.split(")", 1)[0]
    # The web wrapper calls the attribute with three positional arguments.
    scanner = FloatExhaustionScanner(window_days=WINDOW_DAYS)
    scanner._columnar = _window()
    df = scanner._get_tech_df("FLOAT", MIN_DATE, AS_ON)
    assert len(df) > 0


def test_phase39_selector_sees_the_frame_accessor():
    """The generic selective-materialisation harness auto-detects this."""
    assert hasattr(FloatExhaustionScanner, "_get_tech_df")
