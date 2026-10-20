"""Golden parity tests for ``WyckoffAutomaton._detect_events``.

The fixture in ``tests/fixtures/wyckoff_detect_golden.json`` was captured from the
implementation *before* the detection-loop optimisation, so these tests pin the
exact event stream (types, order, every field, every float) rather than just the
counts.

Coverage: SC, SOS and Spring. AR/ST are asserted absent on purpose — see
``test_ar_and_st_remain_unreachable``. They are unreachable in the current code
because the SC lookup compares ``df["date"].astype(str)`` ("2025-03-12") with
``str(Timestamp)`` ("2025-03-12 00:00:00"); fixing that would change scanner
output and is out of scope for a performance change. The fixture would fail if an
optimisation accidentally "fixed" it.

No database access: the scanner's market-cap memo is pre-seeded as "missing", so
``_get_historical_mcap`` returns before it can touch ``myra_valuation.db``.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from myra_app.strategies.wyckoff_automaton import WyckoffAutomaton, _window_mean

FIXTURE = Path(__file__).parent / "fixtures" / "wyckoff_detect_golden.json"


def _load():
    with FIXTURE.open(encoding="utf-8") as fh:
        return json.load(fh)


def _scanner(symbol: str) -> WyckoffAutomaton:
    sc = WyckoffAutomaton(min_mcap=510, max_mcap=530000)
    # Memoized-missing market cap: `_get_historical_mcap` returns None on the
    # first branch instead of querying the valuation DB.
    sc._current_mcap_map = {symbol: None}
    sc._mcap_log_range = None
    return sc


def _frame(case: dict) -> pd.DataFrame:
    df = pd.DataFrame(case["rows"], columns=case["columns"])
    df["date"] = pd.to_datetime(df["date"])
    return df


DATA = _load()
CASE_IDS = [c["name"] for c in DATA["cases"]]


@pytest.mark.parametrize("case", DATA["cases"], ids=CASE_IDS)
def test_detect_events_matches_golden(case):
    symbol = case["name"].upper()
    actual = _scanner(symbol)._detect_events(
        _frame(case), symbol=symbol, as_on_date=DATA["as_on_date"]
    )
    # Full structural equality: list order, dict key order is irrelevant but
    # values (including floats) must match exactly.
    assert actual == case["expected"]


@pytest.mark.parametrize("case", DATA["cases"], ids=CASE_IDS)
def test_golden_is_not_empty_for_expected_cases(case):
    """Guard against a fixture that silently captured nothing."""
    if case["name"] in {
        "all_events",
        "sc_then_rally_ar_st_dead",
        "spring_only",
        "zero_volume_candles",
        "nan_and_non_numeric_delivery",
        "no_swing_low_column",
        "all_nan_swing_low",
        "reverse_sorted_input",
    }:
        assert case["expected"], f"{case['name']} captured no events"


def test_event_type_coverage():
    types = {e["event"] for c in DATA["cases"] for e in c["expected"]}
    assert {"SC", "SOS", "Spring"} <= types


def test_ar_and_st_remain_unreachable():
    """Pins the pre-existing defective SC lookup (out of scope to fix here).

    ``df["date"].astype(str)`` produces "YYYY-MM-DD" while ``event_date`` holds
    ``str(Timestamp)`` -> "YYYY-MM-DD 00:00:00", so ``sc_idx`` is always empty.
    A performance change must not alter that.
    """
    types = {e["event"] for c in DATA["cases"] for e in c["expected"]}
    assert "AR" not in types
    assert "ST" not in types
    # The case designed to emit AR/ST under a correct lookup yields SC only.
    case = next(c for c in DATA["cases"] if c["name"] == "sc_then_rally_ar_st_dead")
    assert {e["event"] for e in case["expected"]} == {"SC"}


def test_ordering_is_stable_across_repeated_calls():
    """Repeat detection on the richest case: identical output every time."""
    case = next(c for c in DATA["cases"] if c["name"] == "all_events")
    symbol = case["name"].upper()
    first = _scanner(symbol)._detect_events(
        _frame(case), symbol=symbol, as_on_date=DATA["as_on_date"]
    )
    for _ in range(3):
        again = _scanner(symbol)._detect_events(
            _frame(case), symbol=symbol, as_on_date=DATA["as_on_date"]
        )
        assert again == first


def test_insufficient_history_returns_empty():
    for name in ("short_history", "exactly_54_rows"):
        case = next(c for c in DATA["cases"] if c["name"] == name)
        assert case["expected"] == []


def test_detection_does_not_mutate_input_frame():
    """The hot path must not write back into the caller's DataFrame."""
    case = next(c for c in DATA["cases"] if c["name"] == "all_events")
    df = _frame(case)
    before = df.copy(deep=True)
    _scanner("ALL_EVENTS")._detect_events(
        df, symbol="ALL_EVENTS", as_on_date=DATA["as_on_date"]
    )
    pd.testing.assert_frame_equal(df, before)


# --------------------------------------------------------------------------
# `_window_mean`: the NaN-free fast path could not be exercised on real data
# (no symbol in the universe has a NaN delivery_pct), so cover every branch.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "values",
    [
        [10.0, 20.0, 30.0],
        [10.0, 20.0],
        [5.0],
        [10.0, float("nan"), 30.0],
        [float("nan"), float("nan"), 42.0],
        [float("nan")],
        [float("nan"), float("nan")],
        [0.0, 0.0, 0.0],
        [-5.0, 5.0, 0.0],
    ],
)
def test_window_mean_matches_pandas_mean(values):
    arr = np.array(values, dtype=float)
    has_nan = bool(np.isnan(arr).any())
    expected = float(pd.Series(arr).mean())
    got = _window_mean(arr, has_nan)
    assert (got == expected) or (np.isnan(got) and np.isnan(expected))


def test_window_mean_empty_window_is_nan():
    assert np.isnan(_window_mean(np.array([], dtype=float), has_nan=False))


def test_window_mean_fast_path_is_bit_identical_to_nanmean():
    """No-NaN windows: skipping the NaN scan must not change the float."""
    rng = np.random.default_rng(20261020)
    for _ in range(200):
        arr = rng.uniform(0, 100, size=50)
        assert _window_mean(arr, has_nan=False) == float(np.nanmean(arr))
