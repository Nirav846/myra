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


# --- Invisible Hand Scanner State ---
_ih_scan_state: dict = {
    "scan_status": "idle",
    "last_scan": None,
    "progress": 0,
    "message": "Idle — click Scan to start",
    "candidates": [],
    "bear_market": False,
    "scanned_date": None,
}
_ih_scan_lock = threading.Lock()
_IH_SCAN_CACHE = os.path.join(MODELS_DIR, "invisible_hand_cache.json")


def _save_ih_cache():
    import json as _json, os as _os

    try:
        _os.makedirs("models", exist_ok=True)
        with _ih_scan_lock:
            data = {
                "last_scan": _ih_scan_state["last_scan"],
                "candidates": _ih_scan_state["candidates"],
                "message": _ih_scan_state["message"],
            }
        with open(_IH_SCAN_CACHE, "w") as _f:
            _json.dump(data, _f)
    except Exception as e:
        logger.warning(f"Cache operation failed: {e}")


def _load_ih_cache() -> dict | None:
    import json as _json, os as _os

    try:
        if _os.path.exists(_IH_SCAN_CACHE):
            with open(_IH_SCAN_CACHE) as _f:
                return _json.load(_f)
    except Exception as e:
        logger.warning(f"Cache operation failed: {e}")
    return None


@app.get("/api/invisible-hand/status")
async def invisible_hand_status():
    import copy

    with _ih_scan_lock:
        state = copy.deepcopy(_ih_scan_state)
    if state["scan_status"] == "idle":
        cache = _load_ih_cache()
        if cache and cache.get("candidates") is not None:
            return {
                "scan_status": "idle",
                "last_scan": cache.get("last_scan"),
                "progress": 100,
                "message": cache.get(
                    "message", f"Found {len(cache['candidates'])} candidates."
                ),
                "candidates": cache["candidates"],
                "bear_market": state.get("bear_market", False),
            }
    return state


@app.post("/api/invisible-hand/scan")
async def invisible_hand_scan(payload: dict = Body(default={})):
    min_mcap = int(payload.get("min_mcap", 200))
    max_mcap = int(payload.get("max_mcap", 50000))
    window = int(payload.get("window", 20))
    hist_window = int(payload.get("hist_window", 60))
    min_ih_score = int(payload.get("min_ih_score", 35))

    raw_date = payload.get("scan_date", "")
    if raw_date and str(raw_date).strip():
        effective_date = _get_latest_trading_day_before(str(raw_date).strip())
    else:
        effective_date = _get_latest_trading_day_before(
            datetime.now().strftime("%Y-%m-%d")
        )
    target_date = effective_date

    with _ih_scan_lock:
        if _ih_scan_state["scan_status"] == "scanning":
            return {"detail": "Scan already in progress"}, 409
        _ih_scan_state.update(
            {
                "scan_status": "scanning",
                "progress": 0,
                "message": "Initialising scanner...",
                "candidates": [],
                "scanned_date": effective_date,
            }
        )

    def _run():
        try:
            from myra_app.strategies.invisible_hand_scanner import InvisibleHandScanner
            import math as _math

            scanner = InvisibleHandScanner(
                min_mcap=min_mcap,
                max_mcap=max_mcap,
                window=window,
                hist_window=hist_window,
                min_ih_score=min_ih_score,
                target_date=target_date,
            )
            _ih_scan_state["message"] = "Loading universe..."
            _ih_scan_state["progress"] = 5
            universe = scanner._get_universe()
            total = max(len(universe), 1)
            _ih_scan_state["message"] = f"Scanning {total} symbols..."
            _ih_scan_state["progress"] = 10

            original_get_tech = scanner._get_tech_data
            processed = [0]

            def _tracked_get_tech(symbol, min_date, max_date=None):
                processed[0] += 1
                if processed[0] % 25 == 0:
                    pct = 10 + int((processed[0] / total) * 82)
                    _ih_scan_state["progress"] = min(pct, 92)
                    _ih_scan_state[
                        "message"
                    ] = f"Scanning {processed[0]}/{total} symbols..."
                return original_get_tech(symbol, min_date, max_date)

            scanner._get_tech_data = _tracked_get_tech

            df = scanner.scan()
            _ih_scan_state["progress"] = 95
            _ih_scan_state["message"] = "Finalising results..."

            candidates = _df_to_safe_records(df)

            _ih_scan_state.update(
                {
                    "scan_status": "completed",
                    "last_scan": datetime.now().isoformat(),
                    "progress": 100,
                    "message": f"Found {len(candidates)} candidates",
                    "candidates": candidates,
                    "bear_market": (
                        scanner.bear_market
                        if hasattr(scanner, "bear_market")
                        else False
                    ),
                    "scanned_date": effective_date,
                }
            )
            _save_ih_cache()

        except Exception as e:
            logger.error("Invisible Hand scan failed: %s", e, exc_info=True)
            _ih_scan_state.update(
                {
                    "scan_status": "error",
                    "progress": 0,
                    "message": str(e),
                }
            )

    threading.Thread(target=_run, daemon=True).start()
    return {"status": "started"}


# --- Trigger Scanner State ---
_trigger_scan_state: dict = {
    "scan_status": "idle",
    "last_scan": None,
    "progress": 0,
    "message": "Idle — click Scan to start",
    "candidates": [],
    "bear_market": False,
    "scanned_date": None,
}
_trigger_scan_lock = threading.Lock()
_TRIGGER_SCAN_CACHE = os.path.join(MODELS_DIR, "trigger_cache.json")


# Darvas scanner state
_darvas_scan_state: dict = {
    "scan_status": "idle",
    "last_scan": None,
    "progress": 0,
    "message": "Idle — click Scan to start",
    "candidates": [],
    "scanned_date": None,
}
_darvas_scan_lock = threading.Lock()
_DARVAS_SCAN_CACHE = os.path.join(MODELS_DIR, "darvas_cache.json")


def _save_trigger_cache():
    import json as _json, os as _os

    try:
        _os.makedirs("models", exist_ok=True)
        with _trigger_scan_lock:
            data = {
                "last_scan": _trigger_scan_state["last_scan"],
                "candidates": _trigger_scan_state["candidates"],
                "message": _trigger_scan_state["message"],
            }
        with open(_TRIGGER_SCAN_CACHE, "w") as _f:
            _json.dump(data, _f)
    except Exception as e:
        logger.warning(f"Cache operation failed: {e}")


def _load_trigger_cache() -> dict | None:
    import json as _json, os as _os

    try:
        if _os.path.exists(_TRIGGER_SCAN_CACHE):
            with open(_TRIGGER_SCAN_CACHE) as _f:
                return _json.load(_f)
    except Exception as e:
        logger.warning(f"Cache operation failed: {e}")
    return None


@app.get("/api/trigger/status")
async def trigger_status():
    import copy

    with _trigger_scan_lock:
        state = copy.deepcopy(_trigger_scan_state)
    if state["scan_status"] == "idle":
        cache = _load_trigger_cache()
        if cache and cache.get("candidates") is not None:
            return {
                "scan_status": "idle",
                "last_scan": cache.get("last_scan"),
                "progress": 100,
                "message": cache.get(
                    "message", f"Found {len(cache['candidates'])} candidates."
                ),
                "candidates": cache["candidates"],
                "bear_market": state.get("bear_market", False),
            }
    return state


