"""Regression tests for myra_app.positional_engine.PositionalScorer.

Locks the v2.5 weighted-score formula and the drawdown / regime
adjustments so refactors cannot silently change rankings.
Pure-python: no DB, no IO.
"""

import math

import numpy as np
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


def _perf_row(**overrides):
    """A strong-trend row with real delivery / volume / smart-money data."""
    row = {
        "sma50": 110,
        "sma150": 100,
        "sma200": 90,
        "close": 100,
        "pct_above_ma50_60d": 0.6,
        "atr_pct": 0.02,
        "keltner_upper": None,
        "roe": 20.0,
        "ProfitGrowth": 15.0,
        "F_Score": 3,
        "drawdown": 0.0,
        "avg_delivery_20d": 1.0,
        "avg_volume_20d": 1_000_000.0,
        "smart_money_score": 0.5,
    }
    row.update(overrides)
    return row


def test_rank_consumes_precomputed_ranks():
    """The precomputed ``_rank_*`` columns must actually reach the scorer.

    Regression: ``rank`` fed the scorer ``itertuples()._asdict()``, and
    ``itertuples`` renames every column whose name starts with ``_``
    (``_rank_delivery`` -> ``_0`` ...). The rank layer was therefore silently
    discarded and delivery / liquidity / ROE / growth always used their neutral
    fallbacks on real scans -- the engine's advertised cross-sectional
    "relative strength" never influenced a score.
    """
    df = pd.DataFrame(
        [
            _perf_row(
                avg_delivery_20d=9.0,
                avg_volume_20d=9_000_000.0,
                smart_money_score=0.9,
            ),
            _perf_row(
                avg_delivery_20d=0.5,
                avg_volume_20d=100_000.0,
                smart_money_score=0.1,
            ),
        ],
        index=["GOOD", "POOR"],
    )
    res = PositionalScorer().rank(df, "NORMAL")

    assert res.at["GOOD", "MYRA_Score_v25"] > res.at["POOR", "MYRA_Score_v25"]
    # Exact scores (deterministic fixture): with the rank layer inert, both rows
    # scored 47.4. Now the real delivery/volume/smart-money data separates them.
    assert res.at["GOOD", "MYRA_Score_v25"] == 69.5
    assert res.at["POOR", "MYRA_Score_v25"] == 59.0
    # Rank-derived (1.0), not the neutral 0.3 fallback -> the ranks were consumed.
    good = res.at["GOOD", "MYRA_Score_v25_breakdown"]["components"]
    assert good["delivery"] == 1.0
    assert good["liquidity"] == 1.0


def test_rank_missing_metrics_are_not_rewarded():
    """A candidate with no delivery/volume/smart-money data must not outrank one
    with real (even weak) data -- the core missing-data regression."""
    df = pd.DataFrame(
        [
            _perf_row(
                avg_delivery_20d=np.nan,
                avg_volume_20d=np.nan,
                smart_money_score=np.nan,
            ),
            _perf_row(
                avg_delivery_20d=0.2,
                avg_volume_20d=100_000.0,
                smart_money_score=0.2,
            ),
        ],
        index=["NO_DATA", "WEAK"],
    )
    res = PositionalScorer().rank(df, "NORMAL")

    assert res.at["WEAK", "MYRA_Score_v25"] > res.at["NO_DATA", "MYRA_Score_v25"]
    assert res.at["WEAK", "MYRA_Score_v25"] == 69.5
    assert res.at["NO_DATA", "MYRA_Score_v25"] == 54.8
    no_data = res.at["NO_DATA", "MYRA_Score_v25_breakdown"]
    assert no_data["components"]["delivery"] == 0.3  # neutral, not top percentile
    assert no_data["components"]["liquidity"] == 0.3
    for key in ("_rank_delivery", "_rank_sm_score", "_rank_volume"):
        assert key in no_data["missing_inputs"]


def test_rank_ties_preserve_input_order():
    """Equal scores must keep their incoming order (stable, deterministic sort)."""
    df = pd.DataFrame(
        [_perf_row(), _perf_row(), _perf_row()], index=["ALPHA", "BETA", "GAMMA"]
    )
    res = PositionalScorer().rank(df, "NORMAL")
    assert list(res.index) == ["ALPHA", "BETA", "GAMMA"]


def test_rank_breakdown_reconstructs_score():
    df = pd.DataFrame([_perf_row()], index=["X"])
    res = PositionalScorer().rank(df, "NORMAL")
    bd = res.at["X", "MYRA_Score_v25_breakdown"]

    assert bd["components"]["trend"] == 1.0
    assert bd["drawdown_multiplier"] == 1.0
    assert bd["regime"] == "NORMAL"
    assert bd["missing_inputs"] == []
    assert set(bd["weights"]) == {
        "trend",
        "stability",
        "delivery",
        "liquidity",
        "base",
        "fundamental",
    }

    comp, w = bd["components"], bd["weights"]
    tech = sum(
        comp[k] * w[k] for k in ("trend", "stability", "delivery", "liquidity", "base")
    )
    expected = round((tech * 0.7 + comp["fundamental"] * 0.3) * 100, 1)
    assert res.at["X", "MYRA_Score_v25"] == expected


def test_rank_explain_false_omits_breakdown():
    df = pd.DataFrame([_perf_row()], index=["X"])
    res = PositionalScorer().rank(df, "NORMAL", explain=False)
    assert "MYRA_Score_v25" in res.columns
    assert "MYRA_Score_v25_breakdown" not in res.columns


def test_explain_score_matches_compute_score():
    ps = PositionalScorer()
    assert ps.explain_score(dict(_BULL), "NORMAL")["score"] == ps.compute_score(
        dict(_BULL), "NORMAL"
    )
    assert ps.explain_score(dict(_BULL), "NORMAL")["missing_inputs"] == []
    assert "sma50" in ps.explain_score({"close": 1}, "NORMAL")["missing_inputs"]


def test_compute_score_tolerates_none_or_nan_drawdown():
    # None / NaN drawdown must not raise (and must not apply a penalty).
    assert (
        PositionalScorer().compute_score(dict(_BULL, drawdown=None), "NORMAL") == 71.1
    )
    assert (
        PositionalScorer().compute_score(dict(_BULL, drawdown=np.nan), "NORMAL") == 71.1
    )


def test_rank_does_not_mutate_input_frame():
    df = pd.DataFrame([_perf_row()], index=["X"])
    cols_before = list(df.columns)
    PositionalScorer().rank(df, "NORMAL")
    assert list(df.columns) == cols_before
    assert "MYRA_Score_v25" not in df.columns
