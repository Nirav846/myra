import unittest
from unittest.mock import MagicMock, patch
import sys

# Mocking dependencies for environments where they are missing
class MockNaN:
    def __repr__(self): return "NaN"

def mock_isna(val):
    return val is None or isinstance(val, MockNaN)

# 1. Create Mocks
mock_pd = MagicMock()
mock_pd.isna.side_effect = mock_isna

mock_np = MagicMock()
mock_np.nan = MockNaN()

# 2. Inject into sys.modules BEFORE any other imports
if 'pandas' not in sys.modules:
    sys.modules['pandas'] = mock_pd
if 'numpy' not in sys.modules:
    sys.modules['numpy'] = mock_np

# 3. Import (will get the mocks)
import pandas as pd
import numpy as np

# 4. Import the module under test (will also use the injected mocks)
from myra_app.score_components_v2 import (
    precompute_ranks, trend_score, stability_score, delivery_score,
    liquidity_score, base_score, fundamental_score, valuation_score,
    regime_adjustment
)

class TestScoreComponentsV2(unittest.TestCase):
    def test_mock_setup(self):
        """Verify that the mock setup works as expected."""
        self.assertTrue(pd.isna(None))
        self.assertTrue(pd.isna(np.nan))
        self.assertFalse(pd.isna(10))

    def test_precompute_ranks_all_columns(self):
        """Verify that precompute_ranks calls .rank() on all target columns."""
        # Setup a mock DataFrame
        df = MagicMock()
        df.columns = ["roe", "ProfitGrowth", "avg_delivery_20d", "avg_volume_20d", "smart_money_score"]

        # Mocking the __setitem__ and __getitem__ to simulate column ranking
        mock_series = MagicMock()
        df.__getitem__.return_value = mock_series
        mock_series.rank.return_value = "ranked_series"

        result_df = precompute_ranks(df)

        # Verify rank calls for all columns
        # roe, ProfitGrowth, avg_delivery_20d, avg_volume_20d, smart_money_score
        self.assertEqual(mock_series.rank.call_count, 5)
        mock_series.rank.assert_called_with(pct=True, na_option='bottom')

        # Verify that new rank columns are assigned (5 columns)
        self.assertEqual(df.__setitem__.call_count, 5)

    def test_precompute_ranks_no_columns(self):
        """Verify that precompute_ranks handles missing columns gracefully."""
        df = MagicMock()
        df.columns = []
        result_df = precompute_ranks(df)
        self.assertEqual(df.__setitem__.call_count, 0)

    def test_trend_score_all_conditions(self):
        """Verify that trend_score correctly increments score based on conditions."""
        # Condition 1: sma50 > sma150 (+0.4)
        # Condition 2: sma150 > sma200 (+0.4)
        # Condition 3: close > sma200 (+0.2)

        # Test case: All conditions met (0.4 + 0.4 + 0.2 = 1.0)
        row = {"sma50": 100, "sma150": 90, "sma200": 80, "close": 85}
        self.assertAlmostEqual(trend_score(row), 1.0)

        # Test case: None conditions met (0.0)
        row = {"sma50": 70, "sma150": 80, "sma200": 90, "close": 75}
        self.assertAlmostEqual(trend_score(row), 0.0)

        # Test case: Partial conditions met (0.4 + 0.2 = 0.6)
        # Condition 1 met, Condition 3 met
        row = {"sma50": 100, "sma150": 90, "sma200": 110, "close": 115}
        self.assertAlmostEqual(trend_score(row), 0.6)

    def test_trend_score_nan_fallback(self):
        """Verify that trend_score returns 0.3 fallback if any input is NaN."""
        # Test each column being NaN independently
        inputs = ["sma50", "sma150", "sma200", "close"]
        for col in inputs:
            row = {"sma50": 100, "sma150": 90, "sma200": 80, "close": 85}
            row[col] = np.nan
            self.assertEqual(trend_score(row), 0.3)

    def test_stability_score(self):
        """Verify that stability_score returns pct_above_ma50_60d or defaults to 0.3."""
        # Normal value
        row = {"pct_above_ma50_60d": 0.85}
        self.assertEqual(stability_score(row), 0.85)

        # NaN value
        row = {"pct_above_ma50_60d": np.nan}
        self.assertEqual(stability_score(row), 0.3)

        # Missing value (get default 0 from row.get, then not pd.isna(0) is True)
        row = {}
        self.assertEqual(stability_score(row), 0.0)

    def test_delivery_score_calculation(self):
        """Verify formula: (sm_rank * 0.6) + (d_rank * 0.4)."""
        # (0.8 * 0.6) + (0.5 * 0.4) = 0.48 + 0.2 = 0.68
        row = {"_rank_sm_score": 0.8, "_rank_delivery": 0.5}
        self.assertAlmostEqual(delivery_score(row), 0.68)

    def test_delivery_score_fallbacks(self):
        """Verify fallbacks for missing or NaN ranks."""
        # sm_rank missing -> fallback 0.3
        # d_rank present 0.5
        # (0.3 * 0.6) + (0.5 * 0.4) = 0.18 + 0.2 = 0.38
        row = {"_rank_delivery": 0.5}
        self.assertAlmostEqual(delivery_score(row), 0.38)

        # Both NaN
        row = {"_rank_sm_score": np.nan, "_rank_delivery": np.nan}
        self.assertAlmostEqual(delivery_score(row), 0.3) # (0.3 * 0.6) + (0.3 * 0.4) = 0.3

        # Both None
        row = {"_rank_sm_score": None, "_rank_delivery": None}
        self.assertAlmostEqual(delivery_score(row), 0.3)

    def test_liquidity_score(self):
        """Verify it returns _rank_volume or 0.3."""
        # Case: normal value
        row = {"_rank_volume": 0.75}
        self.assertEqual(liquidity_score(row), 0.75)

        # Case: NaN
        row = {"_rank_volume": np.nan}
        self.assertEqual(liquidity_score(row), 0.3)

        # Case: None
        row = {"_rank_volume": None}
        self.assertEqual(liquidity_score(row), 0.3)

        # Case: Missing
        row = {}
        self.assertEqual(liquidity_score(row), 0.3)

    def test_base_score_atr_thresholds(self):
        """Verify ATR thresholds: 0.5 if <0.03, 0.3 if <0.05, 0.3 fallback."""
        # Test Case 1: ATR < 0.03
        row = {"atr_pct": 0.02}
        self.assertEqual(base_score(row), 0.5)

        # Test Case 2: ATR < 0.05
        row = {"atr_pct": 0.04}
        self.assertEqual(base_score(row), 0.3)

        # Test Case 3: ATR >= 0.05 (fallback logic returns 0.3 because score remains 0)
        row = {"atr_pct": 0.06}
        self.assertEqual(base_score(row), 0.3)

        # Test Case 4: ATR is NaN (defaults to 0.05 -> score 0 -> fallback 0.3)
        row = {"atr_pct": np.nan}
        self.assertEqual(base_score(row), 0.3)

    def test_base_score_ttm_squeeze(self):
        """Verify TTM Squeeze bonus (+0.5)."""
        # Case: ATR < 0.03 (+0.5) AND TTM Squeeze met (+0.5) = 1.0
        row = {
            "atr_pct": 0.02,
            "keltner_upper": 100,
            "keltner_lower": 80,
            "close": 90,
            "atr5": 5 # close + atr5 = 95 < 100, close - atr5 = 85 > 80
        }
        self.assertEqual(base_score(row), 1.0)

        # Case: ATR >= 0.05 (0) AND TTM Squeeze met (+0.5) = 0.5
        row = {
            "atr_pct": 0.06,
            "keltner_upper": 100,
            "keltner_lower": 80,
            "close": 90,
            "atr5": 5
        }
        self.assertEqual(base_score(row), 0.5)

    def test_fundamental_score_calculation(self):
        """Verify (roe * 0.4) + (growth * 0.3) + (quality * 0.3)."""
        # roe=0.8, growth=0.5, quality (F_Score/5.0) = 4/5 = 0.8
        # (0.8 * 0.4) + (0.5 * 0.3) + (0.8 * 0.3) = 0.32 + 0.15 + 0.24 = 0.71
        row = {"_rank_roe": 0.8, "_rank_growth": 0.5, "F_Score": 4}
        self.assertAlmostEqual(fundamental_score(row), 0.71)

    def test_fundamental_score_fallbacks(self):
        """Verify 0.4 fallback for missing ranks."""
        # roe missing, growth present 0.5, F_Score 3 (0.6)
        # (0.4 * 0.4) + (0.5 * 0.3) + (0.6 * 0.3) = 0.16 + 0.15 + 0.18 = 0.49
        row = {"_rank_growth": 0.5, "F_Score": 3}
        self.assertAlmostEqual(fundamental_score(row), 0.49)

        # All missing/NaN (except F_Score 0)
        # (0.4 * 0.4) + (0.4 * 0.3) + (0 * 0.3) = 0.16 + 0.12 = 0.28
        row = {"_rank_roe": np.nan, "_rank_growth": None, "F_Score": 0}
        self.assertAlmostEqual(fundamental_score(row), 0.28)

    def test_valuation_score_categories(self):
        """Verify Margin of Safety (MOS) thresholds."""
        # mos = (graham / close) - 1

        # Case 1: Deep Value (mos > 0.3)
        # graham = 140, close = 100, mos = 0.4 -> 1.0
        row = {"graham_number": 140, "close": 100}
        self.assertEqual(valuation_score(row), 1.0)

        # Case 2: Fairly Valued (mos > 0)
        # graham = 110, close = 100, mos = 0.1 -> 0.8
        row = {"graham_number": 110, "close": 100}
        self.assertEqual(valuation_score(row), 0.8)

        # Case 3: Slightly Overvalued (mos > -0.2)
        # graham = 90, close = 100, mos = -0.1 -> 0.5
        row = {"graham_number": 90, "close": 100}
        self.assertEqual(valuation_score(row), 0.5)

        # Case 4: Expensive (mos <= -0.2)
        # graham = 70, close = 100, mos = -0.3 -> 0.2
        row = {"graham_number": 70, "close": 100}
        self.assertEqual(valuation_score(row), 0.2)

    def test_valuation_score_fallback(self):
        """Verify 0.5 fallback if graham_number <= 0."""
        # Case: graham_number <= 0
        row = {"graham_number": 0, "close": 100}
        self.assertEqual(valuation_score(row), 0.5)

    def test_regime_adjustment(self):
        """Verify multipliers: 1.15 for FEAR, 0.9 for GREED."""
        score = 0.5

        # EXTREME FEAR
        self.assertAlmostEqual(regime_adjustment(score, "EXTREME FEAR"), 0.575)

        # EXTREME GREED
        self.assertAlmostEqual(regime_adjustment(score, "EXTREME GREED"), 0.45)

        # NEUTRAL / Other
        self.assertAlmostEqual(regime_adjustment(score, "NEUTRAL"), 0.5)

if __name__ == '__main__':
    unittest.main()
