import pandas as pd
import numpy as np
import xgboost as xgb
import os
import joblib
import yfinance as yf


class NiftyDataPipeline:
    def __init__(self, librarian):
        self.lib = librarian

    def fetch_historical_nifty(self, days=500):
        """Fetches Nifty 50 historical data using yfinance."""
        try:
            # We use yfinance for benchmark history as seen in IndexEngine
            data = yf.download("^NSEI", period="2y", interval="1d", progress=False)
            if data.empty:
                return pd.DataFrame()

            # Clean columns (yf sometimes returns multi-index or lowercase)
            data.columns = [
                c.title() if isinstance(c, str) else c[0].title() for c in data.columns
            ]
            return data
        except Exception as e:
            print(f"[ML] Error fetching Nifty history: {e}")
            return None

    def engineer_features(self, df):
        """Applies technical indicators and creates time-series features."""
        if df is None or df.empty or len(df) < 60:
            return pd.DataFrame()

        df = df.copy()
        # 1. Technicals
        df["RSI"] = ta.rsi(df["Close"], length=14)
        df["ATR"] = ta.atr(df["High"], df["Low"], df["Close"], length=14)
        df["MACD"] = ta.macd(df["Close"])["MACD_12_26_9"]

        # 2. Returns & Momentum
        df["Ret_1d"] = df["Close"].pct_change(1)
        df["Ret_5d"] = df["Close"].pct_change(5)
        df["Vol_Shock"] = df["Volume"] / df["Volume"].rolling(20).mean()

        # 3. Labeling (Prediction Target)
        # Goal: Predict if next 3 days return > 0.5%
        df["Target"] = (df["Close"].shift(-3) / df["Close"] - 1 > 0.005).astype(int)

        return df.dropna()


class TrendForecaster:
    def __init__(self, librarian, model_path="models/nifty_trend.joblib"):
        self.pipeline = NiftyDataPipeline(librarian)
        self.model_path = model_path
        self.model = None
        self.features = ["RSI", "ATR", "MACD", "Ret_1d", "Ret_5d", "Vol_Shock"]

        # Ensure directory exists
        model_dir = os.path.dirname(self.model_path)
        if model_dir:
            os.makedirs(model_dir, exist_ok=True)

    def setup_engine(self, force_retrain=False):
        """Orchestrates loading or training the model."""
        if not force_retrain and self.load():
            return True

        df = self.pipeline.fetch_historical_nifty()
        if df.empty:
            return False

        data = self.pipeline.engineer_features(df)
        if data.empty:
            return False

        X = data[self.features]
        y = data["Target"]

        # Simple walk-forward split (80/20)
        split = int(len(X) * 0.8)
        self.train(X.iloc[:split], y.iloc[:split])
        return True

    def train(self, X, y):
        """Trains the XGBoost model."""
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

    def load(self):
        """Loads the model from disk."""
        if os.path.exists(self.model_path):
            try:
                self.model = joblib.load(self.model_path)
                return True
            except Exception:
                pass
        return False

    def get_forecast(self):
        """Fetches latest data and predicts."""
        if not self.model:
            return {"direction": "UNKNOWN", "confidence": 0}

        df = self.pipeline.fetch_historical_nifty()
        if df.empty:
            return {"direction": "ERROR", "confidence": 0}

        # Engineer features but don't drop target (we don't have future target for latest row)
        # We manually apply indicators to the latest row
        self.pipeline.engineer_features(
            df
        )  # This drops last 3 rows because of shift(-3)
        # We need the features for the ABSOLUTE LATEST row

        # Re-engineering without dropping for the very last row
        df["RSI"] = ta.rsi(df["Close"], length=14)
        df["ATR"] = ta.atr(df["High"], df["Low"], df["Close"], length=14)
        df["MACD"] = ta.macd(df["Close"])["MACD_12_26_9"]
        df["Ret_1d"] = df["Close"].pct_change(1)
        df["Ret_5d"] = df["Close"].pct_change(5)
        df["Vol_Shock"] = df["Volume"] / df["Volume"].rolling(20).mean()

        latest_X = df[self.features].iloc[[-1]].fillna(0)

        prob = self.model.predict_proba(latest_X)[0, 1]

        if prob > 0.55:
            return {"direction": "BULLISH", "confidence": round(prob * 100, 1)}
        elif prob < 0.45:
            return {"direction": "BEARISH", "confidence": round((1 - prob) * 100, 1)}
        else:
            return {
                "direction": "NEUTRAL",
                "confidence": round(max(prob, 1 - prob) * 100, 1),
            }


