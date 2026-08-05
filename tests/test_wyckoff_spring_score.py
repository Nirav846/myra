"""
Tests for Wyckoff Spring scoring logic.
No database access, no network — pure computation only.
"""

import numpy as np
import pandas as pd
import pytest

from myra_app.strategies.wyckoff_automaton import WyckoffAutomaton


# ---------------------------------------------------------------------------
# Part 1 — Pure helper tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "del_abs, expected",
    [
        (-5, 0.0),  # negative → clamped to 0
        (0, 0.0),  # zero → 0
        (5, 15.0),  # linear mid
        (10, 30.0),  # full
        (20, 30.0),  # clamped at 30
    ],
)
def test_delivery_absorption_score(del_abs, expected):
    assert WyckoffAutomaton._delivery_absorption_score(del_abs) == pytest.approx(
        expected
    )


@pytest.mark.parametrize(
    "ratio, expected",
    [
        (0.1, 0.0),  # below 0.20 threshold → 0
        (0.2, 0.0),  # at threshold → 0
        (0.4, 15.0),  # at second breakpoint
        (0.5, 18.5),  # linear interp: 15 + (0.1/0.2)*7 = 18.5
        (0.6, 22.0),  # at third breakpoint
        (0.75, 30.0),  # at max
        (0.9, 30.0),  # above max → clamped
    ],
)
def test_lower_wick_score(ratio, expected):
    assert WyckoffAutomaton._lower_wick_score(ratio) == pytest.approx(expected)


@pytest.mark.parametrize(
    "ratio, expected",
    [
        (0.3, 5.0),  # < 0.5
        (0.5, 10.0),  # at 0.5
        (0.6, 10.0),  # in 0.5–0.75
        (0.75, 10.0),  # at upper bound of middle tier
        (0.76, 20.0),  # > 0.75
    ],
)
def test_close_location_score(ratio, expected):
    assert WyckoffAutomaton._close_location_score(ratio) == pytest.approx(expected)


@pytest.mark.parametrize(
    "depth_pct, expected",
    [
        (0.3, 7.0),  # < 0.5
        (0.5, 10.0),  # at 0.5
        (1.0, 10.0),  # mid
        (1.5, 10.0),  # at 1.5
        (1.6, 5.0),  # > 1.5
        (-2.0, 7.0),  # negative → falls in < 0.5 bucket
    ],
)
def test_grab_depth_score(depth_pct, expected):
    assert WyckoffAutomaton._grab_depth_score(depth_pct) == pytest.approx(expected)


@pytest.mark.parametrize(
    "score, expected",
    [
        (70, "A+"),
        (65, "A+"),
        (64.9, "B"),
        (50, "B"),
        (49.9, "C"),
        (35, "C"),
        (34.9, "D"),
    ],
)
def test_spring_grade(score, expected):
    assert WyckoffAutomaton._spring_grade(score) == expected


@pytest.mark.parametrize(
    "del_s, wick_s, close_s, depth_s, bonus, confirm, expected",
    [
        (30, 30, 20, 10, 10, False, 100.0),  # exact max
        (30, 30, 20, 10, 10, True, 100.0),  # clamped from 105
        (15, 15, 10, 7, 10, True, 62.0),  # mid with confirm: 15+15+10+7+10+5=62
        (0, 0, 5, 7, 0, False, 12.0),  # low
    ],
)
def test_compute_spring_score(del_s, wick_s, close_s, depth_s, bonus, confirm, expected):
    result = WyckoffAutomaton._compute_spring_score(
        del_s, wick_s, close_s, depth_s, bonus, confirm
    )
    assert result == pytest.approx(expected)


# ---------------------------------------------------------------------------
# Part 2 — Integration tests for _detect_events
# ---------------------------------------------------------------------------


def _build_wyckoff_df(n=70, override_rows=None):
    """Build a synthetic DataFrame for _detect_events testing.

    Default: flat basing pattern with lows=100, highs=106, closes=103.
    Override specific rows via override_rows dict: {row_index: {col: val}}.
    """
    dates = pd.date_range("2025-01-01", periods=n)
    data = {
        "date": dates,
        "open": [103.5] * n,
        "high": [106.0] * n,
        "low": [100.0] * n,
        "close": [103.0] * n,
        "volume": [50000.0] * n,
        "delivery": [15000.0] * n,
        "delivery_pct": [30.0] * n,
        "swing_low": [float("nan")] * n,
        "nifty_outperformance_score": [0.0] * n,
        "sma_50": [102.0] * n,
        "high_52w": [130.0] * n,
        "low_52w": [95.0] * n,
    }
    df = pd.DataFrame(data)
    if override_rows:
        for idx, overrides in override_rows.items():
            for col, val in overrides.items():
                df.at[idx, col] = val
    return df


