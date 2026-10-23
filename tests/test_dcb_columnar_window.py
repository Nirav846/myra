"""Phase 43: DCB Bargain columnar access path.

Two contracts are covered here:

1. ``DCBBargainScanner._ColumnarWindow`` – the private frame store that lets the
   scanner materialise each symbol's DataFrame from contiguous numpy slices
   instead of rebuilding it per symbol with ``get_df_for_symbol``.
2. The scanner itself – candidates, scores and ordering must be byte-identical
   to the ``get_df_for_symbol``-per-symbol path, and the bulk dictionary must be
   released once the window owns the data.

Every expectation is either derived independently from the fixture rows or
compared against the legacy accessor, never against the window's own internals.
"""

from __future__ import annotations

import hashlib

import numpy as np
import pandas as pd
import pytest
from unittest.mock import patch

import myra_app.strategies.dcb_bargain as dcb_module
from myra_app.db.bulk_loader import COLUMNS_8, get_df_for_symbol
from myra_app.strategies.dcb_bargain import DCBBargainScanner, _ColumnarWindow

AS_ON = "2025-06-15"
MIN_DATE = "2023-08-07"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _rows(n: int, *, start: str = "2024-06-01", drift: float = 0.20) -> list[tuple]:
    """Daily OHLCV+delivery rows.

    ``drift`` > 0 walks the close down so the delivery-cost basis sits above the
    latest close (a DCB discount); ``drift`` == 0 keeps the price flat.
    Even bars carry 45% delivery, odd bars 10%, alternating up/down so
    ``del_abs`` stays near zero.
    """
    rows = []
    day = pd.Timestamp(start)
    close = 100.0
    for i in range(n):
        close = 100.0 - i * drift
        open_ = close - 1.0 if i % 2 == 0 else close + 1.0
        high = max(open_, close) + 0.5
        low = min(open_, close) - 0.5
        volume = 500_000
        del_pct = 45.0 if i % 2 == 0 else 10.0
        delivery = int(volume * del_pct / 100)
        rows.append(
            (
                (day + pd.Timedelta(days=i)).strftime("%Y-%m-%d"),
                open_,
                high,
                low,
                close,
                volume,
                delivery,
                del_pct,
            )
        )
    return rows


def _frame(n: int = 200, **kwargs) -> pd.DataFrame:
    df = pd.DataFrame(_rows(n, **kwargs), columns=list(COLUMNS_8))
    df["date"] = pd.to_datetime(df["date"])
    return df


def _mixed_bulk() -> dict[str, pd.DataFrame]:
    """A slice shaped like ``load_ohlcv_for_universe`` output.

    Includes qualifying symbols, symbols that cannot qualify, a short-history
    symbol, and an empty frame.
    """
    bulk: dict[str, pd.DataFrame] = {}
    for i in range(12):
        bulk[f"CDT{i:02d}"] = _frame(200 + i * 5, drift=0.20 + i * 0.02)
    for i in range(6):
        bulk[f"FLAT{i:02d}"] = _frame(200, drift=0.0)
    bulk["SHORT"] = _frame(40)
    bulk["EMPTY"] = _frame(0)
    return bulk


def _universe(bulk: dict[str, pd.DataFrame]) -> list[tuple]:
    return [(symbol, 5_000.0, 50.0) for symbol in bulk]


def _fingerprint(frame: pd.DataFrame) -> str:
    if frame.empty:
        return "empty"
    keyed = frame.copy()
    keyed.columns = [str(c) for c in keyed.columns]
    keyed = keyed.astype(str).sort_values(list(keyed.columns)).reset_index(drop=True)
    digest = hashlib.sha256(
        pd.util.hash_pandas_object(keyed, index=False).to_numpy().tobytes()
    ).hexdigest()
    return digest[:16]


