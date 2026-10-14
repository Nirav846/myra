"""Opt-in shadow comparison of v2.5 orderings (Phase 6).

Phase 6 connects the v2.5 scorer's cross-sectional inputs (``avg_volume_20d`` /
``avg_delivery_20d`` / ``roe`` / ``ProfitGrowth``) to their authoritative
sources, but deliberately does **not** make the resulting ordering
authoritative. Instead this module measures what *would* change if it did:

* rank correlation between the current ordering and the input-populated one,
* top-N membership overlap,
* how many candidates moved, and by how much,
* how many of the new inputs were actually available (coverage).

Both orderings are computed from the **same** result set inside a single call,
so the comparison can never be an artefact of two different snapshots.

Passing ``inputs={}`` (or running with the opt-in flag off) yields an identical
ordering and a rank correlation of ``1.0`` — that is the regression guarantee
that the default production path is untouched.
"""

from __future__ import annotations

import pandas as pd

from myra_app.positional_engine import PositionalScorer
from myra_app.ranking_inputs import METRIC_KEYS, attach_ranking_inputs

#: A candidate is reported as "materially moved" when its positional delta
#: (in the ranked list) is at least this large.
MATERIAL_MOVE = 5


def _scored_ranks(results, regime, scorer):
    """Return ``[(symbol, score, position)]`` for one ordering."""
    frame = scorer.rank(pd.DataFrame(results), regime, explain=False)
    return [
        (str(row.get("Stock", "")), row.get("MYRA_Score_v25"), i)
        for i, row in enumerate(frame.to_dict("records"))
    ]


def _spearman(base, new):
    """Rank correlation of the two score vectors (no scipy dependency)."""
    if len(base) < 2:
        return None
    a = pd.Series([float(v) if v is not None else float("nan") for v in base])
    b = pd.Series([float(v) if v is not None else float("nan") for v in new])
    if a.nunique(dropna=True) < 2 or b.nunique(dropna=True) < 2:
        # A constant ordering has no rank variance; correlation is undefined.
        return 1.0 if a.equals(b) else None
    value = a.corr(b, method="spearman")
    return None if pd.isna(value) else float(value)


def compare_orderings(results, regime, inputs=None, top_n=20, scorer=None):
    """Compare the current ordering with the input-populated ordering.

    Parameters
    ----------
    results:
        The scan result rows (list of dicts) that ``execute_scan`` produced.
    regime:
        Market-mood string passed through to the scorer.
    inputs:
        Mapping ``{SYMBOL: {metric: value}}`` from
        :mod:`myra_app.ranking_inputs`. Empty/``None`` reproduces the current
        ordering exactly.
    top_n:
        Size of the "top N" set used for overlap.
    scorer:
        Injectable :class:`~myra_app.positional_engine.PositionalScorer`
        (created on demand otherwise) so the comparison shares one code path
        with production scoring.

    Returns
    -------
    dict
        ``n``, ``spearman`` (``None`` when undefined), ``identical_ordering``,
        ``top_n``, ``top_n_overlap``, ``top_n_jaccard``, ``moved``,
        ``max_position_delta``, ``material_moves`` (list), ``coverage`` and
        ``symbols_added`` / ``symbols_dropped`` (top-N churn).
    """
    results = list(results or [])
    inputs = inputs or {}
    scorer = scorer or PositionalScorer()

    if not results:
        return {
            "n": 0,
            "spearman": None,
            "identical_ordering": True,
            "top_n": top_n,
            "top_n_overlap": 0,
            "top_n_jaccard": None,
            "moved": 0,
            "max_position_delta": 0,
            "material_moves": [],
            "coverage": {key: 0 for key in METRIC_KEYS},
            "symbols_added": [],
            "symbols_dropped": [],
        }

    base = _scored_ranks(results, regime, scorer)
    populated = attach_ranking_inputs(results, inputs)
    new = _scored_ranks(populated, regime, scorer)

    base_pos = {sym: pos for sym, _score, pos in base}
    new_pos = {sym: pos for sym, _score, pos in new}
    base_score = {sym: score for sym, score, _pos in base}
    new_score = {sym: score for sym, score, _pos in new}

    common = [sym for sym in base_pos if sym in new_pos]
    deltas = {sym: new_pos[sym] - base_pos[sym] for sym in common}
    moved = sum(1 for d in deltas.values() if d != 0)
    max_delta = max((abs(d) for d in deltas.values()), default=0)

    base_top = {sym for sym, _s, _p in base[:top_n]}
    new_top = {sym for sym, _s, _p in new[:top_n]}
    overlap = base_top & new_top
    union = base_top | new_top

    material = sorted(
        (
            {
                "symbol": sym,
                "base_position": base_pos[sym],
                "new_position": new_pos[sym],
                "position_delta": deltas[sym],
                "base_score": base_score[sym],
                "new_score": new_score[sym],
            }
            for sym in common
            if abs(deltas[sym]) >= MATERIAL_MOVE
        ),
        key=lambda item: abs(item["position_delta"]),
        reverse=True,
    )

    coverage = {key: 0 for key in METRIC_KEYS}
    for row in results:
        symbol = str(row.get("Stock", "")).upper()
        provided = inputs.get(symbol, {})
        for key in METRIC_KEYS:
            if provided.get(key) is not None:
                coverage[key] += 1

    return {
        "n": len(results),
        "spearman": _spearman(
            [base_score.get(sym) for sym in common],
            [new_score.get(sym) for sym in common],
        ),
        "identical_ordering": [sym for sym, _s, _p in base]
        == [sym for sym, _s, _p in new],
        "top_n": top_n,
        "top_n_overlap": len(overlap),
        "top_n_jaccard": (len(overlap) / len(union)) if union else None,
        "moved": moved,
        "max_position_delta": max_delta,
        "material_moves": material,
        "coverage": coverage,
        "symbols_added": sorted(new_top - base_top),
        "symbols_dropped": sorted(base_top - new_top),
    }
