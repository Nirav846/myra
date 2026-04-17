import pandas as pd
import numpy as np

class Strategy:
    """
    Multibagger Early Detection Scanner (v6.2 - INSTITUTIONAL SNIPER)
    Standardized for CamelCase Data Adapter Compliance.
    """

    def __init__(self, librarian=None):
        self.name = "Multibagger Early Detection"
        self.librarian = librarian

    def run(self, df: pd.DataFrame, funda: dict) -> dict:
        # --- STEP 1: DATA CONFORMITY ---
        if df.empty or len(df) < 60:
            return {"signal": False, "reason": "insufficient_data"}

        # Force lowercase keys to CamelCase to ensure old logic doesn't break
        rename_map = {
            "open": "Open", "high": "High", "low": "Low", 
            "close": "Close", "volume": "Volume"
        }
        df.rename(columns={k: v for k, v in rename_map.items() if k in df.columns}, inplace=True)

        # --- STEP 2: TREND & MOMENTUM (Hardened) ---
        ltp = df["Close"].iloc[-1]
        
        # EMA Trend (20/50)
        ema20 = df["Close"].ewm(span=20, adjust=False).mean()
        ema50 = df["Close"].ewm(span=50, adjust=False).mean()
        ema_trend = ltp > ema20.iloc[-1] > ema50.iloc[-1]

        # --- STEP 3: BULLISH RSI DIVERGENCE ---
        # Coiled Spring Detection
        delta = df["Close"].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1+rs))
        df["rsi"] = rsi

        # Detect divergence in last 20 days
        rsi_min = df["rsi"].iloc[-20:-1].min()
        price_min = df["Close"].iloc[-20:-1].min()
        has_rsi_divergence = (df["rsi"].iloc[-1] > rsi_min) and (ltp <= price_min)

        # --- STEP 4: RS vs INDEX ---
        rs_raw = funda.get("rs_rating", 0)
        is_strong_rs = rs_raw > 70

        # --- STEP 5: VCP / TIGHTNESS ---
        std20 = df["Close"].iloc[-20:].std()
        is_compressing = (std20 / ltp) < 0.02  # Less than 2% volatility

        # --- STEP 6: VWAP RECLAIM ---
        # 20-day Volume Weighted Average Price
        vwap_20 = (df["Close"] * df["Volume"]).rolling(20).sum() / df["Volume"].rolling(20).sum()
        is_vwap_reclaim = ltp > vwap_20.iloc[-1]

        # --- FINAL SCORING ---
        score = 0
        if ema_trend: score += 30
        if is_strong_rs: score += 20
        if is_vwap_reclaim: score += 20
        if is_compressing: score += 15
        if has_rsi_divergence: score += 15

        if score >= 65:  # Lowered slightly for early detection
            recent_high = df["High"].iloc[-5:].max()
            entry_price = round(max(ltp * 1.005, recent_high), 2)
            atr_val = (df["High"] - df["Low"]).iloc[-14:].mean()
            sl_price = round(max(df["Low"].iloc[-10:].min(), ltp - (1.5 * atr_val)), 2)

            return {
                "signal": True,
                "metrics": {
                    "Score": score,
                    "Grade": "Elite" if score >= 85 else "Strong" if score >= 70 else "Pass",
                    "Compression": "YES" if is_compressing else "NO",
                    "Divergence": "YES" if has_rsi_divergence else "NO",
                    "VWAP_Reclaim": "YES" if is_vwap_reclaim else "NO",
                    "Entry": entry_price,
                    "SL": sl_price,
                    "T1": round(entry_price * 1.15, 2)
                },
            }

        return {"signal": False}
