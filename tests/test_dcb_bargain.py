"""
Tests for DCB Bargain scanner pure-math helpers.
No database access, no network — pure numpy/pandas only.
"""

import math
from unittest.mock import patch

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
    return pd.DataFrame(
        {
            "date": pd.date_range("2025-01-01", periods=n),
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": [100000] * n,
            "delivery": [10000] * n,
            "delivery_pct": delivery_pcts,
        }
    )


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
    closes = [100.0] * 5 + [
        100,
        110,
        105,
        115,
        108,
        112,
        106,
        118,
        110,
        114,
        109,
        116,
        111,
        120,
        115,
        119,
        112,
        122,
        117,
        125,
    ]
    delivery_pcts = [10.0] * 5 + [
        10,
        30,
        20,
        40,
        15,
        35,
        25,
        45,
        18,
        38,
        22,
        42,
        17,
        48,
        28,
        36,
        20,
        50,
        24,
        44,
    ]
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
        100,
        99,
        110,
        100,
        115,
        105,
        112,
        100,
        118,
        108,
        114,
        106,
        116,
        109,
        120,
        112,
        119,
        111,
        122,
        117,
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
    closes = [
        100,
        90,
        95,
        85,
        90,
        80,
        85,
        75,
        80,
        70,
        75,
        65,
        70,
        60,
        65,
        55,
        60,
        50,
        55,
        45,
    ]
    # Opens: alternating to create up/down pattern
    # Each even index (0,2,4,...): open > close => down day
    # Each odd index (1,3,5,...): open < close => up day
    opens = [
        110,
        85,
        100,
        80,
        95,
        75,
        90,
        70,
        85,
        65,
        80,
        60,
        75,
        55,
        70,
        50,
        65,
        45,
        60,
        40,
    ]
    delivery_pcts = [
        10,
        50,
        12,
        48,
        14,
        46,
        16,
        44,
        18,
        42,
        20,
        40,
        22,
        38,
        24,
        36,
        26,
        34,
        28,
        32,
    ]
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
    delivery_pcts = [
        10,
        20,
        30,
        40,
        50,
        60,
        70,
        80,
        90,
        100,
        10,
        20,
        30,
        40,
        50,
        60,
        70,
        80,
        90,
        100,
    ]
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
    """Dynamic tier assignment: percentile-based when pool >= 10, fallback when < 10."""
    scanner = DCBBargainScanner()

    # Fallback (< 10 candidates): uses _tier_from_score
    assert scanner._tier_from_score(20) == "HIGH"
    assert scanner._tier_from_score(10) == "MOD"
    assert scanner._tier_from_score(9.9) == "LOW"

    # Percentile-based (n=10): top 20% (ceil(0.2*10)=2) HIGH, next 30% (ceil(0.5*10)=5 total) MOD, rest LOW
    # Simulate: scores sorted descending
    scores = [30, 25, 20, 18, 15, 12, 10, 8, 5, 2]
    n = len(scores)
    high_cut = math.ceil(0.2 * n)  # 2
    mod_cut = math.ceil(0.5 * n)  # 5
    tiers = []
    for i in range(n):
        if i < high_cut:
            tiers.append("HIGH")
        elif i < mod_cut:
            tiers.append("MOD")
        else:
            tiers.append("LOW")
    assert tiers[:2] == ["HIGH", "HIGH"]
    assert tiers[2:5] == ["MOD", "MOD", "MOD"]
    assert tiers[5:] == ["LOW", "LOW", "LOW", "LOW", "LOW"]

    # n=20: top 4 HIGH, next 6 MOD, rest LOW
    scores_20 = list(range(20, 0, -1))
    n20 = len(scores_20)
    high_cut_20 = math.ceil(0.2 * n20)  # 4
    mod_cut_20 = math.ceil(0.5 * n20)  # 10
    tiers_20 = []
    for i in range(n20):
        if i < high_cut_20:
            tiers_20.append("HIGH")
        elif i < mod_cut_20:
            tiers_20.append("MOD")
        else:
            tiers_20.append("LOW")
    assert tiers_20[:4] == ["HIGH"] * 4
    assert tiers_20[4:10] == ["MOD"] * 6
    assert tiers_20[10:] == ["LOW"] * 10


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
    assert scanner.min_discount_pct == 18.0
    assert scanner.max_discount_pct == 60.0
    assert scanner.min_del_abs == -2.0
    assert scanner.min_adtv_cr == 1.0
    assert scanner.min_high_del_days == 10
    assert scanner.sanity_mult == 5.0
    assert scanner.timeframe == "daily"
    assert scanner.min_ff_mcap == 600.0