@app.post("/api/trigger/scan")
async def trigger_scan(payload: dict = Body(default={})):
    min_mcap = int(payload.get("min_mcap", 200))
    max_mcap = int(payload.get("max_mcap", 50000))
    min_float_util_pct = float(payload.get("min_float_util_pct", 8.0))
    vol_pinch_ratio = float(payload.get("vol_pinch_ratio", 0.75))
    price_range_max_pct = float(payload.get("price_range_max_pct", 10.0))
    min_smart_float_ratio = float(payload.get("min_smart_float_ratio", 0.55))

    raw_date = payload.get("scan_date", "")
    if raw_date and str(raw_date).strip():
        scan_date = _get_latest_trading_day_before(str(raw_date).strip())
    else:
        scan_date = None

    with _trigger_scan_lock:
        if _trigger_scan_state["scan_status"] == "scanning":
            return {"detail": "Scan already in progress"}, 409
        _trigger_scan_state.update(
            {
                "scan_status": "scanning",
                "progress": 0,
                "message": "Initialising scanner...",
                "candidates": [],
                "scanned_date": scan_date,
            }
        )

    def _run():
        try:
            from myra_app.strategies.trigger_scanner import TriggerScanner
            import math as _math

            scanner = TriggerScanner(
                min_mcap=min_mcap,
                max_mcap=max_mcap,
                min_float_util_pct=min_float_util_pct,
                vol_pinch_ratio=vol_pinch_ratio,
                price_range_max_pct=price_range_max_pct,
                min_smart_float_ratio=min_smart_float_ratio,
            )
            _trigger_scan_state["message"] = "Loading universe..."
            _trigger_scan_state["progress"] = 5
            universe = scanner._get_universe()
            total = max(len(universe), 1)
            _trigger_scan_state["message"] = f"Scanning {total} symbols..."
            _trigger_scan_state["progress"] = 10

            original_get_tech = scanner._get_tech_data
            processed = [0]

            def _tracked_get_tech(symbol, min_date, max_date=None):
                processed[0] += 1
                if processed[0] % 25 == 0:
                    pct = 10 + int((processed[0] / total) * 82)
                    _trigger_scan_state["progress"] = min(pct, 92)
                    _trigger_scan_state[
                        "message"
                    ] = f"Scanning {processed[0]}/{total} symbols..."
                return original_get_tech(symbol, min_date, max_date)

            scanner._get_tech_data = _tracked_get_tech

            candidates = scanner.scan(as_on_date=scan_date)
            _trigger_scan_state["progress"] = 95
            _trigger_scan_state["message"] = "Finalising results..."

            for rec in candidates:
                for key, val in list(rec.items()):
                    if isinstance(val, float) and (
                        _math.isnan(val) or _math.isinf(val)
                    ):
                        rec[key] = None

            _trigger_scan_state.update(
                {
                    "scan_status": "completed",
                    "last_scan": datetime.now().isoformat(),
                    "progress": 100,
                    "message": f"Found {len(candidates)} candidates",
                    "candidates": candidates,
                    "bear_market": (
                        scanner.bear_market
                        if hasattr(scanner, "bear_market")
                        else False
                    ),
                    "scanned_date": scan_date,
                }
            )
            _save_trigger_cache()

        except Exception as e:
            logger.error("Trigger scan failed: %s", e, exc_info=True)
            _trigger_scan_state.update(
                {
                    "scan_status": "error",
                    "progress": 0,
                    "message": str(e),
                }
            )

    threading.Thread(target=_run, daemon=True).start()
    return {"status": "started"}  # --- Liquidity Flip Detector State ---


_lf_scan_state: dict = {
    "scan_status": "idle",
    "last_scan": None,
    "progress": 0,
    "message": "Idle — click Scan to start",
    "candidates": [],
    "bear_market": False,
    "scanned_date": None,
}
_lf_scan_lock = threading.Lock()
_LF_SCAN_CACHE = os.path.join(MODELS_DIR, "liquidity_flip_cache.json")


def _save_lf_cache():
    import json as _json
    import os as _os

    try:
        _os.makedirs("models", exist_ok=True)
        with _lf_scan_lock:
            data = {
                "last_scan": _lf_scan_state["last_scan"],
                "candidates": _lf_scan_state["candidates"],
                "message": _lf_scan_state["message"],
            }
        with open(_LF_SCAN_CACHE, "w") as _f:
            _json.dump(data, _f)
    except Exception as e:
        logger.warning(f"Cache operation failed: {e}")


def _load_lf_cache() -> dict | None:
    import json as _json
    import os as _os

    try:
        if _os.path.exists(_LF_SCAN_CACHE):
            with open(_LF_SCAN_CACHE) as _f:
                return _json.load(_f)
    except Exception as e:
        logger.warning(f"Cache operation failed: {e}")
    return None


@app.get("/api/liquidity-flip/status")
async def liquidity_flip_status():
    import copy

    with _lf_scan_lock:
        state = copy.deepcopy(_lf_scan_state)

    if state["scan_status"] == "idle":
        cache = _load_lf_cache()
        if cache and cache.get("candidates") is not None:
            return {
                "scan_status": "idle",
                "last_scan": cache.get("last_scan"),
                "progress": 100,
                "message": cache.get(
                    "message", f"Found {len(cache['candidates'])} candidates."
                ),
                "candidates": cache["candidates"],
                "bear_market": state.get("bear_market", False),
            }

    return state


@app.post("/api/liquidity-flip/scan")
async def liquidity_flip_scan(payload: dict = Body(default={})):
    min_mcap = int(payload.get("min_mcap", 200))
    max_mcap = int(payload.get("max_mcap", 50000))
    prior_window = int(payload.get("prior_window", 120))
    recent_window = int(payload.get("recent_window", 30))
    lookback_days = int(payload.get("lookback_days", 150))

    raw_date = payload.get("scan_date", "")
    if raw_date and str(raw_date).strip():
        scan_date = _get_latest_trading_day_before(str(raw_date).strip())
    else:
        scan_date = None

    with _lf_scan_lock:
        if _lf_scan_state["scan_status"] == "scanning":
            return {"detail": "Scan already in progress"}, 409

        _lf_scan_state.update(
            {
                "scan_status": "scanning",
                "progress": 0,
                "message": "Initialising scanner...",
                "candidates": [],
                "scanned_date": scan_date,
            }
        )

    def _run():
        try:
            from myra_app.strategies.liquidity_flip_detector import (
                LiquidityFlipDetector,
            )
            import math as _math

            scanner = LiquidityFlipDetector(
                min_mcap=min_mcap,
                max_mcap=max_mcap,
                prior_window=prior_window,
                recent_window=recent_window,
                lookback_days=lookback_days,
            )

            _lf_scan_state["message"] = "Loading universe..."
            _lf_scan_state["progress"] = 5

            universe = scanner._get_universe()
            total = max(len(universe), 1)
            _lf_scan_state["message"] = f"Scanning {total} symbols..."
            _lf_scan_state["progress"] = 10

            original_get_tech = scanner._get_tech_data
            processed = [0]

            def _tracked_get_tech(symbol, min_date, max_date=None):
                processed[0] += 1
                if processed[0] % 25 == 0:
                    pct = 10 + int((processed[0] / total) * 82)
                    _lf_scan_state["progress"] = min(pct, 92)
                    _lf_scan_state[
                        "message"
                    ] = f"Scanning {processed[0]}/{total} symbols..."
                return original_get_tech(symbol, min_date, max_date)

            scanner._get_tech_data = _tracked_get_tech

            df = scanner.scan(as_on_date=scan_date)

            _lf_scan_state["progress"] = 95
            _lf_scan_state["message"] = "Finalising results..."

            candidates = _df_to_safe_records(df)

            _lf_scan_state.update(
                {
                    "scan_status": "completed",
                    "last_scan": datetime.now().isoformat(),
                    "progress": 100,
                    "message": f"Found {len(candidates)} candidates",
                    "candidates": candidates,
                    "bear_market": (
                        scanner.bear_market
                        if hasattr(scanner, "bear_market")
                        else False
                    ),
                    "scanned_date": scan_date,
                }
            )
            _save_lf_cache()

        except Exception as e:
            logger.error("Liquidity Flip scan failed: %s", e, exc_info=True)
            _lf_scan_state.update(
                {
                    "scan_status": "error",
                    "progress": 0,
                    "message": str(e),
                }
            )

    threading.Thread(target=_run, daemon=True).start()
    return {"status": "started"}


# --- DCB Bargain Scanner State ---
_dcb_scan_state: dict = {
    "scan_status": "idle",
    "last_scan": None,
    "progress": 0,
    "message": "Idle — click Scan to start",
    "candidates": [],
    "bear_market": False,
    "scanned_date": None,
}
_dcb_scan_lock = threading.Lock()
_DCB_CACHE = os.path.join(MODELS_DIR, "dcb_bargain_cache.json")


def _save_dcb_cache():
    import json as _json
    import os as _os

    try:
        _os.makedirs("models", exist_ok=True)
        with _dcb_scan_lock:
            data = {
                "last_scan": _dcb_scan_state["last_scan"],
                "candidates": _dcb_scan_state["candidates"],
                "message": _dcb_scan_state["message"],
            }
        with open(_DCB_CACHE, "w") as _f:
            _json.dump(data, _f)
    except Exception as e:
        logger.warning(f"Cache operation failed: {e}")


