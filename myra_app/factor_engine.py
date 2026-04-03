#!/usr/bin/env python
import pandas as pd
import numpy as np
from typing import List, Dict, Any
from .factors import GLOBAL_FACTORS

class FactorEngine:
    """
    MYRA Factor Scoring & Ranking Engine (v4.0 Alpha)
    Orchestrates modular factors into a unified conviction score.
    """
    def __init__(self, enabled_factors: List[str] = None):
        self.factors = []
        if enabled_factors:
            for f_key in enabled_factors:
                if f_key in GLOBAL_FACTORS:
                    self.factors.append(GLOBAL_FACTORS[f_key])
        else:
            self.factors = list(GLOBAL_FACTORS.values())

    def score_symbol(self, df: pd.DataFrame, funda: dict) -> Dict[str, float]:
        """Calculates all enabled factors for a single symbol."""
        scores = {}
        total_weight = 0.0
        final_score = 0.0
        
        for factor in self.factors:
            score = factor.calculate(df, funda)
            scores[factor.name] = score
            final_score += score * factor.weight
            total_weight += factor.weight
            
        if total_weight > 0:
            scores["conviction_score"] = round(final_score / total_weight, 3)
        else:
            scores["conviction_score"] = 0.0
            
        return scores

    def rank_universe(self, results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Calculates percentile rankings across the scanned results."""
        if not results: return []
        
        df_ranks = pd.DataFrame(results)
        if "conviction_score" not in df_ranks.columns: return results
        
        # Calculate Percentile Rank (0-100)
        df_ranks["conviction_rank"] = df_ranks["conviction_score"].rank(pct=True) * 100
        df_ranks["conviction_rank"] = df_ranks["conviction_rank"].round(1)
        
        # Add a visual tag
        def get_tag(rank):
            if rank >= 90: return "ELITE"
            if rank >= 75: return "LEADER"
            if rank >= 50: return "STRONG"
            return "WATCH"
            
        df_ranks["conviction_tag"] = df_ranks["conviction_rank"].apply(get_tag)
        
        return df_ranks.to_dict('records')
