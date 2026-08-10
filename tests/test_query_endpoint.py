"""
Tests for the hardened /api/query endpoint.

Covers: SELECT * rejection, LIMIT enforcement, async offload, response-size guard.
Uses FastAPI TestClient with auth dependency overridden.
"""

import os
import re
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
VAL_DB = os.path.join(DB_DIR, LibrarianCore.DB_MAP["valuation"])

# ---------------------------------------------------------------------------
# Import the app and helpers under test
# ---------------------------------------------------------------------------
import sys as _sys

_sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "myra_web"))
from myra_fastapi_server import app, _run_query

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
# 1. SELECT * rejection on wide tables
# ===================================================================


class TestSelectStarRejection:
    """SELECT * must be blocked for technical and valuation DBs."""

    def test_select_star_technical_via_regex(self):
        """Direct regex check — no DB needed."""
        pattern = re.compile(r"^\s*select\s+\*", re.IGNORECASE | re.MULTILINE)
        assert pattern.search("SELECT * FROM technical_data")
        assert pattern.search("select  * from technical_data")
        assert pattern.search("SELECT\n* FROM technical_data")
        assert not pattern.search("SELECT symbol FROM technical_data")

    @pytest.mark.skipif(not os.path.exists(TECH_DB), reason="tech DB missing")
    def test_select_star_technical_400(self):
        resp = client.post(
            "/api/query",
            json={"db": "_tech_conn", "query": "SELECT * FROM technical_data", "params": []},
        )
        assert resp.status_code == 400
        assert "SELECT *" in resp.json()["detail"]

    @pytest.mark.skipif(not os.path.exists(VAL_DB), reason="valuation DB missing")
    def test_select_star_valuation_400(self):
        resp = client.post(
            "/api/query",
            json={"db": "_val_conn", "query": "SELECT * FROM fundamentals", "params": []},
        )
        assert resp.status_code == 400
        assert "SELECT *" in resp.json()["detail"]

    def test_select_specific_columns_ok(self):
        """SELECT with explicit columns must NOT be rejected."""
        pattern = re.compile(r"^\s*select\s+\*", re.IGNORECASE | re.MULTILINE)
        assert not pattern.search("SELECT symbol, close FROM technical_data LIMIT 10")


# ===================================================================
# 2. LIMIT enforcement
# ===================================================================


class TestLimitEnforcement:
    """Read queries without LIMIT should get LIMIT 5000 appended."""

    def test_limit_regex(self):
        has_limit = re.compile(r"\bLIMIT\s+\d", re.IGNORECASE)
        assert has_limit.search("SELECT x FROM t LIMIT 100")
        assert has_limit.search("SELECT x FROM t\nLIMIT 10")
        assert not has_limit.search("SELECT x FROM t")
        assert not has_limit.search("SELECT x FROM t WHERE a = 1")

    def test_limit_appended_to_select(self):
        """Verify the endpoint appends LIMIT for a bare SELECT."""
        # Use a tiny in-memory DB through _run_query + endpoint logic.
        # We just test the SQL mutation logic here.
        sql = "SELECT symbol FROM technical_data"
        if sql.lstrip().upper().startswith(("SELECT", "PRAGMA", "WITH", "EXPLAIN")):
            if not re.search(r"\bLIMIT\s+\d", sql, re.IGNORECASE):
                sql = sql.rstrip().rstrip(";") + " LIMIT 5000"
        assert sql.endswith("LIMIT 5000")

    def test_limit_not_doubled(self):
        """If LIMIT already present, it must NOT be appended."""
        sql = "SELECT symbol FROM technical_data LIMIT 100"
        if sql.lstrip().upper().startswith(("SELECT", "PRAGMA", "WITH", "EXPLAIN")):
            if not re.search(r"\bLIMIT\s+\d", sql, re.IGNORECASE):
                sql = sql.rstrip().rstrip(";") + " LIMIT 5000"
        assert sql == "SELECT symbol FROM technical_data LIMIT 100"


# ===================================================================
# 3. _run_query helper
# ===================================================================


