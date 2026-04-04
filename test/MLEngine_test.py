import unittest
from unittest.mock import patch, MagicMock
import sys

# Mock modules for testing in environments without these dependencies
for mod in ['pandas', 'numpy', 'xgboost', 'joblib', 'yfinance', 'requests', 'pandas_ta']:
    if mod not in sys.modules:
        sys.modules[mod] = MagicMock()

import pandas as pd
import numpy as np
import os
from myra_app.ml_engine import NiftyDataPipeline, TrendForecaster, DilatedCNNForecaster, SMCEnvironment, AEONEngine, EvolutionaryAgent


class TestMLEngine(unittest.TestCase):
    @unittest.skipIf(isinstance(np, MagicMock), "Numpy not available in this environment")
    def test_evolutionary_agent_get_genes(self):
        """Test that get_genes correctly flattens weights in alphabetical order of keys."""
        agent = EvolutionaryAgent(input_size=2, hidden_size=2, output_size=1)

        # Override weights with a specific mock dictionary to ensure exact testing behavior
        # We use keys that test alphabetical sorting: 'b_vector', 'c_matrix', 'a_scalar'
        # 'a_scalar' -> [1.0]
        # 'b_vector' -> [2.0, 3.0]
        # 'c_matrix' -> [[4.0, 5.0], [6.0, 7.0]]
        agent.weights = {
            'c_matrix': np.array([[4.0, 5.0], [6.0, 7.0]]),
            'a_scalar': np.array([1.0]),
            'b_vector': np.array([2.0, 3.0])
        }

        genes = agent.get_genes()

        # The expected order is a_scalar, b_vector, c_matrix
        # Flattened: [1.0] + [2.0, 3.0] + [4.0, 5.0, 6.0, 7.0]
        # => [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0]
        expected_genes = np.array([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0])

        np.testing.assert_array_equal(genes, expected_genes)
        self.assertEqual(genes.shape, (7,))

    @unittest.skipIf(isinstance(pd, MagicMock), "Pandas not available in this environment")
    def test_feature_engineering_edge_cases(self):
        pipeline = NiftyDataPipeline(None)

        # 1. Test with None
        df_none = pipeline.engineer_features(None)
        self.assertTrue(df_none.empty)

        # 2. Test with Empty DataFrame
        df_empty_in = pd.DataFrame()
        df_empty_out = pipeline.engineer_features(df_empty_in)
        self.assertTrue(df_empty_out.empty)

        # 3. Test with DataFrame less than 60 rows
        dates = pd.date_range(end='2026-03-19', periods=50)
        df_small = pd.DataFrame({
            "Open": np.random.uniform(22000, 23000, 50),
            "High": np.random.uniform(22000, 23000, 50),
            "Low": np.random.uniform(22000, 23000, 50),
            "Close": np.random.uniform(22000, 23000, 50),
            "Volume": np.random.uniform(100000, 200000, 50)
        }, index=dates)
        df_small_out = pipeline.engineer_features(df_small)
        self.assertTrue(df_small_out.empty)

    @unittest.skipIf(isinstance(pd, MagicMock), "Pandas not available in this environment")
    def test_feature_engineering(self):
        # Create mock OHLCV data
        dates = pd.date_range(end='2026-03-19', periods=100)
        df = pd.DataFrame({
            "Open": np.random.uniform(22000, 23000, 100),
            "High": np.random.uniform(22000, 23000, 100),
            "Low": np.random.uniform(22000, 23000, 100),
            "Close": np.random.uniform(22000, 23000, 100),
            "Volume": np.random.uniform(100000, 200000, 100)
        }, index=dates)
        
        pipeline = NiftyDataPipeline(None)
        features_df = pipeline.engineer_features(df)
        
        # Check that expected columns exist
        expected_cols = ["RSI", "ATR", "MACD", "Ret_1d", "Ret_5d", "Vol_Shock", "Target"]
        for col in expected_cols:
            self.assertIn(col, features_df.columns)
            
        # Check that we have fewer rows than 100 due to technical indicators lookback
        self.assertLess(len(features_df), 100)
        self.assertGreater(len(features_df), 50)

    @unittest.skipIf(isinstance(pd, MagicMock), "Pandas not available in this environment")
    def test_training_and_prediction(self):
        # 1. Create synthetic data
        dates = pd.date_range(end='2026-03-19', periods=200)
        # Synthetic upward trend
        prices = np.linspace(20000, 23000, 200) + np.random.normal(0, 100, 200)
        df = pd.DataFrame({
            "Open": prices - 10,
            "High": prices + 20,
            "Low": prices - 20,
            "Close": prices,
            "Volume": np.random.uniform(100000, 200000, 200)
        }, index=dates)
        df.index.name = 'date'
        
        # 2. Setup Forecaster with mock pipeline
        forecaster = TrendForecaster(None, model_path="test_models/test_nifty.joblib")
        forecaster.pipeline.fetch_historical_nifty = lambda: df
        
        # 3. Train
        success = forecaster.setup_engine(force_retrain=True)
        self.assertTrue(success)
        
        # 4. Predict
        forecast = forecaster.get_forecast()
        self.assertIn(forecast["direction"], ["BULLISH", "BEARISH", "NEUTRAL"])
        self.assertGreaterEqual(forecast["confidence"], 0)
        self.assertLessEqual(forecast["confidence"], 100)
        
        # Cleanup
        import shutil
        if os.path.exists("test_models"):
            shutil.rmtree("test_models")

    @unittest.skipIf(isinstance(pd, MagicMock), "Pandas not available in this environment")
    @patch('myra_app.ml_engine.yf.download')
    def test_fetch_historical_nifty_exception(self, mock_yf_download):
        """Test that fetch_historical_nifty handles yf.download exceptions."""
        mock_yf_download.side_effect = Exception("Simulated yfinance error")

        pipeline = NiftyDataPipeline(None)
        result = pipeline.fetch_historical_nifty()

        # Check that None is returned on exception
        self.assertIsNone(result, "Should return None on exception")

    def test_get_forecast_no_model(self):
        forecaster = TrendForecaster(None, model_path="test_models/test_nifty_no_model.joblib")
        forecaster.model = None
        result = forecaster.get_forecast()
        self.assertEqual(result, {"direction": "UNKNOWN", "confidence": 0})

    @unittest.skipIf(isinstance(pd, MagicMock), "Pandas not available in this environment")
    def test_get_forecast_empty_data(self):
        forecaster = TrendForecaster(None, model_path="test_models/test_nifty_empty_data.joblib")
        forecaster.model = MagicMock()

        # Mock fetch_historical_nifty to return empty DataFrame
        forecaster.pipeline.fetch_historical_nifty = MagicMock(return_value=pd.DataFrame())

        result = forecaster.get_forecast()
        self.assertEqual(result, {"direction": "ERROR", "confidence": 0})

    @unittest.skipIf(isinstance(pd, MagicMock), "Pandas not available in this environment")
    def test_get_forecast_bullish(self):
        forecaster = TrendForecaster(None, model_path="test_models/test_nifty_bullish.joblib")
        forecaster.model = MagicMock()
        forecaster.model.predict_proba.return_value = [[0.4, 0.6]] # prob = 0.6

        # Create minimal DataFrame for engineering features
        dates = pd.date_range(end='2026-03-19', periods=20)
        df = pd.DataFrame({
            "Open": np.random.uniform(22000, 23000, 20),
            "High": np.random.uniform(22000, 23000, 20),
            "Low": np.random.uniform(22000, 23000, 20),
            "Close": np.random.uniform(22000, 23000, 20),
            "Volume": np.random.uniform(100000, 200000, 20)
        }, index=dates)

        forecaster.pipeline.fetch_historical_nifty = MagicMock(return_value=df)

        result = forecaster.get_forecast()
        self.assertEqual(result["direction"], "BULLISH")
        self.assertEqual(result["confidence"], 60.0)

    @unittest.skipIf(isinstance(pd, MagicMock), "Pandas not available in this environment")
    def test_get_forecast_bearish(self):
        forecaster = TrendForecaster(None, model_path="test_models/test_nifty_bearish.joblib")
        forecaster.model = MagicMock()
        forecaster.model.predict_proba.return_value = [[0.6, 0.4]] # prob = 0.4

        # Create minimal DataFrame for engineering features
        dates = pd.date_range(end='2026-03-19', periods=20)
        df = pd.DataFrame({
            "Open": np.random.uniform(22000, 23000, 20),
            "High": np.random.uniform(22000, 23000, 20),
            "Low": np.random.uniform(22000, 23000, 20),
            "Close": np.random.uniform(22000, 23000, 20),
            "Volume": np.random.uniform(100000, 200000, 20)
        }, index=dates)

        forecaster.pipeline.fetch_historical_nifty = MagicMock(return_value=df)

        result = forecaster.get_forecast()
        self.assertEqual(result["direction"], "BEARISH")
        self.assertEqual(result["confidence"], 60.0)

    @unittest.skipIf(isinstance(pd, MagicMock), "Pandas not available in this environment")
    def test_get_forecast_neutral(self):
        forecaster = TrendForecaster(None, model_path="test_models/test_nifty_neutral.joblib")
        forecaster.model = MagicMock()
        forecaster.model.predict_proba.return_value = [[0.5, 0.5]] # prob = 0.5

        # Create minimal DataFrame for engineering features
        dates = pd.date_range(end='2026-03-19', periods=20)
        df = pd.DataFrame({
            "Open": np.random.uniform(22000, 23000, 20),
            "High": np.random.uniform(22000, 23000, 20),
            "Low": np.random.uniform(22000, 23000, 20),
            "Close": np.random.uniform(22000, 23000, 20),
            "Volume": np.random.uniform(100000, 200000, 20)
        }, index=dates)

        forecaster.pipeline.fetch_historical_nifty = MagicMock(return_value=df)

        result = forecaster.get_forecast()
        self.assertEqual(result["direction"], "NEUTRAL")
        self.assertEqual(result["confidence"], 50.0)
    @patch('os.path.exists')
    @patch('myra_app.ml_engine.joblib.load')
    def test_trend_forecaster_load(self, mock_joblib_load, mock_os_path_exists):
        """Test the load method of TrendForecaster."""
        forecaster = TrendForecaster(None, model_path="dummy_path.joblib")

        # 1. Test success path
        mock_os_path_exists.return_value = True
        mock_joblib_load.return_value = "dummy_model"

        result = forecaster.load()

        self.assertTrue(result)
        self.assertEqual(forecaster.model, "dummy_model")

        # 2. Test exception path
        forecaster.model = None  # Reset
        mock_joblib_load.side_effect = Exception("Simulated joblib error")

        result = forecaster.load()

        self.assertFalse(result)
        self.assertIsNone(forecaster.model)

        # 3. Test file does not exist path
        forecaster.model = None  # Reset
        mock_os_path_exists.return_value = False

        result = forecaster.load()

        self.assertFalse(result)
        self.assertIsNone(forecaster.model)