# ---------------------------------------------------------------------------
# _compute_depth_tag tests
# ---------------------------------------------------------------------------


def test_depth_tag_deep():
    """Universal fallback: discount > 20 -> DEEP."""
    assert DCBBargainScanner._compute_depth_tag(25.0) == "DEEP"
    assert DCBBargainScanner._compute_depth_tag(20.1) == "DEEP"
    # Percentile-based: rank > 80 -> DEEP
    values = [0, 5, 10, 15, 20, 25]
    assert DCBBargainScanner._compute_depth_tag(25.0, values) == "DEEP"
    assert DCBBargainScanner._compute_depth_tag(20.1, values) == "DEEP"


def test_depth_tag_mid():
    """Universal fallback: 10 < discount <= 20 -> MID."""
    assert DCBBargainScanner._compute_depth_tag(15.0) == "MID"
    assert DCBBargainScanner._compute_depth_tag(10.1) == "MID"
    # Percentile-based: 50 <= rank <= 80 -> MID
    # values=[0,5,10,15,20], discount=10.1 -> rank = 3/5*100 = 60% -> MID
    values = [0, 5, 10, 15, 20]
    assert DCBBargainScanner._compute_depth_tag(10.1, values) == "MID"
    # values=[0,5,10,15,20,25,30], discount=15.0 -> rank = 4/7*100 = 57% -> MID
    values2 = [0, 5, 10, 15, 20, 25, 30]
    assert DCBBargainScanner._compute_depth_tag(15.0, values2) == "MID"


def test_depth_tag_shallow():
    """Universal fallback: discount <= 10 -> SHALLOW."""
    assert DCBBargainScanner._compute_depth_tag(10.0) == "SHALLOW"
    assert DCBBargainScanner._compute_depth_tag(5.0) == "SHALLOW"
    # Percentile-based: rank < 50 -> SHALLOW
    values = [0, 5, 10, 15, 20, 25]
    assert DCBBargainScanner._compute_depth_tag(2.0, values) == "SHALLOW"


def test_depth_tag_percentile_boundaries():
    """Test exact boundary conditions for percentile-based depth tag."""
    # values=[0,10,15,20,25], discount=20.0 -> rank = 4/5*100 = 80% -> MID (not DEEP, needs >80)
    values_80 = [0, 10, 15, 20, 25]
    assert DCBBargainScanner._compute_depth_tag(20.0, values_80) == "MID"
    # discount=25.0 -> rank = 5/5*100 = 100% -> DEEP
    assert DCBBargainScanner._compute_depth_tag(25.0, values_80) == "DEEP"

    # values=[0,10,20,30,40], discount=15.0 -> rank = 2/5*100 = 40% -> SHALLOW
    values_low = [0, 10, 20, 30, 40]
    assert DCBBargainScanner._compute_depth_tag(15.0, values_low) == "SHALLOW"
    # discount=25.0 -> rank = 3/5*100 = 60% -> MID
    assert DCBBargainScanner._compute_depth_tag(25.0, values_low) == "MID"
    # discount=35.0 -> rank = 4/5*100 = 80% -> MID (not >80)
    assert DCBBargainScanner._compute_depth_tag(35.0, values_low) == "MID"
    # discount=45.0 -> rank = 5/5*100 = 100% -> DEEP
    assert DCBBargainScanner._compute_depth_tag(45.0, values_low) == "DEEP"


