"""Tests for myra_app.options_chain — NSE option-chain fetcher + PCR."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import requests

from myra_app.options_chain import (
    INDICES,
    compute_pcr,
    fetch_option_chain,
    parse_option_chain,
    pcr_regime,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_oc(
    *,
    strikes: list[float] | None = None,
    spot: float = 24000.0,
    expiry: str = "2026-01-15",
    other_expiry: str = "2026-01-22",
    ce_oi: int | list[int] = 1000,
    pe_oi: int | list[int] = 1200,
) -> dict[str, Any]:
    """Build a minimal NSE option-chain JSON payload for testing."""
    if strikes is None:
        strikes = [23800.0, 23900.0, 24000.0, 24100.0, 24200.0]

    if isinstance(ce_oi, int):
        ce_oi_list = [ce_oi] * len(strikes)
    else:
        ce_oi_list = ce_oi
    if isinstance(pe_oi, int):
        pe_oi_list = [pe_oi] * len(strikes)
    else:
        pe_oi_list = pe_oi

    data: list[dict[str, Any]] = []
    for s, co, po in zip(strikes, ce_oi_list, pe_oi_list):
        data.append(
            {
                "strikePrice": s,
                "expiryDate": expiry,
                "CE": {
                    "openInterest": co,
                    "changeinOpenInterest": 100,
                    "lastPrice": 150.0,
                    "impliedVolatility": 18.5,
                },
                "PE": {
                    "openInterest": po,
                    "changeinOpenInterest": 50,
                    "lastPrice": 200.0,
                    "impliedVolatility": 20.1,
                },
            }
        )
    # Add a few records for the next expiry to test filtering
    for s, co, po in zip(strikes, ce_oi_list, pe_oi_list):
        data.append(
            {
                "strikePrice": s,
                "expiryDate": other_expiry,
                "CE": {
                    "openInterest": co * 2,
                    "changeinOpenInterest": 200,
                    "lastPrice": 160.0,
                    "impliedVolatility": 19.0,
                },
                "PE": {
                    "openInterest": po * 2,
                    "changeinOpenInterest": 100,
                    "lastPrice": 210.0,
                    "impliedVolatility": 21.0,
                },
            }
        )

    return {
        "records": {
            "data": data,
            "expiryDates": [expiry, other_expiry],
            "underlyingValue": spot,
        }
    }


# ===================================================================
# compute_pcr
# ===================================================================


class TestComputePcr:
    """Tests for the pure PCR computation function."""

    def test_basic_ratio(self) -> None:
        pe = [{"open_interest": 1200}, {"open_interest": 800}]
        ce = [{"open_interest": 1000}, {"open_interest": 500}]
        # 2000 / 1500 = 1.333…
        result = compute_pcr(pe, ce)
        assert result is not None
        assert abs(result - 2000 / 1500) < 1e-9

    def test_equal_oi_returns_one(self) -> None:
        pe = [{"open_interest": 500}]
        ce = [{"open_interest": 500}]
        assert compute_pcr(pe, ce) == pytest.approx(1.0)

    def test_zero_ce_oi_returns_none(self) -> None:
        pe = [{"open_interest": 500}]
        ce = [{"open_interest": 0}]
        assert compute_pcr(pe, ce) is None

    def test_empty_pe_returns_none(self) -> None:
        assert compute_pcr([], [{"open_interest": 100}]) is None

    def test_empty_ce_returns_none(self) -> None:
        assert compute_pcr([{"open_interest": 100}], []) is None

    def test_both_none_returns_none(self) -> None:
        assert compute_pcr(None, None) is None  # type: ignore[arg-type]

    def test_pe_none_ce_not_returns_none(self) -> None:
        assert compute_pcr(None, [{"open_interest": 100}]) is None  # type: ignore[arg-type]

    def test_missing_open_interest_defaults_zero(self) -> None:
        pe: list[dict[str, Any]] = [{"not_a_key": 999}]
        ce: list[dict[str, Any]] = [{"open_interest": 100}]
        # pe total = 0 → 0 / 100 = 0.0
        assert compute_pcr(pe, ce) == pytest.approx(0.0)

    def test_large_numbers(self) -> None:
        pe = [{"open_interest": 10_000_000}]
        ce = [{"open_interest": 5_000_000}]
        assert compute_pcr(pe, ce) == pytest.approx(2.0)


# ===================================================================
# pcr_regime
# ===================================================================


class TestPcrRegime:
    """Tests for PCR → regime classification."""

    def test_bullish(self) -> None:
        assert pcr_regime(1.3) == "BULLISH"

    def test_bearish(self) -> None:
        assert pcr_regime(0.7) == "BEARISH"

    def test_neutral(self) -> None:
        assert pcr_regime(1.0) == "NEUTRAL"

    def test_none_input(self) -> None:
        assert pcr_regime(None) == "NEUTRAL"  # type: ignore[arg-type]

    def test_boundary_bullish_exactly_1_2(self) -> None:
        """Boundary decision: PCR == 1.2 → BULLISH (>= 1.2)."""
        assert pcr_regime(1.2) == "BULLISH"

    def test_boundary_bearish_exactly_0_8(self) -> None:
        """Boundary decision: PCR == 0.8 → BEARISH (<= 0.8)."""
        assert pcr_regime(0.8) == "BEARISH"

    def test_just_below_neutral(self) -> None:
        assert pcr_regime(1.199) == "NEUTRAL"

    def test_just_above_neutral(self) -> None:
        assert pcr_regime(0.801) == "NEUTRAL"

    def test_extreme_bullish(self) -> None:
        assert pcr_regime(3.5) == "BULLISH"

    def test_extreme_bearish(self) -> None:
        assert pcr_regime(0.01) == "BEARISH"

    def test_zero_is_bearish(self) -> None:
        assert pcr_regime(0.0) == "BEARISH"


# ===================================================================
# parse_option_chain
# ===================================================================


class TestParseOptionChain:
    """Tests for the full option-chain parser."""

    def test_nearest_expiry_only(self) -> None:
        payload = _make_oc(strikes=[24000.0])
        result = parse_option_chain(payload)
        assert result["expiry"] == "2026-01-15"

    def test_spot_extracted(self) -> None:
        payload = _make_oc(spot=24150.0)
        result = parse_option_chain(payload)
        assert result["spot"] == 24150.0

    def test_atm_strike_near_spot(self) -> None:
        payload = _make_oc(strikes=[23800, 23900, 24000, 24100, 24200], spot=24100.0)
        result = parse_option_chain(payload)
        assert result["atm_strike"] == 24100.0

    def test_pcr_computed(self) -> None:
        # pe_oi=1200, ce_oi=1000 → PCR = 1.2 for all strikes
        payload = _make_oc(
            strikes=[23800, 23900, 24000, 24100, 24200], ce_oi=1000, pe_oi=1200
        )
        result = parse_option_chain(payload, atm_window=10)
        assert result["pcr"] is not None
        assert result["pcr"] == pytest.approx(1.2)

    def test_pe_ce_rows_populated(self) -> None:
        payload = _make_oc(strikes=[23800, 23900, 24000, 24100, 24200])
        result = parse_option_chain(payload, atm_window=10)
        assert len(result["ce_rows"]) == 5
        assert len(result["pe_rows"]) == 5

    def test_strike_row_fields(self) -> None:
        payload = _make_oc(strikes=[24000.0])
        result = parse_option_chain(payload)
        assert len(result["strikes"]) == 1
        row = result["strikes"][0]
        assert row["strike"] == 24000.0
        assert "ce" in row
        assert "pe" in row
        assert row["ce"]["open_interest"] == 1000
        assert row["pe"]["open_interest"] == 1200

    def test_empty_records_returns_pcr_none(self) -> None:
        payload: dict[str, Any] = {"records": {}}
        result = parse_option_chain(payload)
        assert result["pcr"] is None
        assert result["strikes"] == []

    def test_empty_data_returns_pcr_none(self) -> None:
        payload: dict[str, Any] = {
            "records": {"data": [], "expiryDates": [], "underlyingValue": None}
        }
        result = parse_option_chain(payload)
        assert result["pcr"] is None

    def test_total_oi_correct(self) -> None:
        payload = _make_oc(
            strikes=[23900, 24000, 24100],
            ce_oi=[100, 200, 300],
            pe_oi=[400, 500, 600],
        )
        result = parse_option_chain(payload, atm_window=10)
        assert result["total_ce_oi"] == 600
        assert result["total_pe_oi"] == 1500

    def test_atm_window_limits_strikes(self) -> None:
        # 5 strikes far apart, small window → should filter some
        payload = _make_oc(strikes=[20000, 21000, 24000, 27000, 28000], spot=24000.0)
        result = parse_option_chain(payload, atm_window=2)
        # step=1000, window=2*1000=2000 → only 24000 should be in range
        assert len(result["strikes"]) >= 1

    def test_next_expiry_not_included(self) -> None:
        payload = _make_oc(strikes=[24000.0], ce_oi=100, pe_oi=200)
        result = parse_option_chain(payload)
        # Total OI should come only from nearest expiry
        assert result["total_ce_oi"] == 100
        assert result["total_pe_oi"] == 200


# ===================================================================
# fetch_option_chain
# ===================================================================


class TestFetchOptionChain:
    """Tests for the network fetcher (monkeypatched)."""

    def test_success_index(self) -> None:
        """Correct endpoint used for NIFTY (index)."""
        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"records": {"data": []}}
        mock_resp.raise_for_status = MagicMock()
        mock_session.get.return_value = mock_resp

        with patch("myra_app.options_chain._nse_session", return_value=mock_session):
            result = fetch_option_chain("NIFTY")

        assert result is not None
        assert result["records"]["data"] == []
        # Should have called the index endpoint
        call_args = mock_session.get.call_args_list
        api_call = [c for c in call_args if "option-chain" in str(c)][0]
        assert "option-chain-indices" in api_call[0][0]

    def test_success_equity(self) -> None:
        """Correct endpoint used for RELIANCE (equity)."""
        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"records": {"data": []}}
        mock_resp.raise_for_status = MagicMock()
        mock_session.get.return_value = mock_resp

        with patch("myra_app.options_chain._nse_session", return_value=mock_session):
            result = fetch_option_chain("RELIANCE")

        assert result is not None
        call_args = mock_session.get.call_args_list
        api_call = [c for c in call_args if "option-chain" in str(c)][0]
        assert "option-chain-equities" in api_call[0][0]

    def test_429_returns_none(self) -> None:
        """NSE throttled → graceful None."""
        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 429
        exc = requests.exceptions.HTTPError(response=mock_resp)
        mock_session.get.side_effect = exc

        with patch("myra_app.options_chain._nse_session", return_value=mock_session):
            result = fetch_option_chain("NIFTY")

        assert result is None

    def test_network_error_returns_none(self) -> None:
        """Connection error → graceful None."""
        mock_session = MagicMock()
        mock_session.get.side_effect = requests.exceptions.ConnectionError("timeout")

        with patch("myra_app.options_chain._nse_session", return_value=mock_session):
            result = fetch_option_chain("NIFTY")

        assert result is None

    def test_invalid_json_returns_none(self) -> None:
        """Non-JSON response → graceful None."""
        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.side_effect = ValueError("No JSON")
        mock_session.get.return_value = mock_resp

        with patch("myra_app.options_chain._nse_session", return_value=mock_session):
            result = fetch_option_chain("NIFTY")

        assert result is None

    def test_auto_detect_index(self) -> None:
        """'auto' mode → index endpoint for INDICES members."""
        for sym in INDICES:
            mock_session = MagicMock()
            mock_resp = MagicMock()
            mock_resp.raise_for_status = MagicMock()
            mock_resp.json.return_value = {}
            mock_session.get.return_value = mock_resp

            with patch(
                "myra_app.options_chain._nse_session", return_value=mock_session
            ):
                fetch_option_chain(sym)

            api_call = mock_session.get.call_args_list[-1]
            assert "option-chain-indices" in api_call[0][0]

    def test_explicit_kind_equity(self) -> None:
        """Explicit kind='equity' forces equity endpoint even for index symbols."""
        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {}
        mock_session.get.return_value = mock_resp

        with patch("myra_app.options_chain._nse_session", return_value=mock_session):
            fetch_option_chain("NIFTY", kind="equity")

        api_call = mock_session.get.call_args_list[-1]
        assert "option-chain-equities" in api_call[0][0]

    def test_invalid_kind_raises(self) -> None:
        """Invalid kind string → ValueError (caught → returns None)."""
        with patch("myra_app.options_chain._nse_session", return_value=MagicMock()):
            result = fetch_option_chain("NIFTY", kind="banana")
        # ValueError is caught internally → None
        assert result is None
