"""Tests for myra_app.ai_second_opinion — Gemini LLM second opinion module."""

import json
import os
import sqlite3
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Fixtures & helpers
# ---------------------------------------------------------------------------

_FAKE_RESPONSE = {
    "candidates": [
        {
            "content": {
                "parts": [
                    {"text": '{"signal":"BUY","reason":"Strong momentum","confidence":0.8}'}
                ]
            }
        }
    ]
}


@pytest.fixture(autouse=True)
def _bypass_cache():
    """Neutralise the SQLite cache for every test (avoid cross-test leakage).

    Each test that needs cache behaviour patches _cache_get / _cache_put
    explicitly.
    """
    with (
        patch("myra_app.ai_second_opinion._cache_get", return_value=None),
        patch("myra_app.ai_second_opinion._cache_put"),
    ):
        yield


# ---------------------------------------------------------------------------
# _post_generate_content tests
# ---------------------------------------------------------------------------


class TestPostGenerateContent:
    """Direct unit tests for the network helper."""

    def test_success(self):
        from myra_app.ai_second_opinion import _post_generate_content

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = _FAKE_RESPONSE
        mock_resp.raise_for_status = MagicMock()

        with patch("myra_app.ai_second_opinion.requests.post", return_value=mock_resp) as m:
            result = _post_generate_content("test prompt", "fake-key")
            m.assert_called_once()
            assert result == {"signal": "BUY", "reason": "Strong momentum", "confidence": 0.8}

    def test_429_returns_none(self):
        from myra_app.ai_second_opinion import _post_generate_content

        mock_resp = MagicMock()
        mock_resp.status_code = 429

        with patch("myra_app.ai_second_opinion.requests.post", return_value=mock_resp):
            result = _post_generate_content("prompt", "key")
            assert result is None

    def test_403_returns_none(self):
        from myra_app.ai_second_opinion import _post_generate_content

        mock_resp = MagicMock()
        mock_resp.status_code = 403

        with patch("myra_app.ai_second_opinion.requests.post", return_value=mock_resp):
            result = _post_generate_content("prompt", "key")
            assert result is None

    def test_timeout_returns_none(self):
        import requests as req

        from myra_app.ai_second_opinion import _post_generate_content

        with patch(
            "myra_app.ai_second_opinion.requests.post",
            side_effect=req.Timeout("timeout"),
        ):
            result = _post_generate_content("prompt", "key")
            assert result is None

    def test_connection_error_returns_none(self):
        import requests as req

        from myra_app.ai_second_opinion import _post_generate_content

        with patch(
            "myra_app.ai_second_opinion.requests.post",
            side_effect=req.ConnectionError("network down"),
        ):
            result = _post_generate_content("prompt", "key")
            assert result is None

    def test_malformed_json_returns_none(self):
        from myra_app.ai_second_opinion import _post_generate_content

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"candidates": [{"content": {"parts": [{"text": "not json"}]}}]}
        mock_resp.raise_for_status = MagicMock()

        with patch("myra_app.ai_second_opinion.requests.post", return_value=mock_resp):
            result = _post_generate_content("prompt", "key")
            assert result is None


# ---------------------------------------------------------------------------
# get_ai_second_opinion integration tests
# ---------------------------------------------------------------------------


