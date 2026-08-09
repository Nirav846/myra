"""
Tests for PCR-based market mood in BaseStrategy.get_market_mood()
and GET /api/pcr/status endpoint.

No real DB or network — all dependencies monkeypatched.
"""

import pytest
from unittest.mock import MagicMock, patch

from myra_app.strategies.base_strategy import MarketMoodHelper


# ---------------------------------------------------------------------------
# Helper: minimal mock lib that satisfies the VIX fallback path
# ---------------------------------------------------------------------------


def _make_lib(vix_rows=None):
    """Return a mock LibrarianCore whose safe_execute returns *vix_rows*."""
    lib = MagicMock()
    cursor = MagicMock()
    if vix_rows is not None:
        cursor.fetchone.return_value = vix_rows
    else:
        cursor.fetchone.return_value = (18.0,)  # default VIX → NEUTRAL
    lib.safe_execute.return_value = cursor
    lib._tech_conn = object()
    return lib


# ---------------------------------------------------------------------------
# PCR → mood mapping tests
# ---------------------------------------------------------------------------


@patch("myra_app.options_chain.get_latest_pcr_snapshot")
def test_pcr_bullish_returns_greed(mock_snap):
    """BULLISH PCR regime should map to GREED mood."""
    mock_snap.return_value = {
        "index_symbol": "NIFTY",
        "pcr": 1.35,
        "regime": "BULLISH",
        "spot": 24500.0,
        "expiry": "2026-08-14",
        "updated_at": "2026-08-10T10:00:00+00:00",
    }

    mood = MarketMoodHelper().get_market_mood(_make_lib())

    assert mood == "GREED"


@patch("myra_app.options_chain.get_latest_pcr_snapshot")
def test_pcr_bearish_returns_fear(mock_snap):
    """BEARISH PCR regime should map to FEAR mood."""
    mock_snap.return_value = {
        "index_symbol": "NIFTY",
        "pcr": 0.55,
        "regime": "BEARISH",
        "spot": 24500.0,
        "expiry": "2026-08-14",
        "updated_at": "2026-08-10T10:00:00+00:00",
    }

    mood = MarketMoodHelper().get_market_mood(_make_lib())

    assert mood == "FEAR"


@patch("myra_app.options_chain.get_latest_pcr_snapshot")
def test_pcr_neutral_returns_neutral(mock_snap):
    """NEUTRAL PCR regime should map to NEUTRAL mood."""
    mock_snap.return_value = {
        "index_symbol": "NIFTY",
        "pcr": 1.0,
        "regime": "NEUTRAL",
        "spot": 24500.0,
        "expiry": "2026-08-14",
        "updated_at": "2026-08-10T10:00:00+00:00",
    }

    mood = MarketMoodHelper().get_market_mood(_make_lib())

    assert mood == "NEUTRAL"


# ---------------------------------------------------------------------------
# Fallback tests
# ---------------------------------------------------------------------------


def test_pcr_none_falls_back_to_vix():
    """When PCR snapshot is None (empty DB), should fall back to VIX path."""
    with patch(
        "myra_app.options_chain.get_latest_pcr_snapshot",
        return_value=None,
    ):
        mood = MarketMoodHelper().get_market_mood(_make_lib((18.0,)))

    assert mood == "NEUTRAL"


def test_pcr_exception_falls_back_to_vix():
    """When get_latest_pcr_snapshot raises, should not propagate."""
    with patch(
        "myra_app.options_chain.get_latest_pcr_snapshot",
        side_effect=RuntimeError("DB locked"),
    ):
        mood = MarketMoodHelper().get_market_mood(_make_lib((18.0,)))

    assert isinstance(mood, str)


def test_pcr_none_vix_high_returns_fear():
    """Empty PCR + VIX > 18 → FEAR."""
    with patch(
        "myra_app.options_chain.get_latest_pcr_snapshot",
        return_value=None,
    ):
        mood = MarketMoodHelper().get_market_mood(_make_lib((21.0,)))

    assert mood == "FEAR"


def test_pcr_none_vix_low_returns_greed():
    """Empty PCR + VIX < 14 → GREED."""
    with patch(
        "myra_app.options_chain.get_latest_pcr_snapshot",
        return_value=None,
    ):
        mood = MarketMoodHelper().get_market_mood(_make_lib((12.0,)))

    assert mood == "GREED"


def test_pcr_exception_vix_missing_returns_neutral():
    """PCR exception + VIX query returns None → default NEUTRAL."""
    lib = _make_lib()
    lib.safe_execute.return_value.fetchone.return_value = None

    with patch(
        "myra_app.options_chain.get_latest_pcr_snapshot",
        side_effect=RuntimeError("DB locked"),
    ):
        mood = MarketMoodHelper().get_market_mood(lib)

    assert mood == "NEUTRAL"


# ---------------------------------------------------------------------------
# Endpoint tests — GET /api/pcr/status
# ---------------------------------------------------------------------------


@pytest.fixture
def client():
    """Create a FastAPI TestClient (import only when needed)."""
    from fastapi.testclient import TestClient
    from myra_web.myra_fastapi_server import app

    return TestClient(app, raise_server_exceptions=False)


def test_pcr_status_empty_db(client):
    """When DB has no snapshots, returns ok + empty list."""
    with patch(
        "myra_app.options_chain.get_all_pcr_snapshots",
        return_value=[],
    ):
        resp = client.get("/api/pcr/status")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["snapshots"] == []
    assert "no snapshots yet" in body.get("message", "")


def test_pcr_status_with_snapshots(client):
    """When snapshots exist, returns them in the response."""
    fake_snaps = [
        {
            "index_symbol": "NIFTY",
            "pcr": 1.25,
            "regime": "BULLISH",
            "spot": 24500.0,
            "expiry": "2026-08-14",
            "updated_at": "2026-08-10T10:00:00+00:00",
        },
        {
            "index_symbol": "BANKNIFTY",
            "pcr": 0.78,
            "regime": "BEARISH",
            "spot": 51200.0,
            "expiry": "2026-08-14",
            "updated_at": "2026-08-10T10:00:00+00:00",
        },
    ]

    with patch(
        "myra_app.options_chain.get_all_pcr_snapshots",
        return_value=fake_snaps,
    ):
        resp = client.get("/api/pcr/status")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert len(body["snapshots"]) == 2
    assert body["snapshots"][0]["index_symbol"] == "NIFTY"
    assert body["snapshots"][1]["regime"] == "BEARISH"


def test_pcr_status_exception_returns_error(client):
    """When get_all_pcr_snapshots raises, endpoint returns error gracefully."""
    with patch(
        "myra_app.options_chain.get_all_pcr_snapshots",
        side_effect=RuntimeError("DB corrupt"),
    ):
        resp = client.get("/api/pcr/status")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "error"
    assert "DB corrupt" in body["message"]
