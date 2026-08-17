"""Unit tests for Deep Fundamentals math: Graham Number, Piotroski F-Score, DCF.

All tests are offline (no network, no DB). Piotroski tests mock yfinance.
"""

import math
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "myra_web"))
from routes.full_fundamentals import compute_graham_metrics, compute_piotroski_score, compute_dcf


# =========================================================================== #
# Graham Number + Defensive Criteria
# =========================================================================== #

class TestGrahamMetrics:
    """compute_graham_metrics: conservative intrinsic value + defensive checklist."""

    def test_basic_computation(self):
        """Graham = sqrt(22.5 * eps * bv) = sqrt(22.5*50*200) ≈ 474.34. Price=300 → undervalued."""
        data = {
            "yfinance": {
                "trailing_eps": 50,
                "book_value": 200,
                "current_price": 300,
                "pe": 6,
                "price_to_book": 1.0,
                "current_ratio": 3.0,
                "debt_to_equity": 0.3,
            },
            "snapshot": {"current_price": 300},
        }
        r = compute_graham_metrics(data)
        gn = math.sqrt(22.5 * 50 * 200)
        assert r["graham_number"] == round(gn, 2)
        assert r["current_price"] == 300
        assert r["margin_of_safety"] is not None
        assert r["margin_of_safety"] > 0  # stock is below Graham Number

    def test_graham_number_none_when_eps_missing(self):
        data = {"yfinance": {"book_value": 200, "current_price": 1200}}
        r = compute_graham_metrics(data)
        assert r["graham_number"] is None

    def test_graham_number_none_when_book_value_missing(self):
        data = {"yfinance": {"trailing_eps": 50, "current_price": 1200}}
        r = compute_graham_metrics(data)
        assert r["graham_number"] is None

    def test_graham_number_none_when_eps_negative(self):
        data = {"yfinance": {"trailing_eps": -5, "book_value": 200, "current_price": 1200}}
        r = compute_graham_metrics(data)
        assert r["graham_number"] is None

    def test_graham_number_none_when_book_value_zero(self):
        data = {"yfinance": {"trailing_eps": 50, "book_value": 0, "current_price": 1200}}
        r = compute_graham_metrics(data)
        assert r["graham_number"] is None

    def test_margin_of_safety_negative_when_overvalued(self):
        data = {
            "yfinance": {
                "trailing_eps": 1,
                "book_value": 1,
                "current_price": 200,  # much higher than sqrt(22.5*1*1) ≈ 4.74
            },
        }
        r = compute_graham_metrics(data)
        assert r["margin_of_safety"] is not None
        assert r["margin_of_safety"] < 0

    def test_margin_of_safety_none_when_no_price(self):
        data = {"yfinance": {"trailing_eps": 50, "book_value": 200}}
        r = compute_graham_metrics(data)
        assert r["graham_number"] is not None
        assert r["margin_of_safety"] is None

    def test_defensive_passes_all_criteria(self):
        data = {
            "yfinance": {
                "pe": 10,
                "price_to_book": 1.2,
                "current_ratio": 3.0,
                "debt_to_equity": 0.4,
            },
        }
        r = compute_graham_metrics(data)
        d = r["defensive"]
        assert d["pass"] is True
        assert d["pe_under_15"] is True
        assert d["pb_under_1_5"] is True
        assert d["pe_x_pb_under_22_5"] is True
        assert d["current_ratio_above_2"] is True
        assert d["debt_equity_under_1"] is True
        assert len(d["failures"]) == 0

    def test_defensive_fails_pe(self):
        data = {"yfinance": {"pe": 30, "price_to_book": 1.0}}
        r = compute_graham_metrics(data)
        d = r["defensive"]
        assert d["pass"] is False
        assert any("P/E" in f for f in d["failures"])

    def test_defensive_fails_pb(self):
        data = {"yfinance": {"pe": 10, "price_to_book": 3.0}}
        r = compute_graham_metrics(data)
        d = r["defensive"]
        assert d["pass"] is False
        assert any("P/B" in f for f in d["failures"])

    def test_defensive_fails_pe_x_pb(self):
        data = {"yfinance": {"pe": 12, "price_to_book": 2.0}}  # 12*2=24 > 22.5
        r = compute_graham_metrics(data)
        d = r["defensive"]
        assert d["pass"] is False
        assert any("P/E×P/B" in f for f in d["failures"])

    def test_defensive_skips_optional_criteria_when_missing(self):
        data = {"yfinance": {"pe": 10, "price_to_book": 1.0}}  # no current_ratio, no D/E
        r = compute_graham_metrics(data)
        d = r["defensive"]
        assert "current_ratio_above_2" not in d
        assert "debt_equity_under_1" not in d
        assert d["pass"] is True

    def test_defensive_normalizes_debt_to_equity(self):
        # yfinance returns D/E as percentage (e.g. 50 for 0.5)
        data = {"yfinance": {"pe": 10, "price_to_book": 1.0, "debt_to_equity": 50}}
        r = compute_graham_metrics(data)
        d = r["defensive"]
        assert d["debt_equity_under_1"] is True  # 50/100 = 0.5 < 1

    def test_defensive_no_defensive_when_pe_pb_missing(self):
        data = {"yfinance": {}}
        r = compute_graham_metrics(data)
        assert r["defensive"] is None

    def test_empty_data(self):
        r = compute_graham_metrics({})
        assert r["graham_number"] is None
        assert r["current_price"] is None
        assert r["margin_of_safety"] is None
        assert r["defensive"] is None