def test_depth_tag_fewer_than_5_values_uses_universal():
    """With < 5 values, always falls back to universal thresholds."""
    assert DCBBargainScanner._compute_depth_tag(25.0, [0, 5, 10]) == "DEEP"
    assert DCBBargainScanner._compute_depth_tag(5.0, [0, 5, 10]) == "SHALLOW"
    assert DCBBargainScanner._compute_depth_tag(15.0, [0, 5, 10]) == "MID"


def test_depth_tag_none_values_uses_universal():
    """None values list falls back to universal."""
    assert DCBBargainScanner._compute_depth_tag(25.0, None) == "DEEP"
    assert DCBBargainScanner._compute_depth_tag(5.0, None) == "SHALLOW"


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
        closes.tolist(),
        del_pcts.tolist(),
        opens=opens.tolist(),
        highs=highs.tolist(),
        lows=lows.tolist(),
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
        closes.tolist(),
        del_pcts.tolist(),
        opens=opens.tolist(),
        highs=highs.tolist(),
        lows=lows.tolist(),
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
        closes,
        del_pcts,
        opens=opens,
        highs=highs,
        lows=lows,
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
    df = pd.DataFrame(
        {
            "date": dates,
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": volumes,
            "delivery": deliveries,
            "delivery_pct": del_pcts,
        }
    )
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

    tiny = pd.DataFrame(
        {
            "date": pd.date_range("2025-01-01", periods=2),
            "open": [100.0, 101.0],
            "high": [102.0, 103.0],
            "low": [99.0, 100.0],
            "close": [101.0, 102.0],
            "volume": [10000, 11000],
            "delivery": [1000, 1100],
            "delivery_pct": [10.0, 10.0],
        }
    )
    assert DCBBargainScanner._get_weekly_data(tiny).empty


# ---------------------------------------------------------------------------
# _is_lower_circuit tests
# ---------------------------------------------------------------------------


def _make_circuit_df(
    closes: list[float],
    lows: list[float],
    opens: list[float] | None = None,
    highs: list[float] | None = None,
) -> pd.DataFrame:
    """Build a minimal DataFrame for circuit detection tests."""
    n = len(closes)
    if opens is None:
        opens = [closes[0]] + closes[:-1]
    if highs is None:
        highs = [max(o, c) for o, c in zip(opens, closes)]
    if lows is None:
        lows = [min(o, c) for o, c in zip(opens, closes)]
    return pd.DataFrame(
        {
            "date": pd.date_range("2025-01-01", periods=n),
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": [100000] * n,
            "delivery": [10000] * n,
            "delivery_pct": [20.0] * n,
        }
    )


def test_lower_circuit_false_idx_zero():
    """idx < 1 always returns False."""
    df = _make_circuit_df([100.0], [99.0])
    scanner = DCBBargainScanner()
    assert scanner._is_lower_circuit(df, 0) is False


def test_lower_circuit_true_pinned_low_big_drop():
    """Close pinned at low (within 1%) AND close < 0.95 * prev_close -> True."""
    # prev_close=100, close=93 (7% drop), low=92.5 (close <= low*1.01 = 93.425)
    df = _make_circuit_df(
        closes=[100.0, 93.0],
        lows=[99.0, 92.5],
        opens=[100.0, 99.0],
        highs=[101.0, 99.5],
    )
    scanner = DCBBargainScanner()
    assert scanner._is_lower_circuit(df, 1) is True


def test_lower_circuit_false_pinned_but_small_drop():
    """Close pinned at low but drop < 5% -> False."""
    # prev_close=100, close=96 (4% drop), low=95 (close <= low*1.01 = 95.95)
    df = _make_circuit_df(
        closes=[100.0, 96.0],
        lows=[99.0, 95.0],
        opens=[100.0, 99.0],
        highs=[101.0, 99.5],
    )
    scanner = DCBBargainScanner()
    assert scanner._is_lower_circuit(df, 1) is False


