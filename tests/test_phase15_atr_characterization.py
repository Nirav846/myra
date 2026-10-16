"""Phase 15: characterization tests for the fabricated-zero ``atr_pct`` defect.

These tests **pin current behaviour deliberately**. They assert that a
fabricated ``atr_pct = 0`` is scored exactly like a genuinely tight base and is
*not* reported as a missing input, while a genuinely wide ``atr_pct`` is scored
exactly like an absent one. Every assertion below documents a defect, not a
contract to preserve.

Phase 15 audited the defect and explicitly did **not** fix it (changing scoring
behaviour must be evaluated on its own). When the separately scoped fix lands,
these tests are expected to fail and should be inverted - that is the signal the
behaviour actually changed.

See ``docs/PHASE15_FRESH_SNAPSHOT_RANKING_VALIDATION.md`` (section 8).

The defect:

* ``DataLoader.IndicatorManager`` / ``update_indicator_history`` never write an
  ``atr_pct`` column into the indicator lake.
* ``universe_loader`` therefore emits
  ``c.get("atr_pct", 0) if c.get("atr_pct") is not None else 0`` -> a stored
  ``0.0``, i.e. "missing" is turned into "a numerically observed value".
* ``score_components_v2.base_score`` reads ``atr_pct < 0.03`` as a favourable
  base and adds ``0.5``, so the fabricated ``0.0`` collects the *best* base
  score instead of the documented ``0.3`` neutral.
"""

import pandas as pd
import pytest

from myra_app.positional_engine import PositionalScorer
from myra_app.score_components_v2 import base_score, precompute_ranks

#: The uplift is a float sum, so compare with a tolerance rather than ``==``.
_UPLIFT = 1.4

#: A fully-populated row so the only variable under test is ``atr_pct``.
_ROW = {
    "Stock": "TEST",
    "close": 100.0,
    "sma50": 95.0,
    "sma150": 90.0,
    "sma200": 85.0,
    "pct_above_ma50_60d": 0.8,
    "F_Score": 3,
    "_rank_roe": 0.7,
    "_rank_growth": 0.6,
    "_rank_delivery": 0.5,
    "_rank_volume": 0.5,
    "_rank_sm_score": 0.5,
    "drawdown": 0.0,
}


def _row(**overrides):
    row = dict(_ROW)
    row.update(overrides)
    return row


def _score(**overrides):
    return PositionalScorer().compute_score(_row(**overrides), "NORMAL")


def _prepared(row):
    """``row`` after ``precompute_ranks``, as ``PositionalScorer.rank`` uses it."""
    return precompute_ranks(pd.DataFrame([row])).to_dict("records")[0]


# --- the conflation itself -------------------------------------------------


def test_fabricated_zero_atr_scores_exactly_like_a_genuinely_tight_base():
    """A missing ATR (stored as 0) is indistinguishable from a real tight ATR."""
    assert base_score(_row(atr_pct=0.0)) == base_score(_row(atr_pct=0.02)) == 0.5


def test_fabricated_zero_atr_beats_the_documented_neutral_fallback():
    """0.0 must not out-score "we have no measurement"."""
    assert base_score(_row(atr_pct=0.0)) > base_score(_row())


def test_wide_atr_scores_exactly_like_an_absent_atr():
    """A genuinely wide base collides with the neutral fallback."""
    assert base_score(_row(atr_pct=0.08)) == base_score(_row()) == 0.3


def test_explicit_null_atr_takes_the_neutral_fallback():
    """``None`` (a real SQL NULL) does the right thing - only ``0.0`` is wrong."""
    assert base_score(_row(atr_pct=None)) == 0.3


# --- the explainability contract is defeated too ---------------------------


def test_fabricated_zero_atr_is_not_reported_as_a_missing_input():
    """The breakdown presents a fabricated 0 as an observed, favourable value."""
    breakdown = PositionalScorer().explain_score(_prepared(_row(atr_pct=0.0)), "NORMAL")
    assert "atr_pct" not in breakdown["missing_inputs"]


def test_absent_atr_is_reported_as_missing():
    """The same row with the key genuinely absent *is* flagged."""
    breakdown = PositionalScorer().explain_score(_prepared(_row()), "NORMAL")
    assert "atr_pct" in breakdown["missing_inputs"]


# --- magnitude and scope ---------------------------------------------------


def test_fabricated_zero_atr_adds_about_1_4_points_at_normal_regime():
    """+0.2 on a 0.10-weight component, before the 0.7 technical share."""
    assert _score(atr_pct=0.0) - _score() == pytest.approx(_UPLIFT, abs=1e-6)


def test_the_inflation_is_scaled_by_regime_and_drawdown():
    """The uplift is pre-multiplier, so it is uniform but not constant."""
    scorer = PositionalScorer()
    population = _score(atr_pct=0.0) - _score()
    greed = scorer.compute_score(
        _row(atr_pct=0.0), "EXTREME GREED"
    ) - scorer.compute_score(_row(), "EXTREME GREED")
    fear = scorer.compute_score(
        _row(atr_pct=0.0), "EXTREME FEAR"
    ) - scorer.compute_score(_row(), "EXTREME FEAR")
    assert greed < population < fear


def test_the_effect_is_identical_for_every_row():
    """Uniform uplift: it reorders nothing but inflates every absolute score."""
    scorer = PositionalScorer()
    for symbol in ("AAA", "BBB", "CCC"):
        with_zero = scorer.compute_score(_row(Stock=symbol, atr_pct=0.0), "NORMAL")
        without = scorer.compute_score(_row(Stock=symbol), "NORMAL")
        assert with_zero - without == pytest.approx(_UPLIFT, abs=1e-6)


def test_a_row_near_a_display_band_can_cross_it_on_the_fabricated_zero_alone():
    """A 68.8 row is displayed as 70+ "Elite" purely on a fabricated ATR.

    The uplift is uniform, so it reorders nothing - but it moves absolute
    scores, and therefore display bands, for every symbol at once.
    """
    near_the_line = {
        "close": 120.0,
        "sma50": 110.0,
        "sma150": 100.0,
        "sma200": 95.0,
        "pct_above_ma50_60d": 0.8,
        "F_Score": 4,
        "_rank_roe": 0.3,
        "_rank_growth": 1.0,
        "_rank_delivery": 1.0,
        "_rank_volume": 1.0,
        "_rank_sm_score": 1.0,
        "drawdown": 0.0,
    }
    scorer = PositionalScorer()
    neutral = scorer.compute_score(dict(near_the_line), "NORMAL")
    fabricated = scorer.compute_score(dict(near_the_line, atr_pct=0.0), "NORMAL")
    assert neutral == 68.8
    assert neutral < 70 <= fabricated
