"""
Tests for the validate_row data quality gate.
Logic from daily_ingestor.py:283-296 (nested function).
Checks: OHLC > 0, volume > 0, delivery in [0, volume].
"""

import math

import pandas as pd


def validate_row(row: dict) -> list[str]:
    """Replicates the exact validate_row logic from daily_ingestor.py:283-296."""
    reasons = []
    for col in ["open", "high", "low", "close"]:
        if col in row and (pd.isna(row[col]) or float(row[col]) <= 0):
            reasons.append(f"{col} <= 0")
    if "volume" in row and (pd.isna(row["volume"]) or int(row["volume"]) <= 0):
        reasons.append("volume <= 0")
    if "delivery" in row and "volume" in row:
        if not pd.isna(row["delivery"]) and not pd.isna(row["volume"]):
            delivery_val = float(row["delivery"])
            volume_val = int(row["volume"])
            if delivery_val < 0 or delivery_val > volume_val:
                reasons.append("delivery out of range [0, volume]")
    return reasons


def test_valid_row():
    row = {
        "open": 100.0,
        "high": 110.0,
        "low": 95.0,
        "close": 105.0,
        "volume": 1000,
        "delivery": 500,
    }
    assert validate_row(row) == []


def test_open_zero():
    row = {
        "open": 0,
        "high": 110.0,
        "low": 95.0,
        "close": 105.0,
        "volume": 1000,
        "delivery": 500,
    }
    assert validate_row(row) == ["open <= 0"]


def test_open_negative():
    row = {
        "open": -10.0,
        "high": 110.0,
        "low": 95.0,
        "close": 105.0,
        "volume": 1000,
        "delivery": 500,
    }
    assert validate_row(row) == ["open <= 0"]


def test_high_nan():
    row = {
        "open": 100.0,
        "high": math.nan,
        "low": 95.0,
        "close": 105.0,
        "volume": 1000,
        "delivery": 500,
    }
    assert validate_row(row) == ["high <= 0"]


def test_low_zero():
    row = {
        "open": 100.0,
        "high": 110.0,
        "low": 0,
        "close": 105.0,
        "volume": 1000,
        "delivery": 500,
    }
    assert validate_row(row) == ["low <= 0"]


def test_close_nan():
    row = {
        "open": 100.0,
        "high": 110.0,
        "low": 95.0,
        "close": math.nan,
        "volume": 1000,
        "delivery": 500,
    }
    assert validate_row(row) == ["close <= 0"]


def test_volume_zero():
    row = {
        "open": 100.0,
        "high": 110.0,
        "low": 95.0,
        "close": 105.0,
        "volume": 0,
        "delivery": 500,
    }
    reasons = validate_row(row)
    assert "volume <= 0" in reasons
    assert "delivery out of range [0, volume]" in reasons


def test_volume_nan():
    row = {
        "open": 100.0,
        "high": 110.0,
        "low": 95.0,
        "close": 105.0,
        "volume": math.nan,
        "delivery": 500,
    }
    results = validate_row(row)
    assert "volume <= 0" in results


def test_delivery_negative():
    row = {
        "open": 100.0,
        "high": 110.0,
        "low": 95.0,
        "close": 105.0,
        "volume": 1000,
        "delivery": -50,
    }
    assert validate_row(row) == ["delivery out of range [0, volume]"]


def test_delivery_exceeds_volume():
    row = {
        "open": 100.0,
        "high": 110.0,
        "low": 95.0,
        "close": 105.0,
        "volume": 1000,
        "delivery": 1500,
    }
    assert validate_row(row) == ["delivery out of range [0, volume]"]


def test_delivery_equal_to_volume():
    row = {
        "open": 100.0,
        "high": 110.0,
        "low": 95.0,
        "close": 105.0,
        "volume": 1000,
        "delivery": 1000,
    }
    assert validate_row(row) == []


def test_delivery_missing_column():
    row = {
        "open": 100.0,
        "high": 110.0,
        "low": 95.0,
        "close": 105.0,
        "volume": 1000,
    }
    assert validate_row(row) == []


def test_ohlc_only_no_volume_or_delivery():
    row = {
        "open": 100.0,
        "high": 110.0,
        "low": 95.0,
        "close": 105.0,
    }
    assert validate_row(row) == []


def test_multiple_failures():
    row = {
        "open": 0,
        "high": 0,
        "low": 95.0,
        "close": math.nan,
        "volume": -5,
        "delivery": 9999,
    }
    reasons = validate_row(row)
    assert "open <= 0" in reasons
    assert "high <= 0" in reasons
    assert "close <= 0" in reasons
    assert "volume <= 0" in reasons
    assert "delivery out of range [0, volume]" in reasons
