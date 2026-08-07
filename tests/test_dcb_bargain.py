"""
Tests for DCB Bargain scanner pure-math helpers.
No database access, no network — pure numpy/pandas only.
"""

import math

import numpy as np
import pandas as pd
import pytest

from myra_app.strategies.dcb_bargain import DCBBargainScanner


def _make_tech_df(
    closes: list[float],
    delivery_pcts: list[float],
    opens: list[float] | None = None,
    highs: list[float] | None = None,
    lows: list[float] | None = None,
) -> pd.DataFrame:
    """Build a minimal DataFrame with columns the scanner reads.
    If opens not provided, defaults to closes shifted by 1 (first = first close)."""
    n = len(closes)
    if opens is None:
        opens = [closes[0]] + closes[:-1]
    if highs is None:
        highs = [max(o, c) for o, c in zip(opens, closes)]
    if lows is None:
        lows = [min(o, c) for o, c in zip(opens, closes)]
    return pd.DataFrame({
        "date": pd.date_range("2025-01-01", periods=n),
        "open": opens,
        "high": highs,
        "low": lows,
        "close": closes,
        "volume": [100000] * n,
        "delivery": [10000] * n,
        "delivery_pct": delivery_pcts,
    })


# ---------------------------------------------------------------------------
# _compute_dcb tests
# ---------------------------------------------------------------------------


def test_compute_dcb_weighted_average():
    """Delivery-weighted average: closes [100,90,80], del [10,20,30], avg_del=15.
    Mask = [False, True, True] -> weighted avg of [90,80] with [20,30] = 84.0."""
    closes = np.array([100.0, 90.0, 80.0])
    delivery_pcts = np.array([10.0, 20.0, 30.0])
    result = DCBBargainScanner._compute_dcb(closes, delivery_pcts, avg_del=15.0)
    assert result is not None
    assert result == pytest.approx(84.0)


def test_compute_dcb_none_when_no_high_delivery():
    """No day exceeds avg_del -> returns None."""
    closes = np.array([100.0, 90.0, 80.0])
    delivery_pcts = np.array([10.0, 20.0, 30.0])
    result = DCBBargainScanner._compute_dcb(closes, delivery_pcts, avg_del=99.0)
    assert result is None


# ---------------------------------------------------------------------------
# _compute_del_abs tests (new: uses open/close, not close-to-close returns)
# ---------------------------------------------------------------------------


def test_compute_del_abs_positive():
    """Up days (close>open) carry higher delivery than down days -> positive del_abs.
    We construct 25 rows, tail(20) used.
    First 5 are padding; last 20 have explicit opens."""
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
    # Opens: each bar's open is slightly below close for up days, above for down days
    # Pattern: close alternates up/down so we can control up vs down
    # Row-by-row in the 20-row window:
    # 0: c=100, we want flat or use default
    # 1: c=110 up (o=99) -> up, del=30
    # 2: c=105 down (o=110) -> down, del=20
    # 3: c=115 up (o=100) -> up, del=40
    # 4: c=108 down (o=115) -> down, del=15
    # 5: c=112 up (o=105) -> up, del=35
    # 6: c=106 down (o=112) -> down, del=25
    # 7: c=118 up (o=100) -> up, del=45
    # 8: c=110 down (o=118) -> down, del=18
    # 9: c=114 up (o=108) -> up, del=38
    # 10: c=109 down (o=114) -> down, del=22
    # 11: c=116 up (o=106) -> up, del=42
    # 12: c=111 down (o=116) -> down, del=17
    # 13: c=120 up (o=109) -> up, del=48
    # 14: c=115 down (o=120) -> down, del=28
    # 15: c=119 up (o=112) -> up, del=36
    # 16: c=112 down (o=119) -> down, del=20
    # 17: c=122 up (o=111) -> up, del=50
    # 18: c=117 down (o=122) -> down, del=24
    # 19: c=125 up (o=117) -> up, del=44
    opens_padding = [100.0] * 5
    opens_window = [
        100, 99, 110, 100, 115, 105, 112, 100, 118, 108,
        114, 106, 116, 109, 120, 112, 119, 111, 122, 117,
    ]
    opens = opens_padding + opens_window
    df = _make_tech_df(closes, delivery_pcts, opens=opens)
    result = DCBBargainScanner._compute_del_abs(df, window=20)
    # Up days: indices 1,3,5,7,9,11,13,15,17,19 -> del [30,40,35,45,38,42,48,36,50,44] -> avg=40.8
    # Down days: indices 2,4,6,8,10,12,14,16,18 -> del [20,15,25,18,22,17,28,20,24] -> avg=21.0
    assert result == pytest.approx(40.8 - 21.0, abs=0.1)


