"""
Tests for tools/calibrate_wyckoff_weights.py — pure-computation level.

No database access, no network. These pin the calibration machinery against
the scanner's own scoring helpers so the offline re-scoring (which every
weight-set evaluation and the final PROCEED/ABANDON gate rely on) cannot drift
from `WyckoffAutomaton` without a test failing.

Why not run the full script in --smoke mode as a test? `--smoke` still scans
150 symbols x 12 dates against the SQLite sidecars (~1 min runtime) and the
test rules cap pytest at `--timeout=30`, so a DB-touching smoke test does not
belong in the suite. The closest fast equivalent is asserting here that the
script's pure arithmetic (component fractions, combo scoring, quintile
metrics, split logic) reproduces the scanner byte-for-byte modulo the final
1-dp round, and the CLI itself was verified end-to-end on real data (see
ABANDON comment in wyckoff_automaton.py).
"""

import os
import sys
from datetime import date

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

import backtest_wyckoff  # noqa: E402,F401  (calibration module imports it)
import calibrate_wyckoff_weights as calib  # noqa: E402
from myra_app.strategies.wyckoff_automaton import WyckoffAutomaton  # noqa: E402


# ---------------------------------------------------------------------------
# Split logic
# ---------------------------------------------------------------------------


def test_split_scan_dates_70_15_15_chronological():
    dates = [
        date(2025, 7, 1),
        date(2025, 7, 23),
        date(2025, 8, 14),
        date(2025, 9, 5),
        date(2025, 10, 1),
        date(2025, 10, 19),
        date(2025, 11, 10),
        date(2025, 12, 2),
        date(2025, 12, 24),
        date(2026, 1, 15),
        date(2026, 2, 6),
        date(2026, 2, 28),
    ]
    train, val, hold = calib._split_scan_dates(dates)
    assert len(train) == 8 and len(val) == 2 and len(hold) == 2
    assert train == dates[:8] and val == dates[8:10] and hold == dates[10:]
    assert max(train) < min(val) and max(val) < min(hold)


def test_verify_no_leak_ok():
    cands = [
        {"_scan_date": "2025-07-01", "symbol": "A", "event_date": "2025-08-01"},
        {"_scan_date": "2025-10-01", "symbol": "A", "event_date": "2025-11-01"},
        {"_scan_date": "2026-01-01", "symbol": "A", "event_date": "2026-01-05"},
    ]
    # No exception = integrity holds (each event in exactly one split,
    # chronological, no (symbol,event_date) repeated across splits).
    calib._verify_no_leak(cands, ["2025-07-01"], ["2025-10-01"], ["2026-01-01"])


def test_verify_no_leak_cross_split_event_raises():
    cands = [
        {"_scan_date": "2025-07-01", "symbol": "A", "event_date": "2025-08-01"},
        # Same (symbol, event_date) re-detected by a later scan date now in VALIDATION
        {"_scan_date": "2025-10-01", "symbol": "A", "event_date": "2025-08-01"},
    ]
    with pytest.raises(AssertionError, match="leaked across splits"):
        calib._verify_no_leak(cands, ["2025-07-01"], ["2025-10-01"], ["2026-01-01"])


# ---------------------------------------------------------------------------
# Search-space helpers
# ---------------------------------------------------------------------------


def test_normalize_base_sums_to_100():
    norm = calib._normalize_base(
        {
            "delivery_absorption": 30,
            "lower_wick": 30,
            "close_location": 20,
            "grab_depth": 10,
        }
    )
    assert sum(norm.values()) == pytest.approx(100.0)
    assert norm["delivery_absorption"] == pytest.approx(100 * 30 / 90)
    assert norm["grab_depth"] == pytest.approx(100 * 10 / 90)


def test_normalize_base_all_zero_is_none():
    assert (
        calib._normalize_base(
            {
                "delivery_absorption": 0,
                "lower_wick": 0,
                "close_location": 0,
                "grab_depth": 0,
            }
        )
        is None
    )


def test_parse_weights_flag_merges_over_defaults():
    w = calib._parse_weights_flag("delivery_absorption=40,lower_wick=30")
    assert w["delivery_absorption"] == 40.0
    assert w["lower_wick"] == 30.0
    # everything else keeps the shipped default
    assert w["close_location"] == calib.DEFAULT_SPRING_WEIGHTS["close_location"]
    assert w["grab_depth"] == calib.DEFAULT_SPRING_WEIGHTS["grab_depth"]


def test_random_combos_deterministic_and_normalised():
    a = calib._random_combos(50)
    b = calib._random_combos(50)
    assert [tuple(sorted(wa.items())) for wa in a] == [
        tuple(sorted(wb.items())) for wb in b
    ]
    for w in a:
        base_sum = (
            w["delivery_absorption"]
            + w["lower_wick"]
            + w["close_location"]
            + w["grab_depth"]
        )
        assert base_sum == pytest.approx(100.0)


# ---------------------------------------------------------------------------
# Grab-candle indexing
# ---------------------------------------------------------------------------


def _dates_df(n=10):
    df = pd.DataFrame({"date": pd.date_range("2025-01-01", periods=n)})
    return df