class TestDilatedCNNForecasterErrors(unittest.TestCase):
    @patch.dict('sys.modules', {'tensorflow': None})
    def test_build_model_import_error(self):
        """Test build_model when tensorflow import fails."""
        forecaster = DilatedCNNForecaster()
        model = forecaster.build_model()
        self.assertIsNone(model, "build_model should return None if tensorflow cannot be imported")

    @patch.dict('sys.modules', {})
    def test_build_model_keras_sequential_mock_error(self):
        """Test build_model when tensorflow is present but Model building fails (e.g., OOM)."""
        # Create a mock for tensorflow
        mock_tf = MagicMock()
        mock_keras = MagicMock()
        mock_layers = MagicMock()
        mock_models = MagicMock()

        mock_tf.keras = mock_keras
        mock_keras.layers = mock_layers
        mock_keras.models = mock_models

        # Explicitly patch tensorflow.keras.models.Sequential to raise an Exception if imported/used
        # even though build_model might not use Sequential directly.
        # We also mock tensorflow.keras.models.Model to raise an Exception
        mock_models.Sequential = MagicMock(side_effect=Exception("GPU memory is full"))
        mock_models.Model = MagicMock(side_effect=Exception("GPU memory is full"))

        with patch.dict('sys.modules', {
            'tensorflow': mock_tf,
            'tensorflow.keras': mock_keras,
            'tensorflow.keras.layers': mock_layers,
            'tensorflow.keras.models': mock_models
        }):
            forecaster = DilatedCNNForecaster()
            model = forecaster.build_model()
            self.assertIsNone(model, "build_model should return None when Model building fails")