# =========================================================================== #
# Piotroski F-Score
# =========================================================================== #

class TestPiotroskiScore:
    """compute_piotroski_score: simplified 6-criterion F-Score.

    All tests mock yfinance to avoid network calls.
    The import happens inside the function body: `import yfinance as _yf`
    so we must patch `yfinance.Ticker` directly.
    """

    def _make_data(self, **overrides):
        """Build a minimal data dict for piotroski."""
        base = {
            "symbol": "RELIANCE",
            "yfinance": {
                "roa": 0.08,
                "operating_cashflow": 15_000_000_000,
                "net_income": 12_000_000_000,
                "current_ratio": 1.2,
                "grossMargins": 0.35,
            },
        }
        base["yfinance"].update(overrides.get("yfinance", {}))
        if "symbol" in overrides:
            base["symbol"] = overrides["symbol"]
        return base

    def test_returns_none_when_no_symbol(self):
        r = compute_piotroski_score({"yfinance": {}})
        assert r is None

    def test_returns_none_when_yfinance_fails(self):
        with patch("yfinance.Ticker", side_effect=Exception("network error")):
            r = compute_piotroski_score({"symbol": "RELIANCE", "yfinance": {}})
        assert r is None

    def test_strong_score_all_met(self):
        data = self._make_data()
        import pandas as pd

        # yfinance DataFrames: rows = metric labels, columns = year labels
        fin = pd.DataFrame(
            {"2025": [350, 1000], "2024": [300, 1000]},
            index=["Gross Profit", "Total Revenue"],
        )
        bs = pd.DataFrame(
            {"2025": [1.5, 500, 2000], "2024": [1.2, 600, 2000]},
            index=["Current Ratio", "Total Debt", "Total Assets"],
        )

        mock_ticker = MagicMock()
        mock_ticker.financials = fin
        mock_ticker.balance_sheet = bs

        with patch("yfinance.Ticker", return_value=mock_ticker):
            r = compute_piotroski_score(data)

        assert r is not None
        assert r["score"] == r["max_score"]
        assert r["classification"] == "Strong"
        assert all(c["met"] for c in r["criteria"])

    def test_weak_score_none_met(self):
        data = self._make_data(yfinance={"roa": -0.05, "operating_cashflow": -1e9, "net_income": 5e9})
        import pandas as pd

        fin = pd.DataFrame(
            {"2025": [250, 1000], "2024": [300, 1000]},
            index=["Gross Profit", "Total Revenue"],
        )
        bs = pd.DataFrame(
            {"2025": [0.8, 800, 2000], "2024": [1.2, 500, 2000]},
            index=["Current Ratio", "Total Debt", "Total Assets"],
        )

        mock_ticker = MagicMock()
        mock_ticker.financials = fin
        mock_ticker.balance_sheet = bs

        with patch("yfinance.Ticker", return_value=mock_ticker):
            r = compute_piotroski_score(data)

        assert r is not None
        assert r["classification"] == "Weak"
        assert r["score"] <= 2

    def test_moderate_score(self):
        # ROA positive, CFO positive, CFO > NI — 3 out of 6
        data = self._make_data()
        import pandas as pd

        # No YoY improvements (declining current ratio, increasing leverage, declining margin)
        fin = pd.DataFrame(
            {"2025": [250, 1000], "2024": [300, 1000]},
            index=["Gross Profit", "Total Revenue"],
        )
        bs = pd.DataFrame(
            {"2025": [0.8, 800, 2000], "2024": [1.2, 500, 2000]},
            index=["Current Ratio", "Total Debt", "Total Assets"],
        )

        mock_ticker = MagicMock()
        mock_ticker.financials = fin
        mock_ticker.balance_sheet = bs

        with patch("yfinance.Ticker", return_value=mock_ticker):
            r = compute_piotroski_score(data)

        assert r is not None
        assert r["score"] == 3  # ROA+, CFO+, CFO>NI
        assert r["classification"] == "Moderate"

    def test_returns_none_when_no_criteria_available(self):
        """With no yfinance data and ticker returning None for all fields."""
        data = {"symbol": "FAKE", "yfinance": {}}
        mock_ticker = MagicMock()
        mock_ticker.financials = None
        mock_ticker.balance_sheet = None

        with patch("yfinance.Ticker", return_value=mock_ticker):
            r = compute_piotroski_score(data)
        assert r is None

    def test_criteria_has_correct_structure(self):
        data = self._make_data()
        mock_ticker = MagicMock()
        mock_ticker.financials = None
        mock_ticker.balance_sheet = None

        with patch("yfinance.Ticker", return_value=mock_ticker):
            r = compute_piotroski_score(data)

        assert r is not None
        assert "score" in r
        assert "max_score" in r
        assert "classification" in r
        assert "criteria" in r
        for c in r["criteria"]:
            assert "name" in c
            assert "met" in c
            assert "detail" in c


