"""
Tests for the Wyckoff legacy-schema fallback in `_get_tech_data`.

On databases that predate the `swing_low` column (12-col table), the first
SELECT (13 columns incl. `swing_low`) raises OperationalError and the
function falls back to `NULL AS swing_low` padding. The fallback must return
13 values in COLUMNS_13 positional order (swing_low at index 8, value None).
"""

import os
import sqlite3

import pytest

import myra_app.strategies.wyckoff_automaton as wy_mod
from myra_app.strategies.wyckoff_automaton import WyckoffAutomaton


@pytest.fixture
def legacy_tech_db(tmp_path, monkeypatch):
    """A 12-column technical_data table (no swing_low) in an isolated DB_DIR."""
    monkeypatch.setattr(wy_mod, "DB_DIR", str(tmp_path))
    db_path = os.path.join(str(tmp_path), "myra_technical.db")
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE technical_data (
                symbol TEXT, date TEXT, open REAL, high REAL, low REAL,
                close REAL, volume REAL, delivery REAL, delivery_pct REAL,
                nifty_outperformance_score REAL, sma_50 REAL,
                high_52w REAL, low_52w REAL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO technical_data VALUES (
                'TEST', '2025-01-01', 101.0, 106.0, 100.0, 103.0,
                50000.0, 15000.0, 30.0, 0.0, 102.0, 130.0, 95.0
            )
            """
        )
        conn.commit()
    finally:
        conn.close()
    return db_path


def test_fallback_returns_13_values_with_null_swing_low(legacy_tech_db):
    """Legacy DB (no swing_low) → fallback SELECT must still return 13 values
    with None in the swing_low slot (index 8, per COLUMNS_13)."""
    scanner = WyckoffAutomaton()
    assert scanner._bulk_data is None  # forces the per-symbol SQL path

    rows = scanner._get_tech_data("TEST", "2025-01-01", "2025-01-01")
    assert len(rows) == 1
    row = rows[0]
    assert len(row) == 13, f"expected 13 columns, got {len(row)}"
    assert row[8] is None, "swing_low slot (idx 8) must be NULL on legacy DBs"
    # Positional order must match COLUMNS_13: date..delivery_pct, swing_low,
    # nifty_outperformance_score, sma_50, high_52w, low_52w.
    for idx in range(8):  # date, open, high, low, close, volume, delivery, del%
        assert row[idx] is not None, f"col at idx {idx} must have a value"
    assert row[9] is not None  # nifty_outperformance_score
    # sma_50 / high_52w / low_52w (idx 10-12) are NULL AS by fallback design.


def test_fallback_handles_missing_db_dir(tmp_path, monkeypatch):
    """No technical DB file → _get_tech_data returns [] without raising."""
    monkeypatch.setattr(wy_mod, "DB_DIR", str(tmp_path))
    scanner = WyckoffAutomaton()
    assert scanner._get_tech_data("TEST", "2025-01-01") == []
