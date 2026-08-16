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

MYRA_API_SECRET = os.environ.get("MYRA_API_SECRET", "myra-local-dev-2026")


async def verify_myra_auth(x_myra_auth: str = Header(None)):
    if x_myra_auth != MYRA_API_SECRET:
        raise HTTPException(status_code=401, detail="Unauthorized")


logger = logging.getLogger(__name__)

import sys as _sys, os as _os

_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from pipeline_dashboard import router as pipeline_router
from myra_web.routes.fundamentals import router as fundamentals_router
from myra_web.routes.full_fundamentals import router as full_fundamentals_router

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


def _spawn_task(name, fn, *args, **kwargs):
    """Run fn(*args, **kwargs) in a daemon thread; register in task_tracker; return task id."""
    from myra_app.task_tracker import create_task, update, unregister

    tid = create_task(name)

    def _wrapped():
        try:
            fn(*args, **kwargs)
            update(tid, status="completed")
        except Exception as e:
            update(tid, status=f"error: {e}")
        finally:
            unregister(tid)

    threading.Thread(target=_wrapped, name=f"myra-task-{name}", daemon=True).start()
    return tid


_finstack_cache = {}
CACHE_TTL = 300  # 5 minutes

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


# Use the expected folder structure: Myra\myra_web (this project) side-by-side with Myra\myra_app
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_DIR = os.path.abspath(os.path.join(BASE_DIR, "..", "myra_app", "db"))


def get_db_path(db_key: str):
    """Safely construct the path to a specific SQLite sidecar."""
    filename = LibrarianCore.DB_MAP.get(db_key)
    if not filename:
        return None
    return os.path.join(DB_DIR, filename)


def _df_to_safe_records(df) -> list[dict]:
    """Convert a DataFrame to a list of dicts, replacing NaN/Inf with None."""
    import math as _math

    if df.empty:
        return []
    records = df.to_dict("records")
    for rec in records:
        for key, val in list(rec.items()):
            if isinstance(val, float) and (_math.isnan(val) or _math.isinf(val)):
                rec[key] = None
    return records


@app.get("/api/health")
def health_check():
    """
    Checks if the databases in myra_app/db exist and can be connected to.
    The React UI polls this endpoint to update the green/yellow status lights in the sidebar.
    """
    canonical_to_frontend = {
        "technical": "_tech_conn",
        "meta": "_meta_conn",
        "valuation": "_val_conn",
        "institutional": "_inst_conn",
        "governance": "_gov_conn",
        "network_cache": "_cache_conn",
        "scoring": "_scoring_conn",
        "calendar": "_cal_conn",
    }
    health = {}
    for canonical_key, frontend_key in canonical_to_frontend.items():
        db_file = LibrarianCore.DB_MAP.get(canonical_key)
        if db_file:
            db_path = os.path.join(DB_DIR, db_file)
            if os.path.exists(db_path):
                try:
                    conn = sqlite3.connect(db_path)
                    conn.execute("SELECT 1")
                    conn.close()
                    health[frontend_key] = {"connected": True, "path": db_path}
                except Exception:
                    health[frontend_key] = {"connected": False, "path": db_path}
            else:
                health[frontend_key] = {"connected": False, "path": None}
        else:
            health[frontend_key] = {"connected": False, "path": None}

    # Data coverage metrics for fundamentals table
    val_path = os.path.join(DB_DIR, LibrarianCore.DB_MAP["valuation"])
    coverage = {"error": "not available"}
    if os.path.exists(val_path):
        try:
            conn = sqlite3.connect(val_path)
            total = conn.execute("SELECT COUNT(*) FROM fundamentals").fetchone()[0]
            coverage = {
                "total_symbols": total,
                "shares_outstanding": conn.execute(
                    "SELECT COUNT(*) FROM fundamentals WHERE shares_outstanding IS NOT NULL"
                ).fetchone()[0],
                "insider_holding_pct": conn.execute(
                    "SELECT COUNT(*) FROM fundamentals WHERE insider_holding_pct IS NOT NULL"
                ).fetchone()[0],
                "promoter_holding_pct": conn.execute(
                    "SELECT COUNT(*) FROM fundamentals WHERE promoter_holding_pct IS NOT NULL"
                ).fetchone()[0],
                "industry": conn.execute(
                    "SELECT COUNT(*) FROM fundamentals WHERE industry IS NOT NULL"
                ).fetchone()[0],
                "free_float_pct": conn.execute(
                    "SELECT COUNT(*) FROM fundamentals WHERE free_float_pct IS NOT NULL"
                ).fetchone()[0],
                "market_cap": conn.execute(
                    "SELECT COUNT(*) FROM fundamentals WHERE market_cap IS NOT NULL"
                ).fetchone()[0],
                "pe": conn.execute(
                    "SELECT COUNT(*) FROM fundamentals WHERE pe IS NOT NULL"
                ).fetchone()[0],
            }
            conn.close()
        except Exception:
            coverage = {"error": "query failed"}
    return {"health": health, "coverage": coverage}


@app.get("/api/data-health")
async def data_health():
    import glob
    from datetime import timezone, timedelta

    IST = timezone(timedelta(hours=5, minutes=30))
    result = {
        "latest_ohlcv_date": None,
        "days_behind": None,
        "ohlcv_symbols_today": None,
        "enrichment_complete": None,
        "fundamentals_total": None,
        "fundamentals_with_promoter": None,
        "fundamentals_with_free_float": None,
        "nifty_benchmark_latest": None,
        "last_backup_date": "unknown",
        "scanner_cache_counts": {},
    }

    try:
        tech_db = os.path.join(DB_DIR, LibrarianCore.DB_MAP["technical"])
        if os.path.exists(tech_db):
            conn = sqlite3.connect(tech_db)
            try:
                row = conn.execute("SELECT MAX(date) FROM technical_data").fetchone()
                result["latest_ohlcv_date"] = row[0] if row else None
                if result["latest_ohlcv_date"]:
                    latest = result["latest_ohlcv_date"]
                    ist_now = datetime.now(IST).date()
                    try:
                        latest_date = datetime.strptime(latest, "%Y-%m-%d").date()
                        result["days_behind"] = (ist_now - latest_date).days
                    except ValueError:
                        result["days_behind"] = None
                    cnt = conn.execute(
                        "SELECT COUNT(*) FROM technical_data WHERE date = ?",
                        (latest,),
                    ).fetchone()
                    result["ohlcv_symbols_today"] = cnt[0] if cnt else 0
                    enriched = conn.execute(
                        """SELECT COUNT(*) FROM technical_data
                           WHERE date = ?
                             AND delivery_divergence_score IS NOT NULL""",
                        (latest,),
                    ).fetchone()
                    total = result["ohlcv_symbols_today"] or 1
                    result["enrichment_complete"] = (
                        enriched[0] == total if enriched else False
                    )
                else:
                    result["ohlcv_symbols_today"] = 0
                    result["enrichment_complete"] = False
            except Exception:
                pass
            finally:
                conn.close()

        val_db = os.path.join(DB_DIR, LibrarianCore.DB_MAP["valuation"])
        if os.path.exists(val_db):
            conn = sqlite3.connect(val_db)
            try:
                total = conn.execute("SELECT COUNT(*) FROM fundamentals").fetchone()
                result["fundamentals_total"] = total[0] if total else 0
                prom = conn.execute(
                    "SELECT COUNT(*) FROM fundamentals WHERE promoter_holding_pct > 0"
                ).fetchone()
                result["fundamentals_with_promoter"] = prom[0] if prom else 0
                ff = conn.execute(
                    "SELECT COUNT(*) FROM fundamentals WHERE free_float_pct > 0"
                ).fetchone()
                result["fundamentals_with_free_float"] = ff[0] if ff else 0
            except Exception:
                pass
            finally:
                conn.close()

        meta_db = os.path.join(DB_DIR, LibrarianCore.DB_MAP["meta"])
        if os.path.exists(meta_db):
            conn = sqlite3.connect(meta_db)
            try:
                bm = conn.execute("SELECT MAX(date) FROM benchmarks").fetchone()
                result["nifty_benchmark_latest"] = bm[0] if bm else None
            except Exception:
                pass
            finally:
                conn.close()

        backup_dir = os.path.join(DB_DIR, "backups")
        if os.path.exists(backup_dir):
            try:
                files = [
                    f
                    for f in os.listdir(backup_dir)
                    if f.startswith("technical_") and f.endswith(".db")
                ]
                if files:
                    files.sort(reverse=True)
                    date_part = files[0].replace("technical_", "").replace(".db", "")
                    result["last_backup_date"] = date_part
            except Exception:
                pass

        scanner_names = [
            "invisible_hand",
            "trigger",
            "liquidity_flip",
            "operator_fingerprint",
            "float_exhaustion",
            "seasonal_delivery",
            "wyckoff",
        ]
        models_dir = MODELS_DIR
        for name in scanner_names:
            try:
                cache_path = os.path.join(models_dir, f"{name}_cache.json")
                if os.path.exists(cache_path):
                    with open(cache_path, encoding="utf-8") as f:
                        data = json.load(f)
                    candidates = data.get("candidates", [])
                    result["scanner_cache_counts"][name] = len(candidates)
                else:
                    result["scanner_cache_counts"][name] = 0
            except Exception:
                result["scanner_cache_counts"][name] = 0

    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

    return result


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