def _load_dcb_cache() -> dict | None:
    import json as _json
    import os as _os

    try:
        if _os.path.exists(_DCB_CACHE):
            with open(_DCB_CACHE) as _f:
                return _json.load(_f)
    except Exception as e:
        logger.warning(f"Cache operation failed: {e}")
    return None


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


@app.get("/api/dcb-bargain/defaults")
async def dcb_bargain_defaults():
    """Return backend default parameter values for the DCB Bargain scanner."""
    return {
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
        "min_ff_mcap": 600.0,
        "exclude_circuits": True,
    }


@app.get("/api/dcb-bargain/status")
async def dcb_bargain_status():
    import copy

    with _dcb_scan_lock:
        state = copy.deepcopy(_dcb_scan_state)

    if state["scan_status"] == "idle":
        cache = _load_dcb_cache()
        if cache and cache.get("candidates") is not None:
            _apply_tier_rank(cache["candidates"])
            return {
                "scan_status": "idle",
                "last_scan": cache.get("last_scan"),
                "progress": 100,
                "message": cache.get(
                    "message", f"Found {len(cache['candidates'])} candidates."
                ),
                "candidates": cache["candidates"],
                "bear_market": state.get("bear_market", False),
            }

    return state


@app.post("/api/dcb-bargain/scan")
async def dcb_bargain_scan(payload: dict = Body(default={})):
    min_mcap = int(payload.get("min_mcap", 200))
    max_mcap = int(payload.get("max_mcap", 50000))
    dcb_window = int(payload.get("dcb_window", 120))
    min_discount_pct = float(payload.get("min_discount_pct", 15.0))
    max_discount_pct = float(payload.get("max_discount_pct", 60.0))
    min_del_abs = float(payload.get("min_del_abs", -2.0))
    min_adtv_cr = float(payload.get("min_adtv_cr", 1.0))
    min_high_del_days = int(payload.get("min_high_del_days", 10))
    sanity_mult = float(payload.get("sanity_mult", 5.0))
    timeframe = str(payload.get("timeframe", "daily"))
    if timeframe not in ("daily", "weekly"):
        return {"detail": "timeframe must be 'daily' or 'weekly'"}, 400
    min_ff_mcap = float(payload.get("min_ff_mcap", 600.0))
    exclude_circuits = bool(payload.get("exclude_circuits", True))

    raw_date = payload.get("scan_date", "")
    if raw_date and str(raw_date).strip():
        scan_date = _get_latest_trading_day_before(str(raw_date).strip())
    else:
        scan_date = None

    with _dcb_scan_lock:
        if _dcb_scan_state["scan_status"] == "scanning":
            return {"detail": "Scan already in progress"}, 409

        _dcb_scan_state.update(
            {
                "scan_status": "scanning",
                "progress": 0,
                "message": "Initialising scanner...",
                "candidates": [],
                "scanned_date": scan_date,
            }
        )

    def _run():
        try:
            from myra_app.strategies.dcb_bargain import DCBBargainScanner

            scanner = DCBBargainScanner(
                min_mcap=min_mcap,
                max_mcap=max_mcap,
                dcb_window=dcb_window,
                min_discount_pct=min_discount_pct,
                max_discount_pct=max_discount_pct,
                min_del_abs=min_del_abs,
                min_adtv_cr=min_adtv_cr,
                min_high_del_days=min_high_del_days,
                sanity_mult=sanity_mult,
                timeframe=timeframe,
                min_ff_mcap=min_ff_mcap,
            )

            _dcb_scan_state["message"] = "Loading universe..."
            _dcb_scan_state["progress"] = 5

            universe = scanner._get_universe()
            total = max(len(universe), 1)
            _dcb_scan_state["message"] = f"Scanning {total} symbols..."
            _dcb_scan_state["progress"] = 10

            original_get_tech = scanner._get_tech_data
            processed = [0]

            def _tracked_get_tech(symbol, min_date, max_date=None):
                processed[0] += 1
                if processed[0] % 25 == 0:
                    pct = 10 + int((processed[0] / total) * 82)
                    _dcb_scan_state["progress"] = min(pct, 92)
                    _dcb_scan_state[
                        "message"
                    ] = f"Scanning {processed[0]}/{total} symbols..."
                return original_get_tech(symbol, min_date, max_date)

            scanner._get_tech_data = _tracked_get_tech

            df = scanner.scan(as_on_date=scan_date)

            if exclude_circuits:
                col = (
                    "is_circuit_lock"
                    if "is_circuit_lock" in df.columns
                    else "is_lower_circuit"
                )
                if col in df.columns:
                    df = df[~df[col].fillna(False)].reset_index(drop=True)

            _dcb_scan_state["progress"] = 95
            _dcb_scan_state["message"] = "Finalising results..."

            candidates = _df_to_safe_records(df)
            _apply_tier_rank(candidates)

            _dcb_scan_state.update(
                {
                    "scan_status": "completed",
                    "last_scan": datetime.now().isoformat(),
                    "progress": 100,
                    "message": f"Found {len(candidates)} candidates",
                    "candidates": candidates,
                    "bear_market": (
                        scanner.bear_market
                        if hasattr(scanner, "bear_market")
                        else False
                    ),
                    "scanned_date": scan_date,
                }
            )
            _save_dcb_cache()

        except Exception as e:
            logger.error("DCB Bargain scan failed: %s", e, exc_info=True)
            _dcb_scan_state.update(
                {
                    "scan_status": "error",
                    "progress": 0,
                    "message": str(e),
                }
            )

    threading.Thread(target=_run, daemon=True).start()
    return {"status": "started"}


# --- Operator Fingerprint Scanner State ---
_of_scan_state: dict = {
    "scan_status": "idle",
    "last_scan": None,
    "progress": 0,
    "message": "Idle — click Scan to start",
    "candidates": [],
    "bear_market": False,
    "scanned_date": None,
}
_of_scan_lock = threading.Lock()
_OF_SCAN_CACHE = os.path.join(MODELS_DIR, "operator_fingerprint_cache.json")


def _save_of_cache():
    import json as _json
    import os as _os

    try:
        _os.makedirs("models", exist_ok=True)
        with _of_scan_lock:
            data = {
                "last_scan": _of_scan_state["last_scan"],
                "candidates": _of_scan_state["candidates"],
                "message": _of_scan_state["message"],
            }
        with open(_OF_SCAN_CACHE, "w") as _f:
            _json.dump(data, _f)
    except Exception as e:
        logger.warning(f"Cache operation failed: {e}")


def _load_of_cache() -> dict | None:
    import json as _json
    import os as _os

    try:
        if _os.path.exists(_OF_SCAN_CACHE):
            with open(_OF_SCAN_CACHE) as _f:
                return _json.load(_f)
    except Exception as e:
        logger.warning(f"Cache operation failed: {e}")
    return None


@app.get("/api/operator-fingerprint/status")
async def operator_fingerprint_status():
    import copy

    with _of_scan_lock:
        state = copy.deepcopy(_of_scan_state)

    if state["scan_status"] == "idle":
        cache = _load_of_cache()
        if cache and cache.get("candidates") is not None:
            return {
                "scan_status": "idle",
                "last_scan": cache.get("last_scan"),
                "progress": 100,
                "message": cache.get(
                    "message", f"Found {len(cache['candidates'])} candidates."
                ),
                "candidates": cache["candidates"],
                "bear_market": state.get("bear_market", False),
            }

    return state


