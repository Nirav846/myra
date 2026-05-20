import json
import os
from datetime import datetime

import numpy as np
import pandas as pd
import xgboost as xgb
from myra_app.constants import DB_DIR
from myra_app.librarian import Librarian
from myra_app.librarian_core import LibrarianCore

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


class MLTrainer:
    def __init__(self, config: dict = None):
        self.config = self._load_config(config)
        self.lib = Librarian()
        self.model = None
        self.metadata = None

    def _load_config(self, config: dict = None):
        config_path = "models/ml_config.json"
        if config:
            return config
        if os.path.exists(config_path):
            with open(config_path) as f:
                return json.load(f)
        return DEFAULT_CONFIG.copy()

    def get_status(self):
        model_path = "models/forward_return.xgb"
        metadata_path = "models/model_metadata.json"

        if not os.path.exists(model_path):
            return {
                "exists": False,
                "message": "No trained model found. Run /api/ml/train to train a model.",
            }

        if os.path.exists(metadata_path):
            with open(metadata_path) as f:
                meta = json.load(f)
            return {
                "exists": True,
                "trained_at": meta.get("trained_at"),
                "train_samples": meta.get("train_samples"),
                "test_samples": meta.get("test_samples"),
                "train_accuracy": meta.get("train_accuracy"),
                "test_accuracy": meta.get("test_accuracy"),
                "features": meta.get("features"),
            }

        return {
            "exists": True,
            "message": "Model exists but no metadata. Re-train to generate metadata.",
        }

    def extract_features_and_targets(self):
        import pandas as pd
        import numpy as np
        import time
        import os
        import sqlite3
        from myra_app.constants import DB_DIR
        from myra_app.librarian_core import LibrarianCore

        t0 = time.time()
        min_samples = self.config["min_samples_per_symbol"]
        lookback = self.config["lookback_days"]
        forward = self.config["forward_days"]
        features = self.config["features"]

        tech_db = os.path.join(DB_DIR, LibrarianCore.DB_MAP["technical"])
        conn = sqlite3.connect(tech_db)

        syms = pd.read_sql(
            "SELECT symbol FROM technical_data "
            "WHERE date >= date('now', ?) "
            "GROUP BY symbol HAVING COUNT(*) >= ?",
            conn,
            params=(f"-{lookback} days", min_samples),
        )["symbol"].tolist()
        print(f"Step 1: {len(syms)} qualifying symbols ({time.time()-t0:.1f}s)")

        if len(syms) < 10:
            return pd.DataFrame(), pd.Series(), {}

        placeholders = ",".join(["?"] * len(syms))
        query = f"""
            SELECT symbol, date, {','.join(features)}
            FROM technical_data
            WHERE symbol IN ({placeholders})
              AND date >= date('now', ?)
            ORDER BY symbol, date
        """
        params = syms + [f"-{lookback} days"]
        df = pd.read_sql(query, conn, params=params)
        conn.close()
        print(f"Step 2: {df.shape[0]} rows loaded ({time.time()-t0:.1f}s)")

        df["forward_close"] = df.groupby("symbol")["close"].shift(-forward)
        df["forward_return"] = (df["forward_close"] / df["close"] - 1) * 100
        df.dropna(subset=["forward_return"], inplace=True)

        df["target"] = pd.qcut(df["forward_return"], q=3, labels=False)
        print(f"Step 3: target created ({time.time()-t0:.1f}s)")

        X = df[features].select_dtypes(include=[np.number]).fillna(0)
        y = df["target"]
        meta = {"symbols": df["symbol"], "dates": df["date"]}
        print(
            f"Step 4: final X shape {X.shape}, classes {y.nunique()} ({time.time()-t0:.1f}s)"
        )
        return X, y, meta

    def train(self):
        print("[ML] Extracting features and targets...")
        X, y, meta = self.extract_features_and_targets()

        if X.empty:
            return {
                "error": "Insufficient data for training. Need more symbols with sufficient history."
            }

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
            use_label_encoder=False,
            eval_metric="mlogloss",
            verbosity=0,
        )

        self.model.fit(X_train, y_train)

        train_acc = self.model.score(X_train, y_train)
        test_acc = self.model.score(X_test, y_test)

        importance = self.get_feature_importance()

        os.makedirs("models", exist_ok=True)
        self.model.save_model("models/forward_return.xgb")

        import gc

        gc.collect()

        self.metadata = {
            "trained_at": datetime.now().isoformat(),
            "train_samples": len(X_train),
            "test_samples": len(X_test),
            "train_accuracy": round(train_acc, 4),
            "test_accuracy": round(test_acc, 4),
            "features": self.config["features"],
            "config": self.config,
        }

        with open("models/model_metadata.json", "w") as f:
            json.dump(self.metadata, f, indent=2)

        print(
            f"[ML] Model trained. Train Acc: {train_acc:.4f}, Test Acc: {test_acc:.4f}"
        )

        fi = self.config["features"]
        imp = self.model.feature_importances_

        if hasattr(self, "lib") and self.lib:
            self.lib._tech_conn.close()
            self.lib._meta_conn.close()
            self.lib._inst_conn.close()
            self.lib._gov_conn.close()

        return {
            "train_accuracy": float(train_acc),
            "test_accuracy": float(test_acc),
            "feature_importance": [
                {"feature": fi[i], "importance": float(imp[i])} for i in range(len(fi))
            ],
            "train_samples": int(len(X_train)),
            "test_samples": int(len(X_test)),
            "model_saved": True,
        }

    def predict_today(self):
        model_path = "models/forward_return.xgb"
        if not os.path.exists(model_path):
            return {"error": "No trained model found. Run /api/ml/train first."}

        if not self.model:
            self.model = xgb.XGBClassifier()
            self.model.load_model(model_path)

        features = self.config["features"]
        conn = self.lib._tech_conn

        latest_date = conn.execute("SELECT MAX(date) FROM technical_data").fetchone()[0]

        rows = conn.execute(
            f"""
            SELECT symbol, {', '.join(features)}
            FROM technical_data
            WHERE date = ?
            """,
            (latest_date,),
        ).fetchall()

        if not rows:
            return {"error": "No data for latest date."}

        df = pd.DataFrame(rows, columns=["symbol"] + features)

        for feat in features:
            df[feat] = pd.to_numeric(df[feat], errors="coerce")

        df = df.dropna(subset=features)

        if len(df) == 0:
            return {"error": "No valid data for prediction."}

        X_pred = df[features]

        predictions = self.model.predict(X_pred)
        probs = self.model.predict_proba(X_pred)

        results = []
        for i, row in df.iterrows():
            pred = predictions[i]
            prob = probs[i]
            pred_idx = list(self.model.classes_).index(pred)
            confidence = round(prob[pred_idx] * 100, 1)

            results.append(
                {
                    "symbol": row["symbol"],
                    "prediction": pred,
                    "confidence": confidence,
                    "probabilities": {
                        cls: round(p * 100, 1)
                        for cls, p in zip(self.model.classes_, prob)
                    },
                }
            )

        results.sort(key=lambda x: x["confidence"], reverse=True)

        return {
            "date": latest_date,
            "predictions": results,
            "total_symbols": len(results),
        }

    def get_feature_importance(self):
        if not self.model:
            model_path = "models/forward_return.xgb"
            if os.path.exists(model_path):
                self.model = xgb.XGBClassifier()
                self.model.load_model(model_path)
            else:
                return []

        if not self.model:
            return []

        importance = self.model.feature_importances_
        features = self.config["features"]

        result = [
            {"feature": f, "importance": round(imp, 4)}
            for f, imp in zip(features, importance)
        ]
        result.sort(key=lambda x: x["importance"], reverse=True)
        return result