class ToolRequest(BaseModel):
    tool_id: str


@app.post("/api/tools/execute")
def execute_tool(req: ToolRequest, _=Depends(verify_myra_auth)):
    """
    Hooks the React UI 'Execute' buttons directly into your local Python scripts.
    """
    tool_map = {
        "force_sync": "tools/force_sync.py",
        "force_backfill": "tools/force_backfill.py",
        "train_aeon": "research/train_aeon.py",
        "repair_indicators": "tools/repair_calculated_indicators.py",
    }

    script_path = tool_map.get(req.tool_id)
    if not script_path:
        raise HTTPException(status_code=400, detail="Tool mapping not found")

    full_script_path = os.path.join(BASE_DIR, script_path.replace("/", os.sep))

    if not os.path.exists(full_script_path):
        raise HTTPException(
            status_code=404, detail=f"Script not found at {full_script_path}"
        )

    try:
        # Run the Python script synchronously, capturing output
        # For long-running scripts >1 minute, you would typically use Celery or BackgroundTasks here
        result = subprocess.run(
            ["python", full_script_path], capture_output=True, text=True, timeout=120
        )

        combined_logs = result.stdout + "\n" + result.stderr
        return {
            "success": result.returncode == 0,
            "logs": combined_logs.strip() or "Script executed silently.",
        }
    except subprocess.TimeoutExpired:
        return {"success": False, "logs": "Execution timed out after 120 seconds."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# Note: Parquet Route (/api/parquet) could be added here using pandas/pyarrow to serve DataLakeView


@app.post("/api/tools/sync/fundamentals")
async def force_fundamentals_sync():
    """Trigger a full fundamentals sync (Morningstar + NSE) NOW (async)."""
    try:
        tid = _spawn_task("fundamentals_sync", _task_fundamentals_sync)
        return JSONResponse(
            status_code=202, content={"status": "started", "task_id": tid}
        )
    except Exception as e:
        return JSONResponse(
            status_code=500, content={"status": "error", "message": str(e)}
        )


@app.post("/api/tools/sync/etf")
async def force_etf_sync():
    """Trigger ETF blocklist sync NOW (async)."""
    try:
        tid = _spawn_task("etf_sync", _task_etf_sync)
        return JSONResponse(
            status_code=202, content={"status": "started", "task_id": tid}
        )
    except Exception as e:
        return JSONResponse(
            status_code=500, content={"status": "error", "message": str(e)}
        )


@app.post("/api/tools/sync/index")
async def force_index_sync():
    """Trigger NIFTY index constituents sync NOW (async)."""
    try:
        tid = _spawn_task("index_sync", _task_index_sync)
        return JSONResponse(
            status_code=202, content={"status": "started", "task_id": tid}
        )
    except Exception as e:
        return JSONResponse(
            status_code=500, content={"status": "error", "message": str(e)}
        )


@app.post("/api/tools/ingest")
async def force_daily_ingest():
    """Trigger daily bhavcopy ingest NOW (async)."""
    try:
        tid = _spawn_task("daily_ingest", _task_daily_ingest, force=True)
        return JSONResponse(
            status_code=202, content={"status": "started", "task_id": tid}
        )
    except Exception as e:
        return JSONResponse(
            status_code=500, content={"status": "error", "message": str(e)}
        )


@app.post("/api/tools/db-doctor")
async def run_db_doctor():
    """Run DB Doctor health check NOW."""
    try:
        _task_db_doctor()
        return {"success": True, "message": "DB Doctor completed"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/market-breadth")
async def get_market_breadth():
    """
    Return advances / declines for the latest trading date in technical_data.
    An advance = close > previous close; decline = close < previous close.
    """
    tech_db = os.path.join(DB_DIR, LibrarianCore.DB_MAP["technical"])
    try:
        with sqlite3.connect(tech_db) as conn:
            # Find latest date
            latest_row = conn.execute("SELECT MAX(date) FROM technical_data").fetchone()
            latest = latest_row[0] if latest_row else None
            if not latest:
                return {"advances": 0, "declines": 0, "total": 0, "date": None}

            # We'll use a simple approach: fetch all symbols with their close on latest date
            # and the previous close from the previous trading day.
            prev_row = conn.execute(
                "SELECT MAX(date) FROM technical_data WHERE date < ?", (latest,)
            ).fetchone()
            prev_date = prev_row[0] if prev_row else None

            if not prev_date:
                return {"advances": 0, "declines": 0, "total": 0, "date": latest}

            # For each symbol, get close on latest date and prev close on prev_date
            rows = conn.execute(
                """
                SELECT a.symbol, a.close as close_today, b.close as close_prev
                FROM technical_data a
                JOIN technical_data b ON a.symbol = b.symbol AND b.date = ?
                WHERE a.date = ?
                  AND a.close > 0 AND b.close > 0
            """,
                (prev_date, latest),
            ).fetchall()

            advances = sum(1 for r in rows if r[1] > r[2])
            declines = sum(1 for r in rows if r[1] < r[2])
            total = advances + declines

            return {
                "advances": advances,
                "declines": declines,
                "total": total,
                "date": latest,
            }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/db-size")
async def get_db_size():
    """Return size of the main technical database."""
    try:
        tech_path = os.path.join(DB_DIR, LibrarianCore.DB_MAP["technical"])
        size_mb = os.path.getsize(tech_path) / (1024 * 1024)
        return {"size_mb": round(size_mb, 1)}
    except Exception as e:
        raise HTTPException(status_code=500, detail="Cannot read DB size")


@app.get("/api/system-info")
async def get_system_info():
    """Return CPU and memory usage (simple psutil)."""
    try:
        import psutil

        return {
            "cpu_percent": psutil.cpu_percent(interval=0.1),
            "memory_percent": psutil.virtual_memory().percent,
            "memory_total_gb": round(psutil.virtual_memory().total / (1024**3), 1),
        }
    except ImportError:
        return {"error": "psutil not installed"}


@app.post("/api/portfolio/refresh")
async def refresh_portfolio():
    """Trigger a manual refresh of portfolio prices and fundamentals (async)."""

    def _run():
        from myra_app.portfolio_db import auto_refresh_portfolio

        return auto_refresh_portfolio()

    try:
        tid = _spawn_task("portfolio_refresh", _run)
        return JSONResponse(
            status_code=202, content={"status": "started", "task_id": tid}
        )
    except Exception as e:
        return JSONResponse(
            status_code=500, content={"status": "error", "message": str(e)}
        )


@app.get("/api/portfolio/live-prices")
async def get_live_prices():
    """Fetch live intraday prices from yfinance for all portfolio holdings.
    Cached for 5 minutes in live_price_cache table."""
    import yfinance as yf
    import time as _time

    try:
        from myra_app.portfolio_db import get_all_holdings, get_db_path
    except ImportError as e:
        return {"status": "error", "message": f"portfolio_db not available: {e}"}

    try:
        holdings = get_all_holdings()
    except Exception as e:
        return {"status": "error", "message": f"Failed to read holdings: {e}"}

    if not holdings:
        return {"status": "ok", "prices": {}, "message": "No holdings in portfolio."}

    PORTFOLIO_DB = get_db_path()
    symbols = [h["symbol"] for h in holdings]

    # Create live_price_cache table if not exists
    try:
        lc = sqlite3.connect(PORTFOLIO_DB)
        lc.execute(
            """CREATE TABLE IF NOT EXISTS live_price_cache (
                symbol TEXT PRIMARY KEY,
                ltp REAL,
                change REAL,
                change_pct REAL,
                previous_close REAL,
                fetched_at TEXT DEFAULT (datetime('now','localtime'))
            )"""
        )
        lc.commit()
    except Exception:
        pass

    # Check cache freshness (5 min TTL)
    now = _time.time()
    use_cache = True
    try:
        cached_count = lc.execute("SELECT COUNT(*) FROM live_price_cache").fetchone()[0]
        if cached_count > 0:
            first = lc.execute(
                "SELECT fetched_at FROM live_price_cache LIMIT 1"
            ).fetchone()[0]
            if first:
                try:
                    cached_time = _time.mktime(
                        _time.strptime(first, "%Y-%m-%d %H:%M:%S")
                    )
                    if (now - cached_time) < 300:
                        # Return cached data
                        lc.row_factory = sqlite3.Row
                        rows = lc.execute("SELECT * FROM live_price_cache").fetchall()
                        prices = {}
                        for r in rows:
                            prices[r["symbol"]] = {
                                "ltp": r["ltp"],
                                "change": r["change"],
                                "change_pct": r["change_pct"],
                                "previous_close": r["previous_close"],
                                "fetched_at": r["fetched_at"],
                                "cached": True,
                            }
                        lc.close()
                        return {"status": "ok", "prices": prices, "source": "cache"}
                except Exception:
                    pass
        lc.close()
    except Exception:
        pass

    # Fetch from yfinance
    prices = {}
    warnings = []
    for sym in symbols:
        try:
            ticker = yf.Ticker(f"{sym}.NS")
            info = ticker.info
            ltp = info.get("currentPrice") or info.get("regularMarketPrice")
            prev_close = info.get("previousClose") or info.get(
                "regularMarketPreviousClose"
            )
            change = info.get("regularMarketChange")
            change_pct = info.get("regularMarketChangePercent")
            if ltp is None:
                warnings.append(f"{sym}: no live price available")
                continue
            prices[sym] = {
                "ltp": ltp,
                "change": change,
                "change_pct": change_pct,
                "previous_close": prev_close,
                "fetched_at": datetime.now().strftime("%H:%M:%S"),
                "cached": False,
            }
            _time.sleep(0.2)
        except Exception as e:
            warnings.append(f"{sym}: {e}")
            continue

    # Cache results
    if prices:
        try:
            lc = sqlite3.connect(PORTFOLIO_DB)
            for sym, p in prices.items():
                lc.execute(
                    """INSERT OR REPLACE INTO live_price_cache
                       (symbol, ltp, change, change_pct, previous_close, fetched_at)
                       VALUES (?, ?, ?, ?, ?, datetime('now','localtime'))""",
                    (sym, p["ltp"], p["change"], p["change_pct"], p["previous_close"]),
                )
            lc.commit()
            lc.close()
        except Exception:
            pass

    if not prices:
        return {
            "status": "error",
            "message": "Could not fetch any live prices",
            "warnings": warnings,
        }

    return {
        "status": "ok",
        "prices": prices,
        "source": "yfinance",
        "warnings": warnings if warnings else None,
    }


@app.get("/api/portfolio")
async def get_portfolio():
    """Returns full portfolio data: holdings, summary, sector allocation,
    scanner overlap, alerts, risk metrics, and freshness."""
    try:
        from myra_app.portfolio_db import (
            get_all_holdings,
            get_delivery_metrics,
            get_technical_position,
            get_sector_allocation as _get_sector_allocation,
            get_scanner_overlap as _get_scanner_overlap,
            get_delivery_alerts as _get_delivery_alerts,
            get_concentration_risk as _get_concentration_risk,
            get_drawdown_metrics as _get_drawdown_metrics,
            get_diversification_score as _get_diversification_score,
            _get_portfolio_meta,
            get_db_path,
        )
    except ImportError as e:
        return {"status": "error", "message": f"portfolio_db not available: {e}"}

    try:
        holdings = get_all_holdings()
    except Exception as e:
        return {"status": "error", "message": f"Failed to read holdings: {e}"}

    if not holdings:
        return {
            "status": "empty",
            "message": "No portfolio data. Import your broker XLSX first: python tools/portfolio.py import <file>",
        }

    PORTFOLIO_DB = get_db_path()
    total_invested = 0.0
    total_current = 0.0
    total_day_pnl = 0.0
    enriched = []
    symbols = [h["symbol"] for h in holdings]

    price_map = {}
    prev_price_map = {}
    try:
        if os.path.exists(PORTFOLIO_DB):
            pc = sqlite3.connect(PORTFOLIO_DB)
            pc.row_factory = sqlite3.Row
            for row in pc.execute(
                "SELECT symbol, latest_close, previous_close, latest_date FROM price_cache"
            ).fetchall():
                price_map[row["symbol"]] = row["latest_close"]
                prev_price_map[row["symbol"]] = row["previous_close"]
            pc.close()
    except Exception:
        pass

    funda_map = {}
    try:
        if os.path.exists(PORTFOLIO_DB):
            fc = sqlite3.connect(PORTFOLIO_DB)
            fc.row_factory = sqlite3.Row
            for row in fc.execute(
                "SELECT symbol, pe, sector FROM fundamental_cache"
            ).fetchall():
                funda_map[row["symbol"]] = {
                    "pe": row["pe"],
                    "sector": row["sector"],
                }
            fc.close()
    except Exception:
        pass

    val_funda_map = {}
    VAL_DB = os.path.join(DB_DIR, LibrarianCore.DB_MAP["valuation"])
    if os.path.exists(VAL_DB):
        try:
            vc = sqlite3.connect(VAL_DB)
            vc.row_factory = sqlite3.Row
            placeholders = ",".join("?" for _ in symbols)
            for row in vc.execute(
                f"""SELECT symbol, pe, operatingMargin, grossMargin,
                           freeCashFlowYield, currentRatio, quickRatio,
                           payoutRatio, beta, promoter_holding_pct,
                           sector, market_cap
                    FROM fundamentals WHERE symbol IN ({placeholders})""",
                symbols,
            ).fetchall():
                val_funda_map[row["symbol"]] = dict(row)
            vc.close()
        except Exception:
            pass

    def compute_myra_quality_score(f):
        score = 1
        if f.get("operatingMargin") and f["operatingMargin"] > 0.15:
            score += 1
        if f.get("freeCashFlowYield") and f["freeCashFlowYield"] > 0.05:
            score += 1
        if f.get("promoter_holding_pct") and f["promoter_holding_pct"] > 50:
            score += 1
        if f.get("pe") and 0 < f["pe"] < 20:
            score += 1
        if f.get("currentRatio") and f["currentRatio"] > 1.5:
            score += 1
        return min(score, 5)

    FUNDA_FIELDS = [
        "operatingMargin",
        "grossMargin",
        "freeCashFlowYield",
        "currentRatio",
        "quickRatio",
        "payoutRatio",
        "beta",
        "promoter_holding_pct",
        "market_cap",
    ]

    for h in holdings:
        sym = h["symbol"]
        qty = h.get("net_qty", 0)
        avg = h.get("avg_price", 0)
        invested = qty * avg
        ltp = price_map.get(sym)
        current_value = qty * ltp if ltp else 0
        prev_close = prev_price_map.get(sym)
        day_change = (ltp - prev_close) if ltp and prev_close else 0
        day_pnl = qty * day_change
        overall_pnl = current_value - invested
        overall_pnl_pct = round((overall_pnl / invested * 100), 2) if invested else 0

        delivery = {}
        try:
            delivery = get_delivery_metrics(sym) or {}
        except Exception:
            pass

        tech_pos = {}
        try:
            tech_pos = get_technical_position(sym) or {}
        except Exception:
            pass

        funda = funda_map.get(sym, {})
        vf = val_funda_map.get(sym, {})

        morningstar_rating = compute_myra_quality_score(vf)
        morningstar_fields_available = sum(
            1 for f in FUNDA_FIELDS if vf.get(f) is not None
        )

        total_invested += invested
        total_current += current_value
        total_day_pnl += day_pnl

        enriched.append(
            {
                "symbol": sym,
                "category": h.get("category", "NSE EQ"),
                "net_qty": qty,
                "avg_price": round(avg, 2),
                "ltp": ltp,
                "current_value": round(current_value, 2),
                "current": round(current_value, 2),
                "overall_pnl": round(overall_pnl, 2),
                "overall_pnl_pct": overall_pnl_pct,
                "day_pnl": round(day_pnl, 2),
                "day_pnl_pct": (
                    round((day_pnl / (current_value - day_pnl) * 100), 2)
                    if (current_value - day_pnl)
                    else 0
                ),
                "delivery_pct": delivery.get("del_pct"),
                "delivery_trend": delivery.get("del_trend", "\u2014"),
                "vs_sma50_pct": tech_pos.get("vs_sma_pct"),
                "vs_52w_high_pct": tech_pos.get("vs_52w_high_pct"),
                "pe": funda.get("pe") or vf.get("pe"),
                "sector": funda.get("sector") or vf.get("sector") or "Other",
                "alert": None,
                "operating_margin": vf.get("operatingMargin"),
                "gross_margin": vf.get("grossMargin"),
                "free_cash_flow_yield": vf.get("freeCashFlowYield"),
                "current_ratio": vf.get("currentRatio"),
                "quick_ratio": vf.get("quickRatio"),
                "payout_ratio": vf.get("payoutRatio"),
                "promoter_holding": vf.get("promoter_holding_pct"),
                "market_cap": vf.get("market_cap"),
                "beta": vf.get("beta"),
                "morningstar_rating": morningstar_rating,
                "morningstar_fields_available": morningstar_fields_available,
            }
        )

    # Enrich with industry data from cache (yfinance)
    try:
        from myra_app.portfolio_db import get_cached_industries, refresh_industry_cache

        industry_data = get_cached_industries(symbols)
        missing = [s for s in symbols if s not in industry_data]
        if missing:
            logger.info(
                f"Fetching industry data for {len(missing)} symbols from yfinance"
            )
            fresh = refresh_industry_cache(missing)
            industry_data.update(fresh)
        for h in enriched:
            sym = h["symbol"]
            ind = industry_data.get(sym, {})
            h["industry"] = ind.get("industry")
            h["yf_sector"] = ind.get("yf_sector")
    except Exception as e:
        logger.warning(f"Industry enrichment failed: {e}")

    total_day_pnl_pct = (
        round((total_day_pnl / (total_current - total_day_pnl) * 100), 2)
        if (total_current - total_day_pnl)
        else 0
    )
    overall_pnl = total_current - total_invested
    overall_pnl_pct = (
        round((overall_pnl / total_invested * 100), 2) if total_invested else 0
    )

    summary = {
        "total_invested": round(total_invested, 2),
        "total_current": round(total_current, 2),
        "overall_pnl": round(overall_pnl, 2),
        "overall_pnl_pct": overall_pnl_pct,
        "day_pnl": round(total_day_pnl, 2),
        "day_pnl_pct": total_day_pnl_pct,
        "holdings_count": len(enriched),
        "last_refresh": _get_portfolio_meta("last_refresh") or "Not refreshed yet",
    }

    sector_allocation = []
    try:
        sector_allocation = _get_sector_allocation(enriched) or []
    except Exception:
        pass

    scanner_overlap = {}
    try:
        scanner_overlap = _get_scanner_overlap(enriched) or {}
    except Exception:
        pass

    alerts = []
    try:
        alerts = _get_delivery_alerts(enriched) or []
    except Exception:
        pass

    concentration = {}
    try:
        concentration = _get_concentration_risk() or {}
    except Exception:
        pass

    drawdown = {}
    try:
        drawdown = _get_drawdown_metrics() or {}
    except Exception:
        pass

    diversification = {}
    try:
        diversification = _get_diversification_score() or {}
    except Exception:
        pass

    risk = {
        "concentration": {
            "top3_pct": concentration.get("top3_pct", 0),
            "holdings": concentration.get("top3_holdings", []),
        },
        "drawdown": {
            "peak_value": drawdown.get("peak_value", 0),
            "peak_date": drawdown.get("peak_date", ""),
            "current_value": drawdown.get("current_value", 0),
            "drawdown_pct": drawdown.get("drawdown_pct", 0),
            "days_from_peak": drawdown.get("days_from_peak", 0),
        },
        "diversification_score": diversification.get("score", 0),
        "diversification_rating": diversification.get("rating", ""),
    }

    _prices_from = _get_portfolio_meta("prices_updated_at")
    _funds_cached = _get_portfolio_meta("funds_updated_at")
    if not _prices_from or _prices_from == "unknown":
        try:
            pc = sqlite3.connect(PORTFOLIO_DB)
            row = pc.execute("SELECT MAX(latest_date) FROM price_cache").fetchone()
            _prices_from = row[0] if row and row[0] else "unknown"
            pc.close()
        except Exception:
            _prices_from = "unknown"
    if not _funds_cached or _funds_cached == "unknown":
        try:
            fc = sqlite3.connect(PORTFOLIO_DB)
            row = fc.execute("SELECT MAX(fetched_at) FROM fundamental_cache").fetchone()
            _funds_cached = row[0] if row and row[0] else "unknown"
            fc.close()
        except Exception:
            _funds_cached = "unknown"

    freshness = {
        "prices_from": _prices_from,
        "fundamentals_cached": _funds_cached,
        "fundamentals_coverage_pct": round(
            sum(1 for h in enriched if h.get("pe")) / max(len(enriched), 1) * 100
        ),
    }

    return {
        "status": "ok",
        "summary": summary,
        "holdings": enriched,
        "sector_allocation": sector_allocation,
        "scanner_overlap": scanner_overlap,
        "alerts": alerts,
        "risk": risk,
        "freshness": freshness,
    }


@app.get("/api/portfolio/benchmark")
async def get_portfolio_benchmark():
    """Compare portfolio returns vs Nifty benchmark using snapshot history."""
    from myra_app.portfolio_db import get_snapshots, get_db_path
    import sqlite3
    import os

    # Get portfolio snapshots
    snapshots = get_snapshots(limit=250)  # ~1 year of trading days
    if len(snapshots) < 2:
        return {
            "status": "ok",
            "benchmark": {
                "portfolio_return": 0,
                "nifty_return": 0,
                "alpha": 0,
                "message": "Not enough snapshot data for comparison",
            },
        }

    # Portfolio return from first to latest snapshot
    first = snapshots[-1]
    last = snapshots[0]
    portfolio_return = (
        ((last["total_current"] - first["total_current"]) / first["total_current"])
        * 100
        if first["total_current"]
        else 0
    )

    # Nifty benchmark from myra_metadata.db (benchmarks table, symbol ^NSEI)
    nifty_return = 0
    meta_db = os.path.join(DB_DIR, "myra_metadata.db")
    if os.path.exists(meta_db):
        try:
            mc = sqlite3.connect(meta_db)
            mc.row_factory = sqlite3.Row
            first_date = first["date"]
            last_date = last["date"]
            first_close = mc.execute(
                "SELECT close FROM benchmarks WHERE symbol=? AND date <= ? ORDER BY date DESC LIMIT 1",
                ("^NSEI", first_date),
            ).fetchone()
            last_close = mc.execute(
                "SELECT close FROM benchmarks WHERE symbol=? AND date <= ? ORDER BY date DESC LIMIT 1",
                ("^NSEI", last_date),
            ).fetchone()
            fc = first_close["close"] if first_close else None
            lc = last_close["close"] if last_close else None
            if fc and lc and fc > 0:
                nifty_return = ((lc - fc) / fc) * 100
            mc.close()
        except Exception:
            pass

    alpha = portfolio_return - nifty_return
    return {
        "status": "ok",
        "benchmark": {
            "portfolio_return": round(portfolio_return, 2),
            "nifty_return": round(nifty_return, 2),
            "alpha": round(alpha, 2),
            "period": f"{first['date']} to {last['date']}",
        },
    }


@app.post("/api/portfolio/holdings")
async def add_portfolio_holding(req: Request):
    """Add a new holding or append to existing. Body: {symbol, qty, avg_price, category?}"""
    try:
        body = await req.json()
    except Exception:
        return JSONResponse(
            status_code=400, content={"status": "error", "message": "Invalid JSON body"}
        )
    symbol = body.get("symbol", "").upper().strip()
    qty = body.get("qty")
    avg_price = body.get("avg_price")
    category = body.get("category", "NSE EQ")
    if not symbol or qty is None or avg_price is None:
        return JSONResponse(
            status_code=400,
            content={
                "status": "error",
                "message": "symbol, qty, avg_price are required",
            },
        )
    try:
        qty = int(qty)
        avg_price = float(avg_price)
    except (ValueError, TypeError):
        return JSONResponse(
            status_code=400,
            content={
                "status": "error",
                "message": "qty must be int, avg_price must be number",
            },
        )
    if qty <= 0 or avg_price <= 0:
        return JSONResponse(
            status_code=400,
            content={
                "status": "error",
                "message": "qty and avg_price must be positive",
            },
        )

    from myra_app.portfolio_db import add_holding, get_holding

    existing = get_holding(symbol)
    if existing:
        old_qty = existing["net_qty"]
        old_avg = existing["avg_price"]
        new_qty = old_qty + qty
        new_avg = ((old_qty * old_avg) + (qty * avg_price)) / new_qty
        from myra_app.portfolio_db import update_holding

        update_holding(symbol, net_qty=new_qty, avg_price=round(new_avg, 2))
        return {
            "status": "ok",
            "message": f"Added {qty} to {symbol}. New qty: {new_qty}, new avg: \u20b9{new_avg:.2f}",
            "action": "updated",
            "holding": {
                "symbol": symbol,
                "net_qty": new_qty,
                "avg_price": round(new_avg, 2),
            },
        }
    else:
        add_holding(symbol, qty, avg_price, category)
        return {
            "status": "ok",
            "message": f"Added {symbol}: {qty} @ \u20b9{avg_price}",
            "action": "created",
            "holding": {
                "symbol": symbol,
                "net_qty": qty,
                "avg_price": avg_price,
            },
        }


@app.put("/api/portfolio/holdings/{symbol}")
async def update_portfolio_holding(symbol: str, req: Request):
    """Update a holding's quantity or average price."""
    try:
        body = await req.json()
    except Exception:
        return JSONResponse(
            status_code=400, content={"status": "error", "message": "Invalid JSON body"}
        )
    if not body:
        return JSONResponse(
            status_code=400,
            content={"status": "error", "message": "No fields to update"},
        )
    kwargs = {}
    if "net_qty" in body:
        try:
            kwargs["net_qty"] = int(body["net_qty"])
            if kwargs["net_qty"] <= 0:
                return JSONResponse(
                    status_code=400,
                    content={"status": "error", "message": "net_qty must be positive"},
                )
        except (ValueError, TypeError):
            return JSONResponse(
                status_code=400,
                content={"status": "error", "message": "net_qty must be an integer"},
            )
    if "avg_price" in body:
        try:
            kwargs["avg_price"] = float(body["avg_price"])
            if kwargs["avg_price"] <= 0:
                return JSONResponse(
                    status_code=400,
                    content={
                        "status": "error",
                        "message": "avg_price must be positive",
                    },
                )
        except (ValueError, TypeError):
            return JSONResponse(
                status_code=400,
                content={"status": "error", "message": "avg_price must be a number"},
            )
    if not kwargs:
        return JSONResponse(
            status_code=400,
            content={"status": "error", "message": "No valid fields to update"},
        )
    from myra_app.portfolio_db import update_holding, get_holding

    sym = symbol.upper().strip()
    existing = get_holding(sym)
    if not existing:
        return JSONResponse(
            status_code=404,
            content={"status": "error", "message": f"'{sym}' not found in portfolio"},
        )
    update_holding(sym, **kwargs)
    updated = get_holding(sym)
    return {
        "status": "ok",
        "message": f"Updated {sym}",
        "holding": dict(updated),
    }


@app.delete("/api/portfolio/holdings/{symbol}")
async def delete_portfolio_holding(symbol: str):
    """Remove a holding."""
    from myra_app.portfolio_db import delete_holding, get_holding

    sym = symbol.upper().strip()
    existing = get_holding(sym)
    if not existing:
        return JSONResponse(
            status_code=404,
            content={"status": "error", "message": f"'{sym}' not found in portfolio"},
        )
    delete_holding(sym)
    return {"status": "ok", "message": f"Removed {sym}"}


@app.get("/api/logs/recent")
async def get_recent_logs():
    """Return last 5 lines of pipeline.log or a placeholder."""
    log_path = os.path.join(os.path.dirname(DB_DIR), "pipeline.log")
    try:
        with open(log_path, "r") as f:
            lines = f.readlines()[-5:]
        return {"logs": [l.strip() for l in lines]}
    except Exception:
        return {"logs": ["No log file found. Start the pipeline to populate."]}


@app.get("/api/tools/status")
async def get_pipeline_status():
    """Return last run times of all background tasks."""
    return {
        "fundamentals": (
            _get_last_run("fundamentals_sync")
            if "_get_last_run" in globals()
            else "Never"
        ),
        "etf": _get_last_run("etf_sync") if "_get_last_run" in globals() else "Never",
        "index": (
            _get_last_run("index_sync") if "_get_last_run" in globals() else "Never"
        ),
        "ingest": (
            _get_last_run("daily_ingest") if "_get_last_run" in globals() else "Never"
        ),
        "db_doctor": (
            _get_last_run("db_doctor") if "_get_last_run" in globals() else "Never"
        ),
    }


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


@app.get("/api/ml/status")
async def ml_status():
    """Check if a trained model exists and when it was last trained."""
    import os

    model_path = "models/forward_return.xgb"
    metadata_path = "models/model_metadata.json"
    if not os.path.exists(model_path):
        return {
            "exists": False,
            "message": "No trained model found. Run /api/ml/train to train a model.",
        }
    try:
        if os.path.exists(metadata_path):
            with open(metadata_path) as f:
                meta = json.load(f)
            return {
                "exists": True,
                "trained_at": meta.get("trained_at"),
                "train_accuracy": meta.get("train_accuracy"),
                "test_accuracy": meta.get("test_accuracy"),
            }
        return {"exists": True, "message": "Model exists but metadata not found."}
    except Exception as e:
        return {"exists": False, "error": str(e)}


@app.post("/api/ml/train")
async def ml_train(config: dict = None):
    """Train a new model (async). Optionally pass a config dict to override defaults."""

    def _run():
        from myra_app.ml_trainer import MLTrainer

        trainer = MLTrainer(config)
        return trainer.train()

    try:
        tid = _spawn_task("ml_train", _run)
        return JSONResponse(
            status_code=202, content={"status": "started", "task_id": tid}
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/ml/predict")
async def ml_predict():
    """Return today's predictions for all symbols."""

    def _run():
        from myra_app.ml_trainer import MLTrainer

        trainer = MLTrainer()
        return trainer.predict_today()

    return await asyncio.to_thread(_run)


@app.get("/api/ml/feature-importance")
async def ml_feature_importance():
    """Return feature importance from the latest model."""
    from myra_app.ml_trainer import MLTrainer

    trainer = MLTrainer()
    return trainer.get_feature_importance()


@app.post("/api/ml/config")
async def ml_update_config(config: dict):
    """Update ML config and save to models/ml_config.json. Merges with existing config."""
    import json, os
    from myra_app.ml_trainer import DEFAULT_CONFIG

    os.makedirs("models", exist_ok=True)

    existing_config = DEFAULT_CONFIG.copy()
    if os.path.exists("models/ml_config.json"):
        try:
            with open("models/ml_config.json") as f:
                existing_config.update(json.load(f))
        except Exception:
            pass

    for key, value in config.items():
        if (
            isinstance(value, dict)
            and key in existing_config
            and isinstance(existing_config[key], dict)
        ):
            existing_config[key].update(value)
        else:
            existing_config[key] = value

    with open("models/ml_config.json", "w") as f:
        json.dump(existing_config, f, indent=2)

    return {"status": "ok", "config": existing_config}


@app.get("/api/ml/config")
async def ml_get_config():
    """Get current ML configuration."""
    import json, os

    if os.path.exists("models/ml_config.json"):
        with open("models/ml_config.json") as f:
            return json.load(f)
    return {"status": "defaults"}


@app.post("/api/ml/launchpad/label")
async def label_launchpad_events(config: dict = None):
    """Run launchpad event labelling. Optionally pass config to override defaults."""
    from myra_app.launchpad_labels import LaunchpadLabeler

    labeler = LaunchpadLabeler(config)
    result = labeler.run()
    return result


@app.post("/api/ml/launchpad/train")
async def train_launchpad(config: dict = None):
    """Train the launchpad prediction model. Optionally pass config."""
    from myra_app.ml_trainer import LaunchpadPredictor

    predictor = LaunchpadPredictor(config)
    result = predictor.train()
    return result


@app.get("/api/ml/launchpad/predict")
async def predict_launchpad():
    """Get current launchpad predictions for stocks in digestion phase."""
    import os

    model_path = "models/launchpad_xgb.joblib"
    if not os.path.exists(model_path):
        return {
            "predictions": [],
            "status": "no_model",
            "message": "Launchpad model not trained yet.",
        }
    try:
        import sqlite3
        import pandas as pd
        import numpy as np
        import joblib
        from myra_app.librarian_core import LibrarianCore

        tech_db = os.path.join(DB_DIR, LibrarianCore.DB_MAP["technical"])
        val_db = os.path.join(DB_DIR, LibrarianCore.DB_MAP["valuation"])

        with sqlite3.connect(tech_db) as conn:
            events = conn.execute(
                "SELECT symbol, trigger_date FROM launchpad_events WHERE success = 0 AND trigger_date >= date('now', '-180 days') ORDER BY trigger_date DESC"
            ).fetchall()

        if not events:
            return {
                "predictions": [],
                "status": "no_events",
                "message": "No stocks in digestion phase.",
            }

        model = joblib.load(model_path)
        results = []
        for sym, trig in events[:20]:  # Limit to 20 for performance
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
                    del_zscores = [(d - del_mean) / (del_std + 1e-9) for d in del_vals]
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
                if os.path.exists(val_db):
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
                        "predicted_days_to_breakout": round(float(preds[0, 1]), 1),
                        "current_digestion_days": len(row),
                        "sector": sector,
                        "market_cap": mcap,
                        "breakout_probability": breakout_probability,
                        "confidence": confidence,
                    }
                )
            except Exception:
                continue

        return {"predictions": results, "status": "ok"}
    except Exception as e:
        return {"predictions": [], "status": "error", "message": str(e)}


@app.get("/api/ml/launchpad/status")
async def launchpad_status():
    """Check if a trained launchpad model exists."""
    if os.path.exists("models/launchpad_metadata.json"):
        with open("models/launchpad_metadata.json") as f:
            return json.load(f)
    return {"exists": False}


@app.get("/api/ml/launchpad/feature-importance")
async def launchpad_feature_importance():
    """Get feature importance from the launchpad model."""
    from myra_app.ml_trainer import LaunchpadPredictor

    predictor = LaunchpadPredictor()
    return predictor.get_feature_importance()


@app.get("/api/ml/factor-importance")
async def factor_importance():
    from myra_app.ml_trainer import FactorDiscovery

    fd = FactorDiscovery()
    result = fd.discover_factors()
    return result


@app.get("/api/search/symbols")
async def search_symbols(q: str = Query(..., min_length=1)):
    from myra_app.librarian import Librarian

    lib = Librarian(read_only=True)
    return lib.search_symbols(q)


def _validate_finstack(result: dict) -> dict:
    if "error" in result:
        raise HTTPException(status_code=500, detail=result["error"])
    if "_raw" in result:
        raise HTTPException(
            status_code=502, detail="FinStack MCP returned non-JSON response"
        )
    return result


@app.get("/api/finstack/nifty-outlook")
async def finstack_nifty_outlook():
    cache_key = "nifty_outlook"
    now = time.time()
    if (
        cache_key in _finstack_cache
        and (now - _finstack_cache[cache_key]["ts"]) < CACHE_TTL
    ):
        return _finstack_cache[cache_key]["data"]
    from myra_app.utils.finstack_bridge import get_nifty_outlook

    try:
        data = await get_nifty_outlook()
        _finstack_cache[cache_key] = {"ts": now, "data": data}
        return data
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/finstack/fii-retail-divergence")
async def finstack_fii_retail_divergence(symbol: str = "RELIANCE"):
    cache_key = f"fii_divergence:{symbol}"
    now = time.time()
    if (
        cache_key in _finstack_cache
        and (now - _finstack_cache[cache_key]["ts"]) < CACHE_TTL
    ):
        return _finstack_cache[cache_key]["data"]
    from myra_app.utils.finstack_bridge import get_fii_retail_divergence

    try:
        data = await get_fii_retail_divergence(symbol)
        _finstack_cache[cache_key] = {"ts": now, "data": data}
        return data
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# @app.get("/api/finstack/sebi-alerts")
# async def finstack_sebi_alerts():
#     from myra_app.utils.finstack_bridge import get_sebi_alerts
#     result = await get_sebi_alerts()
#     return _validate_finstack(result)


@app.get("/api/finstack/morning-brief")
async def finstack_morning_brief():
    cache_key = "morning_brief"
    now = time.time()
    if (
        cache_key in _finstack_cache
        and (now - _finstack_cache[cache_key]["ts"]) < CACHE_TTL
    ):
        return _finstack_cache[cache_key]["data"]
    from myra_app.utils.finstack_bridge import get_morning_brief

    try:
        data = await get_morning_brief()
        _finstack_cache[cache_key] = {"ts": now, "data": data}
        return data
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# @app.get("/api/finstack/scan-pledge-risks")
# async def finstack_scan_pledge_risks():
#     from myra_app.utils.finstack_bridge import scan_pledge_risks
#     result = await scan_pledge_risks()
#     return _validate_finstack(result)


# ── Missing routes wired up ─────────────────────────────────────────────


@app.get("/api/finstack/stock-brief/{symbol}")
async def finstack_stock_brief(symbol: str):
    from myra_app.utils.finstack_bridge import get_stock_brief

    result = await get_stock_brief(symbol)
    return _validate_finstack(result)


@app.get("/api/finstack/stock-brief")
async def stock_brief(
    symbol: str = Query(..., description="Stock symbol, e.g., RELIANCE")
):
    cache_key = f"stock_brief:{symbol}"
    now = time.time()
    if (
        cache_key in _finstack_cache
        and (now - _finstack_cache[cache_key]["ts"]) < CACHE_TTL
    ):
        return _finstack_cache[cache_key]["data"]
    from myra_app.utils.finstack_bridge import get_stock_brief

    try:
        data = await get_stock_brief(symbol=symbol.upper())
        _finstack_cache[cache_key] = {"ts": now, "data": data}
        return data
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/finstack/social-sentiment/{symbol}")
async def finstack_social_sentiment(symbol: str):
    from myra_app.utils.finstack_bridge import get_social_sentiment

    result = await get_social_sentiment(symbol)
    return _validate_finstack(result)


@app.get("/api/finstack/pledge-alert/{symbol}")
async def finstack_pledge_alert(symbol: str):
    from myra_app.utils.finstack_bridge import get_pledge_alert

    result = await get_pledge_alert(symbol)
    return _validate_finstack(result)


@app.get("/api/finstack/unusual-activity")
async def unusual_activity(
    symbol: str = Query(..., description="Stock symbol, e.g., RELIANCE")
):
    cache_key = f"unusual_activity:{symbol}"
    now = time.time()
    if (
        cache_key in _finstack_cache
        and (now - _finstack_cache[cache_key]["ts"]) < CACHE_TTL
    ):
        return _finstack_cache[cache_key]["data"]
    from myra_app.utils.finstack_bridge import detect_unusual_activity

    try:
        data = await detect_unusual_activity(symbol=symbol.upper())
        _finstack_cache[cache_key] = {"ts": now, "data": data}
        return data
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/finstack/stock-timeline")
async def finstack_stock_timeline(symbol: str = ""):
    if not symbol:
        raise HTTPException(
            status_code=400, detail="query parameter 'symbol' is required"
        )
    cache_key = f"stock_timeline:{symbol}"
    now = time.time()
    if (
        cache_key in _finstack_cache
        and (now - _finstack_cache[cache_key]["ts"]) < CACHE_TTL
    ):
        return _finstack_cache[cache_key]["data"]
    from myra_app.utils.finstack_bridge import get_stock_timeline

    try:
        data = await get_stock_timeline(symbol)
        _finstack_cache[cache_key] = {"ts": now, "data": data}
        return data
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def _get_latest_trading_day_before(date_str: str) -> str:
    """Find the most recent trading day on or before date_str by querying technical_data."""
    from datetime import datetime, timedelta

    target = datetime.strptime(date_str, "%Y-%m-%d")
    tech_db = os.path.join(DB_DIR, LibrarianCore.DB_MAP["technical"])
    conn = sqlite3.connect(tech_db)
    for offset in range(10):
        check = (target - timedelta(days=offset)).strftime("%Y-%m-%d")
        row = conn.execute(
            "SELECT COUNT(*) FROM technical_data WHERE date = ?", (check,)
        ).fetchone()
        if row and row[0] > 0:
            conn.close()
            return check
    conn.close()
    return date_str


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


@app.get("/api/latest-trading-day")
async def latest_trading_day():
    """Return today's date adjusted to the most recent available trading day."""
    today = datetime.now().strftime("%Y-%m-%d")
    return {"date": _get_latest_trading_day_before(today)}


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

# ---- tier rank helper (module-level for testability) ----
_TIER_RANK_MAP = {"HIGH": 0, "MOD": 1, "LOW": 2}


def _apply_tier_rank(candidates: list[dict]) -> list[dict]:
    """Add numeric ``tier_rank`` (0=HIGH, 1=MOD, 2=LOW) to every candidate dict."""
    for c in candidates:
        if "tier_rank" not in c:
            c["tier_rank"] = _TIER_RANK_MAP.get(c.get("tier"), 2)
    return candidates


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


@app.post("/api/portfolio/refresh-industry")
async def refresh_portfolio_industry():
    """Force refresh industry data for all holdings from yfinance."""
    try:
        from myra_app.portfolio_db import get_all_holdings, refresh_industry_cache

        holdings = get_all_holdings()
        symbols = [h["symbol"] for h in holdings]
        results = refresh_industry_cache(symbols)
        count = sum(1 for r in results.values() if r.get("industry"))
        return {
            "status": "ok",
            "message": f"Refreshed industry data for {count}/{len(symbols)} symbols",
            "count": count,
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


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

# Map cache filenames to friendly display names
_SCANNER_CACHE_MAP: dict[str, str] = {
    "trigger_cache.json": "The Trigger",
    "bottom_hunter_cache.json": "Bottom Hunter",
    "invisible_hand_cache.json": "Invisible Hand",
    "wyckoff_cache.json": "Wyckoff Automaton",
    "liquidity_flip_cache.json": "Liquidity Flip",
    "operator_fingerprint_cache.json": "Operator Fingerprint",
    "float_exhaustion_cache.json": "Float Exhaustion",
    "seasonal_delivery_cache.json": "Seasonal Delivery",
    "darvas_cache.json": "Darvas Box Pro",
    "multibagger_cache.json": "Multibagger Pro",
    "climax_accumulation_cache.json": "Climax Accumulation",
    "launchpad_scan_cache.json": "Launchpad Scanner",
}

# Display-name → frontend route (for link column)
_SCANNER_ROUTES: dict[str, str] = {
    "The Trigger": "/trigger",
    "Bottom Hunter": "/bottom-hunter",
    "Invisible Hand": "/invisible-hand",
    "Wyckoff Automaton": "/wyckoff",
    "Liquidity Flip": "/liquidity-flip",
    "Operator Fingerprint": "/operator-fingerprint",
    "Float Exhaustion": "/float-exhaustion",
    "Seasonal Delivery": "/seasonal-delivery",
    "Darvas Box Pro": "/darvas-box-pro",
    "Multibagger Pro": "/multibagger-pro-scanner",
    "Climax Accumulation": "/climax-accumulation",
    "Launchpad Scanner": "/launchpad-scanner",
}

_GRADE_RANK: dict[str, float] = {
    "A+": 4.5,
    "A": 4,
    "B": 3,
    "C": 2,
    "D": 1,
}
_TIER_RANK: dict[str, float] = {
    "HIGH": 3.5,
    "MID": 2.5,
    "LOW": 1.5,
}


def _grade_rank(value) -> float:
    """Convert a grade/tier/score value to a numeric rank (higher = better)."""
    if value is None:
        return -1
    if isinstance(value, (int, float)):
        return float(value) / 100 * 4  # normalise 0-100 to 0-4 scale
    s = str(value).strip()
    return _GRADE_RANK.get(s.upper(), _TIER_RANK.get(s.upper(), 0))


def _best_grade(candidates: list[dict]) -> str | None:
    """Return the best grade string from a list of candidate dicts."""
    best_rank: float = -1
    best_str: str | None = None

    for c in candidates:
        for key in ("grade", "score", "tier"):
            if key in c and c[key] is not None:
                rank = _grade_rank(c[key])
                if rank > best_rank:
                    best_rank = rank
                    best_str = str(c[key])
    return best_str


def build_confluence_report() -> dict:
    """Aggregate all scanner cache files into a confluence report.

    Only symbols flagged by 2+ distinct scanners are included.
    """
    from datetime import timezone, timedelta

    IST = timezone(timedelta(hours=5, minutes=30))

    # Collect all cache files that match our known names
    cache_files: dict[str, str] = {}  # display_name → filepath
    try:
        for fname in os.listdir(MODELS_DIR):
            if fname not in _SCANNER_CACHE_MAP:
                continue
            display = _SCANNER_CACHE_MAP[fname]
            # Handle darvas_scan_cache.json vs darvas_cache.json — prefer the
            # one with more candidates; if both exist we'll resolve below.
            fpath = os.path.join(MODELS_DIR, fname)
            if display in cache_files:
                # Already have one for this display name — keep the one with
                # more candidates (lazy: replace if new file is larger).
                try:
                    with open(cache_files[display], encoding="utf-8") as f:
                        existing = json.load(f)
                    with open(fpath, encoding="utf-8") as f:
                        new_data = json.load(f)
                    if len(new_data.get("candidates", [])) > len(
                        existing.get("candidates", [])
                    ):
                        cache_files[display] = fpath
                except Exception:
                    pass
            else:
                cache_files[display] = fpath
    except Exception:
        pass

    if len(cache_files) < 2:
        return {"generated_at": datetime.now(IST).isoformat(), "symbols": []}

    # --- Aggregate per-symbol data -------------------------------------------
    # symbol → { sector, scanners: { display_name: candidate }, last_scan str }
    agg: dict[str, dict] = {}

    for display_name, fpath in cache_files.items():
        try:
            with open(fpath, encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception:
            continue  # graceful degradation

        last_scan = data.get("last_scan")
        for cand in data.get("candidates", []):
            sym = cand.get("symbol")
            if not sym:
                continue
            if sym not in agg:
                agg[sym] = {
                    "sector": cand.get("sector", ""),
                    "scanners": {},
                    "last_scan": last_scan,
                }
            agg[sym]["scanners"][display_name] = cand
            # Track the latest scan timestamp across all scanners
            if last_scan and (
                agg[sym]["last_scan"] is None or last_scan > agg[sym]["last_scan"]
            ):
                agg[sym]["last_scan"] = last_scan
            # Update sector if the new candidate has a value
            if cand.get("sector") and not agg[sym]["sector"]:
                agg[sym]["sector"] = cand["sector"]

    # --- Filter to 2+ scanners and build output -----------------------------
    symbols_out: list[dict] = []
    for sym, info in agg.items():
        scanner_names = sorted(info["scanners"].keys())
        if len(scanner_names) < 2:
            continue
        cand_list = [info["scanners"][n] for n in scanner_names]
        symbols_out.append(
            {
                "symbol": sym,
                "sector": info["sector"],
                "scanner_count": len(scanner_names),
                "scanners": scanner_names,
                "last_scan": info["last_scan"],
                "best_grade": _best_grade(cand_list),
            }
        )

    # Sort: scanner_count desc, then symbol asc
    symbols_out.sort(key=lambda x: (-x["scanner_count"], x["symbol"]))

    return {
        "generated_at": datetime.now(IST).isoformat(),
        "symbols": symbols_out,
    }


@app.get("/api/confluence")
async def confluence_endpoint():
    """Return an aggregated view of symbols flagged by 2+ scanners."""
    try:
        return build_confluence_report()
    except Exception as e:
        logger.error("Confluence report failed: %s", e, exc_info=True)
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.get("/api/sentiment/{ticker}")
async def get_news_sentiment(ticker: str, refresh: bool = False):
    """Get news headlines with FinBERT sentiment for a given NSE ticker.
    Results are cached for 6 hours. Use ?refresh=true to force fresh fetch."""
    try:
        from myra_app.news_sentiment import get_ticker_news

        news = get_ticker_news(ticker, refresh=refresh)
        return {
            "ticker": ticker.upper(),
            "count": len(news),
            "news": news,
            "cached": not refresh,
            "status": "success",
        }
    except Exception as e:
        return {
            "ticker": ticker.upper(),
            "error": str(e),
            "news": [],
            "status": "error",
        }


@app.get("/api/ai-opinion/{ticker}")
async def get_ai_opinion(ticker: str):
    """On-demand Gemini LLM second opinion for a stock.

    Returns a BUY/SELL/HOLD signal with rationale, confidence, and the
    technical summary the model evaluated.  Results are cached for 24 h
    by the underlying module (rate-limit-safe, no per-candidate loops).
    """
    try:
        from myra_app.ai_second_opinion import (
            build_technical_summary,
            get_ai_second_opinion,
        )

        summary = build_technical_summary(ticker.upper())
        opinion = get_ai_second_opinion(ticker.upper(), summary)
        return {
            "ticker": ticker.upper(),
            "signal": opinion["signal"],
            "reason": opinion["reason"],
            "confidence": opinion["confidence"],
            "source": opinion["source"],
            "cached": opinion["cached"],
            "summary": summary,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/chart/{symbol}")
async def get_chart(symbol: str, limit: int = 500):
    """Return OHLCV data for a symbol, ordered ascending by date."""
    db_path = get_db_path("technical")
    if not db_path or not os.path.exists(db_path):
        raise HTTPException(status_code=500, detail="Technical database not found")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT date, open, high, low, close, volume "
            "FROM technical_data WHERE symbol = ? ORDER BY date DESC LIMIT ?",
            (symbol.upper(), limit),
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        raise HTTPException(status_code=404, detail="Symbol not found")

    # Reverse to ascending date order
    data = [dict(r) for r in reversed(rows)]
    return {"symbol": symbol.upper(), "data": data}
