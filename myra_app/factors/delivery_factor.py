#!/usr/bin/env python
import pandas as pd

from .base_factor import BaseFactor


class DeliveryFactor(BaseFactor):
    """
    Delivery Absorption Factor (v1.0)
    Quantifies 'Quiet Buying' via high delivery and cluster analysis.
    """

    @property
    def name(self):
        return "delivery_absorption"

    def calculate(self, df: pd.DataFrame, funda: dict) -> float:
        if "delivery_pct" not in df.columns:
            return 0.0

        try:
            d_pct = df["delivery_pct"]
            # Avg 5-day delivery as a percentage (0-100)
            avg_5d = d_pct.iloc[-5:].mean()

            # Normalizing to 0.0 - 1.0 (Assume 60% delivery is a perfect 1.0)
            score = avg_5d / 60.0
            return min(float(score), 1.0)
        except:
            return 0.0