@app.post("/api/operator-fingerprint/scan")
async def operator_fingerprint_scan(payload: dict = Body(default={})):
    min_mcap = int(payload.get("min_mcap", 200))
    max_mcap = int(payload.get("max_mcap", 50000))
    prior_window = int(payload.get("prior_window", 120))
    recent_window = int(payload.get("recent_window", 30))
    lookback_days = int(payload.get("lookback_days", 150))

    raw_date = payload.get("scan_date", "")
    if raw_date and str(raw_date).strip():
        scan_date = _get_latest_trading_day_before(str(raw_date).strip())
    else:
        scan_date = None

    with _of_scan_lock:
        if _of_scan_state["scan_status"] == "scanning":
            return {"detail": "Scan already in progress"}, 409

        _of_scan_state.update(
            {
                "scan_status": "scanning",
                "progress": 0,
                "message": "Initialising scanner...",
                "candidates": [],
                "scanned_date": scan_date,
            }
        )

    def _run():
        try:
            from myra_app.strategies.operator_fingerprint_scanner import (
                OperatorFingerprintScanner,
            )
            import math as _math

            scanner = OperatorFingerprintScanner(
                min_mcap=min_mcap,
                max_mcap=max_mcap,
            )

            _of_scan_state["message"] = "Loading universe..."
            _of_scan_state["progress"] = 5

            universe = scanner._get_universe()
            total = max(len(universe), 1)
            _of_scan_state["message"] = f"Scanning {total} symbols..."
            _of_scan_state["progress"] = 10

            original_get_tech = scanner._get_tech_data
            processed = [0]

            def _tracked_get_tech(symbol, min_date, max_date=None):
                processed[0] += 1
                if processed[0] % 25 == 0:
                    pct = 10 + int((processed[0] / total) * 82)
                    _of_scan_state["progress"] = min(pct, 92)
                    _of_scan_state[
                        "message"
                    ] = f"Scanning {processed[0]}/{total} symbols..."
                return original_get_tech(symbol, min_date, max_date)

            scanner._get_tech_data = _tracked_get_tech

            df = scanner.scan(as_on_date=scan_date)

            _of_scan_state["progress"] = 95
            _of_scan_state["message"] = "Finalising results..."

            candidates = _df_to_safe_records(df)

            _of_scan_state.update(
                {
                    "scan_status": "completed",
                    "last_scan": datetime.now().isoformat(),
                    "progress": 100,
                    "message": f"Found {len(candidates)} candidates",
                    "candidates": candidates,
                    "bear_market": (
                        scanner.bear_market
                        if hasattr(scanner, "bear_market")
                        else False
                    ),
                    "scanned_date": scan_date,
                }
            )
            _save_of_cache()

        except Exception as e:
            logger.error("Operator Fingerprint scan failed: %s", e, exc_info=True)
            _of_scan_state.update(
                {
                    "scan_status": "error",
                    "progress": 0,
                    "message": str(e),
                }
            )

    threading.Thread(target=_run, daemon=True).start()
    return {"status": "started"}


# --- Float Exhaustion Scanner State ---
_fe_scan_state: dict = {
    "scan_status": "idle",
    "last_scan": None,
    "progress": 0,
    "message": "Idle — click Scan to start",
    "candidates": [],
    "bear_market": False,
    "scanned_date": None,
}
_fe_scan_lock = threading.Lock()
_FE_SCAN_CACHE = os.path.join(MODELS_DIR, "float_exhaustion_cache.json")


def _save_fe_cache():
    import json as _json
    import os as _os

    try:
        _os.makedirs("models", exist_ok=True)
        with _fe_scan_lock:
            data = {
                "last_scan": _fe_scan_state["last_scan"],
                "candidates": _fe_scan_state["candidates"],
                "message": _fe_scan_state["message"],
            }
        with open(_FE_SCAN_CACHE, "w") as _f:
            _json.dump(data, _f)
    except Exception as e:
        logger.warning(f"Cache operation failed: {e}")


def _load_fe_cache() -> dict | None:
    import json as _json
    import os as _os

    try:
        if _os.path.exists(_FE_SCAN_CACHE):
            with open(_FE_SCAN_CACHE) as _f:
                return _json.load(_f)
    except Exception as e:
        logger.warning(f"Cache operation failed: {e}")
    return None


@app.get("/api/float-exhaustion/status")
async def float_exhaustion_status():
    import copy

    with _fe_scan_lock:
        state = copy.deepcopy(_fe_scan_state)

    if state["scan_status"] == "idle":
        cache = _load_fe_cache()
        if cache and cache.get("candidates") is not None:
            return {
                "scan_status": "idle",
                "last_scan": cache.get("last_scan"),
                "progress": 100,
                "message": cache.get(
                    "message", f"Found {len(cache['candidates'])} candidates."
                ),
                "candidates": cache["candidates"],
                "bear_market": state.get("bear_market", False),
            }

    return state


@app.post("/api/float-exhaustion/scan")
async def float_exhaustion_scan(payload: dict = Body(default={})):
    min_mcap = int(payload.get("min_mcap", 200))
    max_mcap = int(payload.get("max_mcap", 50000))
    prior_window = int(payload.get("prior_window", 120))
    recent_window = int(payload.get("recent_window", 30))
    lookback_days = int(payload.get("lookback_days", 150))

    raw_date = payload.get("scan_date", "")
    if raw_date and str(raw_date).strip():
        scan_date = _get_latest_trading_day_before(str(raw_date).strip())
    else:
        scan_date = None

    with _fe_scan_lock:
        if _fe_scan_state["scan_status"] == "scanning":
            return {"detail": "Scan already in progress"}, 409

        _fe_scan_state.update(
            {
                "scan_status": "scanning",
                "progress": 0,
                "message": "Initialising scanner...",
                "candidates": [],
                "scanned_date": scan_date,
            }
        )

    def _run():
        try:
            from myra_app.strategies.float_exhaustion_scanner import (
                FloatExhaustionScanner,
            )
            import math as _math

            scanner = FloatExhaustionScanner(
                min_mcap=min_mcap,
                max_mcap=max_mcap,
            )

            _fe_scan_state["message"] = "Loading universe..."
            _fe_scan_state["progress"] = 5

            universe = scanner._get_universe()
            total = max(len(universe), 1)
            _fe_scan_state["message"] = f"Scanning {total} symbols..."
            _fe_scan_state["progress"] = 10

            original_get_tech = scanner._get_tech_data
            processed = [0]

            def _tracked_get_tech(symbol, min_date, max_date=None):
                processed[0] += 1
                if processed[0] % 25 == 0:
                    pct = 10 + int((processed[0] / total) * 82)
                    _fe_scan_state["progress"] = min(pct, 92)
                    _fe_scan_state[
                        "message"
                    ] = f"Scanning {processed[0]}/{total} symbols..."
                return original_get_tech(symbol, min_date, max_date)

            scanner._get_tech_data = _tracked_get_tech

            candidates = scanner.scan(as_on_date=scan_date)

            _fe_scan_state["progress"] = 95
            _fe_scan_state["message"] = "Finalising results..."

            for rec in candidates:
                for key, val in list(rec.items()):
                    if isinstance(val, float) and (
                        _math.isnan(val) or _math.isinf(val)
                    ):
                        rec[key] = None

            _fe_scan_state.update(
                {
                    "scan_status": "completed",
                    "last_scan": datetime.now().isoformat(),
                    "progress": 100,
                    "message": f"Found {len(candidates)} candidates",
                    "candidates": candidates,
                    "bear_market": (
                        scanner.bear_market
                        if hasattr(scanner, "bear_market")
                        else False
                    ),
                    "scanned_date": scan_date,
                }
            )
            _save_fe_cache()

        except Exception as e:
            logger.error("Float Exhaustion scan failed: %s", e, exc_info=True)
            _fe_scan_state.update(
                {
                    "scan_status": "error",
                    "progress": 0,
                    "message": str(e),
                }
            )

    threading.Thread(target=_run, daemon=True).start()
    return {"status": "started"}


# --- Seasonal Delivery Harvester State ---
_sd_scan_state: dict = {
    "scan_status": "idle",
    "last_scan": None,
    "progress": 0,
    "message": "Idle — click Scan to start",
    "candidates": [],
    "bear_market": False,
    "scanned_date": None,
}
_sd_scan_lock = threading.Lock()
_SD_SCAN_CACHE = os.path.join(MODELS_DIR, "seasonal_delivery_cache.json")


def _save_sd_cache():
    import json as _json
    import os as _os

    try:
        _os.makedirs("models", exist_ok=True)
        with _sd_scan_lock:
            data = {
                "last_scan": _sd_scan_state["last_scan"],
                "candidates": _sd_scan_state["candidates"],
                "message": _sd_scan_state["message"],
            }
        with open(_SD_SCAN_CACHE, "w") as _f:
            _json.dump(data, _f)
    except Exception as e:
        logger.warning(f"Cache operation failed: {e}")


def _load_sd_cache() -> dict | None:
    import json as _json
    import os as _os

    try:
        if _os.path.exists(_SD_SCAN_CACHE):
            with open(_SD_SCAN_CACHE) as _f:
                return _json.load(_f)
    except Exception as e:
        logger.warning(f"Cache operation failed: {e}")
    return None


