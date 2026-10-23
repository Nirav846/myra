"""Phase 45: Darvas Box Pro columnar window.

The scan used to ask ``_get_tech_data`` (→ ``rows_for_symbol``) for a twelve
column tuple list per symbol, wrap it in a DataFrame, re-parse the date strings
and sort — then hand that frame to ``_detect_box``, which sorted it again. The
frame is genuinely required by the detector, so this suite pins the accessor it
is built from:

* ``_ColumnarWindow.frame`` must carry the same values, the same column order
  and the same row order as the pre-change tuple path;
* ``_detect_box`` / ``_compute_box_dar`` must return identical dicts for both;
* ``scan()`` must emit the same candidates, ordering and fingerprint;
* absent / empty / short / whitespace symbols and absent columns keep their
  previous behaviour;
* returned frames own their data.

Expectations come from the fixture geometry, never from the window internals.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import myra_app.strategies.darvas_box_scanner as dbs
from myra_app.db.bulk_loader import COLUMNS_12, rows_for_symbol
from myra_app.strategies.darvas_box_scanner import DarvasBoxScanner, _ColumnarWindow

AS_ON = "2026-10-16"
BASE_DAYS = 120
MIN_DATE = f"{(pd.Timestamp(AS_ON) - pd.Timedelta(days=240)):%Y-%m-%d}"

# Universe shapes the scanner expects: (symbol, mcap, free_float_pct, ff_mcap).
MCAP = 5e9  # 500 Cr → "small" tier
FF_MCAP = 1e7


# ---------------------------------------------------------------------------
# Fixtures: an explicit box geometry, so the goldens are derived by hand
# ---------------------------------------------------------------------------


def _bars(n=140, breakout=True):
    """140 daily bars ending on AS_ON.

    Ramp 80 → 118 (n-40), ceiling touched twice at 120.00, floor touched ten
    times at 111.00, then a breakout close at 121.00 (inside the detector's
    1 % ceiling tolerance, above the 0.5 % entry buffer).
    """
    dates = pd.date_range(end=AS_ON, periods=n, freq="D")
    rows = []
    for i, day in enumerate(dates):
        if i <= 99:
            close = 80 + 0.38 * i
            high, low = close + 0.5, close - 1.0
            volume = delivery = 1000
        elif i == 100:
            close, high, low, volume, delivery = 118.5, 120.0, 116.0, 1000, 1000
        elif i <= 104:
            close, high, low, volume, delivery = 117.0, 118.5, 115.0, 1000, 1000
        elif i == 105:
            close, high, low, volume, delivery = 117.5, 120.0, 114.0, 1000, 1000
        elif i <= 115:
            close, high, low, volume, delivery = 117.0, 118.0, 111.0, 1000, 1000
        elif i <= 138:
            close, high, low, volume, delivery = 118.0, 118.5, 113.0, 1000, 1000
        else:
            if breakout:
                close, high, low, volume, delivery = 121.0, 121.5, 120.5, 3000, 3000
            else:
                close, high, low, volume, delivery = 119.0, 119.5, 117.0, 1000, 1000
        rows.append(
            (
                day.strftime("%Y-%m-%d"),
                close - 0.5,
                high,
                low,
                close,
                volume,
                delivery,
                40.0,
                0.5,
                None,
                125.0,
                100.0,
            )
        )
    return rows


def _frame(rows, columns=COLUMNS_12):
    return pd.DataFrame(list(rows), columns=list(columns))


def _bulk():
    return {
        "BOX": _frame(_bars()),
        "INBOX": _frame(_bars(breakout=False)),
        "SHORT": _frame(_bars()[:20]),
        "EMPTY": _frame([]),
    }


def _universe(symbols):
    return [(symbol, MCAP, 40.0, FF_MCAP) for symbol in symbols]


def _fingerprint(frame: pd.DataFrame) -> str:
    if frame.empty:
        return "empty"
    keyed = frame.copy()
    keyed.columns = [str(c) for c in keyed.columns]
    keyed = keyed.astype(str).sort_values(list(keyed.columns)).reset_index(drop=True)
    return hashlib.sha256(
        pd.util.hash_pandas_object(keyed, index=False).to_numpy().tobytes()
    ).hexdigest()[:16]


def _tuple_frame(rows):
    """The pre-change scan built its frame exactly like this."""
    if not rows:
        return pd.DataFrame(columns=list(COLUMNS_12))
    df = pd.DataFrame(rows, columns=list(COLUMNS_12))
    df["date"] = pd.to_datetime(df["date"])
    return df.sort_values("date").reset_index(drop=True)


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
    for symbol in ("BOX", "INBOX", "SHORT"):
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
    window = _window()
    df = window.frame("BOX", MIN_DATE, AS_ON)

    assert df["date"].dtype == pd.to_datetime(pd.Series(["2026-01-02"])).dtype
    assert df["close"].dtype == np.float64
    assert df["high"].dtype == np.float64
    assert df["low"].dtype == np.float64
    assert df["volume"].dtype == np.int64
    assert isinstance(df.index, pd.RangeIndex)


def test_detection_outputs_are_identical_for_both_paths():
    bulk = _bulk()
    window = _window(bulk)
    scanner = DarvasBoxScanner(base_days=BASE_DAYS)
    for symbol in ("BOX", "INBOX"):
        got = window.frame(symbol, MIN_DATE, AS_ON)
        want = _tuple_frame(rows_for_symbol(bulk, symbol, COLUMNS_12, MIN_DATE, AS_ON))
        assert scanner._detect_box(got) == scanner._detect_box(want)
        box = scanner._detect_box(want)
        assert box is not None
        assert scanner._compute_box_dar(
            got, box["box_start_idx"], box["box_end_idx"], FF_MCAP, box["ceiling"]
        ) == scanner._compute_box_dar(
            want, box["box_start_idx"], box["box_end_idx"], FF_MCAP, box["ceiling"]
        )


def test_narrow_window_matches_the_tuple_path():
    bulk = _bulk()
    window = _window(bulk)
    narrow = (pd.Timestamp(AS_ON) - pd.Timedelta(days=100)).strftime("%Y-%m-%d")

    for symbol in ("BOX", "INBOX"):
        got = window.frame(symbol, narrow, AS_ON)
        want = _tuple_frame(rows_for_symbol(bulk, symbol, COLUMNS_12, narrow, AS_ON))
        assert len(got) == len(want) < len(bulk[symbol])
        pd.testing.assert_frame_equal(
            got.astype(object).reset_index(drop=True),
            want.astype(object).reset_index(drop=True),
        )


def test_rows_stay_in_chronological_snapshot_order():
    window = _window()
    df = window.frame("BOX", MIN_DATE, AS_ON)

    assert df["date"].is_monotonic_increasing
    assert len(set(df["date"])) == len(df)
    assert df["date"].iloc[-1] == pd.Timestamp(AS_ON)


def test_a_column_absent_from_the_slice_is_none_padded_in_place():
    bulk = _bulk()
    bulk["DRIFT"] = bulk["BOX"].drop(columns=["high_52w"])
    window = _window(bulk)

    df = window.frame("DRIFT", MIN_DATE, AS_ON)
    assert list(df.columns) == list(COLUMNS_12)
    assert df["high_52w"].isna().all()
    assert df["close"].dtype == np.float64  # neighbours keep their values
    want = rows_for_symbol(bulk, "DRIFT", COLUMNS_12, MIN_DATE, AS_ON)
    assert want[0][10] is None  # high_52w padded, sma_50 keeps index 9
    pd.testing.assert_frame_equal(
        df.astype(object).reset_index(drop=True),
        _tuple_frame(want).astype(object).reset_index(drop=True),
    )


def test_absent_empty_and_unknown_symbols_serve_nothing():
    bulk = _bulk()
    window = _window(bulk)

    assert window.frame("GONE", MIN_DATE, AS_ON) is None
    # "EMPTY" exists in the snapshot but owns no rows: same as absent.
    assert window.frame("EMPTY", MIN_DATE, AS_ON) is None
    # A window over an empty slice serves nothing at all.
    assert (
        _ColumnarWindow.from_frames({}, COLUMNS_12).frame("BOX", MIN_DATE, AS_ON)
        is None
    )


def test_a_window_outside_the_data_returns_no_frame():
    window = _window()
    before = (pd.Timestamp(MIN_DATE) - pd.Timedelta(days=400)).strftime("%Y-%m-%d")
    earlier = (pd.Timestamp(before) + pd.Timedelta(days=10)).strftime("%Y-%m-%d")
    assert window.frame("BOX", before, earlier) is None


def test_returned_frames_own_their_data():
    window = _window()
    first = window.frame("BOX", MIN_DATE, AS_ON)
    baseline = first["close"].tolist()

    assert not np.shares_memory(first["close"].to_numpy(), window.arrays["close"])
    first.loc[:, "close"] = -1.0
    first.loc[:, "date"] = pd.Timestamp("1999-01-01")

    again = window.frame("BOX", MIN_DATE, AS_ON)
    assert again["close"].tolist() == baseline
    assert again["date"].iloc[-1] == pd.Timestamp(AS_ON)
    # A sibling symbol was never reachable through the first frame.
    assert window.frame("INBOX", MIN_DATE, AS_ON)["close"].iloc[-1] == 119.0


def test_a_frame_with_stale_index_is_never_served():
    window = _window()
    df = window.frame("BOX", MIN_DATE, AS_ON)
    assert list(df.index) == list(range(len(df)))


# ---------------------------------------------------------------------------
# Scanner: identical candidates, lifecycle, fallback
# ---------------------------------------------------------------------------


def _legacy_get_tech_df(bulk):
    """Pre-change accessor: tuples → DataFrame → datetime → sort."""

    def impl(symbol, min_date, max_date=None):
        rows = rows_for_symbol(bulk, symbol, COLUMNS_12, min_date, max_date or AS_ON)
        if not rows:
            return pd.DataFrame(columns=list(COLUMNS_12))
        return _tuple_frame(rows)

    return impl


def _scan(monkeypatch, bulk, *, legacy=False, tag="a", symbols=None):
    scanner = DarvasBoxScanner(
        base_days=BASE_DAYS, min_dar=0.2, min_mcap=100, max_mcap=50000
    )
    symbols = list(bulk) if symbols is None else symbols
    monkeypatch.setattr(scanner, "_get_universe", lambda: _universe(symbols))
    monkeypatch.setattr(dbs, "load_ohlcv_for_universe", lambda *a, **k: dict(bulk))
    monkeypatch.setattr(scanner, "_db_path", lambda key: ":memory:")
    if legacy:
        monkeypatch.setattr(scanner, "_get_tech_df", _legacy_get_tech_df(bulk))
    return scanner, scanner.scan(as_on_date=AS_ON)


def test_scan_matches_the_legacy_tuple_path(monkeypatch):
    bulk = _bulk()
    _, expected = _scan(monkeypatch, bulk, legacy=True, tag="legacy")
    _, actual = _scan(monkeypatch, bulk)

    assert len(expected) >= 1, "the fixture must yield candidates"
    pd.testing.assert_frame_equal(actual, expected, check_dtype=True)
    assert _fingerprint(actual) == _fingerprint(expected)
    assert actual["symbol"].tolist() == expected["symbol"].tolist()


def test_scan_candidate_fields_are_unchanged(monkeypatch):
    _, result = _scan(monkeypatch, _bulk())

    row = result[result["symbol"] == "BOX"].iloc[0]
    # Derived from the fixture geometry: ceiling 120.00 touched twice, floor
    # 111.00 touched ten times, 39 bars from the first ceiling touch to the
    # last bar, range (120-111)/111, breakout close 121.00 with 3x volume.
    assert row["ceiling_price"] == pytest.approx(120.0)
    assert row["floor_price"] == pytest.approx(111.0)
    assert row["box_age_days"] == 39
    assert row["box_range_pct"] == pytest.approx(8.11, abs=0.01)
    assert row["touches_ceiling"] == 2
    assert row["touches_floor"] == 10
    assert row["tier"] == "small"
    assert row["close"] == pytest.approx(121.0)
    assert row["status"] == "Triggered"
    assert row["volume_ok"] == True
    assert row["entry"] == pytest.approx(120.6)
    assert row["sl"] == pytest.approx(110.44)
    assert row["t1"] == pytest.approx(130.75)
    assert row["t2"] == pytest.approx(140.91)
    # DAR = delivery * close / free-float mcap * 100 over the box.
    assert row["dar_box_median"] == pytest.approx(1.18, abs=0.01)
    assert row["breakout_dar"] == pytest.approx(3.63, abs=0.01)
    assert row["am"] == pytest.approx(3.08, abs=0.01)
    assert row["composite_score"] == pytest.approx(57.7, abs=0.1)
    assert row["grade"] == "B"
    assert row["sector"] == "Unknown"  # sector map is empty in a hermetic run
    assert row["market_cap_cr"] == pytest.approx(500.0)


def test_scan_reports_an_untriggered_box_without_a_trade(monkeypatch):
    _, result = _scan(monkeypatch, _bulk())

    row = result[result["symbol"] == "INBOX"].iloc[0]
    assert row["status"] == "In Box"
    assert row["close"] == pytest.approx(119.0)
    assert pd.isna(row["entry"])
    assert pd.isna(row["sl"])
    assert pd.isna(row["t1"])
    assert row["volume_ok"] == False
    assert row["composite_score"] > 0  # pre-breakout scoring still runs


def test_scan_skips_short_and_ghost_symbols(monkeypatch):
    bulk = _bulk()
    scanner, result = _scan(
        monkeypatch, bulk, symbols=["BOX", "SHORT", "GONE", "EMPTY"]
    )

    assert result["symbol"].tolist() == ["BOX"]
    assert set(scanner._columnar.spans) == {"BOX", "INBOX", "SHORT"}


def test_scan_normalises_a_whitespace_universe_entry(monkeypatch):
    _, result = _scan(monkeypatch, _bulk(), symbols=[" BOX "])

    assert result["symbol"].tolist() == ["BOX"]


def test_scan_is_repeatable_and_releases_the_bulk_dictionary(monkeypatch):
    bulk = _bulk()
    scanner, first = _scan(monkeypatch, bulk)
    _, second = _scan(monkeypatch, bulk)

    pd.testing.assert_frame_equal(first, second, check_dtype=True)
    assert scanner._columnar is not None
    assert scanner._bulk_data is None
    assert scanner._columnar.spans


def test_scan_without_a_snapshot_falls_back_to_the_rows_accessor(monkeypatch):
    """No bulk load (empty dict) must keep the pre-change tuple branch."""
    scanner = DarvasBoxScanner(
        base_days=BASE_DAYS, min_dar=0.2, min_mcap=100, max_mcap=50000
    )
    monkeypatch.setattr(scanner, "_get_universe", lambda: _universe(["BOX"]))
    monkeypatch.setattr(dbs, "load_ohlcv_for_universe", lambda *a, **k: {})
    monkeypatch.setattr(scanner, "_db_path", lambda key: ":memory:")
    monkeypatch.setattr(
        scanner, "_get_tech_data", lambda symbol, min_date, max_date=None: _bars()
    )

    result = scanner.scan(as_on_date=AS_ON)

    assert scanner._columnar is None
    assert result["symbol"].tolist() == ["BOX"]
    assert result["status"].tolist() == ["Triggered"]


def test_no_snapshot_with_no_rows_yields_an_empty_frame(monkeypatch):
    scanner = DarvasBoxScanner(base_days=BASE_DAYS)
    monkeypatch.setattr(scanner, "_db_path", lambda key: ":memory:")
    monkeypatch.setattr(
        scanner, "_get_tech_data", lambda symbol, min_date, max_date=None: []
    )

    df = scanner._get_tech_df("BOX", MIN_DATE, AS_ON)

    assert df.empty
    assert list(df.columns) == list(COLUMNS_12)


def test_get_tech_df_builds_the_nine_column_shape_when_the_schema_is_old(monkeypatch):
    scanner = DarvasBoxScanner(base_days=BASE_DAYS)
    rows = [row[:9] for row in _bars()]
    monkeypatch.setattr(scanner, "_db_path", lambda key: ":memory:")
    monkeypatch.setattr(
        scanner, "_get_tech_data", lambda symbol, min_date, max_date=None: rows
    )

    df = scanner._get_tech_df("BOX", MIN_DATE, AS_ON)

    assert list(df.columns) == list(COLUMNS_12)
    assert df["sma_50"].isna().all()
    assert df["high_52w"].isna().all()
    assert df["low_52w"].isna().all()
    assert df["close"].iloc[-1] == pytest.approx(121.0)


# ---------------------------------------------------------------------------
# Web wiring: the progress hook must follow the loop's accessor
# ---------------------------------------------------------------------------


def test_get_tech_df_signature_matches_the_progress_hook():
    """``register_scanner``'s wrapper calls ``(symbol, min_date, max_date)``."""
    scanner = DarvasBoxScanner(base_days=BASE_DAYS)
    calls = []
    untouched = scanner._get_tech_df

    def tracked(symbol, min_date=None, max_date=None):
        calls.append((symbol, min_date, max_date))
        return untouched(symbol, min_date, max_date)

    scanner._get_tech_df = tracked
    scanner._get_tech_data = lambda symbol, min_date, max_date=None: _bars()

    df = scanner._get_tech_df("BOX", MIN_DATE, AS_ON)

    assert calls == [("BOX", MIN_DATE, AS_ON)]
    assert len(df) == 140


def test_darvas_registration_tracks_the_frame_accessor():
    """The registration must patch the attribute the loop actually calls."""
    source = Path("myra_web/routes/scanners.py").read_text(encoding="utf-8")
    start = source.index('register_scanner(\n    "darvas"')
    block = source[start : source.index("\n)\n", start)]
    assert re.search(r'progress_attr="_get_tech_df"', block)
