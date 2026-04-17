import pandas as pd
import numpy as np

class Strategy:
    def __init__(self):
        self.name = "Multibagger Early Detection"

    def run(self, df: pd.DataFrame, funda: dict) -> dict:
        if df.empty or len(df) < 60:
            return {"signal": False}

        try:
            ltp = float(df["close"].iloc[-1])
            
            ema20 = df["close"].ewm(span=20, adjust=False).mean()
            ema50 = df["close"].ewm(span=50, adjust=False).mean()
            val_ema20 = float(ema20.iloc[-1])
            val_ema50 = float(ema50.iloc[-1])
            ema_trend = bool((ltp > val_ema20) and (val_ema20 > val_ema50))

            delta = df["close"].diff()
            gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
            rs = gain / loss
            df["rsi"] = 100 - (100 / (1+rs))

            rsi_min = float(df["rsi"].iloc[-20:-1].min())
            price_min = float(df["close"].iloc[-20:-1].min())
            curr_rsi = float(df["rsi"].iloc[-1])
            has_rsi_divergence = bool((curr_rsi > rsi_min) and (ltp <= price_min))

            # THE SERIES FIX (Restored)
            rs_raw = funda.get("rs_rating", 0)
            if isinstance(rs_raw, pd.Series):
                rs_val = float(rs_raw.iloc[-1]) if not rs_raw.empty else 0.0
            elif isinstance(rs_raw, (np.ndarray, list)):
                rs_val = float(rs_raw[-1]) if len(rs_raw) > 0 else 0.0
            else:
                rs_val = float(rs_raw) if rs_raw else 0.0
                
            is_strong_rs = bool(rs_val > 70)
            std20 = float(df["close"].iloc[-20:].std())
            is_compressing = bool((std20 / ltp) < 0.02)  

            vwap_20 = (df["close"] * df["volume"]).rolling(20).sum() / df["volume"].rolling(20).sum()
            curr_vwap = float(vwap_20.iloc[-1])
            is_vwap_reclaim = bool(ltp > curr_vwap)

            score = 0
            if ema_trend: score += 30
            if is_strong_rs: score += 20
            if is_vwap_reclaim: score += 20
            if is_compressing: score += 15
            if has_rsi_divergence: score += 15

            if score >= 65:  
                recent_high = float(df["high"].iloc[-5:].max())
                entry_price = round(max(ltp * 1.005, recent_high), 2)
                atr_val = float((df["high"] - df["low"]).iloc[-14:].mean())
                sl_price = round(max(float(df["low"].iloc[-10:].min()), ltp - (1.5 * atr_val)), 2)

                return {
                    "signal": True,
                    "metrics": {
                        "Score": score,
                        "Grade": "Elite" if score >= 85 else "Strong" if score >= 70 else "Pass",
                        "Comp": "YES" if is_compressing else "NO",
                        "Div": "YES" if has_rsi_divergence else "NO",
                        "VWAP": "YES" if is_vwap_reclaim else "NO",
                        "Entry": entry_price,
                        "SL": sl_price,
                    },
                }
        except Exception:
            pass

        return {"signal": False}