class TestEvolutionaryAgent(unittest.TestCase):
    @patch('myra_app.ml_engine.np.argmax')
    def test_forward_single(self, mock_argmax):
        mock_argmax.return_value = 1
        from myra_app.ml_engine import EvolutionaryAgent
        import numpy as real_np
        agent = EvolutionaryAgent(input_size=4, hidden_size=2, output_size=2)
        state = real_np.array([0.1, 0.2, 0.3, 0.4])

        with patch.object(EvolutionaryAgent, 'get_probs', return_value=real_np.array([[0.2, 0.8]])) as mock_get_probs:
            action = agent.forward(state)
            mock_get_probs.assert_called_once()
            mock_argmax.assert_called_once()
            self.assertEqual(action, 1)

    @patch('myra_app.ml_engine.np.argmax')
    def test_forward_batch(self, mock_argmax):
        import numpy as real_np
        mock_argmax.return_value = [0, 1]
        from myra_app.ml_engine import EvolutionaryAgent
        agent = EvolutionaryAgent(input_size=4, hidden_size=2, output_size=2)
        state = real_np.array([[0.1, 0.2, 0.3, 0.4], [0.4, 0.3, 0.2, 0.1]])

        with patch.object(EvolutionaryAgent, 'get_probs', return_value=real_np.array([[0.8, 0.2], [0.3, 0.7]])) as mock_get_probs:
            actions = agent.forward(state)
            mock_get_probs.assert_called_once()
            mock_argmax.assert_called_once()
            self.assertEqual(list(actions), [0, 1])