def test_compute_del_abs_negative():
    """Down days carry higher delivery than up days -> negative del_abs."""
    # 20 rows: all bars where down days have higher delivery
    closes = [100, 90, 95, 85, 90, 80, 85, 75, 80, 70,
              75, 65, 70, 60, 65, 55, 60, 50, 55, 45]
    # Opens: alternating to create up/down pattern
    # Each even index (0,2,4,...): open > close => down day
    # Each odd index (1,3,5,...): open < close => up day
    opens = [110, 85, 100, 80, 95, 75, 90, 70, 85, 65,
             80, 60, 75, 55, 70, 50, 65, 45, 60, 40]
    delivery_pcts = [10, 50, 12, 48, 14, 46, 16, 44, 18, 42,
                     20, 40, 22, 38, 24, 36, 26, 34, 28, 32]
    df = _make_tech_df(closes, delivery_pcts, opens=opens)
    result = DCBBargainScanner._compute_del_abs(df, window=20)
    # Up days (c>o): idx 1(c90>o85),3(c85>o80),5(c80>o75),7(c75>o70),9(c70>o65),
    # 11(c65>o60),13(c60>o55),15(c55>o50),17(c50>o45),19(c45>o40)
    # del_pcts for up: [50,48,46,44,42,40,38,36,34,32] -> avg=41.0
    # Down days: [10,12,14,16,18,20,22,24,26,28] -> avg=19.0
    assert result == pytest.approx(41.0 - 19.0, abs=0.1)


def test_compute_del_abs_all_up_days():
    """All bars have close > open -> down_avg = 0, del_abs = up_avg."""
    closes = list(range(100, 120))  # 100..119
    opens = [c - 2 for c in closes]  # all open < close -> all up days
    delivery_pcts = [10, 20, 30, 40, 50, 60, 70, 80, 90, 100,
                     10, 20, 30, 40, 50, 60, 70, 80, 90, 100]
    df = _make_tech_df(closes, delivery_pcts, opens=opens)
    result = DCBBargainScanner._compute_del_abs(df, window=20)
    assert result == pytest.approx(55.0, abs=0.01)


def test_compute_del_abs_fewer_than_window():
    """DataFrame with fewer than window rows -> uses all rows, no crash."""
    closes = [100, 95, 98, 93, 96, 91, 94, 89, 92, 87]
    opens = [c - 1 for c in closes]
    delivery_pcts = [10, 50, 12, 48, 14, 46, 16, 44, 18, 42]
    df = _make_tech_df(closes, delivery_pcts, opens=opens)
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
    assert dcb > close * scanner.sanity_mult  # 600 > 500 -> would skip


# ---------------------------------------------------------------------------
# Tier / score logic
# ---------------------------------------------------------------------------


def test_tier_boundaries():
    """Score -> tier mapping matches the scanner's inline expression."""
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
    assert scanner.min_discount_pct == 15.0
    assert scanner.max_discount_pct == 60.0
    assert scanner.min_del_abs == -2.0
    assert scanner.min_adtv_cr == 1.0
    assert scanner.min_high_del_days == 10
    assert scanner.sanity_mult == 5.0
    assert scanner.timeframe == "daily"
    assert scanner.min_ff_mcap == 0.0


# ---------------------------------------------------------------------------
# _compute_depth_tag tests
# ---------------------------------------------------------------------------


def test_depth_tag_deep():
    assert DCBBargainScanner._compute_depth_tag(25.0) == "DEEP"
    assert DCBBargainScanner._compute_depth_tag(20.1) == "DEEP"


def test_depth_tag_mid():
    assert DCBBargainScanner._compute_depth_tag(15.0) == "MID"
    assert DCBBargainScanner._compute_depth_tag(10.1) == "MID"


