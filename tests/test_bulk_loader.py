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
        from myra_app.strategies import dcb_bargain as mod
        from myra_app.strategies.dcb_bargain import DCBBargainScanner

        bulk, db = _scanner_parity(
            DCBBargainScanner(min_mcap=1500, max_mcap=2500, dcb_window=120), mod
        )
        # Both paths should produce the same result (even if both are empty)
        assert bulk == db