class TestSMCEnvironment(unittest.TestCase):
    @unittest.skipIf(isinstance(pd, MagicMock), "Pandas not available in this environment")
    def test_get_all_states(self):
        # 1. Test with insufficient data (<61 rows)
        cols = ['d_poc', 'absorp_ratio', 'std20', 'delivery_percent', 'sma50', 'sma200', 'rdv', 'close']
        df_short = pd.DataFrame(np.zeros((60, len(cols))), columns=cols)
        env_short = SMCEnvironment(df_short)
        states_short = env_short.get_all_states()

        self.assertIsInstance(states_short, np.ndarray)
        self.assertEqual(states_short.shape, (0,))

        # 2. Test with sufficient data (e.g. 65 rows -> 4 states)
        # Because range is (60, len(self.df) - 1), so range(60, 64) -> i=60, 61, 62, 63 -> 4 states
        df_long = pd.DataFrame(np.zeros((65, len(cols))), columns=cols)
        # Avoid division by zero in _standardize_window
        df_long['close'] = 100.0

        env_long = SMCEnvironment(df_long)
        states_long = env_long.get_all_states()

        self.assertIsInstance(states_long, np.ndarray)
        # 4 states, each state is flattened 60x8 = 480
        self.assertEqual(states_long.shape, (4, 480))

    @unittest.skipIf(isinstance(pd, MagicMock), "Pandas not available in this environment")
    def test_evaluate_agent_vectorized_fitness_calculation(self):
        # We need at least 62 rows so that indices 60 to N-2 exists
        # prices will be df['close'].values[60:-1]
        # next_prices will be df['close'].values[61:]
        # If N=65, then:
        # prices = values[60:64] -> length 4
        # next_prices = values[61:65] -> length 4

        # Prices we want at index 60, 61, 62, 63, 64
        # 60: 100.0, 61: 110.0, 62: 90.0, 63: 100.0, 64: 80.0

        close_prices = np.zeros(65)
        close_prices[60:65] = [100.0, 110.0, 90.0, 100.0, 80.0]

        high_1y = np.zeros(65)
        high_1y[60:65] = [100.0, 110.0, 110.0, 110.0, 110.0]

        df = pd.DataFrame({
            'close': close_prices,
            'high_1y': high_1y
        })

        env = SMCEnvironment(df)

        mock_agent = MagicMock()
        # The actions will map to allocations: 0 -> 0, 1 -> 0.25, 2 -> 0.5, 3 -> 1.0
        mock_agent.forward.return_value = np.array([0, 1, 2, 3])

        mock_states = np.zeros((4, 1)) # just dummy states

        fitness = env.evaluate_agent_vectorized(mock_agent, states=mock_states)

        # Expected fitness based on the test_cal script
        expected_fitness = -0.20122912440855095

        self.assertAlmostEqual(fitness, expected_fitness, places=5)

    @unittest.skipIf(isinstance(pd, MagicMock), "Pandas not available in this environment")
    def test_evaluate_agent_vectorized_empty_states(self):
        # Dataframe with length < 61
        df = pd.DataFrame({'close': np.zeros(50), 'high_1y': np.zeros(50)})
        env = SMCEnvironment(df)
        mock_agent = MagicMock()

        # Test with states explicitly passed as empty
        fitness_explicit = env.evaluate_agent_vectorized(mock_agent, states=np.array([]))
        self.assertEqual(fitness_explicit, 0)
        mock_agent.forward.assert_not_called()

        # Test with states=None, should generate empty states due to short dataframe
        fitness_auto = env.evaluate_agent_vectorized(mock_agent, states=None)
        self.assertEqual(fitness_auto, 0)
        mock_agent.forward.assert_not_called()

    @unittest.skipIf(isinstance(pd, MagicMock), "Pandas not available in this environment")
    def test_evaluate_agent_vectorized_generate_states(self):
        # Dataframe with length 62 so get_all_states will return 1 state
        cols = ['d_poc', 'absorp_ratio', 'std20', 'delivery_percent', 'sma50', 'sma200', 'rdv', 'close', 'high_1y']
        df = pd.DataFrame(np.zeros((62, len(cols))), columns=cols)

        # Add some mock values just to prevent zero divisions if standardizer runs
        df['close'] = 100.0
        df['high_1y'] = 110.0

        env = SMCEnvironment(df)

        mock_agent = MagicMock()
        mock_agent.forward.return_value = np.array([2]) # e.g. Action 2 (50%)

        # We don't need to check the exact return value here, just that get_all_states was called
        # implicitly by the fact that the agent's forward function is triggered with generated states.

        # Since we just want to ensure it calculates auto states, we patch _standardize_window to avoid complex math
        with patch.object(env, '_standardize_window', return_value=np.zeros((1, 8))):
            env.evaluate_agent_vectorized(mock_agent, states=None)

            # The agent should be called with an array of shape (1, 8) since 62 rows -> 1 state (index 60 to 60)
            # Actually range is (60, len(self.df)-1) which is (60, 61), so 1 state
            mock_agent.forward.assert_called_once()
            called_states = mock_agent.forward.call_args[0][0]
            self.assertEqual(called_states.shape, (1, 8))
