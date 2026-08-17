"""
MYRA Query Router.

Extracted from myra_fastapi_server.py (Phase 8 of monolith refactor).

POST /api/query — hardened arbitrary SQL executor with auth protection.
Safety rules preserved verbatim: SELECT * rejection on wide tables,
auto-appended LIMIT 5000 for read queries, 10 MB response guard.
"""

import asyncio
import json
import os
import re
import sqlite3

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from myra_app.constants import DB_DIR
from myra_app.librarian_core import LibrarianCore
from myra_web.security import verify_myra_auth

router = APIRouter(prefix="/api", tags=["query"])


class QueryRequest(BaseModel):
    db: str
    query: str
    params: list = []


def _run_query(db_path: str, query: str, params: list):
    """Execute a SQL query synchronously. Called via asyncio.to_thread."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        cursor = conn.execute(query, params)
        try:
            rows = [dict(row) for row in cursor.fetchall()]
        except Exception:
            rows = []

        if (
            not query.lstrip()
            .upper()
            .startswith(("SELECT", "PRAGMA", "WITH", "EXPLAIN"))
        ):
            conn.commit()

        rowcount = cursor.rowcount
        return rows, rowcount
    finally:
        conn.close()


@router.post("/query")
async def execute_query(req: QueryRequest, _=Depends(verify_myra_auth)):
    # Map frontend DB connection names to LibrarianCore canonical keys
    frontend_to_canonical = {
        "_tech_conn": "technical",
        "_meta_conn": "meta",
        "_val_conn": "valuation",
        "_inst_conn": "institutional",
        "_gov_conn": "governance",
        "_cache_conn": "network_cache",
        "_scoring_conn": "scoring",
        "_cal_conn": "calendar",
    }

    canonical_key = frontend_to_canonical.get(req.db) or req.db
    db_file = LibrarianCore.DB_MAP.get(canonical_key)
    if not db_file:
        raise HTTPException(status_code=400, detail=f"Unknown database: {req.db}")

    sql = req.query

    # --- Reject SELECT * on wide tables (technical_data, fundamentals) ---
    # Must happen BEFORE the DB existence check so the query is rejected
    # regardless of whether the database file exists on this machine.
    if canonical_key in ("technical", "valuation"):
        if re.search(r"^\s*select\s+\*", sql, re.IGNORECASE | re.MULTILINE):
            raise HTTPException(
                status_code=400,
                detail="SELECT * is not allowed on wide tables (technical_data, fundamentals). "
                "List columns explicitly or add a LIMIT.",
            )

    db_path = os.path.join(DB_DIR, db_file)
    if not os.path.exists(db_path):
        raise HTTPException(
            status_code=400, detail=f"Database file not found: {db_file}"
        )

    # --- Enforce LIMIT cap for read queries ---
    _read_prefixes = ("SELECT", "PRAGMA", "WITH", "EXPLAIN")
    if sql.lstrip().upper().startswith(_read_prefixes):
        if not re.search(r"\bLIMIT\s+\d", sql, re.IGNORECASE):
            sql = sql.rstrip().rstrip(";") + " LIMIT 5000"

    try:
        # --- Offload blocking sqlite3 work to a thread ---
        rows, rowcount = await asyncio.to_thread(_run_query, db_path, sql, req.params)

        # --- Response-size guard ---
        payload = json.dumps({"data": rows, "rows_affected": rowcount})
        if len(payload.encode("utf-8")) > 10 * 1024 * 1024:
            raise HTTPException(
                status_code=413,
                detail="Response too large (>10 MB). Add a more restrictive LIMIT.",
            )

        return {"data": rows, "rows_affected": rowcount}
    except HTTPException:
        raise
    except sqlite3.Error as e:
        raise HTTPException(status_code=400, detail=str(e))