@app.get("/api/seasonal-delivery/status")
async def seasonal_delivery_status():
    import copy

    with _sd_scan_lock:
        state = copy.deepcopy(_sd_scan_state)

    if state["scan_status"] == "idle":
        cache = _load_sd_cache()
        if cache and cache.get("candidates") is not None:
            return {
                "scan_status": "idle",
                "last_scan": cache.get("last_scan"),
                "progress": 100,
                "message": cache.get(
                    "message", f"Found {len(cache['candidates'])} candidates."
                ),
                "candidates": cache["candidates"],
                "bear_market": state.get("bear_market", False),
            }

    return state


@app.post("/api/seasonal-delivery/scan")
async def seasonal_delivery_scan(payload: dict = Body(default={})):
    min_mcap = int(payload.get("min_mcap", 200))
    max_mcap = int(payload.get("max_mcap", 50000))
    target_month = payload.get("target_month")
    if target_month is not None:
        target_month = int(target_month)

    raw_date = payload.get("scan_date", "")
    if raw_date and str(raw_date).strip():
        scan_date = _get_latest_trading_day_before(str(raw_date).strip())
    else:
        scan_date = None

    with _sd_scan_lock:
        if _sd_scan_state["scan_status"] == "scanning":
            return {"detail": "Scan already in progress"}, 409

        _sd_scan_state.update(
            {
                "scan_status": "scanning",
                "progress": 0,
                "message": "Initialising scanner...",
                "candidates": [],
                "scanned_date": scan_date,
            }
        )

    def _run():
        try:
            from myra_app.strategies.seasonal_delivery_harvester import (
                SeasonalDeliveryHarvester,
            )
            import math as _math

            scanner = SeasonalDeliveryHarvester(
                min_mcap=min_mcap,
                max_mcap=max_mcap,
                target_month=target_month,
            )

            _sd_scan_state["message"] = "Loading universe..."
            _sd_scan_state["progress"] = 5

            universe = scanner._get_universe()
            total = max(len(universe), 1)
            _sd_scan_state["message"] = f"Scanning {total} symbols..."
            _sd_scan_state["progress"] = 10

            original_get_tech = scanner._get_all_tech_data
            processed = [0]

            def _tracked_get_tech(symbol, min_date=None, max_date=None):
                processed[0] += 1
                if processed[0] % 25 == 0:
                    pct = 10 + int((processed[0] / total) * 82)
                    _sd_scan_state["progress"] = min(pct, 92)
                    _sd_scan_state[
                        "message"
                    ] = f"Scanning {processed[0]}/{total} symbols..."
                kwargs = {}
                if min_date is not None:
                    kwargs["min_date"] = min_date
                if max_date is not None:
                    kwargs["max_date"] = max_date
                return original_get_tech(symbol, **kwargs)

            scanner._get_all_tech_data = _tracked_get_tech

            df = scanner.scan(as_on_date=scan_date)

            _sd_scan_state["progress"] = 95
            _sd_scan_state["message"] = "Finalising results..."

            candidates = _df_to_safe_records(df)

            _sd_scan_state.update(
                {
                    "scan_status": "completed",
                    "last_scan": datetime.now().isoformat(),
                    "progress": 100,
                    "message": f"Found {len(candidates)} candidates",
                    "candidates": candidates,
                    "bear_market": (
                        scanner.bear_market
                        if hasattr(scanner, "bear_market")
                        else False
                    ),
                    "scanned_date": scan_date,
                }
            )
            _save_sd_cache()

        except Exception as e:
            logger.error("Seasonal Delivery scan failed: %s", e, exc_info=True)
            _sd_scan_state.update(
                {
                    "scan_status": "error",
                    "progress": 0,
                    "message": str(e),
                }
            )

    threading.Thread(target=_run, daemon=True).start()
    return {"status": "started"}


# --- Launchpad Scan State ---
_launchpad_scan_state: dict = {
    "scan_status": "idle",
    "last_scan": None,
    "predictions": [],
    "message": "",
}
_launchpad_scan_lock = threading.Lock()


@app.get("/api/launchpad/status")
async def launchpad_scan_status():
    return _launchpad_scan_state


@app.post("/api/launchpad/scan")
async def launchpad_scan(payload: dict = Body(default={})):
    with _launchpad_scan_lock:
        if _launchpad_scan_state["scan_status"] == "scanning":
            return {"detail": "Scan already in progress"}, 409
        _launchpad_scan_state.update(
            {
                "scan_status": "scanning",
                "predictions": [],
                "message": "Running launchpad predictions...",
            }
        )

    def _run():
        try:
            import os as _os, sqlite3, pandas as pd, numpy as np, joblib
            from myra_app.librarian_core import LibrarianCore

            model_path = "models/launchpad_xgb.joblib"
            if not _os.path.exists(model_path):
                _launchpad_scan_state.update(
                    {
                        "scan_status": "error",
                        "message": "Model not trained. Run Label + Train first.",
                    }
                )
                return

            tech_db = _os.path.join(DB_DIR, LibrarianCore.DB_MAP["technical"])
            val_db = _os.path.join(DB_DIR, LibrarianCore.DB_MAP["valuation"])

            with sqlite3.connect(tech_db) as conn:
                events = conn.execute(
                    "SELECT symbol, trigger_date FROM launchpad_events WHERE success = 0 AND trigger_date >= date('now', '-180 days') ORDER BY trigger_date DESC"
                ).fetchall()

            if not events:
                _launchpad_scan_state.update(
                    {
                        "scan_status": "completed",
                        "predictions": [],
                        "message": "No stocks in digestion phase.",
                        "last_scan": datetime.now().isoformat(),
                    }
                )
                return

            model = joblib.load(model_path)
            results = []
            for sym, trig in events:
                try:
                    with sqlite3.connect(tech_db) as conn:
                        row = conn.execute(
                            "SELECT date, close, volume, delivery, high, low FROM technical_data WHERE symbol = ? AND date >= ? ORDER BY date ASC LIMIT 30",
                            (sym, trig),
                        ).fetchall()
                    if len(row) < 2:
                        continue
                    closes = [r[1] for r in row]
                    volumes = [r[2] for r in row]
                    deliveries = [r[3] for r in row]
                    highs = [r[4] for r in row]
                    lows = [r[5] for r in row]
                    first_close = closes[0]
                    last_close = closes[-1]
                    max_dd = (
                        (min(closes) - first_close) / first_close * 100
                        if first_close > 0
                        else 0
                    )
                    avg_vol = np.mean(volumes) if volumes else 1
                    avg_del = np.mean(deliveries) if deliveries else 0
                    avg_range = (
                        np.mean([h - l for h, l in zip(highs, lows)]) if highs else 1
                    )
                    del_vals = deliveries
                    if len(del_vals) > 1:
                        del_mean = np.mean(del_vals)
                        del_std = np.std(del_vals) if len(del_vals) > 1 else 1
                        del_zscores = [
                            (d - del_mean) / (del_std + 1e-9) for d in del_vals
                        ]
                        del_z_min = min(del_zscores)
                        del_z_mean = np.mean(del_zscores)
                    else:
                        del_z_min = 0.0
                        del_z_mean = 0.0
                    features = [
                        del_z_min,
                        del_z_mean,
                        avg_range / (avg_range + 1e-9),
                        volumes[-1] / (avg_vol + 1e-9),
                        len(row),
                        max_dd,
                    ]
                    X = pd.DataFrame(
                        [features],
                        columns=[
                            "del_zscore_min",
                            "del_zscore_mean",
                            "range_atr_min",
                            "vol_ratio_min",
                            "digestion_days",
                            "max_drawdown_pct",
                        ],
                    )
                    preds = model.predict(X)
                    predicted_return_pct = round(float(preds[0, 0]), 2)
                    breakout_probability = round(
                        1 / (1 + np.exp(-predicted_return_pct / 10)), 4
                    )
                    confidence = (
                        "High"
                        if breakout_probability >= 0.7
                        else ("Medium" if breakout_probability >= 0.4 else "Low")
                    )
                    sector = None
                    mcap = None
                    if _os.path.exists(val_db):
                        with sqlite3.connect(val_db) as vconn:
                            vrow = vconn.execute(
                                "SELECT COALESCE(market_cap, 0), sector FROM fundamentals WHERE symbol = ? LIMIT 1",
                                (sym,),
                            ).fetchone()
                            if vrow:
                                mcap = float(vrow[0]) if vrow[0] else None
                                sector = vrow[1]
                    results.append(
                        {
                            "symbol": sym,
                            "trigger_date": trig,
                            "predicted_return_pct": predicted_return_pct,
                            "confidence": confidence,
                            "sector": sector,
                            "market_cap": mcap,
                            "breakout_probability": breakout_probability,
                        }
                    )
                except Exception:
                    continue

            _launchpad_scan_state.update(
                {
                    "scan_status": "completed",
                    "last_scan": datetime.now().isoformat(),
                    "predictions": results,
                    "message": f"Found {len(results)} predictions",
                }
            )
        except Exception as e:
            _launchpad_scan_state.update({"scan_status": "error", "message": str(e)})

    threading.Thread(target=_run, daemon=True).start()
    return {"status": "started"}


