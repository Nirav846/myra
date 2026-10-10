"""Route-level tests for the MF Smart Money endpoints (isolated DBs)."""

import sqlite3

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from myra_app.librarian_core import LibrarianCore
from myra_web.routes import scanners as scanners_module


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(scanners_module, "DB_DIR", str(tmp_path))

    val_db = tmp_path / LibrarianCore.DB_MAP["valuation"]
    with sqlite3.connect(val_db) as conn:
        conn.execute("CREATE TABLE mf_holding (fund_slug TEXT, month TEXT)")
        conn.executemany(
            "INSERT INTO mf_holding VALUES (?,?)",
            [("fund_a", "2026-08"), ("fund_a", "2026-07")],
        )

    tech_db = tmp_path / LibrarianCore.DB_MAP["technical"]
    with sqlite3.connect(tech_db) as conn:
        conn.execute("CREATE TABLE technical_data (symbol TEXT, date TEXT)")
        conn.execute("INSERT INTO technical_data VALUES ('ALPHA', '2026-10-09')")

    meta_db = tmp_path / LibrarianCore.DB_MAP["meta"]
    with sqlite3.connect(meta_db) as conn:
        conn.execute(
            "CREATE TABLE symbol_alias (alias TEXT PRIMARY KEY, canonical TEXT,"
            " source TEXT, confidence REAL, updated_at TEXT)"
        )

    app = FastAPI()
    app.include_router(scanners_module.router)
    with TestClient(app) as c:
        yield c, meta_db


def test_defaults_lists_months_and_modes(client):
    c, _ = client
    r = c.get("/api/mf-smart-money/defaults")
    assert r.status_code == 200
    body = r.json()
    assert body["months"] == ["2026-08", "2026-07"]
    assert body["modes"] == ["all", "accumulating", "steady", "trimming", "bargain"]
    assert "vs_dwap_pct" in body["sort_keys"]


def test_resolve_requires_both_fields(client):
    c, _ = client
    r = c.post("/api/mf-smart-money/resolve", json={"company": "Only Name"})
    assert r.status_code == 400


def test_resolve_caches_alias_and_flags_unknown_ticker(client):
    c, meta_db = client
    r = c.post(
        "/api/mf-smart-money/resolve",
        json={"company": "Zzzz Unlisted Pvt", "symbol": "zzzz"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["symbol"] == "ZZZZ"  # normalised to upper-case
    assert body["has_price_data"] is False
    assert body["note"]

    with sqlite3.connect(meta_db) as conn:
        row = conn.execute(
            "SELECT canonical FROM symbol_alias WHERE alias = ?", ("Zzzz Unlisted Pvt",)
        ).fetchone()
    assert row == ("ZZZZ",)


def test_resolve_reports_price_data_when_ticker_known(client):
    c, _ = client
    r = c.post(
        "/api/mf-smart-money/resolve",
        json={"company": "Alpha Industries Ltd.", "symbol": "alpha"},
    )
    assert r.status_code == 200
    assert r.json()["has_price_data"] is True
    assert r.json()["note"] is None