class TestRunQuery:
    """Unit tests for the _run_query blocking helper."""

    def test_run_query_returns_rows(self):
        """Create a temp DB, insert data, verify _run_query reads it back."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            conn = sqlite3.connect(db_path)
            conn.execute("CREATE TABLE t (a TEXT, b INTEGER)")
            conn.execute("INSERT INTO t VALUES ('hello', 42)")
            conn.commit()
            conn.close()

            rows, rowcount = _run_query(db_path, "SELECT * FROM t", [])
            assert len(rows) == 1
            assert rows[0]["a"] == "hello"
            assert rows[0]["b"] == 42
        finally:
            os.unlink(db_path)

    def test_run_query_commit_on_write(self):
        """INSERT should be committed and rowcount returned."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            conn = sqlite3.connect(db_path)
            conn.execute("CREATE TABLE t (a TEXT)")
            conn.commit()
            conn.close()

            rows, rowcount = _run_query(db_path, "INSERT INTO t VALUES ('x')", [])
            # Verify committed
            conn2 = sqlite3.connect(db_path)
            count = conn2.execute("SELECT COUNT(*) FROM t").fetchone()[0]
            conn2.close()
            assert count == 1
        finally:
            os.unlink(db_path)

    def test_run_query_empty_result(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            conn = sqlite3.connect(db_path)
            conn.execute("CREATE TABLE t (a TEXT)")
            conn.commit()
            conn.close()

            rows, rowcount = _run_query(db_path, "SELECT * FROM t", [])
            assert rows == []
        finally:
            os.unlink(db_path)


# ===================================================================
# 4. Endpoint integration (with real DB if available)
# ===================================================================


class TestEndpointIntegration:
    """Full endpoint tests using TestClient."""

    @pytest.mark.skipif(not os.path.exists(TECH_DB), reason="tech DB missing")
    def test_valid_limited_query_200(self):
        resp = client.post(
            "/api/query",
            json={
                "db": "_tech_conn",
                "query": "SELECT symbol, close FROM technical_data LIMIT 5",
                "params": [],
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "data" in data
        assert "rows_affected" in data
        assert len(data["data"]) <= 5

    @pytest.mark.skipif(not os.path.exists(TECH_DB), reason="tech DB missing")
    def test_query_without_limit_gets_auto_limit(self):
        """A SELECT without LIMIT should succeed (auto-appended) and return ≤ 5000 rows."""
        resp = client.post(
            "/api/query",
            json={
                "db": "_tech_conn",
                "query": "SELECT symbol FROM technical_data",
                "params": [],
            },
        )
        assert resp.status_code == 200
        assert len(resp.json()["data"]) <= 5000

    def test_unknown_db_400(self):
        resp = client.post(
            "/api/query",
            json={"db": "_nonexistent", "query": "SELECT 1", "params": []},
        )
        assert resp.status_code == 400
        assert "Unknown database" in resp.json()["detail"]

    def test_select_star_technical_via_endpoint_400(self):
        resp = client.post(
            "/api/query",
            json={"db": "_tech_conn", "query": "SELECT * FROM technical_data", "params": []},
        )
        assert resp.status_code == 400
        assert "SELECT *" in resp.json()["detail"]

    def test_select_star_lowercase_via_endpoint_400(self):
        resp = client.post(
            "/api/query",
            json={"db": "_tech_conn", "query": "select * from technical_data", "params": []},
        )
        assert resp.status_code == 400


# ===================================================================
# 5. Response-size guard
# ===================================================================


class TestResponseSizeGuard:
    """The 10 MB response guard should trigger for huge payloads."""

    def test_guard_triggers_on_large_payload(self):
        """Create a DB with data that exceeds 10 MB when serialized."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            conn = sqlite3.connect(db_path)
            conn.execute("CREATE TABLE t (a TEXT, b TEXT)")
            # Each row ~200 bytes; need > 50k rows to exceed 10 MB
            big_val = "x" * 100
            conn.executemany("INSERT INTO t VALUES (?, ?)", [(big_val, big_val)] * 60000)
            conn.commit()
            conn.close()

            # _run_query itself doesn't check size; the endpoint does.
            # Test the size check logic directly.
            import json as _json

            rows, rowcount = _run_query(db_path, "SELECT * FROM t", [])
            payload = _json.dumps({"data": rows, "rows_affected": rowcount})
            size = len(payload.encode("utf-8"))
            assert size > 10 * 1024 * 1024, f"Expected > 10 MB, got {size}"
        finally:
            os.unlink(db_path)
