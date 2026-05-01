#!/usr/bin/env python
import pandas as pd
import numpy as np
from .base_factor import BaseFactor


class RSFactor(BaseFactor):
    """
    Relative Strength Factor (v1.0)
    Calculates 1-year Price Performance relative to the universe.
    """

    @property
    def name(self):
        return "rs_rating"

    def calculate(self, df: pd.DataFrame, funda: dict) -> float:
        if len(df) < 200:
            return 0.0

        try:
            # Weighted RS: 40% (3m), 20% (6m), 20% (9m), 20% (12m)
            c = df["Close"]
            curr = c.iloc[-1]

            ret_3m = (curr / c.iloc[-63]) - 1 if len(df) >= 63 else 0
            ret_6m = (curr / c.iloc[-126]) - 1 if len(df) >= 126 else 0
            ret_9m = (curr / c.iloc[-189]) - 1 if len(df) >= 189 else 0
            ret_12m = (curr / c.iloc[-252]) - 1 if len(df) >= 252 else 0

            weighted_ret = (
                (ret_3m * 0.4) + (ret_6m * 0.2) + (ret_9m * 0.2) + (ret_12m * 0.2)
            )

            # Normalization happens at Engine level, but we return raw return for now
            return float(weighted_ret)
        except:
            return 0.0