def _legacy_tech_df(bulk, min_date, max_date, calls):
    """Reference accessor: the pre-Phase-43 ``get_df_for_symbol`` per symbol."""

    def _impl(self, symbol, min_d, max_d=None):
        calls["n"] += 1
        frame = get_df_for_symbol(bulk, symbol, COLUMNS_8, min_d, max_d)
        return pd.DataFrame() if frame is None else frame

    return _impl


# ---------------------------------------------------------------------------
# _ColumnarWindow: parity with the legacy accessor
# ---------------------------------------------------------------------------


def test_window_matches_legacy_accessor_for_every_symbol():
    bulk = _mixed_bulk()
    window = _ColumnarWindow.from_frames(bulk, COLUMNS_8)

    compared = 0
    for symbol, frame in bulk.items():
        expected = get_df_for_symbol(bulk, symbol, COLUMNS_8, MIN_DATE, AS_ON)
        actual = window.frame(symbol)
        if expected is None or expected.empty:
            assert actual is None, symbol
            continue
        pd.testing.assert_frame_equal(actual, expected, check_dtype=True)
        compared += 1
    assert compared == len(bulk) - 1  # everything except the empty frame


def test_window_matches_legacy_accessor_on_a_narrow_slice():
    bulk = _mixed_bulk()
    # Loader frames come back with a fresh RangeIndex; boolean filtering alone
    # would keep the old labels, so mirror the loader here.
    narrow = {
        k: v[v["date"] >= "2024-12-01"].reset_index(drop=True) for k, v in bulk.items()
    }
    window = _ColumnarWindow.from_frames(narrow, COLUMNS_8)

    for symbol in ("CDT00", "CDT05", "FLAT03"):
        expected = get_df_for_symbol(narrow, symbol, COLUMNS_8, MIN_DATE, AS_ON)
        pd.testing.assert_frame_equal(window.frame(symbol), expected, check_dtype=True)
    assert window.frame("CDT00")["date"].min() >= pd.Timestamp("2024-12-01")


def test_window_keeps_declared_column_order_and_dtypes():
    bulk = _mixed_bulk()
    window = _ColumnarWindow.from_frames(bulk, COLUMNS_8)

    assert window.columns == tuple(COLUMNS_8)
    frame = window.frame("CDT00")
    assert list(frame.columns) == list(COLUMNS_8)
    assert pd.api.types.is_datetime64_any_dtype(frame["date"])
    assert frame["date"].is_monotonic_increasing
    assert frame.index.tolist() == list(range(len(frame)))
    for column in COLUMNS_8:
        if column == "date":
            continue
        assert str(frame[column].dtype) == str(bulk["CDT00"][column].dtype), column


def test_window_preserves_supplied_row_order():
    unsorted = _frame(60)
    shuffled = unsorted.iloc[[5, 0, 3, 1, 2] + list(range(6, 60))].reset_index(
        drop=True
    )
    window = _ColumnarWindow.from_frames({"WEIRD": shuffled}, COLUMNS_8)

    assert window.frame("WEIRD")["date"].tolist() == shuffled["date"].tolist()


def test_window_drops_a_column_absent_from_one_frame():
    bulk = _mixed_bulk()
    bulk["DRIFT"] = bulk["CDT00"].drop(columns=["delivery_pct"])
    window = _ColumnarWindow.from_frames(bulk, COLUMNS_8)

    assert "delivery_pct" not in window.columns
    # Dropped for the whole slice, not just the symbol that lacked it: one
    # column set is what makes the store columnar. ``load_ohlcv_for_universe``
    # projects a single column list for every symbol, so this rule never fires
    # in production; it only keeps the window self-consistent.
    for symbol in ("CDT01", "FLAT00", "SHORT"):
        assert list(window.frame(symbol).columns) == [
            c for c in COLUMNS_8 if c != "delivery_pct"
        ]


