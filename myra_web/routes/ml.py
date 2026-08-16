import asyncio
import json
import os

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from myra_app.constants import DB_DIR
from myra_web.background import _spawn_task

router = APIRouter(prefix="/api/ml", tags=["ml"])


@router.get("/status")
async def ml_status():
    """Check if a trained model exists and when it was last trained."""
    import os

    model_path = "models/forward_return.xgb"
    metadata_path = "models/model_metadata.json"
    if not os.path.exists(model_path):
        return {
            "exists": False,
            "message": "No trained model found. Run /api/ml/train to train a model.",
        }
    try:
        if os.path.exists(metadata_path):
            with open(metadata_path) as f:
                meta = json.load(f)
            return {
                "exists": True,
                "trained_at": meta.get("trained_at"),
                "train_accuracy": meta.get("train_accuracy"),
                "test_accuracy": meta.get("test_accuracy"),
            }
        return {"exists": True, "message": "Model exists but metadata not found."}
    except Exception as e:
        return {"exists": False, "error": str(e)}


@router.post("/train")
async def ml_train(config: dict = None):
    """Train a new model (async). Optionally pass a config dict to override defaults."""

    def _run():
        from myra_app.ml_trainer import MLTrainer

        trainer = MLTrainer(config)
        return trainer.train()

    try:
        tid = _spawn_task("ml_train", _run)
        return JSONResponse(
            status_code=202, content={"status": "started", "task_id": tid}
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/predict")
async def ml_predict():
    """Return today's predictions for all symbols."""

    def _run():
        from myra_app.ml_trainer import MLTrainer

        trainer = MLTrainer()
        return trainer.predict_today()

    return await asyncio.to_thread(_run)


@router.get("/feature-importance")
async def ml_feature_importance():
    """Return feature importance from the latest model."""
    from myra_app.ml_trainer import MLTrainer

    trainer = MLTrainer()
    return trainer.get_feature_importance()


@router.post("/config")
async def ml_update_config(config: dict):
    """Update ML config and save to models/ml_config.json. Merges with existing config."""
    import json, os
    from myra_app.ml_trainer import DEFAULT_CONFIG

    os.makedirs("models", exist_ok=True)

    existing_config = DEFAULT_CONFIG.copy()
    if os.path.exists("models/ml_config.json"):
        try:
            with open("models/ml_config.json") as f:
                existing_config.update(json.load(f))
        except Exception:
            pass

    for key, value in config.items():
        if (
            isinstance(value, dict)
            and key in existing_config
            and isinstance(existing_config[key], dict)
        ):
            existing_config[key].update(value)
        else:
            existing_config[key] = value

    with open("models/ml_config.json", "w") as f:
        json.dump(existing_config, f, indent=2)

    return {"status": "ok", "config": existing_config}


@router.get("/config")
async def ml_get_config():
    """Get current ML configuration."""
    import json, os

    if os.path.exists("models/ml_config.json"):
        with open("models/ml_config.json") as f:
            return json.load(f)
    return {"status": "defaults"}


@router.post("/launchpad/label")
async def label_launchpad_events(config: dict = None):
    """Run launchpad event labelling. Optionally pass config to override defaults."""
    from myra_app.launchpad_labels import LaunchpadLabeler

    labeler = LaunchpadLabeler(config)
    result = labeler.run()
    return result


@router.post("/launchpad/train")
async def train_launchpad(config: dict = None):
    """Train the launchpad prediction model. Optionally pass config."""
    from myra_app.ml_trainer import LaunchpadPredictor

    predictor = LaunchpadPredictor(config)
    result = predictor.train()
    return result


@router.get("/launchpad/predict")
async def predict_launchpad():
    """Get current launchpad predictions for stocks in digestion phase."""
    import os

    model_path = "models/launchpad_xgb.joblib"
    if not os.path.exists(model_path):
        return {
            "predictions": [],
            "status": "no_model",
            "message": "Launchpad model not trained yet.",
        }
    try:
        import sqlite3
        import pandas as pd
        import numpy as np
        import joblib
        from myra_app.librarian_core import LibrarianCore

        tech_db = os.path.join(DB_DIR, LibrarianCore.DB_MAP["technical"])
        val_db = os.path.join(DB_DIR, LibrarianCore.DB_MAP["valuation"])

        with sqlite3.connect(tech_db) as conn:
            events = conn.execute(
                "SELECT symbol, trigger_date FROM launchpad_events WHERE success = 0 AND trigger_date >= date('now', '-180 days') ORDER BY trigger_date DESC"
            ).fetchall()

        if not events:
            return {
                "predictions": [],
                "status": "no_events",
                "message": "No stocks in digestion phase.",
            }

        model = joblib.load(model_path)
        results = []
        for sym, trig in events[:20]:  # Limit to 20 for performance
            try:
                with sqlite3.connect(tech_db) as conn:
                    row = conn.execute(
                        "SELECT date, close, volume, delivery, high, low FROM technical_data WHERE symbol = ? AND date >= ? ORDER BY date ASC LIMIT 30",
                        (sym, trig),
                    ).fetchall()

                if len(row) < 2:
                    continue

                closes = [r[1] for r in row]
                volumes = [r[2] for r in row]
                deliveries = [r[3] for r in row]
                highs = [r[4] for r in row]
                lows = [r[5] for r in row]

                first_close = closes[0]
                last_close = closes[-1]
                max_dd = (
                    (min(closes) - first_close) / first_close * 100
                    if first_close > 0
                    else 0
                )
                avg_vol = np.mean(volumes) if volumes else 1
                avg_del = np.mean(deliveries) if deliveries else 0
                avg_range = (
                    np.mean([h - l for h, l in zip(highs, lows)]) if highs else 1
                )

                del_vals = deliveries
                if len(del_vals) > 1:
                    del_mean = np.mean(del_vals)
                    del_std = np.std(del_vals) if len(del_vals) > 1 else 1
                    del_zscores = [(d - del_mean) / (del_std + 1e-9) for d in del_vals]
                    del_z_min = min(del_zscores)
                    del_z_mean = np.mean(del_zscores)
                else:
                    del_z_min = 0.0
                    del_z_mean = 0.0

                features = [
                    del_z_min,
                    del_z_mean,
                    avg_range / (avg_range + 1e-9),
                    volumes[-1] / (avg_vol + 1e-9),
                    len(row),
                    max_dd,
                ]
                X = pd.DataFrame(
                    [features],
                    columns=[
                        "del_zscore_min",
                        "del_zscore_mean",
                        "range_atr_min",
                        "vol_ratio_min",
                        "digestion_days",
                        "max_drawdown_pct",
                    ],
                )
                preds = model.predict(X)
                predicted_return_pct = round(float(preds[0, 0]), 2)
                breakout_probability = round(
                    1 / (1 + np.exp(-predicted_return_pct / 10)), 4
                )
                confidence = (
                    "High"
                    if breakout_probability >= 0.7
                    else ("Medium" if breakout_probability >= 0.4 else "Low")
                )

                sector = None
                mcap = None
                if os.path.exists(val_db):
                    with sqlite3.connect(val_db) as vconn:
                        vrow = vconn.execute(
                            "SELECT COALESCE(market_cap, 0), sector FROM fundamentals WHERE symbol = ? LIMIT 1",
                            (sym,),
                        ).fetchone()
                        if vrow:
                            mcap = float(vrow[0]) if vrow[0] else None
                            sector = vrow[1]

                results.append(
                    {
                        "symbol": sym,
                        "trigger_date": trig,
                        "predicted_return_pct": predicted_return_pct,
                        "predicted_days_to_breakout": round(float(preds[0, 1]), 1),
                        "current_digestion_days": len(row),
                        "sector": sector,
                        "market_cap": mcap,
                        "breakout_probability": breakout_probability,
                        "confidence": confidence,
                    }
                )
            except Exception:
                continue

        return {"predictions": results, "status": "ok"}
    except Exception as e:
        return {"predictions": [], "status": "error", "message": str(e)}


@router.get("/launchpad/status")
async def launchpad_status():
    """Check if a trained launchpad model exists."""
    if os.path.exists("models/launchpad_metadata.json"):
        with open("models/launchpad_metadata.json") as f:
            return json.load(f)
    return {"exists": False}


@router.get("/launchpad/feature-importance")
async def launchpad_feature_importance():
    """Get feature importance from the launchpad model."""
    from myra_app.ml_trainer import LaunchpadPredictor

    predictor = LaunchpadPredictor()
    return predictor.get_feature_importance()


@router.get("/factor-importance")
async def factor_importance():
    from myra_app.ml_trainer import FactorDiscovery

    fd = FactorDiscovery()
    result = fd.discover_factors()
    return result
