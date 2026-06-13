import gc
import json
import logging
import os
import sqlite3
from datetime import datetime

import numpy as np
import pandas as pd
import xgboost as xgb

from myra_app.constants import DB_DIR
from myra_app.librarian import Librarian
from myra_app.librarian_core import LibrarianCore

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _db_path(key: str) -> str:
    return os.path.join(DB_DIR, LibrarianCore.DB_MAP[key])


def _require_db(key: str) -> str:
    """Return DB path or raise a clear FileNotFoundError."""
    path = _db_path(key)
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"Required database '{key}' not found at {path}. "
            "Please run the data ingestion pipeline first."
        )
    return path


def _open_db(key: str) -> sqlite3.Connection:
    return sqlite3.connect(_require_db(key))


# ---------------------------------------------------------------------------
# Configs
# ---------------------------------------------------------------------------

DEFAULT_CONFIG = {
    "lookback_days": 252,
    "forward_days": 5,
    "min_samples_per_symbol": 200,
    "test_split_pct": 0.2,
    "features": [
        "delivery_pct",
        "delivery_divergence_score",
        "volatility_compression_score",
        "relative_volume_score",
        "nifty_outperformance_score",
        "stock_return",
        "bullish_fvg",
        "bearish_fvg",
        "has_bullish_fvg",
        "fvg_freshness",
        "liquidity_distance",
        "close",
        "volume",
        "delivery",
    ],
    "xgb_params": {
        "n_estimators": 200,
        "max_depth": 5,
        "learning_rate": 0.05,
        "subsample": 0.8,
        "colsample_bytree": 0.8,
        "random_state": 42,
    },
}

# FIX #6: feature names now match exactly what extract_features_and_targets produces
LAUNCHPAD_DEFAULT_CONFIG = {
    "lookback_days": 120,
    "features": [
        "del_zscore_min",
        "del_zscore_mean",
        "range_atr_min",
        "vol_ratio_min",
        "digestion_days",
        "max_drawdown_pct",
        "close_min",
        "vwap_min",
        "volume_min",
        "liquidity_min",
        "fvg_freshness_min",
    ],
    "xgb_params": {
        "n_estimators": 150,
        "max_depth": 4,
        "learning_rate": 0.05,
        "subsample": 0.8,
        "colsample_bytree": 0.8,
        "random_state": 42,
    },
    "train_val_test_split": [0.70, 0.15, 0.15],
}


# ---------------------------------------------------------------------------
# MLTrainer
# ---------------------------------------------------------------------------