def test_window_keeps_a_column_that_is_entirely_na():
    bulk = _mixed_bulk()
    bulk["NADEL"] = bulk["FLAT00"].assign(delivery_pct=np.nan)
    window = _ColumnarWindow.from_frames(bulk, COLUMNS_8)

    assert "delivery_pct" in window.columns
    frame = window.frame("NADEL")
    assert frame["delivery_pct"].isna().all()
    expected = get_df_for_symbol(bulk, "NADEL", COLUMNS_8, MIN_DATE, AS_ON)
    pd.testing.assert_frame_equal(frame, expected, check_dtype=True)
    # A sibling symbol still carries real values.
    assert window.frame("CDT00")["delivery_pct"].notna().any()


def test_window_returns_none_for_absent_and_empty_symbols():
    bulk = _mixed_bulk()
    window = _ColumnarWindow.from_frames(bulk, COLUMNS_8)

    assert window.frame("NOT-IN-SLICE") is None
    assert window.frame("EMPTY") is None


def test_window_from_no_frames_is_empty():
    window = _ColumnarWindow.from_frames({}, COLUMNS_8)
    assert window.spans == {}
    assert window.frame("ANY") is None
    # Every column is vacuously "present in all frames", but the arrays hold no
    # rows, so no symbol is ever served.
    assert window.columns == tuple(COLUMNS_8)
    assert all(len(array) == 0 for array in window.arrays.values())


def test_window_frames_do_not_share_memory_with_the_store():
    window = _ColumnarWindow.from_frames(_mixed_bulk(), COLUMNS_8)
    frame = window.frame("CDT00")

    for column in window.columns:
        assert not np.shares_memory(
            np.asarray(frame[column].to_numpy()), window.arrays[column]
        ), column


def test_mutating_a_window_frame_does_not_corrupt_later_frames():
    window = _ColumnarWindow.from_frames(_mixed_bulk(), COLUMNS_8)
    first = window.frame("CDT00")
    before = first["close"].tolist()

    first.loc[:, "close"] = -1.0
    first.loc[:, "date"] = pd.Timestamp("1999-01-01")

    second = window.frame("CDT00")
    assert second["close"].tolist() == before
    assert second["date"].min() > pd.Timestamp("1999-01-02")
    # Sibling symbols are untouched too.
    assert window.frame("CDT01")["close"].iloc[-1] > 0


def test_window_rebuilds_an_equivalent_frame_each_call():
    window = _ColumnarWindow.from_frames(_mixed_bulk(), COLUMNS_8)
    pd.testing.assert_frame_equal(window.frame("CDT07"), window.frame("CDT07"))


# ---------------------------------------------------------------------------
# Scanner: identical candidates through the columnar path
# ---------------------------------------------------------------------------


def _scan(monkeypatch, bulk, *, scanner=None, legacy=False):
    scanner = scanner or DCBBargainScanner(min_ff_mcap=0.0)
    monkeypatch.setattr(
        dcb_module, "load_ohlcv_for_universe", lambda *a, **k: dict(bulk)
    )
    monkeypatch.setattr(scanner, "_get_universe", lambda: _universe(bulk))
    calls = None
    if legacy:
        calls = {"n": 0}
        monkeypatch.setattr(
            DCBBargainScanner,
            "_get_tech_df",
            _legacy_tech_df(bulk, MIN_DATE, AS_ON, calls),
        )
    return scanner.scan(as_on_date=AS_ON), calls


def test_scan_matches_the_legacy_per_symbol_path(monkeypatch):
    bulk = _mixed_bulk()
    expected, legacy_calls = _scan(monkeypatch, bulk, legacy=True)
    actual, _ = _scan(monkeypatch, bulk)

    assert len(expected) > 0
    assert legacy_calls["n"] >= len(bulk) - 1  # the legacy arm did hit the accessor
    pd.testing.assert_frame_equal(actual, expected, check_dtype=True)
    assert _fingerprint(actual) == _fingerprint(expected)


def test_scan_is_repeatable_across_runs(monkeypatch):
    bulk = _mixed_bulk()
    first, _ = _scan(monkeypatch, bulk)
    second, _ = _scan(monkeypatch, bulk)

    pd.testing.assert_frame_equal(first, second, check_dtype=True)
    assert _fingerprint(first) == _fingerprint(second)