LAUNCHPAD_DEFAULT_CONFIG = {
    "lookback_days": 120,
    "features": [
        "delivery_zscore_trigger",
        "delivery_zscore_min_digestion",
        "delivery_pct_avg_digestion",
        "range_atr_ratio_min",
        "vol_ratio_min",
        "max_drawdown_pct",
        "days_since_trigger",
        "close_location_avg",
        "nifty_return_digestion",
        "sector_relative_strength",
        "fti_trigger",
        "fti_avg_digestion",
        "free_float_mcap",
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


class LaunchpadPredictor:
    def __init__(self, config: dict = None):
        self.config = self._load_config(config)
        self.lib = Librarian()
        self.classifier = None
        self.regressor = None
        self.metadata = None

    def _load_config(self, config: dict = None):
        config_path = "models/launchpad_trainer_config.json"
        if config:
            return config
        if os.path.exists(config_path):
            with open(config_path) as f:
                return {**LAUNCHPAD_DEFAULT_CONFIG, **json.load(f)}
        return LAUNCHPAD_DEFAULT_CONFIG.copy()

    def _get_fundamentals_for_symbol(self, symbol: str):
        try:
            val_db = os.path.join(DB_DIR, LibrarianCore.DB_MAP["valuation"])
            if not os.path.exists(val_db):
                return {}
            with sqlite3.connect(val_db) as conn:
                row = conn.execute(
                    "SELECT market_cap, sector FROM fundamentals WHERE symbol = ?",
                    (symbol,),
                ).fetchone()
                if row:
                    return {"market_cap": row[0], "sector": row[1]}
        except Exception:
            pass
        return {}

    def _get_promoter_pct(self, symbol: str):
        try:
            inst_db = os.path.join(DB_DIR, LibrarianCore.DB_MAP["institutional"])
            if not os.path.exists(inst_db):
                return 50.0
            with sqlite3.connect(inst_db) as conn:
                row = conn.execute(
                    "SELECT promoter_pct FROM fii_dii_history WHERE symbol = ? ORDER BY date DESC LIMIT 1",
                    (symbol,),
                ).fetchone()
                if row and row[0] is not None:
                    return float(row[0])
        except Exception:
            pass
        return 50.0

    def _compute_fti_features(
        self, df_tech: pd.DataFrame, symbol: str, trig_date: str, end_date: str
    ):
        funda = self._get_fundamentals_for_symbol(symbol)
        mcap = funda.get("market_cap")
        promoter_pct = self._get_promoter_pct(symbol)

        if mcap and mcap > 0:
            free_float_mcap = mcap * (1.0 - promoter_pct / 100.0)
        else:
            free_float_mcap = 0.0

        trig_row = df_tech[df_tech["date"] == trig_date]
        if not trig_row.empty:
            r = trig_row.iloc[0]
            del_qty = r.get("delivery", 0) or 0
            price = r.get("vwap", r.get("close", 0)) or 0
            delivery_value_trigger = del_qty * price
        else:
            delivery_value_trigger = 0.0

        digest_rows = df_tech[df_tech["date"] > trig_date]
        if not digest_rows.empty and "delivery" in digest_rows.columns:
            digest_del = digest_rows["delivery"].fillna(0)
            digest_price = digest_rows.get(
                "vwap", digest_rows.get("close", pd.Series([0] * len(digest_rows)))
            ).fillna(0)
            if isinstance(digest_price, pd.DataFrame):
                digest_price = digest_price.iloc[:, 0]
            digest_values = digest_del * digest_price
            avg_delivery_value_digestion = float(digest_values.mean())
        else:
            avg_delivery_value_digestion = 0.0

        if free_float_mcap > 0:
            fti_trigger = delivery_value_trigger / free_float_mcap * 100
            fti_avg_digestion = avg_delivery_value_digestion / free_float_mcap * 100
        else:
            fti_trigger = 0.0
            fti_avg_digestion = 0.0

        return {
            "fti_trigger": round(fti_trigger, 6),
            "fti_avg_digestion": round(fti_avg_digestion, 6),
            "free_float_mcap": round(free_float_mcap, 2),
        }

    def extract_features_and_targets(self):
        import pandas as pd
        import numpy as np
        import time

        conn = self.lib._tech_conn

        t0 = time.time()
        events = pd.read_sql("SELECT * FROM launchpad_events", conn)
        if events.empty:
            return pd.DataFrame(), pd.DataFrame(), {}

        # Get unique symbols that have at least one event
        symbols = events["symbol"].unique().tolist()
        placeholders = ",".join(["?"] * len(symbols))

        # Load ALL technical data for those symbols in one query
        tech = pd.read_sql(
            f"SELECT * FROM technical_data WHERE symbol IN ({placeholders}) ORDER BY symbol, date",
            conn,
            params=symbols,
        )
        print(
            f"Step 1: {len(tech)} rows loaded for {len(symbols)} symbols ({time.time()-t0:.1f}s)"
        )

        # Build features per event by slicing into the pre‑loaded DataFrame
        rows = []
        for _, ev in events.iterrows():
            sym = ev["symbol"]
            trig_date = ev["trigger_date"]
            bdate = ev.get("breakout_date")
            digest_low_date = ev.get("digestion_low_date")

            if not bdate or pd.isna(bdate):
                continue  # skip failure events (no breakout)

            # Slice the pre‑loaded DataFrame for this symbol + date range
            mask = (
                (tech["symbol"] == sym)
                & (tech["date"] >= trig_date)
                & (tech["date"] <= bdate)
            )
            df = tech.loc[mask].copy()
            if len(df) < 10:
                continue

            df = df.sort_values("date")
            df["del_zscore"] = (
                df["delivery_pct"]
                - df["delivery_pct"].rolling(20, min_periods=10).mean()
            ) / (df["delivery_pct"].rolling(20, min_periods=10).std() + 1e-9)
            df["range_atr_ratio"] = (df["high"] - df["low"]) / (
                (df["high"] - df["low"]).rolling(14, min_periods=5).mean() + 1e-9
            )
            df["vol_ratio"] = df["volume"] / (
                df["volume"].rolling(20, min_periods=10).mean() + 1e-9
            )

            features = {
                "del_zscore_min": df["del_zscore"].min(),
                "del_zscore_mean": df["del_zscore"].mean(),
                "range_atr_min": df["range_atr_ratio"].min(),
                "vol_ratio_min": df["vol_ratio"].min(),
                "digestion_days": len(df),
                "max_drawdown_pct": ev.get("max_drawdown_pct", 0),
                "return_pct": ev.get("return_pct", 0),
                "success": ev.get("success", 0),
            }
            rows.append(features)

        X = pd.DataFrame(rows)
        y = X[["return_pct", "success"]].copy()
        X = X.drop(columns=["return_pct", "success"])
        print(f"Step 2: {len(X)} feature rows extracted ({time.time()-t0:.1f}s)")
        return X, y, {}

    def train(self):
        from sklearn.metrics import accuracy_score, mean_squared_error
        from sklearn.multioutput import MultiOutputRegressor

        print("[Launchpad] Extracting features and targets...")
        X, y, meta = self.extract_features_and_targets()

        if X.empty:
            return {
                "error": "Insufficient data for training. Run launchpad labelling first."
            }

        print(f"[Launchpad] Total samples: {len(X)}")

        splits = self.config.get("train_val_test_split", [0.70, 0.15, 0.15])
        train_end = int(len(X) * splits[0])
        val_end = int(len(X) * (splits[0] + splits[1]))

        X_train = X.iloc[:train_end]
        X_val = X.iloc[train_end:val_end]
        X_test = X.iloc[val_end:]
        y_train = y.iloc[:train_end]
        y_val = y.iloc[train_end:val_end]
        y_test = y.iloc[val_end:]

        print(
            f"[Launchpad] Train: {len(X_train)}, Val: {len(X_val)}, Test: {len(X_test)}"
        )

        params = self.config["xgb_params"]

        self.classifier = xgb.XGBClassifier(
            n_estimators=params["n_estimators"],
            max_depth=params["max_depth"],
            learning_rate=params["learning_rate"],
            subsample=params["subsample"],
            colsample_bytree=params["colsample_bytree"],
            random_state=params["random_state"],
            use_label_encoder=False,
            eval_metric="logloss",
            verbosity=0,
        )
        self.classifier.fit(X_train, y_train["success"])

        self.regressor = MultiOutputRegressor(
            xgb.XGBRegressor(
                n_estimators=params["n_estimators"],
                max_depth=params["max_depth"],
                learning_rate=params["learning_rate"],
                subsample=params["subsample"],
                colsample_bytree=params["colsample_bytree"],
                random_state=params["random_state"],
                verbosity=0,
            )
        )
        self.regressor.fit(X_train, y_train[["return_pct", "days_to_breakout"]])

        train_acc = accuracy_score(y_train["success"], self.classifier.predict(X_train))
        test_acc = accuracy_score(y_test["success"], self.classifier.predict(X_test))

        train_pred_ret = self.regressor.predict(X_train)[:, 0]
        test_pred_ret = self.regressor.predict(X_test)[:, 0]
        train_rmse_ret = float(
            np.sqrt(mean_squared_error(y_train["return_pct"], train_pred_ret))
        )
        test_rmse_ret = float(
            np.sqrt(mean_squared_error(y_test["return_pct"], test_pred_ret))
        )

        train_pred_days = self.regressor.predict(X_train)[:, 1]
        test_pred_days = self.regressor.predict(X_test)[:, 1]
        train_rmse_days = float(
            np.sqrt(mean_squared_error(y_train["days_to_breakout"], train_pred_days))
        )
        test_rmse_days = float(
            np.sqrt(mean_squared_error(y_test["days_to_breakout"], test_pred_days))
        )

        os.makedirs("models", exist_ok=True)
        import joblib

        joblib.dump(
            {"classifier": self.classifier, "regressor": self.regressor},
            "models/launchpad_xgb.joblib",
        )

        importance_cls = dict(
            zip(self.config["features"], self.classifier.feature_importances_)
        )
        importance_reg = dict(
            zip(
                self.config["features"],
                self.regressor.estimators_[0].feature_importances_.tolist(),
            )
        )

        self.metadata = {
            "exists": True,
            "trained_at": datetime.now().isoformat(),
            "train_samples": int(len(X_train)),
            "val_samples": int(len(X_val)),
            "test_samples": int(len(X_test)),
            "train_accuracy": round(train_acc, 4),
            "test_accuracy": round(test_acc, 4),
            "train_rmse_return": round(train_rmse_ret, 4),
            "test_rmse_return": round(test_rmse_ret, 4),
            "train_rmse_days": round(train_rmse_days, 4),
            "test_rmse_days": round(test_rmse_days, 4),
            "features": self.config["features"],
            "feature_importance_classifier": importance_cls,
            "feature_importance_regressor": importance_reg,
            "config": self.config,
        }

        with open("models/launchpad_metadata.json", "w") as f:
            json.dump(self.metadata, f, indent=2)

        print(
            f"[Launchpad] Model trained. Acc: {test_acc:.4f}, RMSE ret: {test_rmse_ret:.4f}, RMSE days: {test_rmse_days:.4f}"
        )

        return {
            "train_accuracy": float(train_acc),
            "test_accuracy": float(test_acc),
            "train_rmse_return": train_rmse_ret,
            "test_rmse_return": test_rmse_ret,
            "train_rmse_days": train_rmse_days,
            "test_rmse_days": test_rmse_days,
            "feature_importance": [
                {"feature": k, "importance": round(v, 4)}
                for k, v in importance_cls.items()
            ],
            "train_samples": int(len(X_train)),
            "test_samples": int(len(X_test)),
            "model_saved": True,
        }

    def predict_current(self):
        import pandas as pd
        import numpy as np
        import joblib
        import os
        import sqlite3

        conn = self.lib._tech_conn

        # Find stocks currently in digestion (trigger exists, no breakout yet)
        current = pd.read_sql(
            """
            SELECT * FROM launchpad_events
            WHERE success = 0
              AND trigger_date >= date('now', '-180 days')
            ORDER BY trigger_date DESC
        """,
            conn,
        )

        if current.empty:
            return []

        model = joblib.load("models/launchpad_xgb.joblib")
        rows = []

        for _, ev in current.iterrows():
            sym = ev["symbol"]
            trig = ev["trigger_date"]

            # Compute digestion features from trigger to today
            feats = conn.execute(
                """
                SELECT
                    MIN((td.delivery_pct - td.avg_del) / (NULLIF(td.std_del, 0) + 1e-9)) AS del_zscore_min,
                    AVG((td.delivery_pct - td.avg_del) / (NULLIF(td.std_del, 0) + 1e-9)) AS del_zscore_mean,
                    MIN(td.range_atr_ratio) AS range_atr_min,
                    MIN(td.vol_ratio) AS vol_ratio_min,
                    COUNT(*) AS digestion_days,
                    COALESCE(e.max_drawdown_pct, 0) AS max_drawdown_pct
                FROM launchpad_events e
                JOIN (
                    SELECT symbol, date, delivery_pct, volume, high, low,
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
                # Not enough digestion data yet — fill with neutral values
                feature_values = [0.0, 0.0, 1.0, 1.0, 5, 0.0]

            rows.append(feature_values + [sym, trig])

        X = pd.DataFrame(
            rows,
            columns=[
                "del_zscore_min",
                "del_zscore_mean",
                "range_atr_min",
                "vol_ratio_min",
                "digestion_days",
                "max_drawdown_pct",
                "symbol",
                "trigger_date",
            ],
        )

        preds = model.predict(X.drop(columns=["symbol", "trigger_date"]))

        val_db = os.path.join(DB_DIR, LibrarianCore.DB_MAP["valuation"])
        fundamentals_cache = {}
        if os.path.exists(val_db):
            with sqlite3.connect(val_db) as val_conn:
                for _, row in X.iterrows():
                    sym = row["symbol"]
                    funda_row = val_conn.execute(
                        "SELECT COALESCE(marketCap, market_cap) AS market_cap, sector FROM fundamentals WHERE symbol = ? LIMIT 1",
                        (sym,),
                    ).fetchone()
                    if funda_row:
                        fundamentals_cache[sym] = {
                            "market_cap": (
                                float(funda_row[0])
                                if funda_row[0] is not None
                                else None
                            ),
                            "sector": funda_row[1],
                        }
                    else:
                        fundamentals_cache[sym] = {"market_cap": None, "sector": None}

        results = []
        for i, row in X.iterrows():
            predicted_return_pct = round(float(preds[i, 0]), 2)
            breakout_probability = round(
                1 / (1 + np.exp(-predicted_return_pct / 10)), 4
            )
            if breakout_probability >= 0.7:
                confidence = "High"
            elif breakout_probability >= 0.4:
                confidence = "Medium"
            else:
                confidence = "Low"

            sym = row["symbol"]
            funda = fundamentals_cache.get(sym, {"market_cap": None, "sector": None})

            results.append(
                {
                    "symbol": sym,
                    "trigger_date": row["trigger_date"],
                    "predicted_return_pct": predicted_return_pct,
                    "predicted_days_to_breakout": round(float(preds[i, 1]), 1),
                    "current_digestion_days": int(row["digestion_days"]),
                    "sector": funda["sector"],
                    "market_cap": funda["market_cap"],
                    "breakout_probability": breakout_probability,
                    "confidence": confidence,
                }
            )

        return results

    def get_feature_importance(self):
        import joblib

        model_path = "models/launchpad_xgb.joblib"
        if not os.path.exists(model_path):
            return {"error": "No trained launchpad model found."}

        model_data = joblib.load(model_path)
        classifier = model_data["classifier"]
        regressor = model_data["regressor"]

        features = self.config["features"]
        importance_cls = [
            {"feature": f, "importance_classifier": round(v, 4)}
            for f, v in zip(features, classifier.feature_importances_)
        ]
        importance_reg = [
            {"feature": f, "importance_regressor_return": round(v, 4)}
            for f, v in zip(features, regressor.estimators_[0].feature_importances_)
        ]

        combined = {}
        for item in importance_cls:
            combined[item["feature"]] = item
        for item in importance_reg:
            if item["feature"] in combined:
                combined[item["feature"]].update(item)

        result = list(combined.values())
        result.sort(key=lambda x: x.get("importance_classifier", 0), reverse=True)
        return result


if __name__ == "__main__":
    t = MLTrainer()
    print(t.get_status())