def test_grab_candle_index_unconfirmed_is_event_row():
    df = _dates_df()
    e = {"event_date": "2025-01-05", "two_candle_confirm": False}
    assert calib._grab_candle_index(df, e) == 4


def test_grab_candle_index_confirmed_is_previous_row():
    df = _dates_df()
    e = {"event_date": "2025-01-05", "two_candle_confirm": True}
    assert calib._grab_candle_index(df, e) == 3


# ---------------------------------------------------------------------------
# Offline re-scoring must match the scanner's helpers exactly
# ---------------------------------------------------------------------------


def _synthetic_cands():
    rng = np.random.default_rng(11)
    cands = []
    for _ in range(60):
        cands.append(
            {
                "del_abs": float(rng.uniform(-5, 15)),
                "lower_wick_ratio": float(rng.uniform(0.05, 0.95)),
                "close_location": float(rng.uniform(0.2, 0.95)),
                "grab_depth_pct": float(rng.uniform(0.0, 2.0)),
                "equal_low_zone": bool(rng.integers(0, 2)),
                "two_candle_confirm": bool(rng.integers(0, 2)),
            }
        )
    return cands


@pytest.mark.parametrize(
    "weights",
    [
        dict(calib.DEFAULT_SPRING_WEIGHTS),  # as shipped (not normalised)
        {
            "delivery_absorption": 25.8621,
            "lower_wick": 17.2414,
            "close_location": 43.1034,
            "grab_depth": 13.7931,
            "equal_low_bonus": 2.0,
            "two_candle_bonus": 6.0,
        },  # 2026-08 best-on-train
        {
            "delivery_absorption": 0,
            "lower_wick": 50,
            "close_location": 0,
            "grab_depth": 20,
            "equal_low_bonus": 15,
            "two_candle_bonus": 10,
        },  # probe-like corner
    ],
)
def test_score_combo_matches_scanner_helpers(weights):
    cands = _synthetic_cands()
    u1, u2, u3, u4, eqf, tcf = calib._score_arrays(cands)
    scores = calib._score_combo(weights, u1, u2, u3, u4, eqf, tcf)
    assert len(scores) == len(cands)

    for i, c in enumerate(cands):
        del_s = WyckoffAutomaton._delivery_absorption_score(
            c["del_abs"], weights["delivery_absorption"]
        )
        wick_s = WyckoffAutomaton._lower_wick_score(
            c["lower_wick_ratio"], weights["lower_wick"]
        )
        close_s = WyckoffAutomaton._close_location_score(
            c["close_location"], weights["close_location"]
        )
        depth_s = WyckoffAutomaton._grab_depth_score(
            c["grab_depth_pct"], weights["grab_depth"]
        )
        eq_bonus = weights["equal_low_bonus"] if c["equal_low_zone"] else 0.0
        ref = WyckoffAutomaton._compute_spring_score(
            del_s,
            wick_s,
            close_s,
            depth_s,
            eq_bonus,
            c["two_candle_confirm"],
            weights["two_candle_bonus"],
        )
        assert scores[i] == pytest.approx(ref)


def test_wick_fraction_matches_scanner_curve():
    for ratio in [0.15, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.75, 0.9]:
        scanner = WyckoffAutomaton._lower_wick_score(ratio)  # default scale 30
        calib_frac = calib._wick_fraction(ratio)
        assert scanner == pytest.approx(round(calib_frac * 30.0, 1))


# ---------------------------------------------------------------------------
# Quintile metrics: grade-D dropout + pandas-qcut equivalence
# ---------------------------------------------------------------------------


def test_split_metrics_excludes_grade_d_scores():
    scores = np.array([30.0, 34.9, 35.0, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0, 100.0])
    net = np.full(10, 5.0)
    n_pass, q5q1, win_rate, sharpe, composite = calib._split_metrics(scores, net, 120)
    # 30.0 and 34.9 (grade D, < 35) must be dropped by the score >= 35 gate.
    assert n_pass == 8


def test_split_metrics_matches_pandas_qcut():
    rng = np.random.default_rng(7)
    scores = np.round(rng.uniform(35.0, 100.0, 300), 1)
    net = rng.normal(0.0, 30.0, 300)
    n_pass, q5q1, _, _, _ = calib._split_metrics(scores.copy(), net.copy(), 120)
    assert n_pass == 300

    df = pd.DataFrame({"s": scores, "r": net}).query("s >= 35")
    grp = df.groupby(pd.qcut(df["s"], q=5, duplicates="drop"), observed=False)[
        "r"
    ].mean()
    assert q5q1 == pytest.approx(float(grp.iloc[-1] - grp.iloc[0]))


def test_split_metrics_invalid_when_fewer_than_five_buckets():
    scores = np.array([49.0, 49.0, 50.0, 50.0, 51.0])
    net = np.array([10.0, -5.0, 3.0, 7.0, -2.0])
    n_pass, q5q1, win_rate, sharpe, composite = calib._split_metrics(scores, net, 120)
    assert n_pass == 5
    assert np.isnan(q5q1) and np.isnan(win_rate) and np.isnan(sharpe)
    assert composite == float("-inf")