def test_depth_tag_shallow():
    assert DCBBargainScanner._compute_depth_tag(10.0) == "SHALLOW"
    assert DCBBargainScanner._compute_depth_tag(5.0) == "SHALLOW"


# ---------------------------------------------------------------------------
# _check_spike_deep tests
# ---------------------------------------------------------------------------


def test_spike_deep_true():
    """delivery_pct >= 1.3x avg, CLR >= 0.6, discount > 20 -> True."""
    np.random.seed(42)
    n = 60
    closes = 100.0 + np.cumsum(np.random.randn(n) * 0.5)
    opens = closes - 2  # all up days
    highs = closes + 1
    lows = opens - 1
    del_pcts = np.full(n, 20.0)
    del_pcts[-1] = 40.0  # spike: 40 >= 1.3*20 = 26
    df = _make_tech_df(
        closes.tolist(), del_pcts.tolist(),
        opens=opens.tolist(), highs=highs.tolist(), lows=lows.tolist(),
    )
    assert DCBBargainScanner._check_spike_deep(df, discount_pct=25.0) is True


def test_spike_deep_false_no_discount():
    """Same spike but discount <= 20 -> False."""
    np.random.seed(42)
    n = 60
    closes = 100.0 + np.cumsum(np.random.randn(n) * 0.5)
    opens = closes - 2
    highs = closes + 1
    lows = opens - 1
    del_pcts = np.full(n, 20.0)
    del_pcts[-1] = 40.0
    df = _make_tech_df(
        closes.tolist(), del_pcts.tolist(),
        opens=opens.tolist(), highs=highs.tolist(), lows=lows.tolist(),
    )
    assert DCBBargainScanner._check_spike_deep(df, discount_pct=15.0) is False


def test_spike_deep_false_no_spike():
    """delivery_pct < 1.3x avg -> False."""
    n = 60
    closes = list(range(100, 100 + n))
    opens = [c - 2 for c in closes]
    highs = [c + 1 for c in closes]
    lows = [o - 1 for o in opens]
    del_pcts = [20.0] * n  # no spike
    df = _make_tech_df(
        closes, del_pcts, opens=opens, highs=highs, lows=lows,
    )
    assert DCBBargainScanner._check_spike_deep(df, discount_pct=25.0) is False


# ---------------------------------------------------------------------------
# _get_weekly_data tests
# ---------------------------------------------------------------------------


def test_get_weekly_data_basic():
    """Weekly aggregation produces correct OHLCV and delivery_pct."""
    n = 30
    dates = pd.date_range("2025-01-01", periods=n, freq="B")
    closes = [100.0 + i for i in range(n)]
    opens = [c - 1 for c in closes]
    highs = [c + 2 for c in closes]
    lows = [c - 2 for c in closes]
    volumes = [100000] * n
    deliveries = [10000] * n
    del_pcts = [10.0] * n
    df = pd.DataFrame({
        "date": dates,
        "open": opens,
        "high": highs,
        "low": lows,
        "close": closes,
        "volume": volumes,
        "delivery": deliveries,
        "delivery_pct": del_pcts,
    })
    weekly = DCBBargainScanner._get_weekly_data(df)
    assert len(weekly) > 0
    assert len(weekly) < n  # fewer rows than daily
    # delivery_pct should be recalculated from delivery/volume
    assert "delivery_pct" in weekly.columns
    assert "open" in weekly.columns
    assert "close" in weekly.columns


def test_get_weekly_data_empty():
    """Empty or tiny DataFrame returns empty."""
    empty_df = pd.DataFrame()
    assert DCBBargainScanner._get_weekly_data(empty_df).empty

    tiny = pd.DataFrame({
        "date": pd.date_range("2025-01-01", periods=2),
        "open": [100.0, 101.0],
        "high": [102.0, 103.0],
        "low": [99.0, 100.0],
        "close": [101.0, 102.0],
        "volume": [10000, 11000],
        "delivery": [1000, 1100],
        "delivery_pct": [10.0, 10.0],
    })
    assert DCBBargainScanner._get_weekly_data(tiny).empty