def test_detect_events_spring_fields():
    """Spring on second-to-last row with two_candle_confirm and equal_low_zone."""
    # Row 63: pivot swing_low matching grab level (equal-low candidate)
    # Row 68: Spring (bearish grab, low=98.5 < 99)
    # Row 69: Confirmation (bullish, close > swing_low_val=100)
    df = _build_wyckoff_df(
        n=70,
        override_rows={
            63: {"swing_low": 100.0},  # low already 100.0 → equal-low match
            68: {
                "low": 98.5,  # < 100 * 0.99 = 99 ✓
                "high": 103.5,
                "close": 101.0,  # > 100 ✓
                "open": 103.0,  # > close → bearish ✓
                "volume": 70000.0,
                "delivery_pct": 60.0,  # > 35 ✓
                "swing_low": 100.0,
            },
            69: {
                "low": 102.0,
                "high": 106.0,
                "close": 105.0,  # > open and > ref_level=100 ✓
                "open": 103.0,
            },
        },
    )

    scanner = WyckoffAutomaton()
    events = scanner._detect_events(df, symbol="TEST")

    spring_events = [e for e in events if e["event"] == "Spring"]
    assert len(spring_events) == 1

    s = spring_events[0]
    assert s["two_candle_confirm"] is True
    assert s["grade"] in ("A+", "B", "C")
    assert 0 <= s["spring_score"] <= 100
    assert s["equal_low_zone"] is True
    assert s["lower_wick_ratio"] is not None
    assert s["close_location"] is not None
    assert s["grab_depth_pct"] is not None


def test_detect_events_skips_grade_d():
    """Very weak Spring (tiny wick, minimal delivery) scores grade D → excluded."""
    # Row 69: weak Spring (last row, no next day for confirmation)
    # Big upper wick → tiny lower_wick_ratio → wick_score=0
    # delivery_pct barely above 35, close to 50-day avg → del_score≈3
    df = _build_wyckoff_df(
        n=70,
        override_rows={
            69: {
                "low": 98.5,  # < 99 ✓
                "high": 110.0,  # big upper wick
                "close": 100.5,  # > 100 ✓
                "open": 108.0,  # bearish
                "delivery_pct": 36.0,  # barely > 35 ✓
            },
        },
    )
    # Override all delivery_pct to 35 so avg_del_50 ≈ 35, del_abs = 1
    df["delivery_pct"] = 35.0
    df.at[69, "delivery_pct"] = 36.0

    scanner = WyckoffAutomaton()
    events = scanner._detect_events(df, symbol="TEST")

    spring_events = [e for e in events if e["event"] == "Spring"]
    assert len(spring_events) == 0


def test_detect_events_spring_no_equal_low():
    """Spring without equal-low zone bonus → lower score, no equal_low_zone."""
    # Same as spring_fields test but WITHOUT a matching pivot swing_low
    df = _build_wyckoff_df(
        n=70,
        override_rows={
            68: {
                "low": 98.5,
                "high": 103.5,
                "close": 101.0,
                "open": 103.0,
                "volume": 70000.0,
                "delivery_pct": 60.0,
                "swing_low": 100.0,
            },
            69: {
                "low": 102.0,
                "high": 106.0,
                "close": 105.0,
                "open": 103.0,
            },
        },
    )
    # No other row has swing_low set → equal_low_zone should be False

    scanner = WyckoffAutomaton()
    events = scanner._detect_events(df, symbol="TEST")

    spring_events = [e for e in events if e["event"] == "Spring"]
    assert len(spring_events) == 1

    s = spring_events[0]
    assert s["equal_low_zone"] is False
    assert s["two_candle_confirm"] is True
    # Score without equal-low bonus: 30 + 18.5 + 10 + 10 + 0 + 5 = 73.5
    assert s["spring_score"] == pytest.approx(73.5)
    assert s["grade"] == "A+"
