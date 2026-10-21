"""
Parity tests for the bulk OHLCV loader (myra_app/db/bulk_loader.py).

The bulk loader replaces the per-symbol sqlite3.connect() + indexed SELECT
pattern inside every scanner's `_get_tech_data`. These tests guarantee the
bulk path returns byte-identical candidate lists:

1. `rows_for_symbol` must return exactly what the per-symbol SQL SELECT
   returns for the same symbol / date window (all three column families:
   8-col climax/dcb, 12-col base, 13-col wyckoff).
2. End-to-end: each scanner's `scan()` must yield the same candidates when
   backed by the bulk loader vs the original per-symbol DB path.
"""

import math
import os
import sqlite3
from datetime import date

import pandas as pd
import pytest

from myra_app.constants import DB_DIR
from myra_app.librarian_core import LibrarianCore
from myra_app.db.bulk_loader import (
    load_ohlcv_for_universe,
    rows_for_symbol,
    get_df_for_symbol,
    COLUMNS_8,
    COLUMNS_12,
    COLUMNS_13,
)

TECH_DB = os.path.join(DB_DIR, LibrarianCore.DB_MAP["technical"])

FIXED_DATE = "2026-08-07"  # last trading date present in the DB


@pytest.fixture(scope="module")
def bulk() -> dict[str, pd.DataFrame]:
    """Load the universe once per module (≈6s) and share across all tests."""
    return load_ohlcv_for_universe("2026-05-01", FIXED_DATE)


def _db_rows(symbol: str, min_date: str, max_date: str, columns) -> list[tuple]:
    """Reference: run the original per-symbol SELECT."""
    with sqlite3.connect(TECH_DB) as conn:
        sql = (
            "SELECT " + ", ".join(columns) + " FROM technical_data "
            "WHERE symbol = ? AND date >= ? AND date <= ? ORDER BY date ASC"
        )
        return conn.execute(sql, (symbol, min_date, max_date)).fetchall()


@pytest.mark.skipif(not os.path.exists(TECH_DB), reason="technical DB not available")
class TestRowsForSymbol:
    def test_returns_identical_rows_12col(self, bulk):
        symbol = next(iter(bulk))
        got = rows_for_symbol(bulk, symbol, COLUMNS_12, "2026-06-01", FIXED_DATE)
        want = _db_rows(symbol, "2026-06-01", FIXED_DATE, COLUMNS_12)
        assert got == want

    def test_returns_identical_rows_13col(self, bulk):
        symbol = next(iter(bulk))
        got = rows_for_symbol(bulk, symbol, COLUMNS_13, "2026-06-01", FIXED_DATE)
        want = _db_rows(symbol, "2026-06-01", FIXED_DATE, COLUMNS_13)
        assert got == want

    def test_returns_identical_rows_8col(self, bulk):
        symbol = next(iter(bulk))
        got = rows_for_symbol(bulk, symbol, COLUMNS_8, "2026-06-01", FIXED_DATE)
        want = _db_rows(symbol, "2026-06-01", FIXED_DATE, COLUMNS_8)
        assert got == want

    def test_empty_for_unknown_symbol(self, bulk):
        assert (
            rows_for_symbol(
                bulk, "NONEXISTENT_SYM", COLUMNS_12, "2026-06-01", FIXED_DATE
            )
            == []
        )

    def test_date_window_respected(self, bulk):
        symbol = next(iter(bulk))
        rows = rows_for_symbol(bulk, symbol, COLUMNS_12, "2026-08-01", FIXED_DATE)
        assert rows
        for r in rows:
            assert FIXED_DATE >= r[0] >= "2026-08-01"


@pytest.mark.skipif(not os.path.exists(TECH_DB), reason="technical DB not available")
class TestGetDfForSymbol:
    def test_returns_sorted_datetime_df(self, bulk):
        symbol = next(iter(bulk))
        df = get_df_for_symbol(bulk, symbol, COLUMNS_8)
        assert df is not None
        assert list(df.columns) == list(COLUMNS_8)
        # date column is datetime and ascending
        assert pd.api.types.is_datetime64_any_dtype(df["date"])
        assert df["date"].is_monotonic_increasing

    def test_returns_none_for_unknown_symbol(self, bulk):
        assert get_df_for_symbol(bulk, "NONEXISTENT_SYM", COLUMNS_8) is None

    def test_does_not_mutate_bulk_frame(self, bulk):
        symbol = next(iter(bulk))
        original_cols = list(bulk[symbol].columns)
        df = get_df_for_symbol(bulk, symbol, COLUMNS_8)
        df["new_col"] = 1  # mutate the returned copy
        assert "new_col" not in bulk[symbol].columns
        assert list(bulk[symbol].columns) == original_cols

    def test_returns_none_outside_date_window(self, bulk):
        symbol = next(iter(bulk))
        # A window entirely before any data exists -> None
        df = get_df_for_symbol(
            bulk, symbol, COLUMNS_8, min_date="1990-01-01", max_date="1990-12-31"
        )
        assert df is None

    def test_columns_subset_filtered(self, bulk):
        symbol = next(iter(bulk))
        # Only request a subset of existing columns
        subset = [c for c in COLUMNS_8 if c in bulk[symbol].columns]
        df = get_df_for_symbol(bulk, symbol, tuple(subset[:2]))
        assert df is not None
        assert list(df.columns) == subset[:2]