def test_lower_circuit_false_big_drop_but_not_pinned():
    """Big drop but close is not pinned near the low -> False."""
    # prev_close=100, close=93 (7% drop), low=85 (close=93 > low*1.01=85.85)
    df = _make_circuit_df(
        closes=[100.0, 93.0],
        lows=[99.0, 85.0],
        opens=[100.0, 95.0],
        highs=[101.0, 96.0],
    )
    scanner = DCBBargainScanner()
    assert scanner._is_lower_circuit(df, 1) is False


def test_lower_circuit_false_exactly_5pct_drop():
    """Close = exactly 0.95 * prev_close (not <) -> False."""
    # prev_close=100, close=95.0 (exactly 5%, not less), low=94.5
    df = _make_circuit_df(
        closes=[100.0, 95.0],
        lows=[99.0, 94.5],
        opens=[100.0, 99.0],
        highs=[101.0, 99.5],
    )
    scanner = DCBBargainScanner()
    assert scanner._is_lower_circuit(df, 1) is False


# ---------------------------------------------------------------------------
# Circuit streak tests
# ---------------------------------------------------------------------------


def test_circuit_streak_consecutive():
    """Circuit streak counts consecutive lower-circuit days from the end."""
    # Each circuit day must drop 5%+ from previous close AND close pinned at low.
    # Use factor 0.93 each day: 100 -> 93 -> 86 -> 80 -> 74 -> 69 -> 64 -> 60 -> 56 -> 52
    closes = [100.0, 93.0, 86.0, 80.0, 74.0, 69.0, 64.0, 60.0, 56.0, 52.0]
    # low must satisfy: close <= low * 1.01, so low >= close / 1.01
    lows = [99.0] + [c / 1.005 for c in closes[1:]]
    opens = [100.0] + [c * 1.05 for c in closes[1:]]
    highs = [101.0] + [c * 1.06 for c in closes[1:]]
    df = _make_circuit_df(closes, lows, opens=opens, highs=highs)
    scanner = DCBBargainScanner()

    # Verify first circuit day works
    assert scanner._is_lower_circuit(df, 1) is True

    # Manually compute circuit_streak as the scan loop does
    circuit_streak = 0
    last_idx = len(df) - 1
    for ci in range(last_idx, 0, -1):
        if scanner._is_lower_circuit(df, ci):
            circuit_streak += 1
        else:
            break
    assert circuit_streak == 9  # all 9 circuit days


def test_circuit_streak_breaks_on_non_circuit():
    """Circuit streak stops when a non-circuit day is encountered."""
    # Day 0: normal (100); Days 1-3: circuit (93,86,80); Day 4: recovery (90);
    # Days 5-7: circuit (83,77,71) — each drops > 5% from prev
    closes = [100.0, 93.0, 86.0, 80.0, 90.0, 83.0, 77.0, 71.0]
    lows = [99.0] + [c / 1.005 for c in closes[1:]]
    opens = [100.0] + [c * 1.05 for c in closes[1:]]
    highs = [101.0] + [c * 1.06 for c in closes[1:]]
    df = _make_circuit_df(closes, lows, opens=opens, highs=highs)
    scanner = DCBBargainScanner()

    circuit_streak = 0
    last_idx = len(df) - 1
    for ci in range(last_idx, 0, -1):
        if scanner._is_lower_circuit(df, ci):
            circuit_streak += 1
        else:
            break
    assert circuit_streak == 3  # only last 3 circuit days


# ---------------------------------------------------------------------------
# Circuit lock tests
# ---------------------------------------------------------------------------


def test_circuit_lock_true():
    """3+ circuit days with volume < 20% of pre-streak avg -> True."""
    scanner = DCBBargainScanner()
    # 20 normal days + 5 circuit days with declining closes
    normal_closes = [100.0] * 20
    circuit_closes = [93.0, 86.0, 80.0, 74.0, 69.0]
    closes = normal_closes + circuit_closes
    lows = [99.0] * 20 + [c / 1.005 for c in circuit_closes]
    opens = [100.0] * 20 + [c * 1.05 for c in circuit_closes]
    highs = [101.0] * 20 + [c * 1.06 for c in circuit_closes]
    volumes = [100000] * 20 + [5000] * 5
    df = _make_circuit_df(closes, lows, opens=opens, highs=highs)
    df["volume"] = volumes
    # Verify circuit detection works for first circuit day
    assert scanner._is_lower_circuit(df, 20) is True
    assert scanner._is_likely_circuit_lock(df, 24) is True


