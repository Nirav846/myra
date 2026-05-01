import numpy as np
import pandas as pd


class Strategy:
    """
    SMC-2: Institutional Structural Flow
    Detects Trend Reversals (CHoCH) and Momentum Gaps (FVG).
    Reference: joshyattridge/smart-money-concepts
    """

    def __init__(self, librarian=None):
        self.name = "Institutional Structural Flow"
        self.librarian = librarian

    def run(self, df: pd.DataFrame, funda: dict) -> dict:
        if df.empty or len(df) < 30:
            return {"signal": False}

        # 1. Validation Layer: Detect Missing Structural Data
        required_keys = ["choch", "bos", "fvg"]
        missing_keys = [k for k in required_keys if k not in funda]
        if missing_keys:
            return {
                "signal": False,
                "error": f"STRUCTURAL DATA MISSING: {', '.join(missing_keys)}",
                "debug": {k: funda.get(k) for k in required_keys},
            }

        # 2. Access Precomputed Structural Data (Lowercase Snake Case as per Project Mandate #4)
        choch = funda.get("choch", 0)
        bos = funda.get("bos", 0)
        fvg = funda.get("fvg", 0)

        # Volume Spike Confirmation (1.5x of 20d Avg)
        volume_spike = 0
        avg_vol_20 = funda.get("avg_volume_20d", 0)
        curr_vol = df["Volume"].iloc[-1] if "Volume" in df.columns else 0
        if avg_vol_20 > 0 and curr_vol > avg_vol_20 * 1.5:
            volume_spike = 1

        # 3. SMC Logic v2.0: Scoring & Confluence (Refined Approach A)
        # Weights: CHoCH (2.0), BOS (1.5), FVG (1.0), Volume Spike (0.5)
        score_details = {
            "choch": 2.0 if choch != 0 else 0,
            "bos": 1.5 if bos != 0 else 0,
            "fvg": 1.0 if fvg != 0 else 0,
            "volume_spike": 0.5 if volume_spike else 0,
        }

        smc_score = sum(score_details.values())

        # 4. Grading System (A+ to C)
        def get_grade(score):
            if score >= 3.5:
                return "A+"  # Strong Reversal + Multi-Confluence
            if score >= 3.0:
                return "A"  # High Quality Setup
            if score >= 2.5:
                return "B+"  # Good Confluence
            if score >= 2.0:
                return "B"  # Valid Setup
            return "C"  # Low Conviction

        grade = get_grade(smc_score)

        # 5. Hard Filter (Noise Control)
        # Only show symbols with Score >= 2.0 (Institutional Footprint Threshold)
        if smc_score < 2.0:
            return {"signal": False}

        # 6. Status & Sentiment
        status = "CHoCH (Reversal)" if choch != 0 else "BOS (Continuation)"
        fvg_sentiment = "BULLISH" if fvg == 1 else "BEARISH" if fvg == -1 else "Neutral"

        return {
            "signal": True,
            "score": round(smc_score, 2),
            "grade": grade,
            "metrics": {
                "Strategy": "SMC-Structural",
                "SMC_Score": round(smc_score, 2),
                "SMC_Grade": grade,
                "Structure": status,
                "FVG": fvg_sentiment,
                "Type": "Institutional",
            },
            "debug": {
                "choch": choch,
                "bos": bos,
                "fvg": fvg,
                "vol_spike": volume_spike,
                "components": score_details,
            },
        }