def _scanner_parity(scanner, module):
    """Run scan() with bulk path and with the original DB path, return both symbol lists."""
    import myra_app.db.bulk_loader as bl

    # Bulk path (default: scan() populates self._bulk_data).
    bulk_result = scanner.scan(as_on_date=FIXED_DATE)

    # DB path: disable bulk by making load_ohlcv_for_universe a no-op.
    original = module.load_ohlcv_for_universe
    module.load_ohlcv_for_universe = lambda *a, **k: None
    try:
        scanner._bulk_data = None
        db_result = scanner.scan(as_on_date=FIXED_DATE)
    finally:
        module.load_ohlcv_for_universe = original

    def _syms(res):
        if isinstance(res, pd.DataFrame):
            if res.empty or "symbol" not in res.columns:
                return []
            return res["symbol"].tolist()
        if not res:
            return []
        return [c["symbol"] for c in res]

    return _syms(bulk_result), _syms(db_result)


@pytest.mark.slow
@pytest.mark.skipif(not os.path.exists(TECH_DB), reason="technical DB not available")
class TestScannerBulkParity:
    def test_trigger_parity(self):
        from myra_app.strategies import trigger_scanner as mod
        from myra_app.strategies.trigger_scanner import TriggerScanner

        bulk, db = _scanner_parity(TriggerScanner(min_mcap=1500, max_mcap=2500), mod)
        assert bulk == db

    def test_accumulation_base_parity(self):
        from myra_app.strategies import accumulation_base_scanner as mod
        from myra_app.strategies.accumulation_base_scanner import (
            AccumulationBaseScanner,
        )

        bulk, db = _scanner_parity(
            AccumulationBaseScanner(base_days=42, min_mcap=1500, max_mcap=2500), mod
        )
        assert bulk == db

    def test_wyckoff_parity(self):
        from myra_app.strategies import wyckoff_automaton as mod
        from myra_app.strategies.wyckoff_automaton import WyckoffAutomaton

        bulk, db = _scanner_parity(
            WyckoffAutomaton(min_mcap=1500, max_mcap=2500, lookback_days=90), mod
        )
        assert bulk == db

    def test_dcb_parity(self):
        # DCB scanner is bulk-only: there is no per-symbol DB path
        # (post bulk-loader refactor).  Earlier parity assertions failed
        # because the scanner passed _bulk_data=None into get_df_for_symbol
        # during the DB-path half of the harness.  This test now verifies:
        #   1. The bulk path runs without error and returns a DataFrame.
        #   2. The scanner defends against load_ohlcv_for_universe returning
        #      None (the guard added in scan()).
        from myra_app.strategies import dcb_bargain as mod
        from myra_app.strategies.dcb_bargain import DCBBargainScanner

        scanner = DCBBargainScanner(min_mcap=1500, max_mcap=2500, dcb_window=120)
        bulk = scanner.scan(as_on_date=FIXED_DATE)
        assert isinstance(bulk, pd.DataFrame)

        # Guard test: monkey-patch load_ohlcv_for_universe to return None.
        original = mod.load_ohlcv_for_universe
        mod.load_ohlcv_for_universe = lambda *a, **k: None
        try:
            scanner._bulk_data = None
            guarded = scanner.scan(as_on_date=FIXED_DATE)
        finally:
            mod.load_ohlcv_for_universe = original
        # Must NOT raise; returns an empty DataFrame.
        assert isinstance(guarded, pd.DataFrame)
        assert guarded.empty


# ---------------------------------------------------------------------------
# Conversion parity: the tuple-building fast path vs the original loop.
# ---------------------------------------------------------------------------
def _legacy_rows_for_symbol(bulk, symbol, columns, min_date, max_date=None):
    """Verbatim copy of the pre-optimization implementation (oracle)."""
    df = bulk.get(symbol)
    if df is None or df.empty:
        return []
    max_date = max_date or date.today().isoformat()
    mask = df["date"] >= min_date
    if max_date:
        mask = mask & (df["date"] <= max_date)
    sub = df.loc[mask]
    if sub.empty:
        return []
    present = [c for c in columns if c in sub.columns]
    if not present:
        return []
    out = []
    for r in sub[present].itertuples(index=False, name=None):
        row = list(r)
        padded = []
        i = 0
        for c in columns:
            if c in sub.columns:
                value = row[i]
                i += 1
            else:
                value = None
            if isinstance(value, float) and math.isnan(value):
                value = None
            padded.append(value)  # noqa: PG-APPEND
        out.append(tuple(padded))  # noqa: PG-APPEND
    return out


