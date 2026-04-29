import numpy as np
import pandas as pd
import pandas_ta as ta

from myra_app.strategies.base_strategy import BaseStrategy


class Strategy(BaseStrategy):
    def __init__(self):
        super().__init__(name="Multi-Year Bottom Hunter", strategy_id="bottom_hunter")

    def run(self, df: pd.DataFrame, funda: dict) -> dict:
        """
        Modular Multi-Year Bottom Hunter (Class-Based v1.0)
        Detects structural bottoms using ATR-Relative Support and Weekly Divergence.
        Prioritizes entries during EXTREME FEAR mood.
        """
        if len(df) < 300:
            return {"signal": False}

        try:
            # 1. Weekly Resample
            df_w = (
                df.resample("W")
                .agg(
                    {
                        "Open": "first",
                        "High": "max",
                        "Low": "min",
                        "Close": "last",
                        "Volume": "sum",
                        "delivery_qty": "sum",
                    }
                )
                .dropna()
            )

            if len(df_w) < 50:
                return {"signal": False}

            df_w["RSI_W"] = ta.rsi(df_w["Close"], length=14)
            df_w["ATR_W"] = ta.atr(df_w["High"], df_w["Low"], df_w["Close"], length=14)
            latest_w = df_w.iloc[-1]

            # 2. Support Levels
            l3y, l2y, l1y = (
                funda.get("low_3y", 0),
                funda.get("low_2y", 0),
                funda.get("low_1y", 0),
            )
            ltp = df["Close"].iloc[-1]
            w_atr = latest_w["ATR_W"]

            is_3y = (ltp <= l3y + (1.5 * w_atr)) if l3y > 0 else False
            is_2y = (ltp <= l2y + (1.5 * w_atr)) if l2y > 0 else False
            is_1y = (ltp <= l1y + (1.5 * w_atr)) if l1y > 0 else False

            if not (is_1y or is_2y or is_3y):
                return {"signal": False}

            # 3. Institutional Vibe & Context
            sm_score = funda.get("smart_money_score", 0)
            inst_intensity = funda.get("Inst_Intensity", 0)
            stage = funda.get("Stage", "-")

            # Label stage context accurately for Bottom Hunter
            # Stage 1 is the goal (Bottom found), Stage 4 is catching a falling knife
            ctx_stage = (
                "Stage 1 (Basing)"
                if "Stage 1" in stage
                else "Stage 4 (Dangerous Fall)" if "Stage 4" in stage else stage
            )

            # 4. Weekly Divergence
            lookback = df_w.iloc[-26:-2]
            if lookback.empty:
                return {"signal": False}
            rsi_prev = lookback.loc[lookback["Close"].idxmin(), "RSI_W"]
            is_divergent = latest_w["RSI_W"] > rsi_prev

            # 5. Market Mood
            mood = funda.get("Market_Mood", "NEUTRAL")

            # 6. Signal logic (Star Ranking)
            stars = 1
            if is_divergent:
                stars += 1
            if is_3y:
                stars += 1
            if sm_score > 0.7:
                stars += 1
            if inst_intensity > 0.5:
                stars += 1
            if mood == "EXTREME FEAR":
                stars += 1

            # 7. SMC: CHoCH
            lookback_choch = df_w.iloc[-15:-3]
            is_choch = (
                latest_w["Close"] > lookback_choch["High"].max()
                if not lookback_choch.empty
                else False
            )

            # 8. Tactics
            entry = round(df["High"].iloc[-1] * 1.002, 2)
            sl = round(ltp - (1.5 * w_atr), 2)

            return {
                "signal": True,
                "stars": "*" * min(5, stars),
                "tactics": {
                    "entry": entry,
                    "sl": sl,
                    "target": round(entry + (5 * (entry - sl)), 2),
                },
                "metrics": {
                    "Support": f"{'3Y' if is_3y else '2Y' if is_2y else '1Y'} Floor",
                    "Weekly_Div": "YES" if is_divergent else "NO",
                    "CHoCH": "YES" if is_choch else "NO",
                    "A/D_Vibe": (
                        "Accumulating"
                        if funda.get("AD_Flow", 0) > 0
                        else "Distributing"
                    ),
                    "Absorption": f"{round(funda.get('Absorp_Ratio', 0) * 100)}%",
                    "SM_Score": round(sm_score, 2),
                    "Stage_Ctx": ctx_stage,
                },
            }
        except Exception:
            pass
        return {"signal": False}
