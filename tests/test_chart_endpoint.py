"""
Tests for the /api/chart/{symbol} endpoint.

Covers: response shape, limit parameter, ascending date order,
404 for unknown symbols, and a temp-DB isolation test.
"""

import os
import sqlite3
import tempfile

import pytest
from fastapi.testclient import TestClient

from myra_app.constants import DB_DIR
from myra_app.librarian_core import LibrarianCore

# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------
TECH_DB = os.path.join(DB_DIR, LibrarianCore.DB_MAP["technical"])

# ---------------------------------------------------------------------------
# Import the app under test
# ---------------------------------------------------------------------------
import sys as _sys

_sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "myra_web"))
from myra_fastapi_server import app

client = TestClient(app)

# Override auth so tests don't need MYRA_API_SECRET header
app.dependency_overrides = {}


@pytest.fixture(autouse=True)
def _override_auth():
    """Always bypass auth for endpoint tests."""
    from myra_fastapi_server import verify_myra_auth

    app.dependency_overrides[verify_myra_auth] = lambda: True
    yield
    app.dependency_overrides.clear()


# ===================================================================
# 1. Response shape (real DB)
# ===================================================================


class TestChartResponseShape:
    """Verify 200 response has correct structure."""

    @pytest.mark.skipif(not os.path.exists(TECH_DB), reason="tech DB missing")
    def test_200_top_level_keys(self):
        resp = client.get("/api/chart/HUHTAMAKI")
        assert resp.status_code == 200
        body = resp.json()
        assert "symbol" in body
        assert "data" in body
        assert body["symbol"] == "HUHTAMAKI"

    @pytest.mark.skipif(not os.path.exists(TECH_DB), reason="tech DB missing")
    def test_200_row_shape(self):
        resp = client.get("/api/chart/HUHTAMAKI?limit=5")
        assert resp.status_code == 200
        rows = resp.json()["data"]
        assert len(rows) <= 5
        for row in rows:
            assert set(row.keys()) == {"date", "open", "high", "low", "close", "volume"}


# ===================================================================
# 2. Limit parameter
# ===================================================================


class TestChartLimit:
    """Limit parameter caps the number of rows."""

    @pytest.mark.skipif(not os.path.exists(TECH_DB), reason="tech DB missing")
    def test_limit_respected(self):
        resp = client.get("/api/chart/HUHTAMAKI?limit=10")
        assert resp.status_code == 200
        assert len(resp.json()["data"]) <= 10

    @pytest.mark.skipif(not os.path.exists(TECH_DB), reason="tech DB missing")
    def test_limit_1_returns_one(self):
        resp = client.get("/api/chart/HUHTAMAKI?limit=1")
        assert resp.status_code == 200
        assert len(resp.json()["data"]) == 1


# ===================================================================
# 3. Ascending date order
# ===================================================================


class TestChartOrder:
    """Rows must be returned in ascending date order."""

    @pytest.mark.skipif(not os.path.exists(TECH_DB), reason="tech DB missing")
    def test_dates_ascending(self):
        resp = client.get("/api/chart/HUHTAMAKI?limit=50")
        assert resp.status_code == 200
        dates = [r["date"] for r in resp.json()["data"]]
        assert dates == sorted(dates)


# ===================================================================
# 4. Unknown symbol -> 404
# ===================================================================