# --- Wyckoff Automaton State ---
_wy_scan_state: dict = {
    "scan_status": "idle",
    "last_scan": None,
    "progress": 0,
    "message": "Idle — click Scan to start",
    "candidates": [],
    "scanned_date": None,
}
_wy_scan_lock = threading.Lock()
_WY_SCAN_CACHE = os.path.join(MODELS_DIR, "wyckoff_cache.json")


def _save_darvas_cache():
    import json as _json
    import os as _os

    try:
        _os.makedirs("models", exist_ok=True)
        with _darvas_scan_lock:
            data = {
                "last_scan": _darvas_scan_state["last_scan"],
                "candidates": _darvas_scan_state["candidates"],
                "message": _darvas_scan_state["message"],
            }
        with open(_DARVAS_SCAN_CACHE, "w") as _f:
            _json.dump(data, _f)
    except Exception as e:
        logger.warning(f"Cache operation failed: {e}")


def _load_darvas_cache() -> dict | None:
    import json as _json
    import os as _os

    try:
        if _os.path.exists(_DARVAS_SCAN_CACHE):
            with open(_DARVAS_SCAN_CACHE) as _f:
                return _json.load(_f)
    except Exception as e:
        logger.warning(f"Cache operation failed: {e}")
    return None


def _save_wy_cache():
    import json as _json
    import os as _os

    try:
        _os.makedirs("models", exist_ok=True)
        with _wy_scan_lock:
            data = {
                "last_scan": _wy_scan_state["last_scan"],
                "candidates": _wy_scan_state["candidates"],
                "message": _wy_scan_state["message"],
            }
        with open(_WY_SCAN_CACHE, "w") as _f:
            _json.dump(data, _f)
    except Exception as e:
        logger.warning(f"Cache operation failed: {e}")


def _load_wy_cache() -> dict | None:
    import json as _json
    import os as _os

    try:
        if _os.path.exists(_WY_SCAN_CACHE):
            with open(_WY_SCAN_CACHE) as _f:
                return _json.load(_f)
    except Exception as e:
        logger.warning(f"Cache operation failed: {e}")
    return None


@app.get("/api/darvas/status")
async def darvas_status():
    import copy

    with _darvas_scan_lock:
        state = copy.deepcopy(_darvas_scan_state)

    if state["scan_status"] == "idle":
        cache = _load_darvas_cache()
        if cache and cache.get("candidates") is not None:
            return {
                "scan_status": "idle",
                "last_scan": cache.get("last_scan"),
                "progress": 100,
                "message": cache.get(
                    "message", f"Found {len(cache['candidates'])} candidates."
                ),
                "candidates": cache["candidates"],
            }

    return state


@app.post("/api/darvas/scan")
async def darvas_scan(payload: dict = Body(default={})):
    lookback = int(payload.get("lookback", 120))
    min_mcap = int(payload.get("min_mcap", 200))
    max_mcap = int(payload.get("max_mcap", 50000))
    prior_window = int(payload.get("prior_window", 120))
    recent_window = int(payload.get("recent_window", 30))
    lookback_days = int(payload.get("lookback_days", 150))

    raw_date = payload.get("scan_date", "")
    if raw_date and str(raw_date).strip():
        scan_date = _get_latest_trading_day_before(str(raw_date).strip())
    else:
        scan_date = None

    with _darvas_scan_lock:
        if _darvas_scan_state["scan_status"] == "scanning":
            return {"detail": "Scan already in progress"}, 409

        _darvas_scan_state.update(
            {
                "scan_status": "scanning",
                "progress": 0,
                "message": "Initialising scanner...",
                "candidates": [],
                "scanned_date": scan_date,
            }
        )

    def _run():
        try:
            from myra_app.strategies.darvas_box_scanner import DarvasBoxScanner
            import math as _math

            # The implementation uses 'base_days' instead of 'lookback'
            scanner = DarvasBoxScanner(
                base_days=lookback,
                min_mcap=min_mcap,
                max_mcap=max_mcap,
            )

            _darvas_scan_state["message"] = "Loading universe..."
            _darvas_scan_state["progress"] = 5

            universe = scanner._get_universe()
            total = max(len(universe), 1)
            _darvas_scan_state["message"] = f"Scanning {total} symbols..."
            _darvas_scan_state["progress"] = 10

            original_get_tech = scanner._get_tech_data
            processed = [0]

            def _tracked_get_tech(symbol, min_date, max_date=None):
                processed[0] += 1
                if processed[0] % 25 == 0:
                    pct = 10 + int((processed[0] / total) * 82)
                    _darvas_scan_state["progress"] = min(pct, 92)
                    _darvas_scan_state[
                        "message"
                    ] = f"Scanning {processed[0]}/{total} symbols..."
                return original_get_tech(symbol, min_date, max_date)

            scanner._get_tech_data = _tracked_get_tech

            df = scanner.scan(as_on_date=scan_date)

            _darvas_scan_state["progress"] = 95
            _darvas_scan_state["message"] = "Finalising results..."

            candidates = _df_to_safe_records(df)

            _darvas_scan_state.update(
                {
                    "scan_status": "completed",
                    "last_scan": datetime.now().isoformat(),
                    "progress": 100,
                    "message": f"Found {len(candidates)} candidates",
                    "candidates": candidates,
                    "scanned_date": scan_date,
                }
            )
            _save_darvas_cache()

        except Exception as e:
            logger.error("Darvas Box scan failed: %s", e, exc_info=True)
            _darvas_scan_state.update(
                {
                    "scan_status": "error",
                    "progress": 0,
                    "message": str(e),
                }
            )

    threading.Thread(target=_run, daemon=True).start()
    return {"status": "started"}


@app.get("/api/wyckoff/status")
async def wyckoff_status():
    import copy

    with _wy_scan_lock:
        state = copy.deepcopy(_wy_scan_state)

    if state["scan_status"] == "idle":
        cache = _load_wy_cache()
        if cache and cache.get("candidates") is not None:
            return {
                "scan_status": "idle",
                "last_scan": cache.get("last_scan"),
                "progress": 100,
                "message": cache.get(
                    "message", f"Found {len(cache['candidates'])} candidates."
                ),
                "candidates": cache["candidates"],
            }

    return state


@app.post("/api/wyckoff/scan")
async def wyckoff_scan(payload: dict = Body(default={})):
    min_mcap = int(payload.get("min_mcap", 200))
    max_mcap = int(payload.get("max_mcap", 50000))
    prior_window = int(payload.get("prior_window", 120))
    recent_window = int(payload.get("recent_window", 30))
    lookback_days = int(payload.get("lookback_days", 150))

    raw_date = payload.get("scan_date", "")
    if raw_date and str(raw_date).strip():
        scan_date = _get_latest_trading_day_before(str(raw_date).strip())
    else:
        scan_date = None

    with _wy_scan_lock:
        if _wy_scan_state["scan_status"] == "scanning":
            return {"detail": "Scan already in progress"}, 409

        _wy_scan_state.update(
            {
                "scan_status": "scanning",
                "progress": 0,
                "message": "Initialising scanner...",
                "candidates": [],
                "scanned_date": scan_date,
            }
        )

    def _run():
        try:
            from myra_app.strategies.wyckoff_automaton import WyckoffAutomaton
            import math as _math

            scanner = WyckoffAutomaton(
                min_mcap=min_mcap,
                max_mcap=max_mcap,
            )

            _wy_scan_state["message"] = "Loading universe..."
            _wy_scan_state["progress"] = 5

            universe = scanner._get_universe()
            total = max(len(universe), 1)
            _wy_scan_state["message"] = f"Scanning {total} symbols..."
            _wy_scan_state["progress"] = 10

            original_get_tech = scanner._get_tech_data
            processed = [0]

            def _tracked_get_tech(symbol, min_date, max_date=None):
                processed[0] += 1
                if processed[0] % 25 == 0:
                    pct = 10 + int((processed[0] / total) * 82)
                    _wy_scan_state["progress"] = min(pct, 92)
                    _wy_scan_state[
                        "message"
                    ] = f"Scanning {processed[0]}/{total} symbols..."
                return original_get_tech(symbol, min_date, max_date)

            scanner._get_tech_data = _tracked_get_tech

            df = scanner.scan(as_on_date=scan_date)

            _wy_scan_state["progress"] = 95
            _wy_scan_state["message"] = "Finalising results..."

            candidates = _df_to_safe_records(df)

            _wy_scan_state.update(
                {
                    "scan_status": "completed",
                    "last_scan": datetime.now().isoformat(),
                    "progress": 100,
                    "message": f"Found {len(candidates)} candidates",
                    "candidates": candidates,
                    "scanned_date": scan_date,
                }
            )
            _save_wy_cache()

        except Exception as e:
            logger.error("Wyckoff scan failed: %s", e, exc_info=True)
            _wy_scan_state.update(
                {
                    "scan_status": "error",
                    "progress": 0,
                    "message": str(e),
                }
            )

    threading.Thread(target=_run, daemon=True).start()
    return {"status": "started"}


