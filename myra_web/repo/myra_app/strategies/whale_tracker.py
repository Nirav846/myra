import os
import pandas as pd
import numpy as np
import warnings
import joblib
from datetime import datetime, timedelta

# Suppress XGBoost warnings
warnings.filterwarnings("ignore", category=UserWarning)

try:
    import xgboost as xgb
    from sklearn.ensemble import RandomForestClassifier, VotingClassifier
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler
except ImportError:
    pass

MODEL_DIR = os.path.join(os.getcwd(), "results", "Models")
os.makedirs(MODEL_DIR, exist_ok=True)


def run(df: pd.DataFrame, funda: dict) -> dict:
    """
    Strategy: Elite Whale Tracker (Persistent Ensemble v3)
    Tailored Tactics: Entry at pivot high, SL below base, 5R Target (Whale Ride).
    """
    if len(df) < 250:
        return {"signal": False}

    symbol = funda.get("symbol", "UNKNOWN")
    model_path = os.path.join(MODEL_DIR, f"{symbol}_whale.joblib")

    try:
        # 1. FEATURE ENGINEERING
        c = df["Close"]
        v = df["Volume"]
        df["RSI"] = ta.rsi(c, length=14)
        df["ATR"] = ta.atr(df["High"], df["Low"], c, length=20)
        df["Force"] = (c.diff() * v) / df["ATR"]
        df["Ret_1d"] = c.pct_change(1)
        df["Ret_3d"] = c.pct_change(3)
        df["RSI_Lag3"] = df["RSI"].shift(3)
        df["Force_Lag3"] = df["Force"].shift(3)
        df["Tightness"] = ta.atr(df["High"], df["Low"], c, length=5) / df["ATR"]
        df["Vol_Dry"] = v / v.rolling(20).mean()
        df["Regime"] = funda.get("Market_Regime", 1)
        del_mean = df["delivery_percent"].rolling(50).mean()
        del_std = df["delivery_percent"].rolling(50).std()
        df["Del_Clump"] = (
            (df["delivery_percent"] > (del_mean + 1.0 * del_std)).rolling(10).sum()
        )

        features = [
            "RSI",
            "Ret_1d",
            "Ret_3d",
            "RSI_Lag3",
            "Force",
            "Force_Lag3",
            "Tightness",
            "Vol_Dry",
            "Regime",
            "Del_Clump",
        ]

        # 2. PERSISTENCE
        committee = None
        scaler = None
        needs_training = True
        if os.path.exists(model_path):
            try:
                m_data = joblib.load(model_path)
                if (datetime.now() - m_data.get("timestamp", datetime.min)).days < 7:
                    committee = m_data.get("model")
                    scaler = m_data.get("scaler")
                    needs_training = False
            except:
                pass

        if needs_training:
            future_max = df["High"].shift(-10).rolling(10).max()
            df["Target"] = (future_max > (c + 1.5 * df["ATR"])).astype(int)
            ml_df = df.dropna(subset=features + ["Target"]).copy()
            if len(ml_df) < 150 or len(ml_df[ml_df["Target"] == 1]) < 5:
                return {"signal": False}
            X = ml_df[features]
            y = ml_df["Target"]
            scaler = StandardScaler()
            X_scaled = scaler.fit_transform(X)
            m1 = xgb.XGBClassifier(
                n_estimators=30,
                max_depth=3,
                learning_rate=0.1,
                verbosity=0,
                random_state=42,
            )
            m2 = RandomForestClassifier(n_estimators=30, max_depth=5, random_state=42)
            m3 = LogisticRegression(class_weight="balanced", random_state=42)
            committee = VotingClassifier(
                estimators=[("xgb", m1), ("rf", m2), ("lr", m3)], voting="soft"
            )
            committee.fit(X_scaled[:-10], y[:-10])
            joblib.dump(
                {"model": committee, "scaler": scaler, "timestamp": datetime.now()},
                model_path,
            )

        # 3. INFERENCE
        latest_raw = df[features].iloc[[-1]].fillna(0)
        latest_scaled = scaler.transform(latest_raw)
        prob = committee.predict_proba(latest_scaled)[0, 1]

        # 4. SIGNAL
        ma50 = ta.sma(c, length=50).iloc[-1]
        if prob > 0.60 and c.iloc[-1] > ma50:
            # TACTICAL PLANNING
            entry = round(df["High"].iloc[-1] * 1.002, 2)
            sl = round(df["Low"].iloc[-1] * 0.995, 2)  # Below today's low
            risk = entry - sl
            target = round(entry + (5 * risk), 2)  # Whale target is larger (5R)

            return {
                "signal": True,
                "tactics": {"entry": entry, "sl": sl, "target": target},
                "metrics": {
                    "LTP": round(c.iloc[-1], 2),
                    "Whale_Conf": f"{round(prob * 100, 1)}%",
                    "Clump": int(df["Del_Clump"].iloc[-1]),
                    "ROE": funda.get("ROE", "N/A"),
                },
            }
    except Exception:
        pass
    return {"signal": False}
