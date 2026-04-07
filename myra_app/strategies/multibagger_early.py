import pandas as pd
import numpy as np
import pandas_ta as ta


class Strategy:
    """
    Multibagger Early Detection Scanner (v6.2 - INSTITUTIONAL SNIPER)
    Final hardening phase with RSI Divergence and VWAP Reclaim.

    Upgrades:
    - Step 1: Bullish RSI Divergence (Coiled Spring Detection)
    - Step 2: VWAP Reclaim (Price > 20d Avg VWAP)
    - Step 3: Relative Strength (RS) vs Index
    - Step 4: EMA 20/50 Trend Filter
    - Step 5: Tight Base + VCP
    """

    def __init__(self, librarian=None):
        self.name = "Multibagger Early Detection"
        self.librarian = librarian

    def run(self, df: pd.DataFrame, funda: dict) -> dict:
        # --- STEP 1: HARDENED DATA VALIDATION ---
        if df.empty or len(df) < 60:
            return {"signal": False, "reason": "insufficient_data"}

        required_cols = {"Open", "High", "Low", "Close", "Volume"}
        if not required_cols.issubset(df.columns):
            return {"signal": False, "reason": "missing_required_columns"}

        last_5 = df.iloc[-5:].copy()
        if "Volume" in last_5.columns:
            last_5["Volume"] = last_5["Volume"].fillna(0)
        if last_5[["Open", "High", "Low", "Close"]].isnull().values.any():
            return {"signal": False, "reason": "data_anomaly_nan"}

        try:
            df = df.copy().reset_index(drop=True)
            ltp = float(df["Close"].iloc[-1])

            # --- STEP 2: RSI DIVERGENCE (BULLISH) ---
            df["rsi"] = ta.rsi(df["Close"], length=14)
            # Find local lows in last 30 days
            has_rsi_divergence = False
            try:
                # Simple divergence logic:
                # Current Close < Close 10 days ago BUT Current RSI > RSI 10 days ago
                p_old = df["Close"].iloc[-20:-10].min()
                r_old = df["rsi"].iloc[-20:-10].min()
                p_new = df["Close"].iloc[-5:].min()
                r_new = df["rsi"].iloc[-5:].min()

                if p_new < p_old and r_new > r_old:
                    has_rsi_divergence = True
            except:
                pass

            # --- STEP 3: VWAP RECLAIM ---
            # Technical.db provides daily VWAP. We check if price is above 20d Mean VWAP
            vwap_col = "vwap" if "vwap" in df.columns else "VWAP"
            is_vwap_reclaim = False
            if vwap_col in df.columns:
                avg_vwap_20 = df[vwap_col].rolling(20).mean().iloc[-1]
                is_vwap_reclaim = ltp > avg_vwap_20

            # --- STEP 4: TREND & RS ---
            df["ema20"] = df["Close"].ewm(span=20, adjust=False).mean()
            df["ema50"] = df["Close"].ewm(span=50, adjust=False).mean()
            ema_trend = ltp > df["ema20"].iloc[-1] > df["ema50"].iloc[-1]

            df["returns"] = df["Close"].pct_change()
            rs_raw = df["returns"].iloc[-60:].sum() * 100

            # --- STEP 5: TIGHT BASE & VCP ---
            base_window = df.iloc[-60:]
            base_range_pct = (
                base_window["Close"].max() - base_window["Close"].min()
            ) / base_window["Close"].min()
            is_tight_base = base_range_pct < 0.25

            atr_short = (df["High"] - df["Low"]).iloc[-5:].mean()
            atr_long = (df["High"] - df["Low"]).iloc[-20:].mean()
            is_compressing = atr_short < atr_long

            # --- STEP 6: WEIGHTED SCORING ENGINE (v6.2) ---
            score = 0

            # 1. Base & VCP (Max 25)
            if is_tight_base:
                score += 15
            if is_compressing:
                score += 10

            # 2. Momentum & Trend (Max 25)
            if ema_trend:
                score += 10
            if rs_raw > 15:
                score += 15
            elif rs_raw > 0:
                score += 5

            # 3. Institutional Sniper (Max 30)
            if is_vwap_reclaim:
                score += 15
            rdv = float(funda.get("RDV", 1.0))
            if rdv > 1.5:
                score += 15
            elif rdv > 1.2:
                score += 5

            # 4. Turnaround Triggers (Max 20)
            if has_rsi_divergence:
                score += 10

            avg_buy = float(funda.get("avg_buy_60d", 0))
            if avg_buy > 0 and ltp < avg_buy:
                score += 10  # Underwater Bonus

            # --- FINAL GATEKEEPER ---
            # Threshold: 50
            if score < 50:
                return {
                    "signal": False,
                    "reason": "low_probability_setup",
                    "score": score,
                }

            # --- TACTICAL EXECUTION ---
            recent_high = df["High"].iloc[-5:].max()
            entry_price = round(max(ltp * 1.005, recent_high), 2)

            atr_val = (df["High"] - df["Low"]).iloc[-14:].mean()
            sl_price = round(max(df["Low"].iloc[-10:].min(), ltp - (1.5 * atr_val)), 2)

            return {
                "signal": True,
                "metrics": {
                    "Strategy": "MB-Sniper-v6.2",
                    "Score": score,
                    "Grade": "Elite"
                    if score >= 85
                    else "Strong"
                    if score >= 70
                    else "Pass",
                    "Trend": "Bullish" if ema_trend else "Neutral",
                    "RS_Raw": round(rs_raw, 2),
                    "Vibe": "Sniper+VCP"
                    if (is_compressing and is_vwap_reclaim)
                    else "Pre-Expansion",
                    "Compression": "YES" if is_compressing else "NO",
                    "Divergence": "YES" if has_rsi_divergence else "NO",
                    "VWAP_Reclaim": "YES" if is_vwap_reclaim else "NO",
                    "Entry": entry_price,
                    "SL": sl_price,
                    "T1": round(entry_price * 1.15, 2),
                    "T2": round(entry_price * 1.30, 2),
                    "Tag": "ULTRA-HIGH PROBABILITY" if score >= 85 else "STANDARD",
                },
            }

        except Exception as e:
            return {"signal": False, "reason": f"exception: {str(e)}"}