class DilatedCNNForecaster:
    """
    Dilated CNN Sequence-to-Sequence Forecaster.
    Reference: huseinzol05/Stock-Prediction-Models Agent #18 (95.86% Accuracy)
    Captures long-range dependencies using dilated convolutions.
    """

    def __init__(self, model_path="models/aeon_cnn_forecast.keras"):
        self.model_path = model_path
        self.model = None
        self.window_size = 60
        self.features_count = 8

    def build_model(self):
        """
        Builds the underlying TensorFlow/Keras architecture.
        """
        try:
            from tensorflow.keras.layers import Input, Conv1D, Dense, Dropout, Lambda
            from tensorflow.keras.models import Model

            inputs = Input(shape=(self.window_size, self.features_count))
            x = Dense(128)(inputs)

            # 4 Blocks of Dilated Convolutions
            for i in range(4):
                dilation_rate = 2**i
                x = Conv1D(
                    filters=128,
                    kernel_size=3,
                    dilation_rate=dilation_rate,
                    padding="causal",
                    activation="relu",
                )(x)

            # Sequence-to-Value Attention (Last step selection)
            x = Lambda(lambda x: x[:, -1, :])(x)

            x = Dropout(0.2)(x)
            outputs = Dense(1)(x)  # Predict next close price

            model = Model(inputs, outputs)
            model.compile(optimizer="adam", loss="mse")
            return model
        except Exception:
            return None

    def train(self, df, epochs=50):
        """Trains the CNN on a single stock's history."""
        if len(df) < self.window_size + 10:
            return False

        from sklearn.preprocessing import StandardScaler

        scaler = StandardScaler()

        cols = [
            "d_poc",
            "absorp_ratio",
            "std20",
            "delivery_percent",
            "sma50",
            "sma200",
            "rdv",
            "close",
        ]
        data = scaler.fit_transform(df[cols].fillna(0))

        # Optimized with list comprehension (Fix 193, 194: Avoid .append in loop)
        X = np.array(
            [data[i - self.window_size : i] for i in range(self.window_size, len(data))]
        )
        y = data[self.window_size :, -1]  # Target is 'close'

        self.model = self.build_model()
        if self.model:
            self.model.fit(X, y, epochs=epochs, verbose=0)
            os.makedirs(os.path.dirname(self.model_path), exist_ok=True)
            self.model.save(self.model_path)
            return True
        return False

    def predict_next(self, df):
        """Predicts the next closing price move."""
        if not self.model:
            if os.path.exists(self.model_path):
                import tensorflow as tf

                self.model = tf.keras.models.load_model(self.model_path)
            else:
                return None

        if len(df) < self.window_size:
            return None

        from sklearn.preprocessing import StandardScaler

        scaler = StandardScaler()
        cols = [
            "d_poc",
            "absorp_ratio",
            "std20",
            "delivery_percent",
            "sma50",
            "sma200",
            "rdv",
            "close",
        ]
        data = scaler.fit_transform(df[cols].fillna(0))

        last_window = data[-self.window_size :].reshape(
            1, self.window_size, self.features_count
        )
        # Fix 223: Comma-separated indexing
        pred_scaled = self.model.predict(last_window, verbose=0)[0, 0]

        # We return the direction and magnitude of the move
        last_close_scaled = data[-1, -1]
        return (pred_scaled - last_close_scaled) / (abs(last_close_scaled) + 1e-7)


