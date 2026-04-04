import os
import pandas as pd
import numpy as np
import warnings
import joblib
from datetime import datetime

# Suppress warnings
warnings.filterwarnings("ignore", category=UserWarning)

try:
    import xgboost as xgb
    from sklearn.preprocessing import StandardScaler
except ImportError:
    pass

MODEL_DIR = os.path.join(os.getcwd(), "results", "Models")
os.makedirs(MODEL_DIR, exist_ok=True)

def run(df: pd.DataFrame, funda: dict = None) -> dict:
    """
    Strategy: Global Nifty Predictor (PKScreener Legacy DNA)
    Predicts the direction of the overall market using percentage changes.
    """
    if len(df) < 200: return {"signal": False}
    
    model_path = os.path.join(MODEL_DIR, "nifty_regime_model.joblib")
    
    try:
        # 1. PREPROCESSING (The PKScreener way: Pct Changes)
        # --------------------------------------------------
        data = df.copy()
        data["H_Pct"] = data["High"].pct_change() * 100
        data["L_Pct"] = data["Low"].pct_change() * 100
        data["O_Pct"] = data["Open"].pct_change() * 100
        data["C_Pct"] = data["Close"].pct_change() * 100
        data["V_Pct"] = data["Volume"].pct_change() * 100
        
        # Target: 1 if next day close is higher
        data["Target"] = (data["Close"].shift(-1) > data["Close"]).astype(int)
        
        features = ["O_Pct", "H_Pct", "L_Pct", "C_Pct", "V_Pct"]
        ml_df = data.dropna(subset=features + ["Target"]).copy()
        
        # 2. PERSISTENCE
        model_data = None
        needs_training = True
        
        if os.path.exists(model_path):
            try:
                model_data = joblib.load(model_path)
                # Nifty model is retrained every 3 days for maximum macro accuracy
                if (datetime.now() - model_data.get("timestamp", datetime.min)).days < 3:
                    needs_training = False
            except: pass

        if needs_training:
            X = ml_df[features]
            y = ml_df["Target"]
            
            scaler = StandardScaler()
            X_scaled = scaler.fit_transform(X)
            
            # Use a slightly deeper model for the complex index movements
            model = xgb.XGBClassifier(
                n_estimators=100,
                max_depth=5,
                learning_rate=0.05,
                subsample=0.8,
                verbosity=0,
                random_state=42
            )
            # Train on all history (except last day)
            model.fit(X_scaled[:-1], y[:-1])
            
            model_data = {"model": model, "scaler": scaler, "timestamp": datetime.now()}
            joblib.dump(model_data, model_path)

        # 3. INFERENCE
        latest_raw = data[features].iloc[[-1]].fillna(0)
        latest_scaled = model_data["scaler"].transform(latest_raw)
        prob = model_data["model"].predict_proba(latest_scaled)[0][1]
        
        # 4. SIGNAL
        # For the Index, anything > 55% is a significant edge
        return {
            "signal": True,
            "metrics": {
                "Probability_UP": f"{round(prob * 100, 1)}%",
                "Trend": "BULLISH" if prob > 0.52 else "BEARISH",
                "Confidence": "HIGH" if (prob > 0.6 or prob < 0.4) else "MODERATE",
                "Last_Close": round(df["Close"].iloc[-1], 2)
            }
        }
            
    except Exception:
        # print(f"Nifty ML Error: {e}")
        pass
        
    return {"signal": False}
