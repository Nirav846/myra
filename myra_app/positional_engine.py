from myra_app.score_components_v2 import (
    precompute_ranks,
    trend_score,
    stability_score,
    delivery_score,
    liquidity_score,
    base_score,
    fundamental_score,
    regime_adjustment
)
import pandas as pd

class PositionalScorer:
    """
    MYRA v2.5 Positional Engine
    Designed for 1-24 month holdings using relative market strength.
    """
    def compute_score(self, row, regime):
        t = trend_score(row)
        s = stability_score(row)
        d = delivery_score(row)
        l = liquidity_score(row)
        b = base_score(row)
        f = fundamental_score(row)

        # Base weights for Positional Analysis
        score = (
            t * 0.25 +
            s * 0.15 +
            d * 0.20 +
            l * 0.10 +
            b * 0.10
        )

        # Add fundamentals if available (30% weight in final calculation if exists)
        if f is not None:
            score = score * 0.7 + f * 0.3

        # Regime adjustment based on Market Psychology
        score = regime_adjustment(score, regime)

        # Drawdown Filter (Soft penalty for broken charts)
        drawdown = row.get("drawdown", 0)
        if drawdown > 0.4:
            score *= 0.8
        elif drawdown > 0.2:
            score *= 0.95

        return round(score * 100, 1)

    def rank(self, results_df, regime):
        """
        Takes a list of dictionaries (from engine.run_scan) 
        and applies v2.5 scoring.
        """
        if results_df.empty:
            return results_df

        # Vectorized optimization: Precompute ranks for the entire universe
        results_df = precompute_ranks(results_df)

        # Optimized with itertuples (Fix 62: Avoid .apply on rows)
        scores = [self.compute_score(row._asdict(), regime) for row in results_df.itertuples(index=False)]
        results_df["MYRA_Score_v25"] = scores

        return results_df.sort_values(by="MYRA_Score_v25", ascending=False)
