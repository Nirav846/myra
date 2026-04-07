#!/usr/bin/env python
import pandas as pd
import numpy as np


class VolatilityAdjustedSizer:
    """
    Position Sizing via Risk Parity.
    Calculates quantity based on ATR so every trade has the same $ risk.
    """

    def get_quantity(self, price, atr, total_capital, risk_per_trade_pct=1.0):
        """
        Example: $100,000 capital, 1% risk ($1,000).
        If ATR is $10, quantity = 100 shares.
        """
        try:
            risk_amount = total_capital * (risk_per_trade_pct / 100.0)
            if atr <= 0:
                return 0

            qty = risk_amount / atr
            return int(qty)
        except:
            return 0


class KellySizer:
    """
    Position Sizing via Kelly Criterion.
    Suggested Allocation % = WinRate - [(1 - WinRate) / (AvgWin / AvgLoss)]
    """

    def get_allocation_pct(self, win_rate, avg_win, avg_loss):
        try:
            if avg_loss == 0:
                return 0.0
            ratio = abs(avg_win / avg_loss)
            kelly = win_rate - ((1 - win_rate) / ratio)
            # Standard "Half-Kelly" for safety
            return max(round(kelly * 0.5 * 100, 2), 0.0)
        except:
            return 0.0