class DeepEvolutionStrategy:
    """
    Advanced Evolution Strategy (NES-style) implementation.
    Reference: huseinzol05/Stock-Prediction-Models Agent #6
    Optimizes weights by estimating the gradient from noisy population rewards.
    """

    def __init__(
        self,
        weights,
        reward_function,
        population_size=50,
        sigma=0.1,
        learning_rate=0.01,
    ):
        self.weights = weights  # List of np.arrays
        self.reward_function = reward_function
        self.population_size = population_size
        self.sigma = sigma
        self.learning_rate = learning_rate

    def _get_jittered_weights(self, weights, noise):
        # Optimized with list comprehension (Fix 245: Avoid .append in loop)
        return [w + self.sigma * n for w, n in zip(weights, noise)]

    def train(self, iterations=100, print_every=10):
        for i in range(iterations):
            # Optimized with nested list comprehension (Fix 256: Avoid .append in loop)
            population_noise = [
                [np.random.randn(*w.shape) for w in self.weights]
                for _ in range(self.population_size)
            ]
            rewards = np.zeros(self.population_size)

            # 2. Evaluate Population
            for k in range(self.population_size):
                jittered = self._get_jittered_weights(self.weights, population_noise[k])
                rewards[k] = self.reward_function(jittered)

            # 3. Fitness Shaping (Standardize Rewards)
            if np.std(rewards) > 1e-7:
                rewards = (rewards - np.mean(rewards)) / np.std(rewards)

            # 4. Gradient Estimation & Weight Update
            # Weight_new = Weight_old + lr * (noise * rewards).mean() / sigma
            for idx, w in enumerate(self.weights):
                update = np.zeros_like(w)
                for k in range(self.population_size):
                    # Fix 278: Avoid chained indexing
                    noise_k = population_noise[k]
                    update += rewards[k] * noise_k[idx]

                self.weights[idx] += (
                    self.learning_rate / (self.population_size * self.sigma)
                ) * update

            if (i + 1) % print_every == 0:
                curr_reward = self.reward_function(self.weights)
                print(f"[ES] Iteration {i+1}/{iterations} | Reward: {curr_reward:.4f}")

        return self.weights


class EvolutionaryAgent:
    """
    AEON Neural Core: Maps technical state to position conviction.
    Optimized via Genetic Mutation rather than Gradient Descent.
    """

    def __init__(self, input_size=480, hidden_size=16, output_size=4):
        self.input_size = input_size
        self.hidden_size = hidden_size
        self.output_size = output_size

        # Initialize weights (Genes)
        self.weights = {
            "W1": np.random.randn(input_size, hidden_size) / np.sqrt(input_size),
            "b1": np.zeros((1, hidden_size)),
            "W2": np.random.randn(hidden_size, output_size) / np.sqrt(hidden_size),
            "b2": np.zeros((1, output_size)),
        }

    def get_probs(self, state):
        """Returns the raw probability distribution over actions."""
        if state.ndim == 1:
            state = state.reshape(1, -1)
        z1 = np.dot(state, self.weights["W1"]) + self.weights["b1"]
        a1 = np.maximum(0, z1)
        z2 = np.dot(a1, self.weights["W2"]) + self.weights["b2"]
        exp_z = np.exp(z2 - np.max(z2, axis=1, keepdims=True))
        return exp_z / exp_z.sum(axis=1, keepdims=True)

    def forward(self, state):
        """Returns the selected action (argmax). Handles batch input."""
        original_ndim = state.ndim
        probs = self.get_probs(state)

        if original_ndim == 1 or state.shape[0] == 1:
            return np.argmax(probs)
        return np.argmax(probs, axis=1)

    def get_genes(self):
        """Flattens all weights into a single vector for evolution."""
        # Optimized with list comprehension (Fix 329: Avoid .append in loop)
        gene_list = [self.weights[key].flatten() for key in sorted(self.weights.keys())]
        return np.concatenate(gene_list)

    def set_genes(self, genes):
        """Restores weights from a flattened vector."""
        start = 0
        for key in sorted(self.weights.keys()):
            shape = self.weights[key].shape
            size = np.prod(shape)
            self.weights[key] = genes[start : start + size].reshape(shape)
            start += size


