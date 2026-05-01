from datetime import datetime

import numpy as np
import pandas as pd
import xgboost as xgb


def run(df: pd.DataFrame, funda: dict) -> dict:
    """
    Strategy: ML-Based Institutional Signals (XGBoost v2.5)
    Uses Triple-Barrier Labeling and Advanced Feature Engineering.
    Refined with ideas from Jansen (ML4T) and Lopez de Prado (FML).
    """
    if len(df) < 100:
        return {"signal": False}

    try:
        # 1. ADVANCED FEATURE ENGINEERING
        # (Using precomputed metrics from funda where possible for speed)
        df["RSI"] = ta.rsi(df["Close"], length=14)
        df["SMA_Dist"] = (
            df["Close"] / funda.get("sma50", df["Close"].rolling(50).mean())
        ) - 1
        df["Vol_Shock"] = funda.get("rel_vol", 1.0)
        df["VSA_Spread"] = funda.get("rel_spread", 1.0)
        df["VSA_Closing"] = funda.get("closing_pos", 0.5)
        df["Drawdown"] = funda.get("drawdown", 0)

        # Log Scale Money Flow for better ML distribution
        mf = funda.get("money_flow_cr", 1.0)
        df["Log_MF"] = np.log1p(max(0, mf))

        # 2. TRIPLE BARRIER LABELING (PKScreener/FML Superpower)
        # Goal: Predict if +3% (Profit) is hit before -2% (Stop) or 10 days (Time)
        pt = 0.03
        sl = 0.02
        window = 10

        # Calculate labels for historical data (Optimized with list comprehension)
        high_vals = df["High"].values
        low_vals = df["Low"].values
        close_vals = df["Close"].values

        labels = [
            (
                1
                if (high_vals[i + 1 : i + window + 1] >= close_vals[i] * (1 + pt)).any()
                and not (
                    low_vals[i + 1 : i + window + 1] <= close_vals[i] * (1 - sl)
                ).any()
                else 0
            )
            for i in range(len(df) - window)
        ]

        # Pad labels to match DF length
        labels.extend([0] * window)
        df["Target"] = labels

        # 3. DATA PREPARATION
        features = [
            "RSI",
            "SMA_Dist",
            "Vol_Shock",
            "VSA_Spread",
            "VSA_Closing",
            "Drawdown",
            "Log_MF",
        ]
        ml_df = df.dropna(subset=features).copy()

        if len(ml_df) < 60:
            return {"signal": False}

        # Train on all except the last 'window' days (where target is unknown)
        train_df = ml_df.iloc[:-window]
        X_train = train_df[features]
        y_train = train_df["Target"]

        # 4. XGBOOST MICRO-MODEL
        # Optimized for small datasets (per-stock)
        model = xgb.XGBClassifier(
            n_estimators=50,
            max_depth=3,
            learning_rate=0.1,
            subsample=0.8,
            objective="binary:logistic",
            random_state=42,
            verbosity=0,
        )

        model.fit(X_train, y_train)

        # 5. INFERENCE
        latest_features = df[features].iloc[[-1]].fillna(0)
        prob_success = model.predict_proba(latest_features)[0, 1]

        # 6. SIGNAL LOGIC
        # High Confidence (>70%) and Technical Alignment
        # Accuracy context from Engine helps filter the best setups
        is_uptrend = df["Close"].iloc[-1] > funda.get("sma50", 0)

        if prob_success > 0.70 and is_uptrend:
            return {
                "signal": True,
                "metrics": {
                    "LTP": round(df["Close"].iloc[-1], 2),
                    "ML_ProbUp": f"{round(prob_success * 100, 1)}%",
                    "Vibe": (
                        "Institutional Accumulation"
                        if df["Vol_Shock"].iloc[-1] > 1.2
                        else "Momentum"
                    ),
                    "Accuracy": funda.get("Accuracy", "-"),
                },
            }

    except Exception:
        pass

    return {"signal": False}
