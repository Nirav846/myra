"""Tests for BottomHunter._check_delivery_spike."""

import pandas as pd
import pytest

from myra_app.strategies.bottom_hunter import BottomHunter


def _make_df(delivery_pcts: list[float], highs: list[float], lows: list[float], closes: list[float]) -> pd.DataFrame:
    """Build a minimal DataFrame with the columns _check_delivery_spike reads."""
    n = len(delivery_pcts)
    return pd.DataFrame({
        "date": pd.date_range("2025-01-01", periods=n),
        "open": [100.0] * n,
        "high": highs,
        "low": lows,
        "close": closes,
        "volume": [1_000_000] * n,
        "delivery": [50_000] * n,
        "delivery_pct": delivery_pcts,
        "nifty_outperformance_score": [0.0] * n,
        "sma_50": [100.0] * n,
        "high_52w": [150.0] * n,
        "low_52w": [50.0] * n,
    })


class TestDeliverySpike:
    def setup_method(self):
        self.scanner = BottomHunter()

    def test_spike_true(self):
        """Delivery 2× average and close near the high → True."""
        # 50 rows of baseline delivery_pct=0.40, last row = 0.80 (2×)
        delps = [0.40] * 50 + [0.80]
        highs = [110.0] * 51
        lows = [90.0] * 51
        closes = [100.0] * 50 + [109.0]  # last close near high → CLR ≈ 0.95
        df = _make_df(delps, highs, lows, closes)
        assert self.scanner._check_delivery_spike(df) is True

    def test_no_spike_delivery_below_threshold(self):
        """Delivery only 1.1× average → False."""
        delps = [0.40] * 50 + [0.44]
        highs = [110.0] * 51
        lows = [90.0] * 51
        closes = [100.0] * 51
        df = _make_df(delps, highs, lows, closes)
        assert self.scanner._check_delivery_spike(df) is False

    def test_spike_but_close_in_bottom_half(self):
        """Delivery spikes but close is in the bottom half of the range → False."""
        delps = [0.40] * 50 + [0.80]  # 2× spike
        highs = [110.0] * 51
        lows = [90.0] * 51
        closes = [100.0] * 50 + [94.0]  # CLR = (94-90)/(110-90) = 0.2 < 0.6
        df = _make_df(delps, highs, lows, closes)
        assert self.scanner._check_delivery_spike(df) is False

    def test_too_few_rows(self):
        """Fewer than 20 rows → False."""
        df = _make_df([0.40] * 10, [110.0] * 10, [90.0] * 10, [100.0] * 10)
        assert self.scanner._check_delivery_spike(df) is False