# ---- Multibagger Pro Scanner ----

_multibagger_result = {
    "scan_status": "idle",
    "candidates": [],
    "message": "Use POST /api/multibagger/scan to run",
}


@app.post("/api/multibagger/scan")
async def multibagger_scan(payload: dict = Body(default={})):
    """Run Multibagger Pro scan and store results for status polling."""
    global _multibagger_result
    _multibagger_result = {
        "scan_status": "scanning",
        "candidates": [],
        "message": "Running...",
    }

    def _run():
        global _multibagger_result
        try:
            from myra_app.strategies.multibagger_early_detection import (
                Strategy as MultibaggerScanner,
            )
            from myra_app.librarian_core import LibrarianCore
            import math as _math, pandas as pd, sqlite3, os
            from myra_app.constants import DB_DIR

            lookback = int(payload.get("lookback", 42))
            min_mcap = int(payload.get("min_mcap", 200))
            max_mcap = int(payload.get("max_mcap", 50000))
            prior_window = int(payload.get("prior_window", 120))
            recent_window = int(payload.get("recent_window", 30))
            lookback_days = int(payload.get("lookback_days", 150))

            scanner = MultibaggerScanner()

            # Build universe from valuation.db
            val_path = os.path.join(DB_DIR, "myra_valuation.db")
            tech_path = os.path.join(DB_DIR, "myra_technical.db")

            val_conn = sqlite3.connect(val_path)
            symbols = [
                r[0]
                for r in val_conn.execute(
                    "SELECT symbol FROM fundamentals WHERE COALESCE(market_cap,0) BETWEEN ? AND ?",
                    (min_mcap, max_mcap),
                ).fetchall()
            ]
            val_conn.close()

            if not symbols:
                symbols = [
                    r[0]
                    for r in sqlite3.connect(tech_path)
                    .execute(
                        "SELECT DISTINCT symbol FROM technical_data ORDER BY symbol"
                    )
                    .fetchall()
                ][:500]

            candidates = []
            tech_conn = sqlite3.connect(tech_path)
            val_conn2 = sqlite3.connect(val_path)
            funda_cols = [
                c[0]
                for c in val_conn2.execute("PRAGMA table_info(fundamentals)").fetchall()
            ]

            for i, sym in enumerate(symbols):
                if i % 50 == 0:
                    _multibagger_result["message"] = f"Scanning {i+1}/{len(symbols)}..."

                # Fetch OHLCV data
                df = pd.read_sql(
                    f"SELECT date, open, high, low, close, volume FROM technical_data WHERE symbol=? AND date >= date('now','-{lookback+30} days') ORDER BY date",
                    tech_conn,
                    params=(sym,),
                )
                if df.empty or len(df) < 30:
                    continue

                # Fetch fundamentals
                row = val_conn2.execute(
                    "SELECT * FROM fundamentals WHERE symbol=?", (sym,)
                ).fetchone()
                if row:
                    funda = dict(zip(funda_cols, row))
                else:
                    funda = {}

                try:
                    result = scanner.run(df, funda)
                    if result and result.get("signal"):
                        result["symbol"] = sym
                        # Sanitize NaN/Inf
                        for k, v in list(result.items()):
                            if isinstance(v, float) and (
                                _math.isnan(v) or _math.isinf(v)
                            ):
                                result[k] = None
                        candidates.append(result)
                except Exception:
                    pass

            tech_conn.close()
            val_conn2.close()

            _multibagger_result = {
                "scan_status": "completed",
                "last_scan": datetime.now().isoformat(),
                "candidates": candidates,
                "message": f"Found {len(candidates)} candidates",
            }
        except Exception as e:
            _multibagger_result = {
                "scan_status": "error",
                "message": str(e),
                "candidates": [],
            }

    threading.Thread(target=_run, daemon=True).start()
    return {"status": "started"}


@app.get("/api/multibagger/status")
async def multibagger_status():
    """Return last Multibagger scan results."""
    return _multibagger_result


# --- Bottom Hunter Scan State ---
_bh_scan_state: dict = {
    "scan_status": "idle",
    "last_scan": None,
    "progress": 0,
    "message": "Idle — click Scan to start",
    "candidates": [],
    "scanned_date": None,
}
_bh_scan_lock = threading.Lock()
_BH_SCAN_CACHE = os.path.join(MODELS_DIR, "bottom_hunter_cache.json")


def _save_bh_cache():
    import json as _json
    import os as _os

    try:
        _os.makedirs("models", exist_ok=True)
        with _bh_scan_lock:
            data = {
                "last_scan": _bh_scan_state["last_scan"],
                "candidates": _bh_scan_state["candidates"],
                "message": _bh_scan_state["message"],
            }
        with open(_BH_SCAN_CACHE, "w") as _f:
            _json.dump(data, _f)
    except Exception as e:
        logger.warning(f"Cache operation failed: {e}")


def _load_bh_cache() -> dict | None:
    import json as _json
    import os as _os

    try:
        if _os.path.exists(_BH_SCAN_CACHE):
            with open(_BH_SCAN_CACHE) as _f:
                return _json.load(_f)
    except Exception as e:
        logger.warning(f"Cache operation failed: {e}")
    return None


@app.get("/api/bottom-hunter/status")
async def bottom_hunter_status():
    import copy

    with _bh_scan_lock:
        state = copy.deepcopy(_bh_scan_state)

    if state["scan_status"] == "idle":
        cache = _load_bh_cache()
        if cache and cache.get("candidates") is not None:
            return {
                "scan_status": "idle",
                "last_scan": cache.get("last_scan"),
                "progress": 100,
                "message": cache.get(
                    "message", f"Found {len(cache['candidates'])} candidates."
                ),
                "candidates": cache["candidates"],
                "scanned_date": None,
            }

    return state


