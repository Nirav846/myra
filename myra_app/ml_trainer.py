import json
import os
from datetime import datetime

import numpy as np
import pandas as pd
import xgboost as xgb
from myra_app.librarian import Librarian


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
            conn, params=(f"-{lookback} days", min_samples)
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
        print(f"Step 4: final X shape {X.shape}, classes {y.nunique()} ({time.time()-t0:.1f}s)")
        return X, y, meta

    def train(self):
        print("[ML] Extracting features and targets...")
        X, y, meta = self.extract_features_and_targets()

        if X.empty:
            return {"error": "Insufficient data for training. Need more symbols with sufficient history."}

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

        print(f"[ML] Model trained. Train Acc: {train_acc:.4f}, Test Acc: {test_acc:.4f}")

        fi = self.config["features"]
        imp = self.model.feature_importances_

        if hasattr(self, 'lib') and self.lib:
            self.lib._tech_conn.close()
            self.lib._meta_conn.close()
            self.lib._inst_conn.close()
            self.lib._gov_conn.close()

        return {
            "train_accuracy": float(train_acc),
            "test_accuracy": float(test_acc),
            "feature_importance": [
                {"feature": fi[i], "importance": float(imp[i])}
                for i in range(len(fi))
            ],
            "train_samples": int(len(X_train)),
            "test_samples": int(len(X_test)),
            "model_saved": True
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
            """
            ,
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

            results.append({
                "symbol": row["symbol"],
                "prediction": pred,
                "confidence": confidence,
                "probabilities": {
                    cls: round(p * 100, 1) for cls, p in zip(self.model.classes_, prob)
                }
            })

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

    def extract_features_and_targets(self):
        conn = self.lib._tech_conn
        events = pd.read_sql("SELECT * FROM launchpad_events", conn)
        if events.empty:
            return pd.DataFrame(), pd.DataFrame(), {}

        features = self.config["features"]
        lookback = self.config["lookback_days"]

        rows_list = []
        for _, ev in events.iterrows():
            sym = ev["symbol"]
            trig_date = ev["trigger_date"]
            bdate = ev["breakout_date"]
            digest_low_date = ev["digestion_low_date"]

            query = f"""
                SELECT * FROM technical_data
                WHERE symbol = ?
                  AND date >= ?
                  AND date <= ?
                ORDER BY date
            """
            start_search = trig_date
            end_search = bdate
            try:
                df = pd.read_sql(query, conn, params=(sym, start_search, end_search))
            except Exception:
                continue

            if df.empty or len(df) < 5:
                continue

            df["del_zscore"] = (
                (df["delivery_pct"] - df["delivery_pct"].rolling(20, min_periods=10).mean())
                / (df["delivery_pct"].rolling(20, min_periods=10).std() + 1e-9)
            )
            high_low = df["high"] - df["low"]
            high_close = (df["high"] - df["close"].shift(1)).abs()
            low_close = (df["low"] - df["close"].shift(1)).abs()
            tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
            df["atr"] = tr.rolling(14, min_periods=5).mean()
            df["vol_avg"] = df["volume"].rolling(20, min_periods=10).mean()

            try:
                trig_zscore = float(df["del_zscore"].max())
            except Exception:
                trig_zscore = 0.0

            try:
                digest_zscore_min = float(df["del_zscore"].min())
            except Exception:
                digest_zscore_min = 0.0

            try:
                del_pct_avg = float(df["delivery_pct"].mean())
            except Exception:
                del_pct_avg = 0.0

            try:
                day_range = df["high"] - df["low"]
                rr = day_range / (df["atr"] + 1e-9)
                range_atr_min = float(rr.min())
            except Exception:
                range_atr_min = 0.0

            try:
                vr = df["volume"] / (df["vol_avg"] + 1e-9)
                vol_ratio_min = float(vr.min())
            except Exception:
                vol_ratio_min = 0.0

            max_dd = float(ev.get("max_drawdown_pct", 0.0))
            days_since = int(ev.get("days_to_breakout", 0))

            try:
                close_loc = (df["close"] - df["low"]) / (df["high"] - df["low"] + 1e-9)
                close_location_avg = float(close_loc.mean())
            except Exception:
                close_location_avg = 0.5

            try:
                nifty_rows = pd.read_sql(
                    "SELECT date, close FROM technical_data WHERE symbol = 'NIFTY' AND date >= ? AND date <= ? ORDER BY date",
                    conn,
                    params=(start_search, end_search),
                )
                if not nifty_rows.empty:
                    nifty_ret = (nifty_rows["close"].iloc[-1] / nifty_rows["close"].iloc[0] - 1) * 100
                else:
                    nifty_ret = 0.0
            except Exception:
                nifty_ret = 0.0

            try:
                sector_query = """
                    SELECT AVG(stock_return) as avg_sector_ret
                    FROM technical_data
                    WHERE date = ?
                      AND symbol IN (
                          SELECT symbol FROM technical_data
                          WHERE date = ?
                            AND symbol != ?
                            AND stock_return IS NOT NULL
                      )
                """
                sector_row = pd.read_sql(
                    "SELECT stock_return FROM technical_data WHERE symbol = ? AND date = ? ORDER BY date DESC LIMIT 1",
                    conn,
                    params=(sym, end_search),
                )
                if not sector_row.empty:
                    stock_ret = float(sector_row["stock_return"].iloc[0])
                else:
                    stock_ret = 0.0
                sector_relative_strength = stock_ret - nifty_ret
            except Exception:
                sector_relative_strength = 0.0

            row_data = {
                "delivery_zscore_trigger": trig_zscore,
                "delivery_zscore_min_digestion": digest_zscore_min,
                "delivery_pct_avg_digestion": del_pct_avg,
                "range_atr_ratio_min": range_atr_min,
                "vol_ratio_min": vol_ratio_min,
                "max_drawdown_pct": max_dd,
                "days_since_trigger": days_since,
                "close_location_avg": close_location_avg,
                "nifty_return_digestion": nifty_ret,
                "sector_relative_strength": sector_relative_strength,
            }
            rows_list.append(row_data)

        if not rows_list:
            return pd.DataFrame(), pd.DataFrame(), {}

        X = pd.DataFrame(rows_list)
        y = pd.DataFrame({
            "success": events["success"].values[:len(X)],
            "return_pct": events["return_pct"].values[:len(X)],
            "days_to_breakout": events["days_to_breakout"].values[:len(X)],
        })
        meta = {
            "symbols": events["symbol"].values[:len(X)],
            "trigger_dates": events["trigger_date"].values[:len(X)],
        }
        return X, y, meta

    def train(self):
        from sklearn.metrics import accuracy_score, mean_squared_error
        from sklearn.multioutput import MultiOutputRegressor

        print("[Launchpad] Extracting features and targets...")
        X, y, meta = self.extract_features_and_targets()

        if X.empty:
            return {"error": "Insufficient data for training. Run launchpad labelling first."}

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

        print(f"[Launchpad] Train: {len(X_train)}, Val: {len(X_val)}, Test: {len(X_test)}")

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
        train_rmse_ret = float(np.sqrt(mean_squared_error(y_train["return_pct"], train_pred_ret)))
        test_rmse_ret = float(np.sqrt(mean_squared_error(y_test["return_pct"], test_pred_ret)))

        train_pred_days = self.regressor.predict(X_train)[:, 1]
        test_pred_days = self.regressor.predict(X_test)[:, 1]
        train_rmse_days = float(np.sqrt(mean_squared_error(y_train["days_to_breakout"], train_pred_days)))
        test_rmse_days = float(np.sqrt(mean_squared_error(y_test["days_to_breakout"], test_pred_days)))

        os.makedirs("models", exist_ok=True)
        import joblib

        joblib.dump(
            {"classifier": self.classifier, "regressor": self.regressor},
            "models/launchpad_xgb.joblib",
        )

        importance_cls = dict(zip(self.config["features"], self.classifier.feature_importances_))
        importance_reg = dict(
            zip(self.config["features"], self.regressor.estimators_[0].feature_importances_.tolist())
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

        print(f"[Launchpad] Model trained. Acc: {test_acc:.4f}, RMSE ret: {test_rmse_ret:.4f}, RMSE days: {test_rmse_days:.4f}")

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
        import joblib

        model_path = "models/launchpad_xgb.joblib"
        if not os.path.exists(model_path):
            return {"error": "No trained launchpad model found. Run /api/ml/launchpad/train first."}

        model_data = joblib.load(model_path)
        classifier = model_data["classifier"]
        regressor = model_data["regressor"]

        conn = self.lib._tech_conn
        latest_date = conn.execute("SELECT MAX(date) FROM technical_data").fetchone()[0]
        if not latest_date:
            return {"error": "No data available."}

        events = pd.read_sql("SELECT * FROM launchpad_events WHERE breakout_date IS NULL OR breakout_date = ''", conn)
        if events.empty:
            return {"predictions": [], "message": "No stocks currently in digestion phase."}

        lookback = self.config["lookback_days"]
        predictions = []

        for _, ev in events.iterrows():
            sym = ev["symbol"]
            trig_date = ev["trigger_date"]
            try:
                df = pd.read_sql(
                    "SELECT * FROM technical_data WHERE symbol = ? AND date >= ? AND date <= ? ORDER BY date",
                    conn,
                    params=(sym, trig_date, latest_date),
                )
            except Exception:
                continue

            if df.empty or len(df) < 5:
                continue

            df["del_zscore"] = (
                (df["delivery_pct"] - df["delivery_pct"].rolling(20, min_periods=10).mean())
                / (df["delivery_pct"].rolling(20, min_periods=10).std() + 1e-9)
            )
            high_low = df["high"] - df["low"]
            high_close = (df["high"] - df["close"].shift(1)).abs()
            low_close = (df["low"] - df["close"].shift(1)).abs()
            tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
            df["atr"] = tr.rolling(14, min_periods=5).mean()
            df["vol_avg"] = df["volume"].rolling(20, min_periods=10).mean()

            try:
                trig_zscore = float(df["del_zscore"].max())
            except Exception:
                trig_zscore = 0.0

            try:
                digest_zscore_min = float(df["del_zscore"].min())
            except Exception:
                digest_zscore_min = 0.0

            try:
                del_pct_avg = float(df["delivery_pct"].mean())
            except Exception:
                del_pct_avg = 0.0

            try:
                day_range = df["high"] - df["low"]
                rr = day_range / (df["atr"] + 1e-9)
                range_atr_min = float(rr.min())
            except Exception:
                range_atr_min = 0.0

            try:
                vr = df["volume"] / (df["vol_avg"] + 1e-9)
                vol_ratio_min = float(vr.min())
            except Exception:
                vol_ratio_min = 0.0

            try:
                trig_price = float(ev["trigger_peak_price"])
                digest_low = float(ev["digestion_low_price"])
                max_dd = ((trig_price - digest_low) / trig_price) * 100
            except Exception:
                max_dd = 0.0

            try:
                days_since = (pd.Timestamp(latest_date) - pd.Timestamp(trig_date)).days
            except Exception:
                days_since = 0

            try:
                close_loc = (df["close"] - df["low"]) / (df["high"] - df["low"] + 1e-9)
                close_location_avg = float(close_loc.mean())
            except Exception:
                close_location_avg = 0.5

            try:
                nifty_rows = pd.read_sql(
                    "SELECT date, close FROM technical_data WHERE symbol = 'NIFTY' AND date >= ? AND date <= ? ORDER BY date",
                    conn,
                    params=(trig_date, latest_date),
                )
                if not nifty_rows.empty:
                    nifty_ret = (nifty_rows["close"].iloc[-1] / nifty_rows["close"].iloc[0] - 1) * 100
                else:
                    nifty_ret = 0.0
            except Exception:
                nifty_ret = 0.0

            try:
                stock_ret_row = pd.read_sql(
                    "SELECT stock_return FROM technical_data WHERE symbol = ? AND date = ? ORDER BY date DESC LIMIT 1",
                    conn,
                    params=(sym, latest_date),
                )
                if not stock_ret_row.empty:
                    stock_ret = float(stock_ret_row["stock_return"].iloc[0])
                else:
                    stock_ret = 0.0
                sector_relative_strength = stock_ret - nifty_ret
            except Exception:
                sector_relative_strength = 0.0

            feat_dict = {
                "delivery_zscore_trigger": trig_zscore,
                "delivery_zscore_min_digestion": digest_zscore_min,
                "delivery_pct_avg_digestion": del_pct_avg,
                "range_atr_ratio_min": range_atr_min,
                "vol_ratio_min": vol_ratio_min,
                "max_drawdown_pct": max_dd,
                "days_since_trigger": days_since,
                "close_location_avg": close_location_avg,
                "nifty_return_digestion": nifty_ret,
                "sector_relative_strength": sector_relative_strength,
            }
            X_pred = pd.DataFrame([feat_dict])

            prob_success = float(classifier.predict_proba(X_pred)[0][1])
            pred_ret = float(regressor.predict(X_pred)[0][0])
            pred_days = float(regressor.predict(X_pred)[0][1])

            confidence = "low"
            if prob_success > 0.7:
                confidence = "high"
            elif prob_success > 0.5:
                confidence = "medium"

            predictions.append({
                "symbol": sym,
                "trigger_date": trig_date,
                "breakout_probability": round(prob_success, 4),
                "expected_return_pct": round(pred_ret, 2),
                "expected_days_to_breakout": round(pred_days, 1),
                "confidence": confidence,
            })

        predictions.sort(key=lambda x: x["breakout_probability"], reverse=True)

        return {
            "date": latest_date,
            "predictions": predictions,
            "total_symbols": len(predictions),
        }

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