class TestAEONEngine(unittest.TestCase):
    def setUp(self):
        self.librarian_mock = MagicMock()
        self.engine = AEONEngine(self.librarian_mock, model_path="nonexistent_path")

    @patch('myra_app.ml_engine.os.path.exists')
    @patch('myra_app.ml_engine.joblib.load')
    def test_load(self, mock_joblib_load, mock_exists):
        # Test when path does not exist
        mock_exists.return_value = False
        self.assertFalse(self.engine.load())

        # Test when path exists but joblib throws an exception
        mock_exists.return_value = True
        mock_joblib_load.side_effect = Exception("Simulated load error")
        self.assertFalse(self.engine.load())

        # Test when path exists but genes have wrong size
        mock_joblib_load.side_effect = None
        mock_joblib_load.return_value = np.zeros(10)
        self.assertFalse(self.engine.load())

        # Test successful load
        expected_size = 480 * 16 + 16 + 16 * 4 + 4
        mock_genes = MagicMock()
        mock_genes.__len__.return_value = expected_size
        mock_joblib_load.return_value = mock_genes
        self.engine.agent.set_genes = MagicMock()
        self.assertTrue(self.engine.load())
        self.engine.agent.set_genes.assert_called_once()

    @unittest.skipIf(isinstance(pd, MagicMock), "Pandas not available in this environment")
    def test_empty_dataframe(self):
        df = pd.DataFrame()
        result = self.engine.get_conviction(symbol="TEST", df=df)
        self.assertEqual(result, "N/A")

    @unittest.skipIf(isinstance(pd, MagicMock), "Pandas not available in this environment")
    def test_short_dataframe(self):
        # 50 rows
        df = pd.DataFrame({'close': np.random.uniform(100, 200, 50)})
        result = self.engine.get_conviction(symbol="TEST", df=df)
        self.assertEqual(result, "N/A")

    @unittest.skipIf(isinstance(pd, MagicMock), "Pandas not available in this environment")
    def test_missing_columns_no_funda(self):
        # 60 rows but missing required columns like 'd_poc', 'absorp_ratio'
        df = pd.DataFrame({'close': np.random.uniform(100, 200, 60)})
        result = self.engine.get_conviction(symbol="TEST", df=df)
        self.assertEqual(result, "N/A")

    @unittest.skipIf(isinstance(pd, MagicMock), "Pandas not available in this environment")
    def test_happy_path(self):
        # 60 rows with all required columns
        df = pd.DataFrame({
            'd_poc': np.random.uniform(100, 200, 60),
            'absorp_ratio': np.random.uniform(0.5, 1.5, 60),
            'std20': np.random.uniform(1, 10, 60),
            'delivery_percent': np.random.uniform(10, 90, 60),
            'sma50': np.random.uniform(100, 200, 60),
            'sma200': np.random.uniform(100, 200, 60),
            'rdv': np.random.uniform(0.1, 2.0, 60),
            'close': np.random.uniform(100, 200, 60)
        })
        result = self.engine.get_conviction(symbol="TEST", df=df)
        valid_returns = ["EXIT / Stay Out", "TACTICAL (25%)", "CORE LOAD (50%)", "CONVICTION (100%)", "Unknown", "N/A"]
        self.assertIn(result, valid_returns)