class SMCEnvironment:
    """
    Simulation Environment for training AEON.
    Uses DuckDB historical indicators as the 'World'.
    """

    def __init__(self, df, initial_balance=100000):
        self.df = df.reset_index()
        self.initial_balance = initial_balance
        self.reset()

    def reset(self):
        self.balance = self.initial_balance
        self.inventory = 0
        self.current_step = 60  # Start with 60 days of history
        self.total_reward = 0
        return self._get_state()

    def _standardize_window(self, window):
        """Internal helper to normalize a 60x8 window of indicators."""
        # Feature columns: d_poc, absorp_ratio, std20, delivery_percent, sma50, sma200, rdv, close
        # Use relative values to Close to ensure scale-invariance
        w = window.copy()
        close = w["close"].values[-1]
        if close == 0:
            close = 1.0  # Avoid div zero

        # 1. Price-relative metrics
        w["d_poc"] = w["d_poc"] / close
        w["sma50"] = w["sma50"] / close
        w["sma200"] = w["sma200"] / close
        w["close"] = w["close"] / close
        w["std20"] = w["std20"] / close

        # 2. Percentage/Ratio metrics (Already mostly normalized)
        w["delivery_percent"] = w["delivery_percent"] / 100.0
        # absorp_ratio and rdv are usually small (0-5), leave as is or clip
        w["absorp_ratio"] = np.clip(w["absorp_ratio"] / 2.0, 0, 2)
        w["rdv"] = np.clip(w["rdv"] / 5.0, 0, 2)

        return np.nan_to_num(w.values.flatten().reshape(1, -1))

    def get_all_states(self):
        """Precomputes all states for the entire dataframe as a batch."""
        cols = [
            "d_poc",
            "absorp_ratio",
            "std20",
            "delivery_percent",
            "sma50",
            "sma200",
            "rdv",
            "close",
        ]
        data_df = self.df[cols].copy()

        if len(data_df) < 61:
            return np.array([])

        # Vectorized standardization (per step) (Fix 394: Avoid .append in loop)
        states = [
            self._standardize_window(data_df.iloc[i - 59 : i + 1])
            for i in range(60, len(self.df) - 1)
        ]

        return np.concatenate(states) if states else np.array([])

    def evaluate_agent_vectorized(self, agent, states=None):
        """
        Runs the agent through the entire history in a single vectorized pass.
        Returns total fitness.
        """
        if states is None:
            states = self.get_all_states()

        if len(states) == 0:
            return 0

        # 1. Get all actions at once
        actions = agent.forward(states)  # Array of action indices

        # 2. Map actions to weight allocations
        allocations = np.array([0, 0.25, 0.5, 1.0])[actions]

        # 3. Calculate price returns
        # Steps are from 60 to N-2
        prices = self.df["close"].values[60:-1]
        next_prices = self.df["close"].values[61:]
        high_1y = self.df["high_1y"].values[60:-1]

        # Calculate returns: (1-w) + w * (P_next/P_curr)
        price_ratios = next_prices / prices
        step_returns = (1 - allocations) + allocations * price_ratios

        # Log rewards (clip to avoid log(0))
        log_rewards = np.log(np.maximum(step_returns, 1e-6))

        # Institutional Reward Engine (Professional Grade):
        # 1. 2x Greed: Sufficient to overcome noise without creating a bubble.
        amplified_rewards = np.where(log_rewards > 0, log_rewards * 2, log_rewards)

        # 2. Strict Penalty: -0.02 (Reduced from -0.05)
        # Forces the agent to be highly selective but not completely paralyzed.
        is_in_drawdown = (next_prices / high_1y - 1) < -0.15
        dd_penalties = np.where((allocations > 0) & is_in_drawdown, -0.02, 0)

        # 3. Participation Bonus: Small reward for action to break ties with EXIT
        # Reduced to 0.0001 to prevent 'Forever-In' bias
        participation_bonus = np.where(allocations > 0, 0.0001, 0)

        return np.sum(amplified_rewards + dd_penalties + participation_bonus)

    def _get_state(self):
        """Extracts the 60-day state window for the current step."""
        cols = [
            "d_poc",
            "absorp_ratio",
            "std20",
            "delivery_percent",
            "sma50",
            "sma200",
            "rdv",
            "close",
        ]
        # Fix 433: Use .loc for safety/performance
        window = self.df.loc[self.current_step - 59 : self.current_step, cols]
        return self._standardize_window(window)

    def step(self, action):
        """
        Executes an action: 0:Sell, 1:25%, 2:50%, 3:100%
        Returns (next_state, reward, done)
        """
        # Fix 446: Use .loc for safety/performance
        price = self.df.loc[self.current_step, "close"]
        high_1y = self.df.loc[self.current_step, "high_1y"]

        prev_val = self.balance + (self.inventory * price)

        # Action Logic
        target_allocation = [0, 0.25, 0.5, 1.0][action]
        target_inventory = (
            (self.balance + self.inventory * price) * target_allocation / price
        )

        # Simple instant execution
        self.inventory = target_inventory
        self.balance = prev_val - (self.inventory * price)

        self.current_step += 1
        done = self.current_step >= len(self.df) - 1

        # Calculate Reward (Log Return + Drawdown Penalty)
        # Fix 471: Avoid chained indexing
        new_price = self.df.loc[self.current_step, "close"]
        current_val = self.balance + (self.inventory * new_price)
        reward = (
            np.log(current_val / prev_val) if current_val > 0 and prev_val > 0 else -1
        )

        # Hard Drawdown Penalty (Spec 4)
        if (new_price / high_1y - 1) < -0.15:
            reward -= 0.05

        return self._get_state(), reward, done


