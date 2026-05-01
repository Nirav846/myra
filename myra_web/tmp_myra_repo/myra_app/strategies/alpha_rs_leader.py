#!/usr/bin/env python
import pandas as pd


class RSLeadershipScanner:
    """
    RS Leadership Scanner
    Targets symbols in Stage 2 with top-tier Relative Strength.
    """

    def run(self, df: pd.DataFrame, funda: dict):
        if len(df) < 200:
            return {"signal": False}

        try:
            # 1. Trend Filter: Stage 2 Only
            if funda.get("Stage") != "Stage 2":
                return {"signal": False}

            # 2. RS Filter: Top 25% of universe (using the new Factor score)
            # conviction_score already includes RS weighted at 30%
            rs_score = funda.get("rs_rating", 0)
            if rs_score < 0.7:
                return {"signal": False}

            # 3. Pullback check: Price near 20 SMA or 50 SMA (Buy point)
            c = df["Close"].iloc[-1]
            ma20 = df["Close"].rolling(20).mean().iloc[-1]
            ma50 = df["Close"].rolling(50).mean().iloc[-1]

            # Within 3% of MA
            near_ma = abs(c / ma20 - 1) < 0.03 or abs(c / ma50 - 1) < 0.03

            if not near_ma:
                return {"signal": False}

            return {
                "signal": True,
                "metrics": {
                    "Strategy": "RS_Leader",
                    "RS_Rating": round(rs_score * 100),
                    "Uptrend": "Confirmed",
                },
            }
        except:
            return {"signal": False}
