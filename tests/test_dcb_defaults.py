"""
Tests for DCB Bargain defaults endpoint and tier_rank helper.
No database access, no network — pure endpoint + unit tests.
"""

import os
import sys

import pytest
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "myra_web"))
from myra_fastapi_server import app, _apply_tier_rank

client = TestClient(app)

# Override auth so tests don't need MYRA_API_SECRET header
app.dependency_overrides = {}


@pytest.fixture(autouse=True)
def _override_auth():
    """Bypass MYRA_API_SECRET for all tests in this module."""
    from myra_fastapi_server import verify_myra_auth

    async def _noop():
        pass

    app.dependency_overrides[verify_myra_auth] = _noop
    yield
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Expected defaults
# ---------------------------------------------------------------------------
EXPECTED_DEFAULTS = {
    "min_mcap": 200,
    "max_mcap": 50000,
    "dcb_window": 120,
    "min_discount_pct": 15.0,
    "max_discount_pct": 60.0,
    "min_del_abs": -2.0,
    "min_adtv_cr": 1.0,
    "min_high_del_days": 10,
    "sanity_mult": 5.0,
    "timeframe": "daily",
    "min_ff_mcap": 0.0,
    "exclude_circuits": True,
}


class TestDefaultsEndpoint:
    """GET /api/dcb-bargain/defaults returns correct JSON."""

    def test_status_200(self):
        resp = client.get("/api/dcb-bargain/defaults")
        assert resp.status_code == 200

    def test_all_keys_present(self):
        resp = client.get("/api/dcb-bargain/defaults")
        data = resp.json()
        for key in EXPECTED_DEFAULTS:
            assert key in data, f"Missing key: {key}"

    def test_values_match(self):
        resp = client.get("/api/dcb-bargain/defaults")
        data = resp.json()
        for key, expected in EXPECTED_DEFAULTS.items():
            assert data[key] == expected, (
                f"{key}: expected {expected!r}, got {data[key]!r}"
            )

    def test_no_extra_keys(self):
        resp = client.get("/api/dcb-bargain/defaults")
        data = resp.json()
        assert set(data.keys()) == set(EXPECTED_DEFAULTS.keys())


class TestApplyTierRank:
    """_apply_tier_rank maps tier string to numeric rank."""

    def test_high(self):
        candidates = [{"tier": "HIGH"}]
        _apply_tier_rank(candidates)
        assert candidates[0]["tier_rank"] == 0

    def test_mod(self):
        candidates = [{"tier": "MOD"}]
        _apply_tier_rank(candidates)
        assert candidates[0]["tier_rank"] == 1

    def test_low(self):
        candidates = [{"tier": "LOW"}]
        _apply_tier_rank(candidates)
        assert candidates[0]["tier_rank"] == 2

    def test_unknown_defaults_to_2(self):
        candidates = [{"tier": "UNKNOWN"}]
        _apply_tier_rank(candidates)
        assert candidates[0]["tier_rank"] == 2

    def test_missing_tier_defaults_to_2(self):
        candidates = [{}]
        _apply_tier_rank(candidates)
        assert candidates[0]["tier_rank"] == 2

    def test_preserves_existing_tier_rank(self):
        candidates = [{"tier": "HIGH", "tier_rank": 99}]
        _apply_tier_rank(candidates)
        assert candidates[0]["tier_rank"] == 99

    def test_mixed_batch(self):
        candidates = [
            {"tier": "HIGH"},
            {"tier": "MOD"},
            {"tier": "LOW"},
            {"tier": "JUNK"},
        ]
        _apply_tier_rank(candidates)
        assert [c["tier_rank"] for c in candidates] == [0, 1, 2, 2]

    def test_empty_list(self):
        assert _apply_tier_rank([]) == []

    def test_returns_same_list(self):
        candidates = [{"tier": "HIGH"}]
        result = _apply_tier_rank(candidates)
        assert result is candidates


class TestStatusEndpointTierRank:
    """dcb_bargain_status should backfill tier_rank on cached candidates."""

    def test_status_returns_200(self):
        """Status endpoint should not crash even without cache."""
        resp = client.get("/api/dcb-bargain/status")
        assert resp.status_code == 200

    def test_status_has_candidates_key(self):
        resp = client.get("/api/dcb-bargain/status")
        data = resp.json()
        assert "candidates" in data
        assert isinstance(data["candidates"], list)
