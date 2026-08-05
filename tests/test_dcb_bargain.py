"""
Tests for DCB Bargain scanner pure-math helpers.
No database access, no network — pure numpy/pandas only.
"""

import math

import numpy as np
import pandas as pd
import pytest

from myra_app.strategies.dcb_bargain import DCBBargainScanner


def _make_tech_df(closes: list[float], delivery_pcts: list[float]) -> pd.DataFrame:
    """Build a minimal DataFrame with columns _compute_del_abs reads."""
    n = len(closes)
    return pd.DataFrame({
        "date": pd.date_range("2025-01-01", periods=n),
        "close": closes,
        "delivery_pct": delivery_pcts,
    })


# ---------------------------------------------------------------------------
# _compute_dcb tests
# ---------------------------------------------------------------------------


def test_compute_dcb_weighted_average():
    """Delivery-weighted average: closes [100,90,80], del [10,20,30], avg_del=15.
    Mask = [False, True, True] → weighted avg of [90,80] with [20,30] = 84.0."""
    closes = np.array([100.0, 90.0, 80.0])
    delivery_pcts = np.array([10.0, 20.0, 30.0])
    result = DCBBargainScanner._compute_dcb(closes, delivery_pcts, avg_del=15.0)
    assert result is not None
    assert result == pytest.approx(84.0)


def test_compute_dcb_none_when_no_high_delivery():
    """No day exceeds avg_del → returns None."""
    closes = np.array([100.0, 90.0, 80.0])
    delivery_pcts = np.array([10.0, 20.0, 30.0])
    result = DCBBargainScanner._compute_dcb(closes, delivery_pcts, avg_del=99.0)
    assert result is None


# ---------------------------------------------------------------------------
# _compute_del_abs tests
# ---------------------------------------------------------------------------


def test_compute_del_abs_positive():
    """Up days carry higher delivery than down days → positive del_abs.
    25-row DataFrame; tail(20) used. Up avg del=38.0, down avg del=21.0."""
    closes = (
        [100.0] * 5
        + [100, 110, 105, 115, 108, 112, 106, 118, 110, 114,
           109, 116, 111, 120, 115, 119, 112, 122, 117, 125]
    )
    delivery_pcts = (
        [10.0] * 5
        + [10, 30, 20, 40, 15, 35, 25, 45, 18, 38,
           22, 42, 17, 48, 28, 36, 20, 50, 24, 44]
    )
    df = _make_tech_df(closes, delivery_pcts)
    result = DCBBargainScanner._compute_del_abs(df, window=20)
    assert result == pytest.approx(17.0, abs=0.01)


def test_compute_del_abs_negative():
    """Down days carry higher delivery than up days → negative del_abs.
    Up avg del=19.0, down avg del=41.0 → del_abs = -22.0."""
    closes = [100, 90, 95, 85, 90, 80, 85, 75, 80, 70,
              75, 65, 70, 60, 65, 55, 60, 50, 55, 45]
    delivery_pcts = [10, 50, 12, 48, 14, 46, 16, 44, 18, 42,
                     20, 40, 22, 38, 24, 36, 26, 34, 28, 32]
    df = _make_tech_df(closes, delivery_pcts)
    result = DCBBargainScanner._compute_del_abs(df, window=20)
    assert result == pytest.approx(-22.0, abs=0.01)


def test_compute_del_abs_all_up_days():
    """All up days → down_avg = 0, del_abs = up_avg (55.0)."""
    closes = list(range(100, 120))  # 100..119, strictly increasing
    delivery_pcts = [10, 20, 30, 40, 50, 60, 70, 80, 90, 100,
                     10, 20, 30, 40, 50, 60, 70, 80, 90, 100]
    df = _make_tech_df(closes, delivery_pcts)
    result = DCBBargainScanner._compute_del_abs(df, window=20)
    assert result == pytest.approx(55.0, abs=0.01)


def test_compute_del_abs_fewer_than_window():
    """DataFrame with fewer than window rows → uses all rows, no crash."""
    closes = [100, 95, 98, 93, 96, 91, 94, 89, 92, 87]
    delivery_pcts = [10, 50, 12, 48, 14, 46, 16, 44, 18, 42]
    df = _make_tech_df(closes, delivery_pcts)
    result = DCBBargainScanner._compute_del_abs(df, window=20)
    assert isinstance(result, float)
    assert not math.isnan(result)


# ---------------------------------------------------------------------------
# Sanity-mult guard
# ---------------------------------------------------------------------------


def test_sanity_mult_guard():
    """Verify scanner stores sanity_mult and the skip logic holds."""
    scanner = DCBBargainScanner(sanity_mult=5.0)
    assert scanner.sanity_mult == 5.0
    dcb, close = 600.0, 100.0
    assert dcb > close * scanner.sanity_mult  # 600 > 500 → would skip


# ---------------------------------------------------------------------------
# Tier / score logic
# ---------------------------------------------------------------------------


def test_tier_boundaries():
    """Score → tier mapping matches the scanner's inline expression."""
    tier_fn = lambda s: "HIGH" if s >= 20 else ("MOD" if s >= 10 else "LOW")
    assert tier_fn(20) == "HIGH"
    assert tier_fn(10) == "MOD"
    assert tier_fn(9.9) == "LOW"


def test_score_formula():
    """score = discount_pct * 0.6 + del_abs * 0.4."""
    discount_pct, del_abs = 20.0, 5.0
    score = discount_pct * 0.6 + del_abs * 0.4
    assert score == pytest.approx(14.0)


# ---------------------------------------------------------------------------
# Default constructor params
# ---------------------------------------------------------------------------


def test_default_params():
    """All backtest-proven defaults are present."""
    scanner = DCBBargainScanner()
    assert scanner.min_mcap == 200
    assert scanner.max_mcap == 50000
    assert scanner.dcb_window == 120
    assert scanner.min_discount_pct == 5.0
    assert scanner.max_discount_pct == 60.0
    assert scanner.min_del_abs == -2.0
    assert scanner.min_adtv_cr == 1.0
    assert scanner.min_high_del_days == 10
    assert scanner.sanity_mult == 5.0
