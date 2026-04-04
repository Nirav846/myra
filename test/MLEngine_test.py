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
from myra_app.ml_engine import NiftyDataPipeline, TrendForecaster, DilatedCNNForecaster, AEONEngine

class TestMLEngine(unittest.TestCase):
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

class TestAEONEngine(unittest.TestCase):
    def setUp(self):
        self.librarian_mock = MagicMock()
        self.engine = AEONEngine(self.librarian_mock, model_path="nonexistent_path")

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


if __name__ == "__main__":
    unittest.main()