class MLTrainer:
    MODEL_PATH = "models/forward_return.xgb"
    META_PATH = "models/model_metadata.json"

    def __init__(self, config: dict = None):
        self.config = self._load_config(config)
        self.model: xgb.XGBClassifier | None = None
        self.metadata: dict | None = None

    # ------------------------------------------------------------------
    def _load_config(self, config: dict = None) -> dict:
        config_path = "models/ml_config.json"
        if config:
            return config
        if os.path.exists(config_path):
            with open(config_path) as f:
                return json.load(f)
        return DEFAULT_CONFIG.copy()

    # ------------------------------------------------------------------
    def get_status(self) -> dict:
        if not os.path.exists(self.MODEL_PATH):
            return {
                "exists": False,
                "message": "No trained model found. Call /api/ml/train to train one.",
            }

        # FIX #11: guard against corrupt metadata
        if os.path.exists(self.META_PATH):
            try:
                with open(self.META_PATH) as f:
                    meta = json.load(f)
                return {"exists": True, **meta}
            except (json.JSONDecodeError, OSError) as exc:
                return {
                    "exists": True,
                    "message": f"Model file present but metadata is corrupt ({exc}). Re-train to fix.",
                }

        return {
            "exists": True,
            "message": "Model exists but metadata is missing. Re-train to regenerate it.",
        }

    # ------------------------------------------------------------------
    def extract_features_and_targets(self) -> tuple[pd.DataFrame, pd.Series, dict]:
        import time

        t0 = time.time()
        cfg = self.config
        min_samples = cfg["min_samples_per_symbol"]
        lookback = cfg["lookback_days"]
        forward = cfg["forward_days"]
        features = cfg["features"]

        # FIX #10: explicit DB existence check
        try:
            conn = _open_db("technical")
        except FileNotFoundError as exc:
            logger.error(str(exc))
            return pd.DataFrame(), pd.Series(dtype=float), {"error": str(exc)}

        try:
            syms = pd.read_sql(
                "SELECT symbol FROM technical_data "
                "WHERE date >= date('now', ?) "
                "GROUP BY symbol HAVING COUNT(*) >= ?",
                conn,
                params=(f"-{lookback} days", min_samples),
            )["symbol"].tolist()
        finally:
            conn.close()

        print(f"Step 1: {len(syms)} qualifying symbols ({time.time()-t0:.1f}s)")

        if len(syms) < 10:
            msg = (
                f"Only {len(syms)} symbols have ≥{min_samples} rows in the last "
                f"{lookback} days. Need at least 10 to train. Ingest more data."
            )
            logger.warning(msg)
            return pd.DataFrame(), pd.Series(dtype=float), {"error": msg}

        conn = _open_db("technical")
        try:
            placeholders = ",".join(["?"] * len(syms))
            query = (
                f"SELECT symbol, date, {','.join(features)} "
                f"FROM technical_data "
                f"WHERE symbol IN ({placeholders}) AND date >= date('now', ?) "
                f"ORDER BY symbol, date"
            )
            df = pd.read_sql(query, conn, params=syms + [f"-{lookback} days"])
        finally:
            conn.close()

        print(f"Step 2: {df.shape[0]} rows loaded ({time.time()-t0:.1f}s)")

        df["forward_close"] = df.groupby("symbol")["close"].shift(-forward)
        df["forward_return"] = (df["forward_close"] / df["close"] - 1) * 100
        df.dropna(subset=["forward_return"], inplace=True)

        if df.empty:
            msg = (
                "No rows remain after computing forward returns. Check 'close' column."
            )
            logger.warning(msg)
            return pd.DataFrame(), pd.Series(dtype=float), {"error": msg}

        df["target"] = pd.qcut(df["forward_return"], q=3, labels=False)
        print(f"Step 3: target created ({time.time()-t0:.1f}s)")

        X = df[features].select_dtypes(include=[np.number]).fillna(0)
        y = df["target"]
        meta = {"symbols": df["symbol"].tolist(), "dates": df["date"].tolist()}
        print(
            f"Step 4: X shape {X.shape}, classes {y.nunique()} ({time.time()-t0:.1f}s)"
        )
        return X, y, meta

    # ------------------------------------------------------------------
    def train(self) -> dict:
        print("[ML] Extracting features and targets...")
        X, y, meta = self.extract_features_and_targets()

        if X.empty:
            error = meta.get("error", "Insufficient data for training.")
            return {"error": error}

        print(f"[ML] Total samples: {len(X)}")

        split_idx = int(len(X) * (1 - self.config["test_split_pct"]))
        X_train, X_test = X.iloc[:split_idx], X.iloc[split_idx:]
        y_train, y_test = y.iloc[:split_idx], y.iloc[split_idx:]
        print(f"[ML] Train: {len(X_train)}, Test: {len(X_test)}")

        params = self.config["xgb_params"]
        self.model = xgb.XGBClassifier(
            n_estimators=params["n_estimators"],
            max_depth=params["max_depth"],
            learning_rate=params["learning_rate"],
            subsample=params["subsample"],
            colsample_bytree=params["colsample_bytree"],
            random_state=params["random_state"],
            eval_metric="mlogloss",
            verbosity=0,
        )
        self.model.fit(X_train, y_train)

        train_acc = self.model.score(X_train, y_train)
        test_acc = self.model.score(X_test, y_test)

        os.makedirs("models", exist_ok=True)
        self.model.save_model(self.MODEL_PATH)
        gc.collect()

        features = self.config["features"]
        importances = self.model.feature_importances_

        self.metadata = {
            "trained_at": datetime.now().isoformat(),
            "train_samples": len(X_train),
            "test_samples": len(X_test),
            "train_accuracy": round(float(train_acc), 4),
            "test_accuracy": round(float(test_acc), 4),
            "features": features,
            "config": self.config,
        }
        with open(self.META_PATH, "w") as f:
            json.dump(self.metadata, f, indent=2)

        print(f"[ML] Train Acc: {train_acc:.4f}, Test Acc: {test_acc:.4f}")
        return {
            "train_accuracy": float(train_acc),
            "test_accuracy": float(test_acc),
            "feature_importance": [
                {"feature": features[i], "importance": round(float(importances[i]), 4)}
                for i in range(len(features))
            ],
            "train_samples": len(X_train),
            "test_samples": len(X_test),
            "model_saved": True,
        }

    # ------------------------------------------------------------------
    def _load_model(self) -> bool:
        """Load model from disk. Returns True on success."""
        if self.model:
            return True
        if not os.path.exists(self.MODEL_PATH):
            return False
        try:
            self.model = xgb.XGBClassifier()
            self.model.load_model(self.MODEL_PATH)
            return True
        except Exception as exc:
            logger.error("Failed to load model from %s: %s", self.MODEL_PATH, exc)
            self.model = None
            return False

    # ------------------------------------------------------------------
    def predict_today(self) -> dict:
        # FIX #14: guard model loading
        if not self._load_model():
            return {
                "error": "No trained model found or model is corrupt. Run /api/ml/train first."
            }

        features = self.config["features"]

        # FIX #5: open a fresh connection instead of reusing the potentially-closed lib conn
        try:
            conn = _open_db("technical")
        except FileNotFoundError as exc:
            return {"error": str(exc)}

        try:
            latest_date = conn.execute(
                "SELECT MAX(date) FROM technical_data"
            ).fetchone()[0]

            if latest_date is None:
                return {"error": "technical_data table is empty."}

            rows = conn.execute(
                f"SELECT symbol, {', '.join(features)} FROM technical_data WHERE date = ?",
                (latest_date,),
            ).fetchall()
        finally:
            conn.close()

        if not rows:
            return {
                "error": f"No data found for the latest date ({latest_date}). "
                "Check that the ETL pipeline has run."
            }

        df = pd.DataFrame(rows, columns=["symbol"] + features)
        for feat in features:
            df[feat] = pd.to_numeric(df[feat], errors="coerce")

        before = len(df)
        df = df.dropna(subset=features).reset_index(drop=True)  # FIX #1: reset index
        dropped = before - len(df)
        if dropped:
            logger.info("predict_today: dropped %d rows with NaN features.", dropped)

        if df.empty:
            return {
                "error": "All rows for today have missing feature values. "
                "Ensure the feature computation pipeline has completed."
            }

        X_pred = df[features]
        # FIX #1: vectorised — no iterrows(), no index misalignment
        predictions = self.model.predict(X_pred)
        probs = self.model.predict_proba(X_pred)
        classes = list(self.model.classes_)

        results = []
        for idx in range(len(df)):
            pred = predictions[idx]
            prob = probs[idx]
            pred_idx = classes.index(pred)
            confidence = round(float(prob[pred_idx]) * 100, 1)
            results.append(
                {
                    "symbol": df.at[idx, "symbol"],
                    "prediction": int(pred),
                    "confidence": confidence,
                    "probabilities": {
                        int(cls): round(float(p) * 100, 1)
                        for cls, p in zip(classes, prob)
                    },
                }
            )

        results.sort(key=lambda x: x["confidence"], reverse=True)
        return {
            "date": latest_date,
            "predictions": results,
            "total_symbols": len(results),
        }

    # ------------------------------------------------------------------
    def get_feature_importance(self) -> list[dict]:
        if not self._load_model():
            return []
        importance = self.model.feature_importances_
        features = self.config["features"]
        result = [
            {"feature": f, "importance": round(float(imp), 4)}
            for f, imp in zip(features, importance)
        ]
        result.sort(key=lambda x: x["importance"], reverse=True)
        return result