class TestChartNotFound:
    """Unknown symbol should return 404."""

    def test_unknown_symbol_404(self):
        """Create a temp DB with correct schema so the endpoint reaches the symbol check."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            conn = sqlite3.connect(db_path)
            conn.execute(
                "CREATE TABLE technical_data ("
                "symbol TEXT, date TEXT, open REAL, high REAL, low REAL, "
                "close REAL, volume INTEGER)"
            )
            conn.commit()
            conn.close()

            from myra_fastapi_server import get_db_path

            original = get_db_path

            def fake_get_db_path(key):
                if key == "technical":
                    return db_path
                return original(key)

            import myra_fastapi_server

            myra_fastapi_server.get_db_path = fake_get_db_path
            try:
                resp = client.get("/api/chart/ZZZZNONEXISTENT")
                assert resp.status_code == 404
                assert "Symbol not found" in resp.json()["detail"]
            finally:
                myra_fastapi_server.get_db_path = original
        finally:
            os.unlink(db_path)


# ===================================================================
# 5. Temp-DB isolation test (no real DB required)
# ===================================================================


class TestChartTempDB:
    """Use a temp DB to verify endpoint logic independently."""

    def test_temp_db_returns_data(self):
        """Create a temp DB, insert OHLCV rows, verify endpoint reads them."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            conn = sqlite3.connect(db_path)
            conn.execute(
                "CREATE TABLE technical_data ("
                "symbol TEXT, date TEXT, open REAL, high REAL, low REAL, "
                "close REAL, volume INTEGER)"
            )
            conn.executemany(
                "INSERT INTO technical_data VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    ("TESTSYM", "2025-01-01", 100, 110, 90, 105, 1000),
                    ("TESTSYM", "2025-01-02", 105, 115, 95, 110, 1200),
                    ("TESTSYM", "2025-01-03", 110, 120, 100, 115, 800),
                ],
            )
            conn.commit()
            conn.close()

            # Monkeypatch get_db_path to point at temp DB
            from myra_fastapi_server import get_db_path

            original = get_db_path

            def fake_get_db_path(key):
                if key == "technical":
                    return db_path
                return original(key)

            import myra_fastapi_server

            myra_fastapi_server.get_db_path = fake_get_db_path
            try:
                resp = client.get("/api/chart/TESTSYM")
                assert resp.status_code == 200
                body = resp.json()
                assert body["symbol"] == "TESTSYM"
                assert len(body["data"]) == 3
                # Verify ascending order
                dates = [r["date"] for r in body["data"]]
                assert dates == ["2025-01-01", "2025-01-02", "2025-01-03"]
            finally:
                myra_fastapi_server.get_db_path = original
        finally:
            os.unlink(db_path)

    def test_temp_db_empty_symbol_404(self):
        """A temp DB with no matching symbol returns 404."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            conn = sqlite3.connect(db_path)
            conn.execute(
                "CREATE TABLE technical_data ("
                "symbol TEXT, date TEXT, open REAL, high REAL, low REAL, "
                "close REAL, volume INTEGER)"
            )
            conn.executemany(
                "INSERT INTO technical_data VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    ("REALSYM", "2025-01-01", 100, 110, 90, 105, 1000),
                ],
            )
            conn.commit()
            conn.close()

            from myra_fastapi_server import get_db_path

            original = get_db_path

            def fake_get_db_path(key):
                if key == "technical":
                    return db_path
                return original(key)

            import myra_fastapi_server

            myra_fastapi_server.get_db_path = fake_get_db_path
            try:
                resp = client.get("/api/chart/ZZZZNOPE")
                assert resp.status_code == 404
            finally:
                myra_fastapi_server.get_db_path = original
        finally:
            os.unlink(db_path)

    def test_temp_db_limit(self):
        """Limit parameter works with temp DB."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            conn = sqlite3.connect(db_path)
            conn.execute(
                "CREATE TABLE technical_data ("
                "symbol TEXT, date TEXT, open REAL, high REAL, low REAL, "
                "close REAL, volume INTEGER)"
            )
            rows = [
                ("LIMSYM", f"2025-01-{d:02d}", 100, 110, 90, 105, 1000)
                for d in range(1, 21)
            ]
            conn.executemany(
                "INSERT INTO technical_data VALUES (?, ?, ?, ?, ?, ?, ?)", rows
            )
            conn.commit()
            conn.close()

            from myra_fastapi_server import get_db_path

            original = get_db_path

            def fake_get_db_path(key):
                if key == "technical":
                    return db_path
                return original(key)

            import myra_fastapi_server

            myra_fastapi_server.get_db_path = fake_get_db_path
            try:
                resp = client.get("/api/chart/LIMSYM?limit=5")
                assert resp.status_code == 200
                assert len(resp.json()["data"]) == 5
            finally:
                myra_fastapi_server.get_db_path = original
        finally:
            os.unlink(db_path)
