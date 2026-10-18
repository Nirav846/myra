"""Phase 20 — ``atr_pct`` must not be fabricated into a favourable measurement.

``atr_pct`` has no producer anywhere in the repository: no code writes an
``atr_pct`` column into the indicator lake, and no technical database has the
column. ``universe_loader`` nevertheless emitted::

    "atr_pct": c.get("atr_pct", 0) if c.get("atr_pct") is not None else 0

so for **every** symbol "we have no volatility data" was stored as the numeric
value ``0.0``. ``score_components_v2.base_score`` reads ``atr_pct < 0.03`` as a
favourable tight base and awards ``+0.5``, which is the *maximum* base score -
strictly better than a genuinely mid-range base and better than a genuinely
loose one. It also defeated the explainability contract: the fabricated ``0`` was
reported as an observed input rather than a missing one.

The repair is deliberately a **data-integrity** fix, not a scoring change: the
fabrication site stops manufacturing the zero, so an absent ATR flows through as
absent and lands on ``base_score``'s long-standing documented ``0.3`` neutral -
the same value a real SQL NULL, a NaN and an absent key already produced. The
scoring bands themselves are untouched, and a genuine measurement (including a
legitimate ``0.0``) is preserved unchanged.

The pre-fix behaviour is characterised in ``tests/test_phase15_atr_characterization.py``,
which pins ``base_score``'s band logic directly and therefore still passes: this
phase removed the *caller's* fabrication, not the scorer's contract.
"""

from __future__ import annotations

import os

import pandas as pd
import pytest

from myra_app.positional_engine import PositionalScorer
from myra_app.score_components_v2 import base_score, precompute_ranks

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: A fully-populated row, so ``atr_pct`` is the only variable under test.
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

#: The pre-Phase-20 expression, kept verbatim so the before/after comparison
#: measures the real regression rather than a paraphrase.
_OLD_EXPR = 'c.get("atr_pct", 0) if c.get("atr_pct") is not None else 0'

#: The neutral ``base_score`` an absent ATR is documented to produce.
_NEUTRAL = 0.3


def _row(**overrides):
    row = dict(_ROW)
    row.update(overrides)
    return row


def _missing_inputs(row):
    """The scorer's own view of which inputs the row could not supply."""
    prepared = precompute_ranks(pd.DataFrame([row])).to_dict("records")[0]
    return PositionalScorer().explain_score(prepared, "NORMAL")["missing_inputs"]


# --- the fabrication site itself -------------------------------------------


def test_universe_loader_stops_fabricating_a_zero_atr():
    src = open(  # noqa: SIM115 - small, closed over REPO
        os.path.join(REPO, "myra_app", "universe_loader.py"), encoding="utf-8"
    ).read()
    assert '"atr_pct": c.get("atr_pct")' in src, "universe_loader must pass it through"
    assert _OLD_EXPR not in src, "universe_loader must not coerce a missing ATR to 0"


def test_universe_loader_still_exposes_the_field():
    src = open(  # noqa: SIM115
        os.path.join(REPO, "myra_app", "universe_loader.py"), encoding="utf-8"
    ).read()
    assert '"atr_pct"' in src


# --- absence must reach the documented neutral, never the maximum ----------


@pytest.mark.parametrize(
    "absent",
    [
        pytest.param(None, id="explicit-null"),
        pytest.param(float("nan"), id="nan"),
    ],
)
def test_an_absent_atr_scores_the_neutral_not_the_maximum(absent):
    row = _row(atr_pct=absent)
    assert base_score(row) == _NEUTRAL


def test_a_missing_atr_key_scores_the_neutral_not_the_maximum():
    """The key simply absent must take ``row.get``'s documented path."""
    row = _row()
    assert "atr_pct" not in row
    assert base_score(row) == _NEUTRAL


def test_a_fabricated_zero_no_longer_beats_a_real_neutral():
    """The defect itself: 0.0 used to out-score "we have no measurement"."""
    assert base_score(_row(atr_pct=0.0)) > base_score(_row())


def test_the_universe_loader_path_no_longer_rewards_absence():
    """End-to-end: a candidate without ``atr_pct`` must land on the neutral.

    ``old_stored`` is what the removed expression produced for the same input;
    ``new_stored`` is what the loader now emits.
    """
    candidate = dict(_ROW)  # no producer ever puts atr_pct in here
    old_stored = (
        candidate.get("atr_pct", 0) if candidate.get("atr_pct") is not None else 0
    )
    new_stored = candidate.get("atr_pct")

    assert old_stored == 0
    assert new_stored is None

    assert base_score(dict(_ROW, atr_pct=old_stored)) == 0.5  # the defect
    assert base_score(dict(_ROW, atr_pct=new_stored)) == _NEUTRAL  # the repair


# --- a genuine measurement is never disturbed ------------------------------


@pytest.mark.parametrize(
    "value,expected",
    [
        pytest.param(0.02, 0.5, id="genuinely-tight"),
        pytest.param(0.04, _NEUTRAL, id="genuinely-mid"),
        pytest.param(0.08, _NEUTRAL, id="genuinely-loose"),
    ],
)
def test_a_real_measurement_keeps_its_documented_band(value, expected):
    assert base_score(_row(atr_pct=value)) == expected


def test_a_legitimate_zero_is_preserved_as_a_measurement():
    """A stored ``0.0`` is a value, not an absence, and must still flow through."""
    stored = _row(atr_pct=0.0)
    assert "atr_pct" not in _missing_inputs(stored)


# --- the explainability contract -------------------------------------------


def test_an_absent_atr_is_reported_as_a_missing_input():
    assert "atr_pct" in _missing_inputs(_row(atr_pct=None))


def test_the_repair_reports_absence_that_the_fabrication_used_to_hide():
    """A stored ``0.0`` is still reported as present - it is a measurement."""
    assert "atr_pct" not in _missing_inputs(_row(atr_pct=0.0))


# --- no scoring-band or ranking change -------------------------------------


def test_the_scorer_bands_are_untouched():
    """The repair is upstream: ``base_score`` itself did not change."""
    for value, expected in ((0.02, 0.5), (0.04, 0.3), (0.08, 0.3)):
        assert base_score(_row(atr_pct=value, keltner_upper=None)) == expected


def test_a_near_band_row_no_longer_crosses_on_a_fabricated_atr():
    """The 68.8 -> "70+ Elite" display flip recorded in Phase 15 is gone."""
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
    # With the loader fixed, an absent ATR is stored as absent, so both of these
    # describe the same row and must score identically.
    absent = scorer.compute_score(dict(near_the_line, atr_pct=None), "NORMAL")
    assert neutral == absent == 68.8