# ---------------------------------------------------------------------------
# LaunchpadPredictor
# ---------------------------------------------------------------------------

_LAUNCHPAD_FEATURES = LAUNCHPAD_DEFAULT_CONFIG["features"]  # single source of truth
_LAUNCHPAD_MODEL_PATH = "models/launchpad_xgb.joblib"
_LAUNCHPAD_META_PATH = "models/launchpad_metadata.json"


class LaunchpadPredictor:
    def __init__(self, config: dict = None):
        self.config = self._load_config(config)
        self.classifier: xgb.XGBClassifier | None = None
        self.regressor = None
        self.metadata: dict | None = None

    # ------------------------------------------------------------------
    def _load_config(self, config: dict = None) -> dict:
        config_path = "models/launchpad_trainer_config.json"
        if config:
            return config
        if os.path.exists(config_path):
            with open(config_path) as f:
                return {**LAUNCHPAD_DEFAULT_CONFIG, **json.load(f)}
        return LAUNCHPAD_DEFAULT_CONFIG.copy()

    # ------------------------------------------------------------------
    def _load_models(self) -> bool:
        """Load classifier + regressor. Returns True on success."""
        if self.classifier and self.regressor:
            return True
        if not os.path.exists(_LAUNCHPAD_MODEL_PATH):
            return False
        try:
            import joblib

            data = joblib.load(_LAUNCHPAD_MODEL_PATH)
            self.classifier = data["classifier"]
            self.regressor = data["regressor"]
            return True
        except Exception as exc:
            logger.error("Failed to load launchpad model: %s", exc)
            return False

    # ------------------------------------------------------------------
    def get_status(self) -> dict:
        if not os.path.exists(_LAUNCHPAD_MODEL_PATH):
            return {
                "exists": False,
                "message": "No launchpad model found. Run /api/launchpad/train first.",
            }
        if os.path.exists(_LAUNCHPAD_META_PATH):
            try:
                with open(_LAUNCHPAD_META_PATH) as f:
                    return {"exists": True, **json.load(f)}
            except (json.JSONDecodeError, OSError) as exc:
                return {
                    "exists": True,
                    "message": f"Model present but metadata corrupt ({exc}). Re-train.",
                }
        return {
            "exists": True,
            "message": "Model exists but metadata missing. Re-train.",
        }

    # ------------------------------------------------------------------
    def _get_fundamentals_bulk(self, symbols: list[str]) -> dict[str, dict]:
        """Return {symbol: {market_cap, sector}} for all symbols in one query."""
        result = {s: {"market_cap": None, "sector": None} for s in symbols}
        try:
            val_path = _db_path("valuation")
            if not os.path.exists(val_path):
                return result
            placeholders = ",".join(["?"] * len(symbols))
            with sqlite3.connect(val_path) as conn:
                rows = conn.execute(
                    f"SELECT symbol, COALESCE(market_cap, 0), sector "
                    f"FROM fundamentals WHERE symbol IN ({placeholders})",
                    symbols,
                ).fetchall()
            for sym, mcap, sector in rows:
                result[sym] = {
                    "market_cap": float(mcap) if mcap is not None else None,
                    "sector": sector,
                }
        except Exception as exc:
            logger.warning("Could not load fundamentals: %s", exc)

        # Fallback: use screenercli for symbols still missing market cap
        missing = [s for s, v in result.items() if v.get("market_cap") is None]
        if missing:
            import subprocess, json, re

            logger.info(
                "Fetching market cap via screenercli for %d symbols", len(missing)
            )
            for sym in missing:
                try:
                    proc = subprocess.run(
                        [
                            r"C:\Users\Admin\AppData\Local\Programs\Python\Python312\Scripts\screener.exe",
                            sym,
                            "all",
                        ],
                        capture_output=True,
                        text=True,
                        timeout=15,
                        encoding="utf-8",
                        errors="replace",
                        env={**__import__("os").environ, "PYTHONIOENCODING": "utf-8"},
                    )
                    if proc.returncode == 0:
                        data = json.loads(proc.stdout)
                        km = (
                            data.get("sections", {})
                            .get("pros_cons", {})
                            .get("key_metrics", {})
                        )
                        mcap_str = km.get("Market Cap", "")
                        mcap_num = re.sub(
                            r"[^\d.]", "", mcap_str.replace("Cr", "").replace(",", "")
                        )
                        if mcap_num:
                            result[sym]["market_cap"] = float(mcap_num)
                except Exception as exc:
                    logger.warning("screenercli fallback failed for %s: %s", sym, exc)
        return result

    # ------------------------------------------------------------------
    def extract_features_and_targets(self):
        import pandas as pd, sqlite3, os, time
        from myra_app.constants import DB_DIR
        from myra_app.librarian_core import LibrarianCore

        t0 = time.time()
        conn = sqlite3.connect(os.path.join(DB_DIR, LibrarianCore.DB_MAP["technical"]))

        # Read from the pre‑computed table
        df = pd.read_sql("SELECT * FROM launchpad_features WHERE success = 1", conn)
        conn.close()
        print(
            f"Loaded {len(df)} feature rows from launchpad_features ({time.time()-t0:.1f}s)"
        )

        if len(df) < 50:
            return (
                pd.DataFrame(),
                pd.DataFrame(),
                {
                    "error": "Not enough feature rows. Run extract_launchpad_features.py first."
                },
            )

        features = self.config["features"]
        X = df[features].fillna(0)
        y = df[["return_pct", "days_to_breakout", "success"]]
        print(f"X shape: {X.shape}, y shape: {y.shape}")
        return X, y, {}

    # ------------------------------------------------------------------
    def train(self) -> dict:
        from sklearn.metrics import accuracy_score, mean_squared_error
        from sklearn.multioutput import MultiOutputRegressor
        import joblib

        print("[Launchpad] Extracting features and targets...")
        X, y, meta = self.extract_features_and_targets()

        if X.empty:
            return {"error": meta.get("error", "Insufficient data.")}

        print(f"[Launchpad] Total samples: {len(X)}")
        splits = self.config.get("train_val_test_split", [0.70, 0.15, 0.15])
        train_end = int(len(X) * splits[0])
        val_end = int(len(X) * (splits[0] + splits[1]))

        X_train, X_val, X_test = (
            X.iloc[:train_end],
            X.iloc[train_end:val_end],
            X.iloc[val_end:],
        )
        y_train, y_val, y_test = (
            y.iloc[:train_end],
            y.iloc[train_end:val_end],
            y.iloc[val_end:],
        )
        print(f"[Launchpad] Train:{len(X_train)} Val:{len(X_val)} Test:{len(X_test)}")

        if X_test.empty:
            return {
                "error": f"Not enough samples ({len(X)}) to form a test split. "
                "Collect more labelled launchpad events."
            }

        params = self.config["xgb_params"]
        base_xgb_kwargs = dict(
            n_estimators=params["n_estimators"],
            max_depth=params["max_depth"],
            learning_rate=params["learning_rate"],
            subsample=params["subsample"],
            colsample_bytree=params["colsample_bytree"],
            random_state=params["random_state"],
            verbosity=0,
        )

        self.classifier = xgb.XGBClassifier(**base_xgb_kwargs, eval_metric="logloss")
        self.classifier.fit(X_train, y_train["success"])

        # FIX #2: regressor targets now include days_to_breakout (which exists in y)
        self.regressor = MultiOutputRegressor(xgb.XGBRegressor(**base_xgb_kwargs))
        self.regressor.fit(X_train, y_train[["return_pct", "days_to_breakout"]])

        train_acc = accuracy_score(y_train["success"], self.classifier.predict(X_train))
        test_acc = accuracy_score(y_test["success"], self.classifier.predict(X_test))

        train_pred = self.regressor.predict(X_train)
        test_pred = self.regressor.predict(X_test)
        train_rmse_ret = float(
            np.sqrt(mean_squared_error(y_train["return_pct"], train_pred[:, 0]))
        )
        test_rmse_ret = float(
            np.sqrt(mean_squared_error(y_test["return_pct"], test_pred[:, 0]))
        )
        train_rmse_days = float(
            np.sqrt(mean_squared_error(y_train["days_to_breakout"], train_pred[:, 1]))
        )
        test_rmse_days = float(
            np.sqrt(mean_squared_error(y_test["days_to_breakout"], test_pred[:, 1]))
        )

        os.makedirs("models", exist_ok=True)
        # FIX #3: save as dict so predict_current can unpack correctly
        joblib.dump(
            {"classifier": self.classifier, "regressor": self.regressor},
            _LAUNCHPAD_MODEL_PATH,
        )

        features = self.config["features"]
        importance_cls = dict(zip(features, self.classifier.feature_importances_))
        importance_reg = dict(
            zip(features, self.regressor.estimators_[0].feature_importances_)
        )

        self.metadata = {
            "exists": True,
            "trained_at": datetime.now().isoformat(),
            "train_samples": len(X_train),
            "val_samples": len(X_val),
            "test_samples": len(X_test),
            "train_accuracy": round(float(train_acc), 4),
            "test_accuracy": round(float(test_acc), 4),
            "train_rmse_return": round(train_rmse_ret, 4),
            "test_rmse_return": round(test_rmse_ret, 4),
            "train_rmse_days": round(train_rmse_days, 4),
            "test_rmse_days": round(test_rmse_days, 4),
            "features": features,
            "feature_importance_classifier": importance_cls,
            "feature_importance_regressor": importance_reg,
            "config": self.config,
        }
        with open(_LAUNCHPAD_META_PATH, "w") as f:
            json.dump(self.metadata, f, indent=2)

        print(
            f"[Launchpad] Acc:{test_acc:.4f} RMSE-ret:{test_rmse_ret:.4f} RMSE-days:{test_rmse_days:.4f}"
        )
        return {**self.metadata, "model_saved": True}

    # ------------------------------------------------------------------
    def predict_current(self) -> dict:
        # FIX #3 + #4: load model dict and use classifier + regressor separately
        if not self._load_models():
            return {
                "error": "No trained launchpad model found or model is corrupt. "
                "Run /api/launchpad/train first.",
                "predictions": [],
            }

        try:
            conn = _open_db("technical")
        except FileNotFoundError as exc:
            return {"error": str(exc), "predictions": []}

        try:
            current = pd.read_sql(
                """
                SELECT * FROM launchpad_events
                WHERE success = 0
                  AND trigger_date >= date('now', '-180 days')
                ORDER BY trigger_date DESC
                """,
                conn,
            )
        finally:
            conn.close()

        if current.empty:
            return {
                "message": "No stocks are currently in an active digestion phase.",
                "predictions": [],
            }

        try:
            conn = _open_db("technical")
        except FileNotFoundError as exc:
            return {"error": str(exc), "predictions": []}

        rows = []
        try:
            for _, ev in current.iterrows():
                sym = ev["symbol"]
                trig = ev["trigger_date"]
                feats = conn.execute(
                    """
                    SELECT
                        MIN((td.delivery_pct - td.avg_del) / (NULLIF(td.std_del, 0) + 1e-9)),
                        AVG((td.delivery_pct - td.avg_del) / (NULLIF(td.std_del, 0) + 1e-9)),
                        MIN(td.range_atr_ratio),
                        MIN(td.vol_ratio),
                        COUNT(*),
                        COALESCE(e.max_drawdown_pct, 0),
                        MIN(td.close),
                        MIN(td.vwap),
                        MIN(td.volume),
                        COALESCE(MIN(td.liquidity_distance), 0.0),
                        COALESCE(MIN(td.fvg_freshness), 0.0)
                    FROM launchpad_events e
                    JOIN (
                        SELECT symbol, date, delivery_pct, volume, high, low, close, vwap, liquidity_distance, fvg_freshness,
                            (high - low) / (AVG(high - low) OVER w14 + 1e-9) AS range_atr_ratio,
                            volume / (AVG(volume) OVER w20 + 1e-9) AS vol_ratio,
                            AVG(delivery_pct) OVER w20 AS avg_del,
                            (AVG(delivery_pct * delivery_pct) OVER w20 -
                             AVG(delivery_pct) OVER w20 * AVG(delivery_pct) OVER w20) AS std_del
                        FROM technical_data
                        WINDOW w14 AS (PARTITION BY symbol ORDER BY date ROWS BETWEEN 13 PRECEDING AND CURRENT ROW),
                               w20 AS (PARTITION BY symbol ORDER BY date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW)
                    ) td ON td.symbol = e.symbol
                       AND td.date >= e.trigger_date
                       AND td.date <= date('now')
                    WHERE e.symbol = ? AND e.trigger_date = ?
                    GROUP BY e.symbol, e.trigger_date
                    """,
                    (sym, trig),
                ).fetchone()

                if feats and feats[0] is not None:
                    feature_values = list(feats)
                else:
                    feature_values = [
                        0.0,
                        0.0,
                        1.0,
                        1.0,
                        5,
                        0.0,
                        0.0,
                        0.0,
                        0.0,
                        0.0,
                        0.0,
                    ]

                rows.append(feature_values + [sym, trig])
        finally:
            conn.close()

        feature_cols = self.config["features"]
        X = pd.DataFrame(rows, columns=feature_cols + ["symbol", "trigger_date"])
        X_feat = X[feature_cols]

        # FIX #4: use classifier for probability, regressor for return+days
        cls_probs = self.classifier.predict_proba(X_feat)
        success_class_idx = (
            list(self.classifier.classes_).index(1)
            if 1 in self.classifier.classes_
            else -1
        )
        reg_preds = self.regressor.predict(
            X_feat
        )  # shape (N, 2): [return_pct, days_to_breakout]

        # FIX #17: batch fundamentals lookup
        symbols = X["symbol"].tolist()
        fundamentals = self._get_fundamentals_bulk(symbols)

        results = []
        for idx in range(len(X)):
            breakout_prob = (
                round(float(cls_probs[idx][success_class_idx]), 4)
                if success_class_idx >= 0
                else 0.5
            )
            predicted_return = round(float(reg_preds[idx, 0]), 2)
            predicted_days = round(float(reg_preds[idx, 1]), 1)
            confidence = (
                "High"
                if breakout_prob >= 0.7
                else ("Medium" if breakout_prob >= 0.4 else "Low")
            )

            sym = X.at[idx, "symbol"]
            results.append(
                {
                    "symbol": sym,
                    "trigger_date": X.at[idx, "trigger_date"],
                    "predicted_return_pct": predicted_return,
                    "predicted_days_to_breakout": predicted_days,
                    "current_digestion_days": int(X.at[idx, "digestion_days"]),
                    "breakout_probability": breakout_prob,
                    "confidence": confidence,
                    **fundamentals.get(sym, {"market_cap": None, "sector": None}),
                }
            )

        return {"predictions": results, "total": len(results)}

    # ------------------------------------------------------------------
    def get_feature_importance(self) -> list[dict] | dict:
        if not self._load_models():
            return {
                "error": "No trained launchpad model found. Run /api/launchpad/train first."
            }

        features = self.config["features"]
        cls_imp = dict(zip(features, self.classifier.feature_importances_))
        reg_imp = dict(
            zip(features, self.regressor.estimators_[0].feature_importances_)
        )

        result = [
            {
                "feature": f,
                "importance_classifier": round(float(cls_imp.get(f, 0)), 4),
                "importance_regressor_return": round(float(reg_imp.get(f, 0)), 4),
            }
            for f in features
        ]
        result.sort(key=lambda x: x["importance_classifier"], reverse=True)
        return result


