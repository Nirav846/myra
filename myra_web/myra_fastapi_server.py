import asyncio
import json
import logging
import os
import re
import sqlite3
import threading
import subprocess
import time
import math
from datetime import datetime
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Body, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import List, Optional
from myra_app.constants import DB_DIR, MODELS_DIR
from myra_app.librarian_core import LibrarianCore

from myra_web.security import MYRA_API_SECRET, verify_myra_auth


logger = logging.getLogger(__name__)

import sys as _sys, os as _os

_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from pipeline_dashboard import router as pipeline_router
from myra_web.routes.fundamentals import router as fundamentals_router
from myra_web.routes.full_fundamentals import router as full_fundamentals_router
from myra_web.routes.sentiment import router as sentiment_router
from myra_web.routes.ai_opinion import router as ai_opinion_router
from myra_web.routes.chart import router as chart_router
from myra_web.routes.search import router as search_router
from myra_web.routes.finstack import router as finstack_router
from myra_web.routes.portfolio import router as portfolio_router
from myra_web.routes.health import router as health_router
from myra_web.utils import (
    _GRADE_RANK,
    _SCANNER_CACHE_MAP,
    _SCANNER_ROUTES,
    _TIER_RANK,
    _apply_tier_rank,
    _best_grade,
    _df_to_safe_records,
    _get_latest_trading_day_before,
    _grade_rank,
    build_confluence_report,
)

try:
    from myra_app.background_orchestrator import (
        _task_fundamentals_sync,
        _task_etf_sync,
        _task_index_sync,
        _task_daily_ingest,
        _task_db_doctor,
        _get_last_run,
    )
except ImportError:
    pass


from myra_web.background import _spawn_task  # re-export for backward compat


app = FastAPI(title="MYRA v3.2 API Bridge")

# Allow the React frontend to communicate with this local API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    return JSONResponse(
        status_code=500, content={"detail": f"Internal server error: {exc}"}
    )


app.include_router(fundamentals_router)
app.include_router(full_fundamentals_router)
app.include_router(sentiment_router)
app.include_router(ai_opinion_router)
app.include_router(chart_router)
app.include_router(search_router)
app.include_router(finstack_router)

from myra_web.routes.ml import router as ml_router

app.include_router(ml_router)

from myra_web.routes.tools import router as tools_router
from myra_web.routes.tools import portfolio_tools_router

app.include_router(tools_router)
app.include_router(portfolio_tools_router)
app.include_router(portfolio_router)
app.include_router(health_router)

from myra_web.routes.scanners import router as scanners_router

app.include_router(scanners_router)


# Use the expected folder structure: Myra\myra_web (this project) side-by-side with Myra\myra_app
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_DIR = os.path.abspath(os.path.join(BASE_DIR, "..", "myra_app", "db"))


def get_db_path(db_key: str):
    """Safely construct the path to a specific SQLite sidecar."""
    filename = LibrarianCore.DB_MAP.get(db_key)
    if not filename:
        return None
    return os.path.join(DB_DIR, filename)


