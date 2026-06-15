"""
Tests for Delivery Efficiency Ratio (DER) calculation.
Formula from invisible_hand_scanner.py:91-103:
  DER = Σ(delivery_i * close_i) / 1e7 / max(|price_drift%|, 0.5)
  where price_drift% = |close_last - close_first| / close_first * 100
"""

import numpy as np


def _compute_der(delivery: list[float], close: list[float]) -> float:
    """Replicates InvisibleHandScanner._compute_der static method."""
    arr_del = np.array(delivery, dtype=float)
    arr_close = np.array(close, dtype=float)
    if len(arr_del) < 2 or len(arr_close) < 2:
        return 0.0
    delivery_value_cr = float(np.nansum(arr_del * arr_close)) / 1e7
    first_close = arr_close[0]
    if first_close > 0:
        price_drift_abs = abs(arr_close[-1] - first_close) / first_close * 100
    else:
        price_drift_abs = 0.5
    return delivery_value_cr / max(price_drift_abs, 0.5)


def test_der_fewer_than_two_rows():
    assert _compute_der([100], [500]) == 0.0


def test_der_zero_price_drift():
    """Same close → drift = 0, denominator clamped to 0.5."""
    delivery = [100000, 200000]
    close = [500.0, 500.0]
    total_val_cr = (100000 * 500 + 200000 * 500) / 1e7
    expected = total_val_cr / 0.5
    assert _compute_der(delivery, close) == expected


def test_der_ten_percent_drift():
    """Close moves from 100 to 110 (10% drift)."""
    delivery = [100_000, 200_000]
    close = [100.0, 110.0]
    total_val_cr = (100_000 * 100 + 200_000 * 110) / 1e7
    price_drift = 10.0  # |110-100|/100*100
    expected = total_val_cr / price_drift
    result = _compute_der(delivery, close)
    assert abs(result - expected) < 1e-9


def test_der_high_drift_lowers_score():
    """More price drift with same delivery → lower DER."""
    low_drift = _compute_der([500_000, 500_000], [100.0, 101.0])
    high_drift = _compute_der([500_000, 500_000], [100.0, 120.0])
    assert low_drift > high_drift


def test_der_more_delivery_higher_score():
    """More delivery with same drift → higher DER."""
    low_del = _compute_der([100_000, 100_000], [100.0, 110.0])
    high_del = _compute_der([500_000, 500_000], [100.0, 110.0])
    assert high_del > low_del


def test_der_first_close_zero():
    """When first close is 0, price_drift_abs defaults to 0.5."""
    delivery = [100_000, 200_000]
    close = [0.0, 110.0]
    total_val_cr = (100_000 * 0 + 200_000 * 110) / 1e7
    expected = total_val_cr / 0.5
    assert _compute_der(delivery, close) == expected


def test_der_with_nan():
    """NaN values should be ignored via nansum."""
    delivery = [100_000, float("nan"), 200_000]
    close = [100.0, 105.0, 110.0]
    total_val_cr = (100_000 * 100 + 0 + 200_000 * 110) / 1e7
    price_drift = 10.0
    expected = total_val_cr / price_drift
    result = _compute_der(delivery, close)
    assert abs(result - expected) < 1e-9


def test_der_three_rows():
    delivery = [100_000, 150_000, 200_000]
    close = [100.0, 102.0, 108.0]
    total_val_cr = (100_000 * 100 + 150_000 * 102 + 200_000 * 108) / 1e7
    price_drift = 8.0
    expected = total_val_cr / price_drift
    result = _compute_der(delivery, close)
    assert abs(result - expected) < 1e-9