# ---------------------------------------------------------------------------
# FactorDiscovery
# ---------------------------------------------------------------------------


class FactorDiscovery:
    def __init__(self, config: dict = None):
        self.config = config or {}
        self.features = self.config.get(
            "features",
            [
                "delivery_pct",
                "delivery",
                "volume",
                "close",
                "bullish_fvg",
                "bearish_fvg",
                "has_bullish_fvg",
                "fvg_freshness",
                "swing_high",
                "swing_low",
                "liquidity_distance",
                "delivery_divergence_score",
                "volatility_compression_score",
                "relative_volume_score",
                "nifty_outperformance_score",
                "stock_return",
                "vwap",
                "delivery_ratio",
            ],
        )

    # ------------------------------------------------------------------
    def discover_factors(
        self, target: str = "forward_return_5d", top_n: int = 20
    ) -> dict:
        # FIX #9: always close conn, even on early return
        try:
            conn = _open_db("technical")
        except FileNotFoundError as exc:
            return {"error": str(exc)}

        try:
            avail = self._get_available_columns(conn)
            feat_cols = [f for f in self.features if f in avail]

            if not feat_cols:
                return {
                    "error": "None of the configured features exist in technical_data. "
                    "Check your feature list and schema."
                }

            missing = [f for f in self.features if f not in avail]
            if missing:
                logger.warning(
                    "FactorDiscovery: %d features missing from DB: %s",
                    len(missing),
                    missing,
                )

            cols = ", ".join([f"MAX({f}) as {f}" for f in feat_cols])
            df = pd.read_sql(
                f"SELECT symbol, {cols} FROM technical_data GROUP BY symbol", conn
            )
            close_ts = pd.read_sql(
                "SELECT symbol, date, close FROM technical_data ORDER BY symbol, date",
                conn,
            )
        finally:
            conn.close()  # FIX #9: always closed now

        close_ts["close_fwd"] = close_ts.groupby("symbol")["close"].shift(-5)
        close_ts["forward_return_5d"] = (
            close_ts["close_fwd"] / close_ts["close"] - 1
        ) * 100

        valid = close_ts.dropna(subset=["forward_return_5d"])
        if valid.empty:
            return {
                "error": "Could not compute any forward returns. "
                "Ensure at least 5 future trading days of data are present."
            }

        latest = valid.groupby("symbol")["date"].max().reset_index()
        target_df = latest.merge(
            valid[["symbol", "date", "forward_return_5d"]], on=["symbol", "date"]
        )
        df = df.merge(
            target_df[["symbol", "forward_return_5d"]], on="symbol", how="inner"
        )
        df = df.dropna(subset=["forward_return_5d"])

        if len(df) < 50:
            return {
                "error": f"Only {len(df)} symbols have sufficient data (need ≥50). "
                "Ingest more historical data before running factor discovery."
            }

        X = df[feat_cols].fillna(0)
        y = df["forward_return_5d"]

        model = xgb.XGBRegressor(
            n_estimators=100, max_depth=4, learning_rate=0.05, random_state=42
        )
        model.fit(X, y)

        importance = model.feature_importances_
        ranked = sorted(zip(feat_cols, importance), key=lambda x: x[1], reverse=True)[
            :top_n
        ]

        categories = {
            "Delivery": [
                "delivery_pct",
                "delivery",
                "delivery_ratio",
                "delivery_divergence_score",
            ],
            "SMC": [
                "bullish_fvg",
                "bearish_fvg",
                "has_bullish_fvg",
                "fvg_freshness",
                "swing_high",
                "swing_low",
                "liquidity_distance",
            ],
            "Price": ["close", "vwap", "stock_return"],
            "Volume": [
                "volume",
                "relative_volume_score",
                "volatility_compression_score",
            ],
            "Market": ["nifty_outperformance_score"],
        }

        by_category: dict[str, list] = {cat: [] for cat in categories}
        for name, imp in ranked:
            for cat, cols_list in categories.items():
                if name in cols_list:
                    by_category[cat].append(
                        {"feature": name, "importance": round(float(imp), 4)}
                    )
                    break

        return {
            "top_features": [
                {"feature": n, "importance": round(float(i), 4)} for n, i in ranked
            ],
            "by_category": {k: v for k, v in by_category.items() if v},
            "missing_features": missing,
            "trained_at": pd.Timestamp.now().isoformat(),
        }

    # ------------------------------------------------------------------
    def _get_available_columns(self, conn: sqlite3.Connection) -> list[str]:
        cols = conn.execute("PRAGMA table_info(technical_data)").fetchall()
        return [c[1] for c in cols]


# ---------------------------------------------------------------------------

if __name__ == "__main__":
    t = MLTrainer()
    print(t.get_status())
