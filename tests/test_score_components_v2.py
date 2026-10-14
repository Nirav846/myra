"""Regression tests for myra_app.score_components_v2.

These lock the current (deterministic) scoring semantics so that future
refactors of the scoring components cannot silently change rankings.
Pure-python: no DB, no IO.
"""

import math

import numpy as np
import pandas as pd

from myra_app import score_components_v2


def test_precompute_ranks_creates_expected_columns():
    df = pd.DataFrame(
        {
            "roe": [10, 5, np.nan],
            "ProfitGrowth": [20, 10, 0],
            "avg_delivery_20d": [1.0, 2.0, 0.5],
            "avg_volume_20d": [1000, 500, 2000],
            "smart_money_score": [0.9, 0.5, 0.1],
        }
    )
    out = score_components_v2.precompute_ranks(df.copy())

    rank_cols = [
        "_rank_roe",
        "_rank_growth",
        "_rank_delivery",
        "_rank_volume",
        "_rank_sm_score",
    ]
    for col in rank_cols:
        assert col in out.columns
        assert out[col].min() >= 0.0
        assert out[col].max() <= 1.0

    # Ranks are ascending percentile ranks (higher value -> higher rank).
    assert out.at[0, "_rank_growth"] > out.at[1, "_rank_growth"]
    assert out.at[1, "_rank_growth"] > out.at[2, "_rank_growth"]
    assert out.at[2, "_rank_volume"] > out.at[0, "_rank_volume"]
    assert out.at[0, "_rank_sm_score"] > out.at[1, "_rank_sm_score"]


def test_precompute_ranks_missing_values_are_not_ranked():
    """Missing data must never be rewarded with a percentile rank.

    Regression for the ``na_option="bottom"`` defect: in pandas that option
    assigns NaN the *largest* rank (the comment claimed the opposite), so a
    missing metric silently received the top percentile. Now NaN is preserved
    and the present values are ranked among themselves.
    """
    df = pd.DataFrame({"roe": [10, 5, np.nan]})
    out = score_components_v2.precompute_ranks(df.copy())
    assert np.isnan(out.at[2, "_rank_roe"])  # missing -> NaN, not 1.0
    assert out.at[0, "_rank_roe"] == 1.0  # highest of the *present* values
    assert out.at[1, "_rank_roe"] == 0.5


def test_precompute_ranks_missing_does_not_outrank_real_weak_value():
    """A row with no data must rank at-or-below every row that has real data."""
    df = pd.DataFrame({"smart_money_score": [np.nan, 0.01, 0.02]})
    out = score_components_v2.precompute_ranks(df.copy())
    assert np.isnan(out.at[0, "_rank_sm_score"])
    assert out.at[1, "_rank_sm_score"] < out.at[2, "_rank_sm_score"]


def test_precompute_ranks_all_missing_gives_all_nan():
    df = pd.DataFrame({"roe": [np.nan, np.nan]})
    out = score_components_v2.precompute_ranks(df.copy())
    assert out["_rank_roe"].isna().all()


def test_precompute_ranks_masks_non_finite():
    # +/-inf must not dominate the distribution (masked -> NaN -> unranked).
    df = pd.DataFrame({"roe": [1.0, np.inf, -np.inf, 2.0, np.nan]})
    out = score_components_v2.precompute_ranks(df.copy())
    ranks = out["_rank_roe"].tolist()
    assert ranks[0] == 0.5 and ranks[3] == 1.0
    assert np.isnan(ranks[1]) and np.isnan(ranks[2]) and np.isnan(ranks[4])


def test_precompute_ranks_ties_share_percentile():
    df = pd.DataFrame({"roe": [5.0, 5.0, 5.0]})
    out = score_components_v2.precompute_ranks(df.copy())
    assert np.allclose(out["_rank_roe"].to_numpy(), [2 / 3, 2 / 3, 2 / 3])


def test_precompute_ranks_does_not_mutate_input():
    df = pd.DataFrame({"roe": [10, 5, np.nan]})
    before = list(df.columns)
    out = score_components_v2.precompute_ranks(df)
    assert "_rank_roe" in out.columns
    assert list(df.columns) == before  # caller's frame untouched


def test_precompute_ranks_preserves_custom_index():
    df = pd.DataFrame({"roe": [10, 5, np.nan]}, index=["a", "b", "c"])
    out = score_components_v2.precompute_ranks(df)
    assert list(out.index) == ["a", "b", "c"]
    assert out.at["a", "_rank_roe"] == 1.0
    assert out.at["b", "_rank_roe"] == 0.5
    assert np.isnan(out.at["c", "_rank_roe"])


def test_precompute_ranks_partial_columns():
    df = pd.DataFrame({"roe": [5, 10, 15]})
    out = score_components_v2.precompute_ranks(df.copy())
    assert "_rank_roe" in out.columns
    assert "_rank_growth" not in out.columns  # source column absent
    assert np.allclose(out["_rank_roe"].to_numpy(), [1 / 3, 2 / 3, 1.0])


def test_trend_score_fallback_when_missing():
    row = {"sma50": np.nan, "sma150": 100, "sma200": 90, "close": 95}
    assert score_components_v2.trend_score(row) == 0.3


