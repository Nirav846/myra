#!/usr/bin/env python
import pandas as pd

from myra_app.ias_manager import IASManager

from .base_factor import BaseFactor


class IASFactor(BaseFactor):
    """
    Institutional Activity Factor (v1.0)
    Bridges the 5-pillar IAS logic into the modular Factor Engine.
    """

    def __init__(self, weight=1.0):
        super().__init__(weight=weight)
        self.mgr = IASManager()

    @property
    def name(self):
        return "institutional_activity"

    def calculate(self, df: pd.DataFrame, funda: dict) -> float:
        # Reuses the sophisticated logic from IASManager
        symbol = funda.get("symbol")
        if not symbol:
            return 0.5

        score, _ = self.mgr.calculate_ias(symbol, df)
        # Normalize 0-10 to 0.0 - 1.0
        return float(score / 10.0)