# =========================================================================== #
# Two-Stage DCF Intrinsic Value
# =========================================================================== #

class TestDCF:
    """compute_dcf: two-stage discounted cash flow model."""

    def test_basic_computation(self):
        data = {
            "yfinance": {
                "free_cashflow": 10_000_000_000,
                "shares_outstanding": 6_700_000_000,
                "total_debt": 200_000_000_000,
                "total_cash": 500_000_000_000,
                "current_price": 2500,
                "revenue_growth": 0.15,
            },
            "snapshot": {"current_price": 2500},
        }
        r = compute_dcf(data)
        assert r is not None
        assert "fair_value" in r
        assert "margin_of_safety" in r
        assert r["discount_rate"] == 0.10
        assert r["terminal_growth"] == 0.03
        assert r["fair_value"] > 0

    def test_returns_none_when_fcf_missing(self):
        data = {
            "yfinance": {"shares_outstanding": 6_700_000_000, "current_price": 2500},
            "snapshot": {"current_price": 2500},
        }
        r = compute_dcf(data)
        assert r is None

    def test_returns_none_when_shares_missing(self):
        data = {
            "yfinance": {"free_cashflow": 10_000_000_000, "current_price": 2500},
            "snapshot": {"current_price": 2500},
        }
        r = compute_dcf(data)
        assert r is None

    def test_returns_none_when_price_missing(self):
        data = {
            "yfinance": {
                "free_cashflow": 10_000_000_000,
                "shares_outstanding": 6_700_000_000,
            },
            "snapshot": {},
        }
        r = compute_dcf(data)
        assert r is None

    def test_returns_none_when_fcf_zero(self):
        data = {
            "yfinance": {
                "free_cashflow": 0,
                "shares_outstanding": 6_700_000_000,
                "current_price": 2500,
            },
            "snapshot": {"current_price": 2500},
        }
        r = compute_dcf(data)
        assert r is None

    def test_growth_capped_at_25_percent(self):
        data = {
            "yfinance": {
                "free_cashflow": 10_000_000_000,
                "shares_outstanding": 6_700_000_000,
                "current_price": 2500,
                "revenue_growth": 0.50,  # > 25% → should be capped
            },
            "snapshot": {"current_price": 2500},
        }
        r = compute_dcf(data)
        assert r is not None
        # The fair value should be the same as if growth was 25%
        data_capped = {
            "yfinance": {
                "free_cashflow": 10_000_000_000,
                "shares_outstanding": 6_700_000_000,
                "current_price": 2500,
                "revenue_growth": 0.25,
            },
            "snapshot": {"current_price": 2500},
        }
        r_capped = compute_dcf(data_capped)
        assert r["fair_value"] == r_capped["fair_value"]

    def test_defaults_to_5_percent_growth_when_no_growth(self):
        data = {
            "yfinance": {
                "free_cashflow": 10_000_000_000,
                "shares_outstanding": 6_700_000_000,
                "current_price": 2500,
            },
            "snapshot": {"current_price": 2500},
        }
        r = compute_dcf(data)
        assert r is not None
        assert r["fair_value"] > 0

    def test_negative_growth_defaults_to_5_percent(self):
        data = {
            "yfinance": {
                "free_cashflow": 10_000_000_000,
                "shares_outstanding": 6_700_000_000,
                "current_price": 2500,
                "revenue_growth": -0.10,  # negative → should default to 5%
            },
            "snapshot": {"current_price": 2500},
        }
        r = compute_dcf(data)
        assert r is not None
        # Should be same as no-growth case
        data_no_growth = {
            "yfinance": {
                "free_cashflow": 10_000_000_000,
                "shares_outstanding": 6_700_000_000,
                "current_price": 2500,
            },
            "snapshot": {"current_price": 2500},
        }
        r_no_growth = compute_dcf(data_no_growth)
        assert r["fair_value"] == r_no_growth["fair_value"]

    def test_net_cash_adds_to_equity_value(self):
        data_with_cash = {
            "yfinance": {
                "free_cashflow": 10_000_000_000,
                "shares_outstanding": 6_700_000_000,
                "current_price": 2500,
                "total_debt": 100_000_000_000,
                "total_cash": 300_000_000_000,
            },
            "snapshot": {"current_price": 2500},
        }
        data_no_cash = {
            "yfinance": {
                "free_cashflow": 10_000_000_000,
                "shares_outstanding": 6_700_000_000,
                "current_price": 2500,
            },
            "snapshot": {"current_price": 2500},
        }
        r_with = compute_dcf(data_with_cash)
        r_without = compute_dcf(data_no_cash)
        # Net cash = 300B - 100B = 200B → per share = 200B/6.7B ≈ 29.85
        assert r_with["fair_value"] > r_without["fair_value"]

    def test_margin_of_safety_positive_when_undervalued(self):
        data = {
            "yfinance": {
                "free_cashflow": 50_000_000_000,  # large FCF
                "shares_outstanding": 6_700_000_000,
                "current_price": 100,  # very low price → undervalued
                "revenue_growth": 0.20,
            },
            "snapshot": {"current_price": 100},
        }
        r = compute_dcf(data)
        assert r["margin_of_safety"] > 0

    def test_margin_of_safety_negative_when_overvalued(self):
        data = {
            "yfinance": {
                "free_cashflow": 100_000_000,  # tiny FCF
                "shares_outstanding": 6_700_000_000,
                "current_price": 5000,  # very high price → overvalued
                "revenue_growth": 0.05,
            },
            "snapshot": {"current_price": 5000},
        }
        r = compute_dcf(data)
        assert r["margin_of_safety"] < 0

    def test_uses_earnings_growth_when_revenue_growth_missing(self):
        data_rev = {
            "yfinance": {
                "free_cashflow": 10_000_000_000,
                "shares_outstanding": 6_700_000_000,
                "current_price": 2500,
                "revenue_growth": 0.15,
            },
            "snapshot": {"current_price": 2500},
        }
        data_earn = {
            "yfinance": {
                "free_cashflow": 10_000_000_000,
                "shares_outstanding": 6_700_000_000,
                "current_price": 2500,
                "earnings_growth": 0.15,
            },
            "snapshot": {"current_price": 2500},
        }
        r_rev = compute_dcf(data_rev)
        r_earn = compute_dcf(data_earn)
        # Same growth rate → same fair value
        assert r_rev["fair_value"] == r_earn["fair_value"]