class TestGetAiSecondOpinion:
    """End-to-end tests for the public function."""

    def test_success(self):
        from myra_app.ai_second_opinion import get_ai_second_opinion

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = _FAKE_RESPONSE
        mock_resp.raise_for_status = MagicMock()

        with patch("myra_app.ai_second_opinion.requests.post", return_value=mock_resp):
            result = get_ai_second_opinion("RELIANCE", "Ticker: RELIANCE\nClose: 2500")

        assert result["signal"] == "BUY"
        assert result["source"] == "gemini"
        assert result["cached"] is False
        assert 0.0 <= result["confidence"] <= 1.0

    def test_no_api_key_returns_degraded(self):
        from myra_app.ai_second_opinion import get_ai_second_opinion

        with patch("myra_app.ai_second_opinion._GEMINI_API_KEY", None):
            with patch("myra_app.ai_second_opinion.requests.post") as m:
                result = get_ai_second_opinion("RELIANCE", "summary")
                assert result["signal"] == "HOLD"
                assert result["source"] == "degraded"
                m.assert_not_called()

    def test_429_returns_degraded(self):
        from myra_app.ai_second_opinion import get_ai_second_opinion

        mock_resp = MagicMock()
        mock_resp.status_code = 429

        with patch("myra_app.ai_second_opinion._GEMINI_API_KEY", "fake-key"):
            with patch("myra_app.ai_second_opinion.requests.post", return_value=mock_resp):
                result = get_ai_second_opinion("RELIANCE", "summary")
                assert result["signal"] == "HOLD"
                assert result["source"] == "degraded"
                assert result["cached"] is False

    def test_timeout_returns_degraded(self):
        import requests as req

        from myra_app.ai_second_opinion import get_ai_second_opinion

        with patch("myra_app.ai_second_opinion._GEMINI_API_KEY", "fake-key"):
            with patch(
                "myra_app.ai_second_opinion.requests.post",
                side_effect=req.Timeout("timeout"),
            ):
                result = get_ai_second_opinion("RELIANCE", "summary")
                assert result["signal"] == "HOLD"
                assert result["source"] == "degraded"

    def test_invalid_signal_defaults_to_hold(self):
        from myra_app.ai_second_opinion import get_ai_second_opinion

        raw = {
            "signal": "BUYING",
            "reason": "test",
            "confidence": 0.9,
        }
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "candidates": [{"content": {"parts": [{"text": json.dumps(raw)}]}}]
        }
        mock_resp.raise_for_status = MagicMock()

        with patch("myra_app.ai_second_opinion._GEMINI_API_KEY", "fake-key"):
            with patch("myra_app.ai_second_opinion.requests.post", return_value=mock_resp):
                result = get_ai_second_opinion("TCS", "summary")
                # Invalid signal should be clamped to HOLD
                assert result["signal"] == "HOLD"

    def test_confidence_clamped_above_1(self):
        from myra_app.ai_second_opinion import get_ai_second_opinion

        raw = {"signal": "BUY", "reason": "ok", "confidence": 3.5}
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "candidates": [{"content": {"parts": [{"text": json.dumps(raw)}]}}]
        }
        mock_resp.raise_for_status = MagicMock()

        with patch("myra_app.ai_second_opinion._GEMINI_API_KEY", "fake-key"):
            with patch("myra_app.ai_second_opinion.requests.post", return_value=mock_resp):
                result = get_ai_second_opinion("INFY", "summary")
                assert result["confidence"] == 1.0

    def test_confidence_clamped_below_0(self):
        from myra_app.ai_second_opinion import get_ai_second_opinion

        raw = {"signal": "SELL", "reason": "bad", "confidence": -2.0}
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "candidates": [{"content": {"parts": [{"text": json.dumps(raw)}]}}]
        }
        mock_resp.raise_for_status = MagicMock()

        with patch("myra_app.ai_second_opinion._GEMINI_API_KEY", "fake-key"):
            with patch("myra_app.ai_second_opinion.requests.post", return_value=mock_resp):
                result = get_ai_second_opinion("INFY", "summary")
                assert result["confidence"] == 0.0

    def test_cache_hit(self):
        """Second call with same symbol+date should hit cache (network called once)."""
        from myra_app.ai_second_opinion import get_ai_second_opinion

        call_count = 0

        def counting_post(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = _FAKE_RESPONSE
            mock_resp.raise_for_status = MagicMock()
            return mock_resp

        # Use a fake in-memory cache for this test
        fake_store: dict = {}

        def fake_cache_get(key):
            return fake_store.get(key)

        def fake_cache_put(key, result):
            fake_store[key] = result

        with patch("myra_app.ai_second_opinion._cache_get", side_effect=fake_cache_get):
            with patch("myra_app.ai_second_opinion._cache_put", side_effect=fake_cache_put):
                with patch("myra_app.ai_second_opinion._GEMINI_API_KEY", "fake-key"):
                    with patch("myra_app.ai_second_opinion.requests.post", side_effect=counting_post):
                        r1 = get_ai_second_opinion("TCS", "summary1")
                        r2 = get_ai_second_opinion("TCS", "summary2")

        assert call_count == 1, f"Expected 1 network call, got {call_count}"
        assert r1["cached"] is False
        assert r2["cached"] is True
        assert r1["signal"] == r2["signal"]

    def test_no_api_key_no_network_call(self):
        """When GEMINI_API_KEY is absent, requests.post must never be called."""
        from myra_app.ai_second_opinion import get_ai_second_opinion

        with patch("myra_app.ai_second_opinion._GEMINI_API_KEY", None):
            with patch("myra_app.ai_second_opinion.requests.post") as m:
                result = get_ai_second_opinion("WIPRO", "summary")
                assert result["source"] == "degraded"
                m.assert_not_called()


# ---------------------------------------------------------------------------
# build_technical_summary tests
# ---------------------------------------------------------------------------


class TestBuildTechnicalSummary:
    """Tests for local data summary builder."""

    def test_summary_with_data(self, tmp_path):
        """With real temp DBs, summary should contain ticker + close."""
        from myra_app.ai_second_opinion import build_technical_summary

        tech_db = tmp_path / "myra_technical.db"
        val_db = tmp_path / "myra_valuation.db"

        # Create technical_data table
        conn = sqlite3.connect(str(tech_db))
        conn.execute(
            """
            CREATE TABLE technical_data (
                symbol TEXT, date TEXT, close REAL, delivery_pct REAL,
                sma_50 REAL, high_52w REAL, low_52w REAL,
                trend_alignment TEXT, relative_volume_score REAL
            )
            """
        )
        conn.execute(
            "INSERT INTO technical_data VALUES ('RELIANCE','2025-01-15',2500.0,45.2,2480.0,2800.0,1900.0,'BULLISH',1.3)"
        )
        conn.commit()
        conn.close()

        # Create fundamentals table
        conn = sqlite3.connect(str(val_db))
        conn.execute(
            """
            CREATE TABLE fundamentals (
                symbol TEXT, pe REAL, roe REAL, sector TEXT,
                promoter_holding_pct REAL, peRatio REAL, returnOnEquity REAL,
                date TEXT
            )
            """
        )
        conn.execute(
            "INSERT INTO fundamentals VALUES ('RELIANCE',28.5,14.2,'Energy',50.1,28.5,14.2,'2025-01-15')"
        )
        conn.commit()
        conn.close()

        with patch("myra_app.ai_second_opinion.DB_DIR", str(tmp_path)):
            summary = build_technical_summary("RELIANCE")

        assert "RELIANCE" in summary
        assert "2500" in summary  # close
        assert "Energy" in summary  # sector

    def test_empty_db_returns_minimal_string(self, tmp_path):
        """Empty DBs should return minimal string without exception."""
        from myra_app.ai_second_opinion import build_technical_summary

        # Create empty tables
        tech_db = tmp_path / "myra_technical.db"
        val_db = tmp_path / "myra_valuation.db"

        conn = sqlite3.connect(str(tech_db))
        conn.execute(
            """
            CREATE TABLE technical_data (
                symbol TEXT, date TEXT, close REAL, delivery_pct REAL,
                sma_50 REAL, high_52w REAL, low_52w REAL,
                trend_alignment TEXT, relative_volume_score REAL
            )
            """
        )
        conn.commit()
        conn.close()

        conn = sqlite3.connect(str(val_db))
        conn.execute(
            """
            CREATE TABLE fundamentals (
                symbol TEXT, pe REAL, roe REAL, sector TEXT,
                promoter_holding_pct REAL, peRatio REAL, returnOnEquity REAL,
                date TEXT
            )
            """
        )
        conn.commit()
        conn.close()

        with patch("myra_app.ai_second_opinion.DB_DIR", str(tmp_path)):
            summary = build_technical_summary("NOPE")

        assert "Ticker: NOPE" in summary
        assert "No local data" not in summary  # still has ticker line

    def test_missing_db_returns_minimal(self):
        """If DB files don't exist, still returns without exception."""
        from myra_app.ai_second_opinion import build_technical_summary

        with patch("myra_app.ai_second_opinion.DB_DIR", "/nonexistent/path"):
            summary = build_technical_summary("FAKE")

        assert "Ticker: FAKE" in summary

    def test_exception_in_get_market_mood_does_not_propagate(self):
        """Market mood failure should be caught gracefully."""
        from myra_app.ai_second_opinion import build_technical_summary

        with patch("myra_app.ai_second_opinion.DB_DIR", "/nonexistent/path"):
            summary = build_technical_summary("TEST")

        # Should contain mood fallback
        assert "Market mood" in summary