class TestDilatedCNNForecasterPredictNext(unittest.TestCase):
    @patch('os.path.exists')
    def test_predict_next_no_model_path(self, mock_exists):
        """Test predict_next when no model exists."""
        mock_exists.return_value = False
        forecaster = DilatedCNNForecaster()
        forecaster.model = None
        self.assertIsNone(forecaster.predict_next(MagicMock()))

    def test_predict_next_insufficient_data(self):
        """Test predict_next when data length is less than window_size."""
        forecaster = DilatedCNNForecaster()
        forecaster.model = MagicMock()
        forecaster.window_size = 60

        mock_df = MagicMock()
        mock_df.__len__.return_value = 50

        self.assertIsNone(forecaster.predict_next(mock_df))

    @patch.dict('sys.modules', {'sklearn': MagicMock(), 'sklearn.preprocessing': MagicMock()})
    def test_predict_next_success(self):
        """Test predict_next successful prediction."""
        forecaster = DilatedCNNForecaster()
        forecaster.window_size = 60
        forecaster.features_count = 8

        mock_model = MagicMock()
        mock_model.predict.return_value = [[0.8]]
        forecaster.model = mock_model

        mock_df = MagicMock()
        mock_df.__len__.return_value = 65

        class MockDataArray:
            def __getitem__(self, key):
                if isinstance(key, slice):
                    mock_slice = MagicMock()
                    mock_slice.reshape.return_value = "reshaped_window"
                    return mock_slice
                elif isinstance(key, tuple) and key == (-1, -1):
                    return 0.5
                return MagicMock()

        mock_scaler = MagicMock()
        mock_scaler.fit_transform.return_value = MockDataArray()

        with patch('sklearn.preprocessing.StandardScaler', return_value=mock_scaler, create=True):
            result = forecaster.predict_next(mock_df)

        self.assertAlmostEqual(result, 0.6, places=5)

    @unittest.skipIf(isinstance(pd, MagicMock), "Requires actual libraries")
    def test_build_model(self):
        try:
            import tensorflow.keras.models
        except ImportError:
            self.skipTest("tensorflow not available")

        forecaster = DilatedCNNForecaster()
        model = forecaster.build_model()

        self.assertIsNotNone(model, "Model should not be None")

        from tensorflow.keras.models import Model
        self.assertIsInstance(model, Model, "Built model should be a Keras Model instance")

        # Validate shapes
        self.assertEqual(model.input_shape, (None, 60, 8))
        self.assertEqual(model.output_shape, (None, 1))

        # Validate layer presence
        layer_types = [type(layer).__name__ for layer in model.layers]
        self.assertIn("Conv1D", layer_types)
        self.assertIn("Dense", layer_types)