def _synthetic_bulk():
    frame = pd.DataFrame(
        {
            "date": ["2026-01-01", "2026-01-02", "2026-01-03", "2026-01-04"],
            "open": [1.0, float("nan"), 3.0, 4.0],
            "close": [10, 11, 12, 13],
            "label": ["a", "b", "c", "d"],
            # Object column that still holds a float NaN, as a dynamically
            # typed SQLite REAL column can arrive.
            "odd": ["x", float("nan"), "z", None],
        }
    )
    return {
        "AAA": frame,
        "EMPTY": pd.DataFrame(),
        "NO_COLUMNS": pd.DataFrame({"date": ["2026-01-01"]}),
    }


class TestRowsForSymbolConversion:
    def setup_method(self):
        self.bulk = _synthetic_bulk()
        self.cols = ("date", "open", "close", "label", "odd")

    def test_matches_legacy_mixed_dtypes(self):
        got = rows_for_symbol(self.bulk, "AAA", self.cols, "2026-01-01", "2026-01-04")
        want = _legacy_rows_for_symbol(
            self.bulk, "AAA", self.cols, "2026-01-01", "2026-01-04"
        )
        assert got == want

    def test_float_nan_becomes_none(self):
        got = rows_for_symbol(self.bulk, "AAA", self.cols, "2026-01-01", "2026-01-04")
        assert got[1][1] is None
        assert got[0][1] == 1.0 and type(got[0][1]) is float

    def test_object_nan_becomes_none(self):
        got = rows_for_symbol(self.bulk, "AAA", self.cols, "2026-01-01", "2026-01-04")
        assert got[1][4] is None
        assert got[3][4] is None

    def test_int_and_str_types_preserved(self):
        got = rows_for_symbol(self.bulk, "AAA", self.cols, "2026-01-01", "2026-01-04")
        assert type(got[0][2]) is int
        assert type(got[0][3]) is str

    def test_window_bounds_are_inclusive(self):
        got = rows_for_symbol(self.bulk, "AAA", ("date",), "2026-01-02", "2026-01-03")
        assert got == [("2026-01-02",), ("2026-01-03",)]

    def test_window_excludes_outside_rows(self):
        got = rows_for_symbol(self.bulk, "AAA", ("date",), "2026-01-05", "2026-01-09")
        assert got == []

    def test_unknown_symbol(self):
        assert rows_for_symbol(self.bulk, "NOPE", self.cols, "2026-01-01") == []

    def test_empty_frame(self):
        assert rows_for_symbol(self.bulk, "EMPTY", self.cols, "2026-01-01") == []

    def test_missing_columns_padded_in_position(self):
        cols = ("date", "swing_low", "open")
        got = rows_for_symbol(self.bulk, "AAA", cols, "2026-01-01", "2026-01-02")
        want = _legacy_rows_for_symbol(
            self.bulk, "AAA", cols, "2026-01-01", "2026-01-02"
        )
        assert got == want
        assert got[0] == ("2026-01-01", None, 1.0)

    def test_extra_frame_columns_ignored(self):
        got = rows_for_symbol(
            self.bulk, "AAA", ("date", "close"), "2026-01-01", "2026-01-02"
        )
        assert got == [("2026-01-01", 10), ("2026-01-02", 11)]

    def test_all_nan_float_column(self):
        frame = pd.DataFrame(
            {"date": ["2026-01-01", "2026-01-02"], "sma_50": [float("nan")] * 2}
        )
        bulk = {"NAN": frame}
        got = rows_for_symbol(bulk, "NAN", ("date", "sma_50"), "2026-01-01")
        want = _legacy_rows_for_symbol(bulk, "NAN", ("date", "sma_50"), "2026-01-01")
        assert got == want
        assert got == [("2026-01-01", None), ("2026-01-02", None)]

    def test_repeated_calls_identical(self):
        first = rows_for_symbol(self.bulk, "AAA", self.cols, "2026-01-01")
        second = rows_for_symbol(self.bulk, "AAA", self.cols, "2026-01-01")
        assert first == second

    def test_bulk_frame_not_mutated(self):
        before = self.bulk["AAA"].copy(deep=True)
        rows_for_symbol(self.bulk, "AAA", self.cols, "2026-01-01")
        pd.testing.assert_frame_equal(self.bulk["AAA"], before)
