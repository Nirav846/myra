"""Regression tests for myra_app.positional_engine.PositionalScorer.

Locks the v2.5 weighted-score formula and the drawdown / regime
adjustments so refactors cannot silently change rankings.
Pure-python: no DB, no IO.
"""

import math

import pandas as pd

from myra_app.positional_engine import PositionalScorer

# A fully specified, bullish row. ``compute_score`` takes a plain dict so
# the precomputed ``_rank_*`` values are used verbatim (no re-ranking).
_BULL = {
    "sma50": 110,
    "sma150": 100,
    "sma200": 90,
    "close": 100,
    "pct_above_ma50_60d": 0.6,
    "_rank_delivery": 0.8,
    "_rank_sm_score": 0.8,
    "_rank_volume": 0.6,
    "atr_pct": 0.02,
    "keltner_upper": 105,
    "keltner_lower": 95,
    "atr5": 2.0,
    "_rank_roe": 0.8,
    "_rank_growth": 0.7,
    "F_Score": 5,
    "drawdown": 0.1,
}


def test_compute_score_known_value():
    # trend 1.0, stability 0.6, delivery 0.8, liquidity 0.6, base 1.0,
    # fundamentals 0.83 -> weighted 0.66 -> blend 0.711 -> 71.1
    assert PositionalScorer().compute_score(dict(_BULL), "NORMAL") == 71.1


def test_compute_score_regime_adjustment():
    assert PositionalScorer().compute_score(dict(_BULL), "EXTREME FEAR") == 81.8
    assert PositionalScorer().compute_score(dict(_BULL), "EXTREME GREED") == 64.0


def test_compute_score_drawdown_penalties():
    assert PositionalScorer().compute_score(dict(_BULL, drawdown=0.3), "NORMAL") == 67.5
    assert PositionalScorer().compute_score(dict(_BULL, drawdown=0.5), "NORMAL") == 56.9


def test_compute_score_minimal_row():
    # Everything missing except close -> the documented fallback path.
    assert PositionalScorer().compute_score({"close": 1}, "NORMAL") == 20.3


def test_rank_empty_returns_empty():
    res = PositionalScorer().rank(pd.DataFrame(), "NORMAL")
    assert res.empty


def test_rank_adds_scores_and_sorts_descending():
    strong = {
        "sma50": 120,
        "sma150": 110,
        "sma200": 100,
        "close": 130,
        "pct_above_ma50_60d": 0.9,
        "roe": 25,
        "ProfitGrowth": 40,
        "avg_delivery_20d": 5.0,
        "avg_volume_20d": 5_000_000,
        "smart_money_score": 0.95,
        "F_Score": 5,
        "atr_pct": 0.02,
        "keltner_upper": 140,
        "keltner_lower": 120,
        "atr5": 3.0,
        "drawdown": 0.0,
    }
    weak = {
        "sma50": 90,
        "sma150": 95,
        "sma200": 100,
        "close": 85,
        "pct_above_ma50_60d": 0.1,
        "roe": 3,
        "ProfitGrowth": 2,
        "avg_delivery_20d": 1.0,
        "avg_volume_20d": 100_000,
        "smart_money_score": 0.1,
        "F_Score": 1,
        "atr_pct": 0.08,
        "drawdown": 0.6,
    }
    df = pd.DataFrame([strong, weak], index=["STRONG", "WEAK"])
    res = PositionalScorer().rank(df, "NORMAL")

    assert "MYRA_Score_v25" in res.columns
    assert list(res.index) == ["STRONG", "WEAK"]  # sorted descending
    assert res.at["STRONG", "MYRA_Score_v25"] > res.at["WEAK", "MYRA_Score_v25"]
    assert all(0.0 <= s <= 100.0 for s in res["MYRA_Score_v25"])


def test_rank_preserves_original_values():
    df = pd.DataFrame([dict(_BULL)])
    res = PositionalScorer().rank(df, "NORMAL")
    # score column added, original columns preserved
    assert "MYRA_Score_v25" in res.columns
    assert math.isclose(res.at[0, "close"], 100)
