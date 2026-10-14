import pandas as pd

from myra_app.score_components_v2 import (
    base_score,
    delivery_score,
    fundamental_score,
    liquidity_score,
    precompute_ranks,
    regime_adjustment,
    stability_score,
    trend_score,
)

# Component weights for the 1-24 month positional horizon. Trend and delivery
# dominate; stability / liquidity / base shape the tail.
_BASE_WEIGHTS = {
    "trend": 0.25,
    "stability": 0.15,
    "delivery": 0.20,
    "liquidity": 0.10,
    "base": 0.10,
}
# The technical block keeps 70% of the final score; fundamentals take 30%.
_TECH_SHARE = 0.7
_FUNDAMENTAL_SHARE = 0.3

# Optional explainability column attached by ``rank(..., explain=True)``.
BREAKDOWN_COLUMN = "MYRA_Score_v25_breakdown"


class PositionalScorer:
    """
    MYRA v2.5 Positional Engine
    Designed for 1-24 month holdings using relative market strength.

    Missing-data policy: components never reward an absent input. When a source
    metric is unavailable the component substitutes its documented neutral
    fallback (0.3 / 0.4), so a candidate with no fundamentals is neither
    auto-rejected nor pushed up the ranking by the gap.
    """

    def calculate_components(self, row):
        """Return the five technical components plus the fundamental block.

        Single source of truth for both :meth:`compute_score` and
        :meth:`explain_score`, so the explanation cannot drift from the score.
        """
        t = trend_score(row)
        s = stability_score(row)
        d = delivery_score(row)
        l = liquidity_score(row)
        b = base_score(row)
        f = fundamental_score(row)
        return {
            "trend": t,
            "stability": s,
            "delivery": d,
            "liquidity": l,
            "base": b,
            "fundamental": f,
        }

    @staticmethod
    def _drawdown_multiplier(row):
        """Soft penalty for broken charts; returns the multiplier applied."""
        drawdown = row.get("drawdown", 0)
        if pd.isna(drawdown):
            drawdown = 0
        if drawdown > 0.4:
            return 0.8
        if drawdown > 0.2:
            return 0.95
        return 1.0

    def _score_and_breakdown(self, row, regime):
        """Compute ``(score_0_100, breakdown)`` in a single pass over ``row``."""
        c = self.calculate_components(row)

        # Base weights for Positional Analysis
        score = sum(c[key] * weight for key, weight in _BASE_WEIGHTS.items())

        # Fundamentals carry a fixed 30% of the final score.
        # ``fundamental_score`` never returns None -- it substitutes documented
        # neutral fallbacks for missing inputs -- so this blend is unconditional.
        # (The former ``if f is not None:`` guard was unreachable dead code.)
        score = score * _TECH_SHARE + c["fundamental"] * _FUNDAMENTAL_SHARE

        # Regime adjustment based on Market Psychology
        score = regime_adjustment(score, regime)

        dd_multiplier = self._drawdown_multiplier(row)
        score *= dd_multiplier

        rounded = round(score * 100, 1)
        breakdown = {
            "score": rounded,
            "components": c,
            "weights": dict(_BASE_WEIGHTS, fundamental=_FUNDAMENTAL_SHARE),
            "regime": regime if regime else "NEUTRAL",
            "drawdown_multiplier": dd_multiplier,
            "missing_inputs": self._missing_inputs(row),
        }
        return rounded, breakdown

    @staticmethod
    def _missing_inputs(row):
        """List the scoring inputs that were absent and fell back to neutral.

        Distinguishes "no data" (neutral fallback) from "genuinely weak"
        (a real, low value) so a score can be audited rather than trusted.
        """
        sources = (
            "close",
            "sma50",
            "sma150",
            "sma200",
            "pct_above_ma50_60d",
            "_rank_roe",
            "_rank_growth",
            "F_Score",
            "_rank_delivery",
            "_rank_sm_score",
            "_rank_volume",
            "atr_pct",
        )
        return [key for key in sources if pd.isna(row.get(key, None))]

    def compute_score(self, row, regime):
        return self._score_and_breakdown(row, regime)[0]

    def explain_score(self, row, regime):
        """Explain how ``row`` scores: components, weights and adjustments.

        Pure function -- given any result row (plus the regime) a caller can
        reconstruct the final ``MYRA_Score_v25`` and see which inputs were
        missing. This is the documented traceability contract for the v2.5
        positional score.
        """
        return self._score_and_breakdown(row, regime)[1]

    def rank(self, results_df, regime, explain=True):
        """
        Takes a list of dictionaries (from engine.run_scan)
        and applies v2.5 scoring.
        """
        if results_df.empty:
            return results_df

        # Vectorized optimization: Precompute ranks for the entire universe
        # (returns a copy, so the caller's frame is never mutated)
        results_df = precompute_ranks(results_df)

        # NOTE: previously this used ``itertuples(index=False)`` + ``_asdict()``.
        # ``itertuples`` renames every column whose name starts with ``_`` (the
        # ``_rank_*`` columns) to positional ``_0``/``_1``/..., so the precomputed
        # ranks were silently dropped before scoring and delivery / liquidity /
        # ROE / growth all fell back to their neutral constants on every real
        # scan. ``to_dict("records")`` preserves the exact column names.
        rows = results_df.to_dict("records")
        scored = [self._score_and_breakdown(row, regime) for row in rows]
        results_df["MYRA_Score_v25"] = [score for score, _ in scored]
        if explain:
            results_df[BREAKDOWN_COLUMN] = [breakdown for _, breakdown in scored]

        # Stable sort: ties keep their incoming (upstream Score) order so the
        # final ranking is deterministic instead of depending on the unstable
        # default quicksort.
        return results_df.sort_values(
            by="MYRA_Score_v25", ascending=False, kind="mergesort"
        )