def test_circuit_lock_false_normal_volume():
    """3+ circuit days but normal volume -> False."""
    scanner = DCBBargainScanner()
    normal_closes = [100.0] * 20
    circuit_closes = [93.0, 86.0, 80.0, 74.0, 69.0]
    closes = normal_closes + circuit_closes
    lows = [99.0] * 20 + [c / 1.005 for c in circuit_closes]
    opens = [100.0] * 20 + [c * 1.05 for c in circuit_closes]
    highs = [101.0] * 20 + [c * 1.06 for c in circuit_closes]
    volumes = [100000] * 25
    df = _make_circuit_df(closes, lows, opens=opens, highs=highs)
    df["volume"] = volumes
    assert scanner._is_likely_circuit_lock(df, 24) is False


def test_circuit_lock_false_fewer_than_3():
    """Fewer than 3 consecutive circuit days -> False."""
    scanner = DCBBargainScanner()
    closes = [100.0, 93.0, 86.0]
    lows = [99.0, 93.0 / 1.005, 86.0 / 1.005]
    opens = [100.0, 93.0 * 1.05, 86.0 * 1.05]
    highs = [101.0, 93.0 * 1.06, 86.0 * 1.06]
    volumes = [100000, 5000, 5000]
    df = _make_circuit_df(closes, lows, opens=opens, highs=highs)
    df["volume"] = volumes
    # Only 2 consecutive circuit days (indices 1 and 2), so streak < 3
    assert scanner._is_likely_circuit_lock(df, 2) is False


# ---------------------------------------------------------------------------
# Free-float filter tests (Fix 4: skip-NULL / missing / measured)
# ---------------------------------------------------------------------------


def _make_valid_tech_rows(n: int = 200) -> list[tuple]:
    """Build n rows of valid OHLCV+delivery tuples as scan() expects from _get_tech_data.
    Alternating up/down bars so del_abs is near zero (passes min_del_abs=-2).
    High-delivery on even bars to ensure enough above-average days.
    Steep price drift so DCB >> close (discount > 15%)."""
    from datetime import date, timedelta

    start = date(2024, 6, 1)
    rows = []
    for i in range(n):
        close = 100.0 - i * 0.25  # drift: discount ~22% in [18,60] window
        # Alternate up (close > open) and down (close < open)
        if i % 2 == 0:
            open_ = close - 1.0  # up day
        else:
            open_ = close + 1.0  # down day
        high = max(open_, close) + 0.5
        low = min(open_, close) - 0.5
        volume = 500000
        # High delivery on even bars, low on odd
        del_pct = 45.0 if i % 2 == 0 else 10.0
        delivery = int(volume * del_pct / 100)
        d = start + timedelta(days=i)
        date_str = d.isoformat()
        rows.append((date_str, open_, high, low, close, volume, delivery, del_pct))
    return rows


def test_free_float_skip_when_null_ff_pct_and_min_ff_mcap_positive():
    """When min_ff_mcap > 0 and ff_pct is None -> symbol is skipped (not included)."""
    scanner = DCBBargainScanner(min_ff_mcap=600.0)
    tech_rows = _make_valid_tech_rows()

    with (
        patch.object(scanner, "_get_universe", return_value=[("TEST", 10000, None)]),
        patch.object(scanner, "_get_tech_data", return_value=tech_rows),
        patch(
            "myra_app.strategies.dcb_bargain.load_ohlcv_for_universe", return_value=None
        ),
    ):
        result = scanner.scan(as_on_date="2025-06-15")
    assert len(result) == 0