def test_scan_releases_the_bulk_dictionary(monkeypatch):
    bulk = _mixed_bulk()
    scanner = DCBBargainScanner(min_ff_mcap=0.0)
    _scan(monkeypatch, bulk, scanner=scanner)

    assert scanner._columnar is not None
    assert scanner._bulk_data is None
    assert scanner._columnar.spans  # the window kept the data


def test_scan_columns_and_scores_are_unchanged(monkeypatch):
    bulk = _mixed_bulk()
    result, _ = _scan(monkeypatch, bulk)

    for column in ("symbol", "close", "dcb", "discount_pct", "score", "tier"):
        assert column in result.columns
    row = result.iloc[0]
    # score is documented as 0.6 * discount_pct + 0.4 * del_abs (rounded).
    assert row["score"] == pytest.approx(
        round(row["discount_pct"] * 0.6 + row["del_abs"] * 0.4, 2), abs=0.011
    )
    assert row["discount_pct"] == pytest.approx(
        (row["dcb"] - row["close"]) / row["dcb"] * 100.0, abs=0.02
    )


def test_scan_skips_short_history_but_keeps_long_history(monkeypatch):
    bulk = {"SHORT": _frame(40), "LONG": _frame(200)}
    result, _ = _scan(monkeypatch, bulk)

    assert result["symbol"].tolist() == ["LONG"]


def test_scan_skips_a_symbol_missing_from_the_slice(monkeypatch):
    bulk = _mixed_bulk()
    scanner = DCBBargainScanner(min_ff_mcap=0.0)
    monkeypatch.setattr(
        dcb_module, "load_ohlcv_for_universe", lambda *a, **k: dict(bulk)
    )
    monkeypatch.setattr(
        scanner, "_get_universe", lambda: _universe(bulk) + [("GHOST", 5_000.0, 50.0)]
    )
    result = scanner.scan(as_on_date=AS_ON)
    # Same fixture without the ghost symbol: the symbol set must be unchanged.
    reference, _ = _scan(monkeypatch, bulk)

    assert "GHOST" not in result["symbol"].tolist()
    assert result["symbol"].tolist() == reference["symbol"].tolist()
    assert len(result) == len(reference) > 0


@pytest.mark.filterwarnings("ignore:Mean of empty slice:RuntimeWarning")
def test_scan_follows_the_legacy_arm_when_delivery_pct_is_all_na(monkeypatch):
    """A column that exists but is empty for one symbol must not change others.

    An all-NULL ``delivery_pct`` makes the DCB discount window hit
    ``np.nanmean`` on an empty slice — pre-existing behaviour, identical in
    both arms, and deliberately not changed here.
    """
    bulk = _mixed_bulk()
    bulk["NADEL"] = bulk["FLAT00"].assign(delivery_pct=np.nan)

    expected, _ = _scan(monkeypatch, bulk, legacy=True)
    actual, _ = _scan(monkeypatch, bulk)

    pd.testing.assert_frame_equal(actual, expected, check_dtype=True)
    assert _fingerprint(actual) == _fingerprint(expected)


def test_scan_returns_empty_without_a_snapshot():
    scanner = DCBBargainScanner()
    assert scanner._get_tech_df("ANY", MIN_DATE, AS_ON).empty
    assert scanner._get_tech_data("ANY", MIN_DATE, AS_ON) == []


# ---------------------------------------------------------------------------
# The legacy tuple accessor keeps its contract
# ---------------------------------------------------------------------------


def test_get_tech_data_still_returns_plain_tuples():
    bulk = _mixed_bulk()
    scanner = DCBBargainScanner()
    scanner._bulk_data = bulk

    rows = scanner._get_tech_data("CDT00", MIN_DATE, AS_ON)

    expected = bulk["CDT00"]
    assert len(rows) == len(expected)
    assert rows[0] == tuple(expected.iloc[0].tolist())
    assert rows[-1] == tuple(expected.iloc[-1].tolist())
    assert isinstance(rows[0], tuple)
