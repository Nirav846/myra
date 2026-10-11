"""API tests for the ``/api/sources`` router (Data Sources panel backend).

The router holds no state; everything flows through
``myra_app.data_sources.source_config`` (persisted in the meta sidecar) and the
persisted source-health snapshot. Tests use a per-test meta-DB copy.
"""

import os
import shutil

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from myra_app.data_sources import source_config as sc
from myra_web.routes.sources import router as sources_router


@pytest.fixture(autouse=True)
def _reset_config_cache():
    sc.invalidate_cache()
    yield
    sc.invalidate_cache()


@pytest.fixture
def writable_meta_db(fixture_db_dir, tmp_path, monkeypatch):
    src = os.path.join(fixture_db_dir, "myra_metadata.db")
    dst = tmp_path / "myra_metadata.db"
    shutil.copy2(src, dst)
    monkeypatch.setattr("myra_app.librarian_core.DB_DIR", str(tmp_path))
    yield str(dst)


@pytest.fixture
def client(writable_meta_db):
    app = FastAPI()
    app.include_router(sources_router)
    return TestClient(app)


def test_list_sources(client):
    res = client.get("/api/sources")
    assert res.status_code == 200
    body = res.json()
    assert body["version"] == sc.SCHEMA_VERSION
    names = [s["name"] for s in body["sources"]]
    assert "screener_in" in names and "morningstar" in names
    by_name = {s["name"]: s for s in body["sources"]}
    assert by_name["screener_in"]["enabled"] is True
    # Health is always present (zeros when never recorded), never an error.
    assert by_name["yfinance"]["health"]["fail_streak"] == 0


def test_get_source_and_unknown_404(client):
    res = client.get("/api/sources/yfinance")
    assert res.status_code == 200
    # Single-source read carries the health block the panel expects.
    assert "health" in res.json()
    assert client.get("/api/sources/nope").status_code == 404


def test_patch_toggle_enabled(client):
    res = client.patch("/api/sources/screener_in", json={"enabled": False})
    assert res.status_code == 200
    body = res.json()
    assert body["enabled"] is False
    # Regression: write responses must include `health` (the panel replaces the
    # list entry with this payload and crashes without it).
    assert body["health"]["fail_streak"] == 0
    # Persisted: a fresh GET sees it.
    fresh = client.get("/api/sources/screener_in")
    assert fresh.json()["enabled"] is False


def test_patch_validates(client):
    assert (
        client.patch("/api/sources/screener_in", json={"rate_per_sec": -5}).status_code
        == 400
    )
    # A non-bool for `enabled` fails pydantic validation before our validator.
    assert (
        client.patch("/api/sources/screener_in", json={"enabled": [1]}).status_code
        == 422
    )
    assert client.patch("/api/sources/screener_in", json={}).status_code == 400
    assert client.patch("/api/sources/nope", json={"enabled": False}).status_code == 404


def test_patch_500_when_config_unreadable(client, monkeypatch):
    # A write must never proceed on an unreadable blob (it would drop the other
    # sources' overrides); the router surfaces that as a 500, not a silent write.
    monkeypatch.setattr(sc, "_read_raw_ok", lambda: ({}, False))
    res = client.patch("/api/sources/yfinance", json={"enabled": False})
    assert res.status_code == 500


def test_reset_source(client):
    client.patch("/api/sources/morningstar", json={"enabled": False, "ttl_s": 5})
    assert client.get("/api/sources/morningstar").json()["enabled"] is False
    reset = client.post("/api/sources/morningstar/reset")
    assert reset.status_code == 200
    assert "health" in reset.json()
    fresh = client.get("/api/sources/morningstar").json()
    assert fresh["enabled"] is True
    assert fresh["ttl_s"] == sc.CATALOG["morningstar"].default_ttl_s
    assert client.post("/api/sources/nope/reset").status_code == 404


def test_test_unsupported_source(client):
    res = client.post("/api/sources/finology/test")
    assert res.status_code == 200
    assert res.json()["supported"] is False
    assert client.post("/api/sources/nope/test").status_code == 404


def test_test_supported_source_with_fake_fetcher(client, monkeypatch):
    import myra_app.enrichment_sources as es

    monkeypatch.setattr(
        es,
        "SOURCE_FETCHERS",
        {"yfinance": lambda symbol: {"pe": 21.5, "market_cap": 1e9}},
    )
    res = client.post("/api/sources/yfinance/test")
    assert res.status_code == 200
    body = res.json()
    assert body["supported"] is True
    assert body["ok"] is True
    assert body["values"]["pe"] == 21.5
    assert "elapsed_s" in body


def test_test_source_failure_is_reported_not_500(client, monkeypatch):
    import myra_app.enrichment_sources as es

    def _boom(_symbol):
        raise RuntimeError("upstream refused")

    monkeypatch.setattr(es, "SOURCE_FETCHERS", {"nse_shareholding": _boom})
    res = client.post("/api/sources/nse_shareholding/test")
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is False
    assert "upstream refused" in body["error"]