class TestEvolutionaryAgent(unittest.TestCase):
    @unittest.skipIf(isinstance(np, MagicMock), "Numpy not available in this environment")
    def test_get_genes(self):
        """Test that get_genes flattens weights deterministically into a single vector."""
        agent = EvolutionaryAgent(input_size=2, hidden_size=2, output_size=2)

        # Override weights with deterministic arrays
        # Sorted keys: ['W1', 'W2', 'b1', 'b2']
        agent.weights = {
            'W1': np.array([[1.0, 2.0], [3.0, 4.0]]),
            'b1': np.array([[0.1, 0.2]]),
            'W2': np.array([[5.0, 6.0], [7.0, 8.0]]),
            'b2': np.array([[0.3, 0.4]])
        }

        # Expected flattened arrays based on sorted keys
        # W1: [1.0, 2.0, 3.0, 4.0]
        # W2: [5.0, 6.0, 7.0, 8.0]
        # b1: [0.1, 0.2]
        # b2: [0.3, 0.4]
        # Expected concatenation order: W1, W2, b1, b2
        expected_genes = np.array([
            1.0, 2.0, 3.0, 4.0,  # W1
            5.0, 6.0, 7.0, 8.0,  # W2
            0.1, 0.2,            # b1
            0.3, 0.4             # b2
        ])

        genes = agent.get_genes()

        # We handle np.testing gracefully in case it's mocked, but with @skipIf this shouldn't execute
        np.testing.assert_array_almost_equal(genes, expected_genes)
        self.assertEqual(genes.shape, expected_genes.shape)

if __name__ == "__main__":
    unittest.main()