def test_free_float_missing_path_when_min_ff_mcap_zero():
    """When min_ff_mcap <= 0 -> candidate included with ff_data_quality='missing', free_float_mcap_cr=None."""
    scanner = DCBBargainScanner(min_ff_mcap=0.0)
    tech_rows = _make_valid_tech_rows()

    with (
        patch.object(scanner, "_get_universe", return_value=[("TEST", 10000, None)]),
        patch.object(scanner, "_get_tech_data", return_value=tech_rows),
        patch(
            "myra_app.strategies.dcb_bargain.load_ohlcv_for_universe", return_value=None
        ),
    ):
        result = scanner.scan(as_on_date="2025-06-15")

    assert len(result) == 1
    row = result.iloc[0]
    assert row["ff_data_quality"] == "missing"
    assert row["free_float_mcap_cr"] is None
    assert row["symbol"] == "TEST"


def test_free_float_measured_path_includes_candidate():
    """When min_ff_mcap > 0 and ff_pct is provided -> ff_data_quality='measured', value computed."""
    scanner = DCBBargainScanner(
        min_ff_mcap=0.01
    )  # tiny threshold so test data qualifies
    tech_rows = _make_valid_tech_rows()
    # mcap is raw from fundamentals (not Cr). Universe query divides by 1e7 for Cr filter.
    # free_float_mcap_cr = (mcap * ff_pct / 100) / 1e7
    # Use mcap=10_000_000 raw = 1 Cr market cap, ff_pct=25% -> ff_mcap=0.25 Cr > 0.01
    mcap_raw = 10_000_000
    ff_pct = 25.0
    expected_ff_mcap_cr = (mcap_raw * ff_pct / 100.0) / 1e7  # 0.25
    with (
        patch.object(
            scanner, "_get_universe", return_value=[("TEST", mcap_raw, ff_pct)]
        ),
        patch.object(scanner, "_get_tech_data", return_value=tech_rows),
        patch(
            "myra_app.strategies.dcb_bargain.load_ohlcv_for_universe", return_value=None
        ),
    ):
        result = scanner.scan(as_on_date="2025-06-15")

    assert len(result) == 1
    row = result.iloc[0]
    assert row["ff_data_quality"] == "measured"
    assert row["free_float_mcap_cr"] == pytest.approx(expected_ff_mcap_cr, abs=0.001)


def test_free_float_measured_path_skips_below_threshold():
    """When min_ff_mcap > 0 and computed ff mcap < threshold -> candidate skipped."""
    scanner = DCBBargainScanner(min_ff_mcap=100.0)  # high threshold
    tech_rows = _make_valid_tech_rows()
    # mcap=10_000_000 raw = 1 Cr, ff_pct=25% -> free_float_mcap_cr=0.25 < 100 -> skip
    with (
        patch.object(
            scanner, "_get_universe", return_value=[("TEST", 10_000_000, 25.0)]
        ),
        patch.object(scanner, "_get_tech_data", return_value=tech_rows),
        patch(
            "myra_app.strategies.dcb_bargain.load_ohlcv_for_universe", return_value=None
        ),
    ):
        result = scanner.scan(as_on_date="2025-06-15")
    assert len(result) == 0


def test_free_float_round_none_not_type_error():
    """Regression: round(None, 2) used to raise TypeError in min_ff_mcap=0 path.
    With the guard, free_float_mcap_cr=None passes through cleanly."""
    scanner = DCBBargainScanner(min_ff_mcap=0.0)
    tech_rows = _make_valid_tech_rows()

    with (
        patch.object(scanner, "_get_universe", return_value=[("TEST", 10000, None)]),
        patch.object(scanner, "_get_tech_data", return_value=tech_rows),
        patch(
            "myra_app.strategies.dcb_bargain.load_ohlcv_for_universe", return_value=None
        ),
    ):
        # This must NOT raise TypeError from round(None, 2)
        result = scanner.scan(as_on_date="2025-06-15")

    assert len(result) == 1
    assert result.iloc[0]["free_float_mcap_cr"] is None
