"""Tests for the opt-in v2.5 shadow ordering comparison (Phase 6)."""

from __future__ import annotations

import pytest

from myra_app.shadow_ranking import compare_orderings


def _row(symbol, **extra):
    """A minimal result row: identical technicals apart from the symbol."""
    row = {
        "Stock": symbol,
        "MYRA_Score": 50,
        "Stars": "***",
        "Stage": "Stage 2",
        "LTP": 100.0,
        "close": 100.0,
        "sma50": 95.0,
        "sma150": 90.0,
        "sma200": 85.0,
    }
    row.update(extra)
    return row


def test_empty_results_are_safe():
    report = compare_orderings([], "NEUTRAL", {"A": {"roe": 12.0}})
    assert report["n"] == 0
    assert report["identical_ordering"] is True
    assert report["spearman"] is None
    assert report["material_moves"] == []


def test_no_inputs_reproduces_current_ordering():
    """The default path (no populated inputs) must be byte-for-byte unchanged."""
    rows = [_row("AAA"), _row("BBB"), _row("CCC")]
    report = compare_orderings(rows, "NEUTRAL", inputs={}, top_n=3)

    assert report["identical_ordering"] is True
    assert report["spearman"] == pytest.approx(1.0)
    assert report["moved"] == 0
    assert report["max_position_delta"] == 0
    assert report["top_n_overlap"] == report["top_n"]
    assert set(report["coverage"].values()) == {0}


def test_inputs_are_never_required_to_be_complete():
    """Absent inputs leave the ordering identical (neutral fallbacks)."""
    rows = [_row("AAA"), _row("BBB")]
    report = compare_orderings(rows, "NEUTRAL", inputs={"AAA": {}, "BBB": {}})
    assert report["identical_ordering"] is True
    assert report["spearman"] == pytest.approx(1.0)


def test_populated_inputs_change_ordering_and_report_coverage():
    # Baseline ranks by technical stability; the populated inputs should be
    # strong enough to reverse it, which is exactly the movement we measure.
    rows = [
        _row("AAA", pct_above_ma50_60d=0.9),
        _row("BBB", pct_above_ma50_60d=0.5),
        _row("CCC", pct_above_ma50_60d=0.1),
    ]
    inputs = {
        "AAA": {
            "avg_volume_20d": 10.0,
            "avg_delivery_20d": 5.0,
            "roe": 1.0,
            "ProfitGrowth": 1.0,
        },
        "BBB": {
            "avg_volume_20d": 500.0,
            "avg_delivery_20d": 40.0,
            "roe": 20.0,
            "ProfitGrowth": 25.0,
        },
        "CCC": {
            "avg_volume_20d": 1000.0,
            "avg_delivery_20d": 70.0,
            "roe": 30.0,
            "ProfitGrowth": 40.0,
        },
    }
    report = compare_orderings(rows, "NEUTRAL", inputs=inputs, top_n=2)

    assert report["n"] == 3
    assert report["coverage"] == {
        "roe": 3,
        "ProfitGrowth": 3,
        "avg_delivery_20d": 3,
        "avg_volume_20d": 3,
    }
    assert report["identical_ordering"] is False
    assert report["moved"] > 0
    assert report["spearman"] is not None and report["spearman"] < 1.0
    # The strongest candidate (CCC) displaces the weakest (AAA) in the top 2.
    assert report["symbols_added"] == ["CCC"]
    assert report["symbols_dropped"] == ["AAA"]


def test_coverage_counts_partial_inputs():
    rows = [_row("AAA"), _row("BBB"), _row("CCC")]
    inputs = {
        "AAA": {"roe": 10.0},
        "CCC": {"avg_volume_20d": 1.0, "roe": 0.0},
    }
    report = compare_orderings(rows, "NEUTRAL", inputs=inputs)
    assert report["coverage"]["roe"] == 2
    assert report["coverage"]["avg_volume_20d"] == 1
    assert report["coverage"]["avg_delivery_20d"] == 0
    assert report["coverage"]["ProfitGrowth"] == 0


def test_report_is_deterministic():
    rows = [_row("AAA"), _row("BBB"), _row("CCC"), _row("DDD")]
    inputs = {
        "BBB": {"avg_volume_20d": 500.0, "roe": 20.0},
        "DDD": {"avg_delivery_20d": 60.0},
    }
    first = compare_orderings(rows, "NEUTRAL", inputs=inputs, top_n=2)
    second = compare_orderings(rows, "NEUTRAL", inputs=inputs, top_n=2)
    assert first == second


def test_top_n_overlap_is_full_when_ordering_is_stable():
    rows = [_row("AAA"), _row("BBB"), _row("CCC"), _row("DDD")]
    inputs = {
        "AAA": {"avg_volume_20d": 1.0},
        "BBB": {"avg_volume_20d": 2.0},
        "CCC": {"avg_volume_20d": 3.0},
        "DDD": {"avg_volume_20d": 4.0},
    }
    report = compare_orderings(rows, "NEUTRAL", inputs=inputs, top_n=4)
    assert report["top_n_overlap"] == 4
    assert report["top_n_jaccard"] == pytest.approx(1.0)