@app.post("/api/bottom-hunter/scan")
async def bottom_hunter_scan(payload: dict = Body(default={})):
    min_mcap = int(payload.get("min_mcap", 200))
    max_mcap = int(payload.get("max_mcap", 50000))
    min_delivery_absorption = float(payload.get("min_delivery_absorption", 5.0))
    adtv_min_cr = float(payload.get("adtv_min_cr", 1.0))
    lookback_days = int(payload.get("lookback_days", 260))

    timeframe = str(payload.get("timeframe", "daily")).strip().lower()
    if timeframe not in ("daily", "weekly"):
        timeframe = "daily"

    raw_date = payload.get("scan_date", "")
    if raw_date and str(raw_date).strip():
        scan_date = _get_latest_trading_day_before(str(raw_date).strip())
    else:
        scan_date = None

    with _bh_scan_lock:
        if _bh_scan_state["scan_status"] == "scanning":
            return {"detail": "Scan already in progress"}, 409

        _bh_scan_state.update(
            {
                "scan_status": "scanning",
                "progress": 0,
                "message": "Initializing scanner...",
                "candidates": [],
                "scanned_date": scan_date,
            }
        )

    def _run():
        try:
            from myra_app.strategies.bottom_hunter import BottomHunter
            import math as _math

            scanner = BottomHunter(
                min_mcap=min_mcap,
                max_mcap=max_mcap,
                min_delivery_absorption=min_delivery_absorption,
                adtv_min_cr=adtv_min_cr,
                lookback_days=lookback_days,
                timeframe=timeframe,
            )

            _bh_scan_state["message"] = "Loading universe..."
            _bh_scan_state["progress"] = 5

            universe = scanner._get_universe()
            total = max(len(universe), 1)
            _bh_scan_state["message"] = f"Scanning {total} symbols..."
            _bh_scan_state["progress"] = 10

            original_get_tech = scanner._get_tech_data
            processed = [0]

            def _tracked_get_tech(symbol, min_date, max_date=None):
                processed[0] += 1
                if processed[0] % 25 == 0:
                    pct = 10 + int((processed[0] / total) * 82)
                    _bh_scan_state["progress"] = min(pct, 92)
                    _bh_scan_state[
                        "message"
                    ] = f"Scanning {processed[0]}/{total} symbols..."
                return original_get_tech(symbol, min_date, max_date)

            scanner._get_tech_data = _tracked_get_tech

            df = scanner.scan(as_on_date=scan_date)

            _bh_scan_state["progress"] = 95
            _bh_scan_state["message"] = "Finalizing results..."

            candidates = _df_to_safe_records(df)

            _bh_scan_state.update(
                {
                    "scan_status": "completed",
                    "last_scan": datetime.now().isoformat(),
                    "progress": 100,
                    "message": f"Found {len(candidates)} candidates",
                    "candidates": candidates,
                    "scanned_date": scan_date,
                }
            )
            _save_bh_cache()

        except Exception as e:
            logger.error("Bottom Hunter scan failed: %s", e, exc_info=True)
            _bh_scan_state.update(
                {
                    "scan_status": "error",
                    "progress": 0,
                    "message": str(e),
                }
            )

    threading.Thread(target=_run, daemon=True).start()
    return {"status": "started"}


# --- Climax Accumulation State ---
_climax_scan_state: dict = {
    "scan_status": "idle",
    "last_scan": None,
    "progress": 0,
    "message": "Idle — click Scan to start",
    "candidates": [],
    "scanned_date": None,
}
_climax_scan_lock = threading.Lock()
_CLIMAX_CACHE = os.path.join(MODELS_DIR, "climax_accumulation_cache.json")


def _save_climax_cache():
    import json as _json
    import os as _os

    try:
        _os.makedirs("models", exist_ok=True)
        with _climax_scan_lock:
            data = {
                "last_scan": _climax_scan_state["last_scan"],
                "candidates": _climax_scan_state["candidates"],
                "message": _climax_scan_state["message"],
            }
        with open(_CLIMAX_CACHE, "w") as _f:
            _json.dump(data, _f)
    except Exception as e:
        logger.warning(f"Climax cache save failed: {e}")


def _load_climax_cache() -> dict | None:
    import json as _json
    import os as _os

    try:
        if _os.path.exists(_CLIMAX_CACHE):
            with open(_CLIMAX_CACHE) as _f:
                return _json.load(_f)
    except Exception as e:
        logger.warning(f"Climax cache load failed: {e}")
    return None


@app.get("/api/climax-accumulation/status")
async def climax_accumulation_status():
    import copy

    with _climax_scan_lock:
        state = copy.deepcopy(_climax_scan_state)

    if state["scan_status"] == "idle":
        cache = _load_climax_cache()
        if cache and cache.get("candidates") is not None:
            return {
                "scan_status": "idle",
                "last_scan": cache.get("last_scan"),
                "progress": 100,
                "message": cache.get(
                    "message", f"Found {len(cache['candidates'])} candidates."
                ),
                "candidates": cache["candidates"],
            }

    return state


@app.post("/api/climax-accumulation/scan")
async def climax_accumulation_scan(payload: dict = Body(default={})):
    min_adtv_cr = float(payload.get("min_adtv_cr", 1.0))
    raw_date = payload.get("scan_date", "")
    if raw_date and str(raw_date).strip():
        scan_date = _get_latest_trading_day_before(str(raw_date).strip())
    else:
        scan_date = None

    with _climax_scan_lock:
        if _climax_scan_state["scan_status"] == "scanning":
            return {"detail": "Scan already in progress"}, 409

        _climax_scan_state.update(
            {
                "scan_status": "scanning",
                "progress": 0,
                "message": "Initialising Climax Accumulation scanner...",
                "candidates": [],
                "scanned_date": scan_date,
            }
        )

    def _run():
        try:
            from myra_app.strategies.climax_accumulation import (
                ClimaxAccumulationScanner,
            )
            import math as _math

            scanner = ClimaxAccumulationScanner(
                target_date=scan_date,
                min_adtv_cr=min_adtv_cr,
            )

            _climax_scan_state["message"] = "Loading universe..."
            _climax_scan_state["progress"] = 5

            universe = scanner._get_universe()
            total = max(len(universe), 1)
            _climax_scan_state["message"] = f"Scanning {total} symbols..."
            _climax_scan_state["progress"] = 10

            original_get_tech = scanner._get_tech_data
            processed = [0]

            def _tracked_get_tech(symbol, min_date, max_date=None):
                processed[0] += 1
                if processed[0] % 25 == 0:
                    pct = 10 + int((processed[0] / total) * 82)
                    _climax_scan_state["progress"] = min(pct, 92)
                    _climax_scan_state[
                        "message"
                    ] = f"Scanning {processed[0]}/{total} symbols..."
                return original_get_tech(symbol, min_date, max_date)

            scanner._get_tech_data = _tracked_get_tech

            df = scanner.scan(as_on_date=scan_date)

            _climax_scan_state["progress"] = 95
            _climax_scan_state["message"] = "Finalising results..."

            candidates = _df_to_safe_records(df)

            _climax_scan_state.update(
                {
                    "scan_status": "completed",
                    "last_scan": datetime.now().isoformat(),
                    "progress": 100,
                    "message": f"Found {len(candidates)} candidates",
                    "candidates": candidates,
                    "scanned_date": scan_date,
                }
            )
            _save_climax_cache()

        except Exception as e:
            logger.error("Climax Accumulation scan failed: %s", e, exc_info=True)
            _climax_scan_state.update(
                {
                    "scan_status": "error",
                    "progress": 0,
                    "message": str(e),
                }
            )

    threading.Thread(target=_run, daemon=True).start()
    return {"status": "started"}


@app.delete("/api/cache/{scanner_name}")
async def clear_scanner_cache(scanner_name: str):
    """Delete the cached scan results for a given scanner."""
    allowed = {
        "invisible-hand",
        "trigger",
        "wyckoff",
        "float-exhaustion",
        "liquidity-flip",
        "operator-fingerprint",
        "seasonal-delivery",
        "darvas",
        "multibagger",
        "launchpad",
        "bottom-hunter",
        "climax-accumulation",
        "dcb-bargain",
    }
    if scanner_name not in allowed:
        raise HTTPException(status_code=400, detail="Unknown scanner")

    stem = scanner_name.replace("-", "_")
    cache_path = os.path.join(MODELS_DIR, f"{stem}_cache.json")
    existed = os.path.exists(cache_path)
    if existed:
        os.remove(cache_path)

    reset = {
        "scan_status": "idle",
        "candidates": [],
        "message": "Cache cleared",
        "last_scan": None,
    }
    if scanner_name == "invisible-hand":
        _ih_scan_state.update(reset)
    elif scanner_name == "trigger":
        _trigger_scan_state.update(reset)
    elif scanner_name == "darvas":
        _darvas_scan_state.update(reset)
    elif scanner_name == "liquidity-flip":
        _lf_scan_state.update(reset)
    elif scanner_name == "operator-fingerprint":
        _of_scan_state.update(reset)
    elif scanner_name == "float-exhaustion":
        _fe_scan_state.update(reset)
    elif scanner_name == "seasonal-delivery":
        _sd_scan_state.update(reset)
    elif scanner_name == "wyckoff":
        _wy_scan_state.update(reset)
    elif scanner_name == "bottom-hunter":
        _bh_scan_state.update(reset)
    elif scanner_name == "climax-accumulation":
        _climax_scan_state.update(reset)
    elif scanner_name == "dcb-bargain":
        _dcb_scan_state.update(reset)

    return {"status": "deleted" if existed else "not_found", "scanner": scanner_name}


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