def test_trend_score_flags():
    # sma50>sma150, sma150>sma200, close>sma200 -> full 1.0
    assert math.isclose(
        score_components_v2.trend_score(
            {"sma50": 110, "sma150": 100, "sma200": 90, "close": 95}
        ),
        1.0,
    )
    # close below sma200 -> 0.8
    assert math.isclose(
        score_components_v2.trend_score(
            {"sma50": 110, "sma150": 100, "sma200": 90, "close": 85}
        ),
        0.8,
    )
    # sma50 <= sma150 -> 0.6
    assert math.isclose(
        score_components_v2.trend_score(
            {"sma50": 95, "sma150": 100, "sma200": 90, "close": 95}
        ),
        0.6,
    )
    # nothing aligned -> 0.0
    assert (
        score_components_v2.trend_score(
            {"sma50": 70, "sma150": 80, "sma200": 90, "close": 60}
        )
        == 0.0
    )


def test_stability_score():
    assert score_components_v2.stability_score({"pct_above_ma50_60d": 0.7}) == 0.7
    assert score_components_v2.stability_score({"pct_above_ma50_60d": np.nan}) == 0.3
    # missing key falls back via row.get default 0 (kept as-is)
    assert score_components_v2.stability_score({}) == 0.0


def test_delivery_score_weights_and_fallbacks():
    assert math.isclose(
        score_components_v2.delivery_score(
            {"_rank_delivery": 0.8, "_rank_sm_score": 0.6}
        ),
        0.68,  # 0.6*0.6 + 0.8*0.4
    )
    assert math.isclose(
        score_components_v2.delivery_score(
            {"_rank_delivery": np.nan, "_rank_sm_score": None}
        ),
        0.3,
    )


def test_liquidity_score():
    assert score_components_v2.liquidity_score({"_rank_volume": 0.4}) == 0.4
    assert score_components_v2.liquidity_score({"_rank_volume": np.nan}) == 0.3
    assert score_components_v2.liquidity_score({}) == 0.3


def test_base_score_atr_bands():
    assert math.isclose(
        score_components_v2.base_score({"atr_pct": 0.02, "keltner_upper": None}), 0.5
    )
    assert math.isclose(
        score_components_v2.base_score({"atr_pct": 0.04, "keltner_upper": None}), 0.3
    )
    # exactly 0.05 is not < 0.05 -> no atr credit -> floor 0.3
    assert math.isclose(
        score_components_v2.base_score({"atr_pct": 0.05, "keltner_upper": None}), 0.3
    )
    # NaN atr defaults to 0.05 -> floor 0.3
    assert math.isclose(
        score_components_v2.base_score({"atr_pct": np.nan, "keltner_upper": None}), 0.3
    )


def test_base_score_ttm_squeeze():
    squeeze = {
        "atr_pct": 0.02,
        "keltner_upper": 105,
        "keltner_lower": 95,
        "close": 100,
        "atr5": 2.0,
    }
    assert math.isclose(score_components_v2.base_score(squeeze), 1.0)

    # close + atr5 breaches the upper band -> no squeeze bonus
    assert math.isclose(
        score_components_v2.base_score(dict(squeeze, keltner_upper=101)), 0.5
    )
    # close - atr5 breaches the lower band -> no squeeze bonus
    assert math.isclose(
        score_components_v2.base_score(dict(squeeze, keltner_lower=99)), 0.5
    )


def test_fundamental_score():
    row = {"_rank_roe": 0.8, "_rank_growth": 0.6, "F_Score": 5}
    assert math.isclose(score_components_v2.fundamental_score(row), 0.8)
    # missing ranks fall back to 0.4; missing F_Score -> 0
    assert math.isclose(
        score_components_v2.fundamental_score(
            {"_rank_roe": np.nan, "_rank_growth": np.nan}
        ),
        0.28,
    )


def test_fundamental_score_nan_f_score_does_not_propagate():
    # Regression: a NaN F_Score used to propagate into the composite score.
    val = score_components_v2.fundamental_score(
        {"_rank_roe": 0.8, "_rank_growth": 0.6, "F_Score": np.nan}
    )
    assert not math.isnan(val)
    assert math.isclose(val, 0.5)  # 0.8*0.4 + 0.6*0.3 + 0


def test_valuation_score_nan_graham_is_neutral():
    # Regression: ``NaN <= 0`` is False, so a missing Graham Number used to fall
    # through to the 0.2 "Expensive" band instead of the neutral 0.5.
    assert (
        score_components_v2.valuation_score({"close": 100, "graham_number": np.nan})
        == 0.5
    )
    assert (
        score_components_v2.valuation_score({"close": np.nan, "graham_number": 120})
        == 0.5
    )
    assert (
        score_components_v2.valuation_score({"close": 0, "graham_number": 120}) == 0.5
    )


def test_valuation_score_bands():
    assert (
        score_components_v2.valuation_score({"close": 100, "graham_number": 150}) == 1.0
    )
    assert (
        score_components_v2.valuation_score({"close": 100, "graham_number": 110}) == 0.8
    )
    assert (
        score_components_v2.valuation_score({"close": 100, "graham_number": 90}) == 0.5
    )
    assert (
        score_components_v2.valuation_score({"close": 100, "graham_number": 70}) == 0.2
    )
    # non-positive graham -> neutral 0.5
    assert (
        score_components_v2.valuation_score({"close": 100, "graham_number": 0}) == 0.5
    )
    assert (
        score_components_v2.valuation_score({"close": 100, "graham_number": -10}) == 0.5
    )


def test_regime_adjustment():
    assert math.isclose(
        score_components_v2.regime_adjustment(0.8, "EXTREME FEAR"), 0.92
    )
    assert math.isclose(
        score_components_v2.regime_adjustment(0.8, "EXTREME GREED"), 0.72
    )
    assert math.isclose(score_components_v2.regime_adjustment(0.8, "NORMAL"), 0.8)
    assert math.isclose(score_components_v2.regime_adjustment(0.8, None), 0.8)
