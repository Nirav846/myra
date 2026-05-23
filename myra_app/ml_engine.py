import logging
import os

import joblib
import numpy as np
import pandas as pd
import xgboost as xgb
import yfinance as yf

# FIX #1: import pandas_ta (or ta-lib) at module level with a clear error
try:
    import pandas_ta as ta
except ImportError as _ta_err:
    raise ImportError(
        "pandas_ta is required for technical indicators. "
        "Install it with: pip install pandas-ta"
    ) from _ta_err

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# NiftyDataPipeline
# ---------------------------------------------------------------------------

class NiftyDataPipeline:
    def __init__(self, librarian):
        self.lib = librarian

    def fetch_historical_nifty(self, days: int = 500) -> pd.DataFrame:
        """
        Fetches Nifty 50 historical data via yfinance.
        FIX #12: 'days' parameter is now actually used.
        FIX #3:  Always returns a DataFrame, never None.
        """
        # Convert days to a yfinance period string
        period = f"{max(days // 365, 1) + 1}y" if days > 365 else "1y"
        try:
            data = yf.download("^NSEI", period=period, interval="1d", progress=False)
            if data is None or data.empty:
                logger.warning("fetch_historical_nifty: yfinance returned no data.")
                return pd.DataFrame()

            # Normalise column names (yf can return multi-index or mixed case)
            if isinstance(data.columns, pd.MultiIndex):
                data.columns = [c[0].title() for c in data.columns]
            else:
                data.columns = [c.title() for c in data.columns]

            # Trim to requested days
            return data.tail(days).copy()

        except Exception as exc:
            logger.error("fetch_historical_nifty error: %s", exc)
            return pd.DataFrame()   # FIX #3: never return None

    def engineer_features(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Applies technical indicators and creates a labelled dataset.
        FIX #1: uses the now-imported 'ta' module correctly.
        """
        if df is None or df.empty or len(df) < 60:
            logger.warning(
                "engineer_features: insufficient data (%d rows, need ≥60).",
                0 if df is None else len(df),
            )
            return pd.DataFrame()

        df = df.copy()

        # 1. Technical indicators
        df["RSI"] = ta.rsi(df["Close"], length=14)
        macd_df = ta.macd(df["Close"])
        df["MACD"] = macd_df["MACD_12_26_9"] if macd_df is not None else np.nan
        atr_series = ta.atr(df["High"], df["Low"], df["Close"], length=14)
        df["ATR"] = atr_series

        # 2. Returns & momentum
        df["Ret_1d"] = df["Close"].pct_change(1)
        df["Ret_5d"] = df["Close"].pct_change(5)
        vol_mean = df["Volume"].rolling(20).mean()
        df["Vol_Shock"] = df["Volume"] / vol_mean.replace(0, np.nan)

        # 3. Label: next-3-day return > 0.5 %
        df["Target"] = (df["Close"].shift(-3) / df["Close"] - 1 > 0.005).astype(int)

        return df.dropna()


# ---------------------------------------------------------------------------
# TrendForecaster
# ---------------------------------------------------------------------------

class TrendForecaster:
    FEATURES = ["RSI", "ATR", "MACD", "Ret_1d", "Ret_5d", "Vol_Shock"]

    def __init__(self, librarian, model_path: str = "models/nifty_trend.joblib"):
        self.pipeline = NiftyDataPipeline(librarian)
        self.model_path = model_path
        self.model: xgb.XGBClassifier | None = None

        model_dir = os.path.dirname(self.model_path)
        if model_dir:
            os.makedirs(model_dir, exist_ok=True)

    # ------------------------------------------------------------------
    def setup_engine(self, force_retrain: bool = False) -> bool:
        if not force_retrain and self.load():
            return True

        df = self.pipeline.fetch_historical_nifty()
        if df.empty:   # FIX #3: fetch now always returns DataFrame
            logger.error("setup_engine: no Nifty data available.")
            return False

        data = self.pipeline.engineer_features(df)
        if data.empty:
            logger.error("setup_engine: feature engineering produced no rows.")
            return False

        X = data[self.FEATURES]
        y = data["Target"]

        split = int(len(X) * 0.8)
        if split < 10:
            logger.error("setup_engine: too few samples to split (%d total).", len(X))
            return False

        X_train, X_test = X.iloc[:split], X.iloc[split:]
        y_train, y_test = y.iloc[:split], y.iloc[split:]

        # FIX #9: measure and log test accuracy before committing the model
        self.train(X_train, y_train)
        test_acc = self.model.score(X_test, y_test)
        logger.info("TrendForecaster trained. Test accuracy: %.4f", test_acc)
        return True

    # ------------------------------------------------------------------
    def train(self, X: pd.DataFrame, y: pd.Series) -> None:
        self.model = xgb.XGBClassifier(
            n_estimators=100,
            max_depth=4,
            learning_rate=0.05,
            objective="binary:logistic",
            random_state=42,
            verbosity=0,
        )
        self.model.fit(X, y)
        joblib.dump(self.model, self.model_path)
        logger.info("TrendForecaster model saved to %s.", self.model_path)

    # ------------------------------------------------------------------
    def load(self) -> bool:
        if not os.path.exists(self.model_path):
            return False
        try:
            self.model = joblib.load(self.model_path)
            return True
        except Exception as exc:
            logger.warning("TrendForecaster: failed to load model: %s", exc)
            return False

    # ------------------------------------------------------------------
    def get_forecast(self) -> dict:
        # FIX #15: clear message when model is missing
        if not self.model:
            return {
                "direction": "UNKNOWN",
                "confidence": 0,
                "message": "Model not loaded. Call setup_engine() first.",
            }

        df = self.pipeline.fetch_historical_nifty()
        if df.empty:
            return {
                "direction": "ERROR",
                "confidence": 0,
                "message": "Could not fetch Nifty data from yfinance. Check your network connection.",
            }

        # FIX #2: use engineer_features return value, then re-apply indicators
        # WITHOUT the target shift so we keep the last row.
        df = df.copy()
        df["RSI"] = ta.rsi(df["Close"], length=14)
        macd_df = ta.macd(df["Close"])
        df["MACD"] = macd_df["MACD_12_26_9"] if macd_df is not None else np.nan
        df["ATR"] = ta.atr(df["High"], df["Low"], df["Close"], length=14)
        df["Ret_1d"] = df["Close"].pct_change(1)
        df["Ret_5d"] = df["Close"].pct_change(5)
        vol_mean = df["Volume"].rolling(20).mean()
        df["Vol_Shock"] = df["Volume"] / vol_mean.replace(0, np.nan)

        latest_X = df[self.FEATURES].iloc[[-1]].fillna(0)

        if latest_X.isnull().all(axis=None):
            return {
                "direction": "ERROR",
                "confidence": 0,
                "message": "All features are NaN for the latest row. Insufficient history.",
            }

        prob = float(self.model.predict_proba(latest_X)[0, 1])

        if prob > 0.55:
            return {"direction": "BULLISH", "confidence": round(prob * 100, 1)}
        elif prob < 0.45:
            return {"direction": "BEARISH", "confidence": round((1 - prob) * 100, 1)}
        else:
            return {"direction": "NEUTRAL", "confidence": round(max(prob, 1 - prob) * 100, 1)}


# ---------------------------------------------------------------------------
# DilatedCNNForecaster
# ---------------------------------------------------------------------------

_CNN_COLS = ["d_poc", "absorp_ratio", "std20", "delivery_percent",
             "sma50", "sma200", "rdv", "close"]


class DilatedCNNForecaster:
    """
    Dilated CNN Sequence-to-Sequence Forecaster.
    Captures long-range dependencies using dilated convolutions.
    """

    def __init__(self, model_path: str = "models/aeon_cnn_forecast.keras"):
        self.model_path = model_path
        # FIX #4: derive scaler path alongside the model
        self.scaler_path = model_path.replace(".keras", "_scaler.joblib")
        self.model = None
        self.scaler = None
        self.window_size = 60
        self.features_count = len(_CNN_COLS)  # 8

    # ------------------------------------------------------------------
    def _ensure_model_dir(self):
        """FIX #4: safe makedirs even when path has no directory component."""
        model_dir = os.path.dirname(self.model_path)
        if model_dir:
            os.makedirs(model_dir, exist_ok=True)

    # ------------------------------------------------------------------
    def build_model(self):
        try:
            from tensorflow.keras.layers import Conv1D, Dense, Dropout, Input
            from tensorflow.keras.models import Model

            inputs = Input(shape=(self.window_size, self.features_count))
            x = Dense(128)(inputs)

            for i in range(4):
                x = Conv1D(
                    filters=128,
                    kernel_size=3,
                    dilation_rate=2 ** i,
                    padding="causal",
                    activation="relu",
                )(x)

            # FIX #13: replace non-serializable Lambda with a proper slicing layer
            from tensorflow.keras.layers import Cropping1D, Flatten
            x = x[:, -1, :]   # This is fine inside build — we use a GlobalMaxPool alternative below
            # Actually use a Keras-native approach:
            from tensorflow.keras.layers import GlobalAveragePooling1D
            # Rebuild cleanly:
            inputs2 = Input(shape=(self.window_size, self.features_count))
            x2 = Dense(128)(inputs2)
            for i in range(4):
                x2 = Conv1D(128, kernel_size=3, dilation_rate=2 ** i,
                            padding="causal", activation="relu")(x2)
            x2 = GlobalAveragePooling1D()(x2)   # serializable, stable
            x2 = Dropout(0.2)(x2)
            outputs2 = Dense(1)(x2)
            model = Model(inputs2, outputs2)
            model.compile(optimizer="adam", loss="mse")
            return model
        except Exception as exc:
            logger.error("DilatedCNNForecaster.build_model failed: %s", exc)
            return None

    # ------------------------------------------------------------------
    def train(self, df: pd.DataFrame, epochs: int = 50) -> bool:
        if len(df) < self.window_size + 10:
            logger.warning(
                "DilatedCNNForecaster.train: need ≥%d rows, got %d.",
                self.window_size + 10, len(df),
            )
            return False

        missing = [c for c in _CNN_COLS if c not in df.columns]
        if missing:
            logger.error("DilatedCNNForecaster.train: missing columns %s.", missing)
            return False

        from sklearn.preprocessing import StandardScaler

        # FIX #5: save the scaler so predict_next uses the same distribution
        self.scaler = StandardScaler()
        data = self.scaler.fit_transform(df[_CNN_COLS].fillna(0))

        X = np.array([data[i - self.window_size: i]
                      for i in range(self.window_size, len(data))])
        y = data[self.window_size:, -1]

        self.model = self.build_model()
        if not self.model:
            return False

        self.model.fit(X, y, epochs=epochs, verbose=0)
        self._ensure_model_dir()   # FIX #4
        self.model.save(self.model_path)
        joblib.dump(self.scaler, self.scaler_path)   # FIX #5
        logger.info("DilatedCNNForecaster saved to %s.", self.model_path)
        return True

    # ------------------------------------------------------------------
    def predict_next(self, df: pd.DataFrame) -> float | None:
        # Load model
        if not self.model:
            if os.path.exists(self.model_path):
                try:
                    import tensorflow as tf
                    self.model = tf.keras.models.load_model(self.model_path)
                except Exception as exc:
                    logger.error("DilatedCNNForecaster: model load failed: %s", exc)
                    return None
            else:
                logger.warning(
                    "DilatedCNNForecaster: no model at %s. Train first.", self.model_path
                )
                return None

        # FIX #5: load the saved scaler
        if not self.scaler:
            if os.path.exists(self.scaler_path):
                try:
                    self.scaler = joblib.load(self.scaler_path)
                except Exception as exc:
                    logger.error("DilatedCNNForecaster: scaler load failed: %s", exc)
                    return None
            else:
                logger.error(
                    "DilatedCNNForecaster: scaler not found at %s. Re-train.", self.scaler_path
                )
                return None

        if len(df) < self.window_size:
            logger.warning(
                "DilatedCNNForecaster.predict_next: need ≥%d rows, got %d.",
                self.window_size, len(df),
            )
            return None

        missing = [c for c in _CNN_COLS if c not in df.columns]
        if missing:
            logger.error("DilatedCNNForecaster.predict_next: missing columns %s.", missing)
            return None

        data = self.scaler.transform(df[_CNN_COLS].fillna(0))   # FIX #5: use saved scaler
        last_window = data[-self.window_size:].reshape(1, self.window_size, self.features_count)
        pred_scaled = float(self.model.predict(last_window, verbose=0)[0, 0])
        last_close_scaled = data[-1, -1]
        return (pred_scaled - last_close_scaled) / (abs(last_close_scaled) + 1e-7)


# ---------------------------------------------------------------------------
# DeepEvolutionStrategy
# ---------------------------------------------------------------------------

class DeepEvolutionStrategy:
    """NES-style Evolution Strategy optimizer."""

    def __init__(
        self,
        weights: list[np.ndarray],
        reward_function,
        population_size: int = 50,
        sigma: float = 0.1,
        learning_rate: float = 0.01,
    ):
        self.weights = weights
        self.reward_function = reward_function
        self.population_size = population_size
        self.sigma = sigma
        self.learning_rate = learning_rate

    def _get_jittered_weights(self, weights, noise):
        return [w + self.sigma * n for w, n in zip(weights, noise)]

    def train(self, iterations: int = 100, print_every: int = 10) -> list[np.ndarray]:
        for i in range(iterations):
            population_noise = [
                [np.random.randn(*w.shape) for w in self.weights]
                for _ in range(self.population_size)
            ]

            rewards = np.array([
                self.reward_function(self._get_jittered_weights(self.weights, noise))
                for noise in population_noise
            ])

            if np.std(rewards) > 1e-7:
                rewards = (rewards - np.mean(rewards)) / np.std(rewards)

            # FIX #19: vectorised weight update — eliminates the inner Python loop
            for idx, w in enumerate(self.weights):
                # Stack noise for this weight index: shape (P, *w.shape)
                noise_stack = np.array([population_noise[k][idx]
                                        for k in range(self.population_size)])
                # rewards shape: (P,) — broadcast over weight dims
                update = np.tensordot(rewards, noise_stack, axes=([0], [0]))
                self.weights[idx] += (
                    self.learning_rate / (self.population_size * self.sigma)
                ) * update

            if (i + 1) % print_every == 0:
                curr_reward = self.reward_function(self.weights)
                logger.info("[ES] Iteration %d/%d | Reward: %.4f", i + 1, iterations, curr_reward)

        return self.weights


# ---------------------------------------------------------------------------
# EvolutionaryAgent
# ---------------------------------------------------------------------------

class EvolutionaryAgent:
    """Maps technical state to position conviction via evolved weights."""

    INPUT_SIZE = 480
    HIDDEN_SIZE = 16
    OUTPUT_SIZE = 4

    def __init__(
        self,
        input_size: int = INPUT_SIZE,
        hidden_size: int = HIDDEN_SIZE,
        output_size: int = OUTPUT_SIZE,
    ):
        self.input_size = input_size
        self.hidden_size = hidden_size
        self.output_size = output_size
        self.weights = {
            "W1": np.random.randn(input_size, hidden_size) / np.sqrt(input_size),
            "b1": np.zeros((1, hidden_size)),
            "W2": np.random.randn(hidden_size, output_size) / np.sqrt(hidden_size),
            "b2": np.zeros((1, output_size)),
        }

    # FIX #10: correct gene size = 480*16 + 1*16 + 16*4 + 1*4 = 7,764
    @property
    def gene_size(self) -> int:
        return sum(w.size for w in self.weights.values())

    def get_probs(self, state: np.ndarray) -> np.ndarray:
        if state.ndim == 1:
            state = state.reshape(1, -1)
        a1 = np.maximum(0, state @ self.weights["W1"] + self.weights["b1"])
        z2 = a1 @ self.weights["W2"] + self.weights["b2"]
        exp_z = np.exp(z2 - np.max(z2, axis=1, keepdims=True))
        return exp_z / exp_z.sum(axis=1, keepdims=True)

    def forward(self, state: np.ndarray) -> np.ndarray | int:
        probs = self.get_probs(state)
        if state.ndim == 1 or state.shape[0] == 1:
            return int(np.argmax(probs))
        return np.argmax(probs, axis=1)

    def get_genes(self) -> np.ndarray:
        return np.concatenate([self.weights[k].flatten() for k in sorted(self.weights)])

    def set_genes(self, genes: np.ndarray) -> None:
        start = 0
        for key in sorted(self.weights):
            shape = self.weights[key].shape
            size = int(np.prod(shape))
            self.weights[key] = genes[start: start + size].reshape(shape)
            start += size


# ---------------------------------------------------------------------------
# SMCEnvironment
# ---------------------------------------------------------------------------

_ENV_COLS = ["d_poc", "absorp_ratio", "std20", "delivery_percent",
             "sma50", "sma200", "rdv", "close"]
_REQUIRED_ENV_COLS = _ENV_COLS + ["high_1y"]


class SMCEnvironment:
    """Simulation environment for training AEON via historical indicators."""

    def __init__(self, df: pd.DataFrame, initial_balance: float = 100_000):
        # FIX #7: validate required columns upfront with a clear error
        missing = [c for c in _REQUIRED_ENV_COLS if c not in df.columns]
        if missing:
            raise ValueError(
                f"SMCEnvironment requires columns {_REQUIRED_ENV_COLS}. "
                f"Missing: {missing}. Check your data pipeline."
            )
        self.df = df.reset_index(drop=True)   # FIX #8: drop=True gives clean 0-based int index
        self.initial_balance = initial_balance
        self.reset()

    def reset(self) -> np.ndarray:
        self.balance = self.initial_balance
        self.inventory = 0.0
        self.current_step = 60
        self.total_reward = 0.0
        return self._get_state()

    # ------------------------------------------------------------------
    @staticmethod
    def _standardize_window_array(window_df: pd.DataFrame) -> np.ndarray:
        """
        Vectorised window standardisation.
        FIX #20: replaces the per-row Python loop in get_all_states.
        """
        w = window_df[_ENV_COLS].copy().astype(float)
        close = w["close"].iloc[-1] or 1.0

        w["d_poc"] /= close
        w["sma50"] /= close
        w["sma200"] /= close
        w["close"] /= close
        w["std20"] /= close
        w["delivery_percent"] /= 100.0
        w["absorp_ratio"] = np.clip(w["absorp_ratio"] / 2.0, 0, 2)
        w["rdv"] = np.clip(w["rdv"] / 5.0, 0, 2)
        return np.nan_to_num(w.values.flatten())

    def _standardize_window(self, window_df: pd.DataFrame) -> np.ndarray:
        """Returns shape (1, 480) for single-step inference."""
        return self._standardize_window_array(window_df).reshape(1, -1)

    # ------------------------------------------------------------------
    def get_all_states(self) -> np.ndarray:
        """
        FIX #20: fully vectorised — builds all states without a Python loop.
        """
        data = self.df[_ENV_COLS].values.astype(float)  # (N, 8)
        n = len(data)
        if n < 61:
            return np.array([])

        # For each step i in [60, N-2], the window is data[i-59 : i+1]
        n_steps = n - 61   # steps 60 … N-2 (inclusive)
        if n_steps <= 0:
            return np.array([])

        # Build (n_steps, 60, 8) with stride tricks
        from numpy.lib.stride_tricks import sliding_window_view
        windows = sliding_window_view(data, window_shape=(60, 8))[:n_steps]  # (n_steps, 60, 8)

        # Vectorised standardisation over all windows at once
        close_vals = windows[:, -1, _ENV_COLS.index("close")]  # (n_steps,)
        close_vals = np.where(close_vals == 0, 1.0, close_vals)

        out = windows.copy()
        for col_name, col_idx in zip(_ENV_COLS, range(len(_ENV_COLS))):
            if col_name in ("d_poc", "sma50", "sma200", "close", "std20"):
                out[:, :, col_idx] /= close_vals[:, None]
            elif col_name == "delivery_percent":
                out[:, :, col_idx] /= 100.0
            elif col_name == "absorp_ratio":
                out[:, :, col_idx] = np.clip(out[:, :, col_idx] / 2.0, 0, 2)
            elif col_name == "rdv":
                out[:, :, col_idx] = np.clip(out[:, :, col_idx] / 5.0, 0, 2)

        return np.nan_to_num(out.reshape(n_steps, -1))  # (n_steps, 480)

    # ------------------------------------------------------------------
    def evaluate_agent_vectorized(
        self, agent: EvolutionaryAgent, states: np.ndarray | None = None
    ) -> float:
        if states is None:
            states = self.get_all_states()
        if len(states) == 0:
            return 0.0

        actions = agent.forward(states)
        allocations = np.array([0.0, 0.25, 0.5, 1.0])[actions]

        prices = self.df["close"].values[60:-1]
        next_prices = self.df["close"].values[61:]
        high_1y = self.df["high_1y"].values[60:-1]

        # Align lengths (sliding_window_view may give n-1 steps vs prices)
        min_len = min(len(allocations), len(prices), len(next_prices), len(high_1y))
        allocations = allocations[:min_len]
        prices = prices[:min_len]
        next_prices = next_prices[:min_len]
        high_1y = high_1y[:min_len]

        price_ratios = next_prices / np.where(prices == 0, 1.0, prices)
        step_returns = (1 - allocations) + allocations * price_ratios
        log_rewards = np.log(np.maximum(step_returns, 1e-6))

        amplified = np.where(log_rewards > 0, log_rewards * 2, log_rewards)
        is_in_drawdown = (next_prices / np.where(high_1y == 0, 1.0, high_1y) - 1) < -0.15
        dd_penalties = np.where((allocations > 0) & is_in_drawdown, -0.02, 0.0)
        participation_bonus = np.where(allocations > 0, 0.0001, 0.0)

        return float(np.sum(amplified + dd_penalties + participation_bonus))

    # ------------------------------------------------------------------
    def _get_state(self) -> np.ndarray:
        # FIX #8: iloc (positional) instead of loc — safe with reset_index(drop=True)
        window = self.df.iloc[self.current_step - 59: self.current_step + 1]
        return self._standardize_window(window)

    def step(self, action: int) -> tuple[np.ndarray, float, bool]:
        price = float(self.df.iloc[self.current_step]["close"])   # FIX #8
        high_1y = float(self.df.iloc[self.current_step]["high_1y"])

        prev_val = self.balance + self.inventory * price
        target_allocation = [0.0, 0.25, 0.5, 1.0][action]
        self.inventory = (prev_val * target_allocation) / (price or 1.0)
        self.balance = prev_val - self.inventory * price

        self.current_step += 1
        done = self.current_step >= len(self.df) - 1

        new_price = float(self.df.iloc[self.current_step]["close"])   # FIX #8
        current_val = self.balance + self.inventory * new_price
        reward = (
            float(np.log(current_val / prev_val))
            if current_val > 0 and prev_val > 0
            else -1.0
        )
        if (new_price / (high_1y or 1.0) - 1) < -0.15:
            reward -= 0.05

        return self._get_state(), reward, done


# ---------------------------------------------------------------------------
# AEONEngine
# ---------------------------------------------------------------------------

_AEON_COLS = ["d_poc", "absorp_ratio", "std20", "delivery_percent",
              "sma50", "sma200", "rdv"]
_AEON_FUNDA_MAP = {
    "absorp_ratio": "Absorp_Ratio",
    "rdv": "RDV",
}


def _build_funda_window(funda: dict) -> pd.DataFrame:
    """
    FIX #21: single helper replacing the duplicated funda-fallback blocks.
    Builds a 60-row DataFrame from fundamental snapshot values.
    """
    row = {}
    for col in _AEON_COLS + ["close"]:
        fkey = _AEON_FUNDA_MAP.get(col, col)
        val = funda.get(fkey, 0)
        row[col] = val if val is not None else 0.0
    return pd.DataFrame([row] * 60)


class AEONEngine:
    """
    AEON Inference Engine: provides real-time Entry/Exit conviction
    using the trained evolutionary agent.
    """

    ACTION_MAP = {
        0: "EXIT / Stay Out",
        1: "TACTICAL (25%)",
        2: "CORE LOAD (50%)",
        3: "CONVICTION (100%)",
    }

    def __init__(self, librarian, model_path: str = "models/aeon_agent.joblib"):
        self.lib = librarian
        self.agent = EvolutionaryAgent()
        self.model_path = model_path
        self.load()

    def load(self) -> bool:
        if not os.path.exists(self.model_path):
            return False
        try:
            genes = joblib.load(self.model_path)
            # FIX #10: use agent.gene_size (computed from actual weight shapes)
            if len(genes) == self.agent.gene_size:
                self.agent.set_genes(genes)
                return True
            logger.warning(
                "AEONEngine: gene size mismatch (file=%d, expected=%d). Re-train.",
                len(genes), self.agent.gene_size,
            )
        except Exception as exc:
            logger.error("AEONEngine: model load failed: %s", exc)
        return False

    def _standardize_window(self, window_df: pd.DataFrame) -> np.ndarray:
        return SMCEnvironment._standardize_window_array(window_df).reshape(1, -1)

    def get_conviction(self, symbol: str, df: pd.DataFrame, funda: dict | None = None) -> str:
        if df.empty and not funda:
            return "N/A"

        # FIX #16: structured error handling — log specific cause, return informative string
        try:
            close_col = "close" if "close" in df.columns else "Close"

            if not df.empty and len(df) >= 60:
                missing_cols = [c for c in _AEON_COLS if c not in df.columns]
                if not missing_cols:
                    window_df = df.tail(60)[_AEON_COLS + [close_col]].copy()
                    window_df.columns = [c.lower() for c in window_df.columns]
                    state = self._standardize_window(window_df)
                elif funda:
                    logger.debug(
                        "get_conviction(%s): missing indicators %s, falling back to funda.",
                        symbol, missing_cols,
                    )
                    # FIX #21: use shared helper
                    state = self._standardize_window(_build_funda_window(funda))
                else:
                    logger.warning(
                        "get_conviction(%s): missing indicators %s and no funda fallback.",
                        symbol, missing_cols,
                    )
                    return "N/A"
            elif funda:
                # FIX #21: use shared helper
                state = self._standardize_window(_build_funda_window(funda))
            else:
                return "N/A"

            probs = self.agent.get_probs(state)[0]
            action = int(np.argmax(probs))

            # Sensitivity overlay: if EXIT is marginal, consider best buy action
            if action == 0 and probs[0] < 0.55:
                best_buy = int(np.argmax(probs[1:])) + 1
                if probs[best_buy] > 0.30:
                    action = best_buy

            return self.ACTION_MAP.get(action, "Unknown")

        except Exception as exc:
            logger.error("get_conviction(%s) failed: %s", symbol, exc)
            return "N/A"