class AEONEngine:
    """
    AEON Inference Engine: Uses the trained evolutionary model to
    provide real-time Entry/Exit conviction.
    """

    def __init__(self, librarian, model_path="models/aeon_agent.joblib"):
        self.lib = librarian
        self.agent = EvolutionaryAgent(input_size=480)
        self.model_path = model_path
        self.load()

    def load(self):
        if os.path.exists(self.model_path):
            try:
                genes = joblib.load(self.model_path)
                # architecture sizing
                expected_size = 480 * 16 + 16 + 16 * 4 + 4
                if len(genes) == expected_size:
                    self.agent.set_genes(genes)
                    return True
            except Exception:
                pass
        return False

    def _standardize_window(self, window):
        """Internal helper to normalize a 60x8 window of indicators."""
        # Feature columns: d_poc, absorp_ratio, std20, delivery_percent, sma50, sma200, rdv, close
        # Use relative values to Close to ensure scale-invariance
        w = window.copy()
        close = w["close"].values[-1]
        if close == 0:
            close = 1.0  # Avoid div zero

        # 1. Price-relative metrics
        w["d_poc"] = w["d_poc"] / close
        w["sma50"] = w["sma50"] / close
        w["sma200"] = w["sma200"] / close
        w["close"] = w["close"] / close
        w["std20"] = w["std20"] / close

        # 2. Percentage/Ratio metrics (Already mostly normalized)
        w["delivery_percent"] = w["delivery_percent"] / 100.0
        # absorp_ratio and rdv are usually small (0-5), leave as is or clip
        w["absorp_ratio"] = np.clip(w["absorp_ratio"] / 2.0, 0, 2)
        w["rdv"] = np.clip(w["rdv"] / 5.0, 0, 2)

        return np.nan_to_num(w.values.flatten().reshape(1, -1))

    def get_conviction(self, symbol, df, funda=None):
        """Returns the Agent's conviction level (0-3)."""
        if df.empty and not funda:
            return "N/A"

        try:
            # Feature columns mapped from Spec
            cols = [
                "d_poc",
                "absorp_ratio",
                "std20",
                "delivery_percent",
                "sma50",
                "sma200",
                "rdv",
            ]
            close_col = "close" if "close" in df.columns else "Close"
            window_cols = cols + [close_col]

            # 1. Prepare the historical state window
            if not df.empty and len(df) >= 60:
                # Check if all other indicators exist in the history
                missing = [c for c in cols if c not in df.columns]
                if not missing:
                    # Use full history (Momentum Vision)
                    window_df = df.tail(60)[window_cols].copy()
                    window_df.columns = [
                        c.lower() for c in window_df.columns
                    ]  # Ensure consistency
                    state = self._standardize_window(window_df)
                elif funda:
                    # Optimized Vectorized Reconstruction (Jules Boost)
                    # Priority for 680-stock scan performance on AMD APU systems
                    row_dict = {}
                    for c in cols + ["close"]:
                        f_key = c
                        if c == "absorp_ratio":
                            f_key = "Absorp_Ratio"
                        if c == "rdv":
                            f_key = "RDV"
                        val = funda.get(f_key, 0)
                        row_dict[c] = val if val is not None else 0

                    window_df = pd.DataFrame([row_dict] * 60)
                    state = self._standardize_window(window_df)
                else:
                    return "N/A"
            elif funda:
                # Optimized Vectorized Reconstruction for short-history/new stocks
                row_dict = {}
                for c in cols + ["close"]:
                    f_key = c
                    if c == "absorp_ratio":
                        f_key = "Absorp_Ratio"
                    if c == "rdv":
                        f_key = "RDV"
                    val = funda.get(f_key, 0)
                    row_dict[c] = val if val is not None else 0

                window_df = pd.DataFrame([row_dict] * 60)
                state = self._standardize_window(window_df)
            else:
                return "N/A"

            # --- STRATEGIC SENSITIVITY INFERENCE ---
            probs = self.agent.get_probs(state)[0]  # Shape (4,)

            # 1. Base Argmax
            action = np.argmax(probs)

            # 2. Sensitivity Overlay:
            if action == 0 and probs[0] < 0.55:
                best_buy = np.argmax(probs[1:]) + 1
                if probs[best_buy] > 0.30:
                    action = best_buy

            # Map action to text
            mapping = {
                0: "EXIT / Stay Out",
                1: "TACTICAL (25%)",
                2: "CORE LOAD (50%)",
                3: "CONVICTION (100%)",
            }
            return mapping.get(action, "Unknown")
        except Exception:
            return "N/A"