@app.get("/api/pipeline/status")
async def pipeline_status():
    """Return background pipeline task statuses."""
    try:
        from myra_app.task_tracker import list_tasks

        tasks = list_tasks(limit=50)
        return {"tasks": tasks, "status": "ok"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.get("/api/pipeline/events")
async def pipeline_events():
    """Return recent pipeline events (last 50 task updates)."""
    try:
        from myra_app.task_tracker import list_tasks

        tasks = list_tasks(limit=50)
        events = []
        for t in tasks:
            if t.get("message"):
                events.append(
                    {
                        "time": t.get("updated_at") or t.get("started_at"),
                        "task": t.get("name"),
                        "message": t.get("message"),
                        "status": t.get("status"),
                    }
                )
        return {"events": events, "status": "ok"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


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


@app.post("/api/query")
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

    db_path = os.path.join(DB_DIR, db_file)
    if not os.path.exists(db_path):
        raise HTTPException(
            status_code=400, detail=f"Database file not found: {db_file}"
        )

    sql = req.query

    # --- Reject SELECT * on wide tables (technical_data, fundamentals) ---
    if canonical_key in ("technical", "valuation"):
        if re.search(r"^\s*select\s+\*", sql, re.IGNORECASE | re.MULTILINE):
            raise HTTPException(
                status_code=400,
                detail="SELECT * is not allowed on wide tables (technical_data, fundamentals). "
                "List columns explicitly or add a LIMIT.",
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


# Note: Parquet Route (/api/parquet) could be added here using pandas/pyarrow to serve DataLakeView


@app.get("/api/fundamentals/live/{symbol}")
async def get_live_fundamentals(symbol: str):
    import json, subprocess, sqlite3, os
    from myra_app.constants import DB_DIR, MODELS_DIR
    from myra_app.librarian_core import LibrarianCore

    result = {
        "symbol": symbol.upper(),
        "source": "db",
        "fundamentals": {},
        "shareholding": None,
        "key_metrics": {},
        "pros_cons": {"pros": [], "cons": [], "about": ""},
        "ratios": {},
        "peer_comparison": [],
    }

    val_path = os.path.join(DB_DIR, LibrarianCore.DB_MAP["valuation"])
    if os.path.exists(val_path):
        conn = sqlite3.connect(val_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM fundamentals WHERE symbol = ?",
            (symbol.upper(),),
        ).fetchone()
        if row:
            funda = dict(row)
            merged = {
                "symbol": funda.get("symbol"),
                "sector": funda.get("sector"),
                "pe": funda.get("pe") or funda.get("peRatio"),
                "pb": funda.get("priceToBook"),
                "ps": funda.get("priceToSales"),
                "roe": funda.get("roe") or funda.get("returnOnEquity"),
                "eps": funda.get("eps") or funda.get("earningsPerShare"),
                "book_value": funda.get("book_value") or funda.get("bookValuePerShare"),
                "market_cap": funda.get("market_cap") or funda.get("marketCap"),
                "net_margin": funda.get("net_margin") or funda.get("netMargin"),
                "operating_margin": funda.get("operatingMargin"),
                "gross_margin": funda.get("grossMargin"),
                "debt_equity": funda.get("debt_to_equity") or funda.get("debtToEquity"),
                "current_ratio": funda.get("currentRatio"),
                "quick_ratio": funda.get("quickRatio"),
                "dividend_yield": funda.get("dividend_yield")
                or funda.get("dividendYield"),
                "free_cash_flow_yield": funda.get("freeCashFlowYield"),
                "revenue_growth": funda.get("revenueGrowth"),
                "earnings_growth": funda.get("earningsGrowth"),
                "payout_ratio": funda.get("payoutRatio"),
                "beta": funda.get("beta"),
                "source": funda.get("source_ms") or funda.get("source_nse"),
                "date": funda.get("date") or funda.get("last_updated"),
            }
            result["fundamentals"] = merged
        conn.close()

    try:
        import os as _os

        proc = subprocess.run(
            ["screener", symbol.upper(), "all"],
            capture_output=True,
            text=True,
            timeout=25,
            encoding="utf-8",
            errors="replace",
            env={**_os.environ, "PYTHONIOENCODING": "utf-8"},
        )
        if proc.returncode == 0:
            data = json.loads(proc.stdout)

            sh = data.get("sections", {}).get("shareholding", {})
            latest_sh = sh.get("latest", {})
            if latest_sh:
                result["shareholding"] = {
                    "promoter_pct": latest_sh.get("Promoters"),
                    "fii_pct": latest_sh.get("FIIs"),
                    "dii_pct": latest_sh.get("DIIs"),
                    "public_pct": latest_sh.get("Public"),
                    "government_pct": latest_sh.get("Government"),
                    "period_end": (
                        sh.get("headers", [None])[-1] if sh.get("headers") else None
                    ),
                }

            pc = data.get("sections", {}).get("pros_cons", {})
            km = pc.get("key_metrics", {})
            if km:
                result["key_metrics"] = {
                    "market_cap": km.get("Market Cap"),
                    "current_price": km.get("Current Price"),
                    "high_low": km.get("High / Low"),
                    "pe": km.get("Stock P/E"),
                    "book_value": km.get("Book Value"),
                    "dividend_yield": km.get("Dividend Yield"),
                    "roce": km.get("ROCE"),
                    "roe": km.get("ROE"),
                    "face_value": km.get("Face Value"),
                }
            result["pros_cons"] = {
                "pros": pc.get("pros", []),
                "cons": pc.get("cons", []),
                "about": pc.get("about", ""),
            }

            ratios = data.get("sections", {}).get("ratios", {})
            if ratios:
                result["ratios"] = ratios

            peers = data.get("sections", {}).get("peer_comparison", {})
            if peers:
                result["peer_comparison"] = peers.get("peers", [])

            result["source"] = "live"
    except Exception:
        pass

    return result


@app.get("/api/pcr/status")
async def pcr_status():
    """Read-only status of PCR snapshots stored in myra_options.db."""
    try:
        from myra_app.options_chain import get_all_pcr_snapshots

        snapshots = get_all_pcr_snapshots()
        if not snapshots:
            return {"status": "ok", "snapshots": [], "message": "no snapshots yet"}
        return {"status": "ok", "snapshots": snapshots}
    except Exception as exc:
        logger.warning("pcr_status failed: %s", exc)
        return {"status": "error", "snapshots": [], "message": str(exc)}


# ---------------------------------------------------------------------------
# Scanner Confluence endpoint
# ---------------------------------------------------------------------------


@app.get("/api/confluence")
async def confluence_endpoint():
    """Return an aggregated view of symbols flagged by 2+ scanners."""
    try:
        return build_confluence_report()
    except Exception as e:
        logger.error("Confluence report failed: %s", e, exc_info=True)
        return JSONResponse(status_code=500, content={"error": str(e)})
