import json
import logging
import os
import sqlite3
import threading
import subprocess
import time
import math
from datetime import datetime
from fastapi import FastAPI, HTTPException, Query, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import List, Optional
from myra_app.constants import DB_DIR
from myra_app.librarian_core import LibrarianCore

logger = logging.getLogger(__name__)

import sys as _sys, os as _os; _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from pipeline_dashboard import router as pipeline_router

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

_active_queries_file = os.path.join(DB_DIR, ".active_queries")
_queries_lock = threading.Lock()


def _inc_active_queries():
    with _queries_lock:
        try:
            os.makedirs(os.path.dirname(_active_queries_file), exist_ok=True)
            count = 0
            if os.path.exists(_active_queries_file):
                with open(_active_queries_file) as f:
                    count = int(f.read().strip() or "0")
            with open(_active_queries_file, 'w') as f:
                f.write(str(count + 1))
        except Exception:
            pass


def _dec_active_queries():
    with _queries_lock:
        try:
            if not os.path.exists(_active_queries_file):
                return
            with open(_active_queries_file) as f:
                count = int(f.read().strip() or "0")
            count -= 1
            if count <= 0:
                os.remove(_active_queries_file)
            else:
                with open(_active_queries_file, 'w') as f:
                    f.write(str(count))
        except Exception:
            pass


_finstack_cache = {}
CACHE_TTL = 300  # 5 minutes

# ---------------------------------------------------------------------------
# Launchpad scan state (background scan tracking)
# ---------------------------------------------------------------------------
import threading as _launchpad_threading
from datetime import datetime as _launchpad_dt

_launchpad_scan_state = {
    "scan_status": "idle",
    "last_scan": None,
    "progress": 0,
    "message": "",
    "predictions": [],
}
_launchpad_scan_lock = _launchpad_threading.Lock()
_LAUNCHPAD_SCAN_CACHE = "models/launchpad_scan_cache.json"


def _save_scan_cache():
    import json as _json
    import os as _os
    try:
        _os.makedirs("models", exist_ok=True)
        with _launchpad_scan_lock:
            data = {
                "last_scan": _launchpad_scan_state["last_scan"],
                "predictions": _launchpad_scan_state["predictions"],
                "message": _launchpad_scan_state["message"],
            }
        with open(_LAUNCHPAD_SCAN_CACHE, "w") as _f:
            _json.dump(data, _f)
    except Exception:
        pass


def _load_scan_cache() -> dict | None:
    import json as _json
    import os as _os
    try:
        if _os.path.exists(_LAUNCHPAD_SCAN_CACHE):
            with open(_LAUNCHPAD_SCAN_CACHE) as _f:
                return _json.load(_f)
    except Exception:
        pass
    return None

app = FastAPI(title="MYRA v3.2 API Bridge")

# Allow the React frontend to communicate with this local API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allows all origins for development
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(pipeline_router)

from pipeline_dashboard import manager as pipeline_manager

@app.on_event("shutdown")
async def shutdown_event():
    pipeline_manager.signal_shutdown()

@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    return JSONResponse(status_code=500, content={"detail": f"Internal server error: {exc}"})


# Use the expected folder structure: Myra\myra_web (this project) side-by-side with Myra\myra_app
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_DIR = os.path.abspath(os.path.join(BASE_DIR, "..", "myra_app", "db"))


def get_db_path(db_key: str):
    """Safely construct the path to a specific SQLite sidecar."""
    filename = LibrarianCore.DB_MAP.get(db_key)
    if not filename:
        return None
    return os.path.join(DB_DIR, filename)


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
                "shares_outstanding": conn.execute("SELECT COUNT(*) FROM fundamentals WHERE shares_outstanding IS NOT NULL").fetchone()[0],
                "insider_holding_pct": conn.execute("SELECT COUNT(*) FROM fundamentals WHERE insider_holding_pct IS NOT NULL").fetchone()[0],
                "promoter_holding_pct": conn.execute("SELECT COUNT(*) FROM fundamentals WHERE promoter_holding_pct IS NOT NULL").fetchone()[0],
                "industry": conn.execute("SELECT COUNT(*) FROM fundamentals WHERE industry IS NOT NULL").fetchone()[0],
                "free_float_pct": conn.execute("SELECT COUNT(*) FROM fundamentals WHERE free_float_pct IS NOT NULL").fetchone()[0],
                "market_cap": conn.execute("SELECT COUNT(*) FROM fundamentals WHERE market_cap IS NOT NULL").fetchone()[0],
                "pe": conn.execute("SELECT COUNT(*) FROM fundamentals WHERE pe IS NOT NULL").fetchone()[0],
            }
            # Check if shares_outstanding data is stale
            shares_stale = False
            try:
                import datetime as _dt
                threshold = (_dt.date.today() - _dt.timedelta(days=90)).isoformat()
                max_date = conn.execute(
                    "SELECT MAX(last_fundamental_update) FROM fundamentals WHERE shares_outstanding > 0"
                ).fetchone()[0]
                if max_date and max_date < threshold:
                    shares_stale = True
            except Exception:
                pass
            coverage["shares_stale"] = shares_stale
            conn.close()
        except Exception:
            coverage = {"error": "query failed"}
    return {"health": health, "coverage": coverage}


class QueryRequest(BaseModel):
    db: str
    query: str
    params: list = []


@app.post("/api/query")
async def execute_query(req: QueryRequest):
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

    _inc_active_queries()
    try:
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA busy_timeout = 30000")
        conn.execute("PRAGMA cache_size = -32000")
        conn.execute("PRAGMA temp_store = MEMORY")
        if req.db == '_tech_conn':
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_tech_symbol_date
                ON technical_data(symbol, date DESC)
            """)
        conn.row_factory = sqlite3.Row
        cursor = conn.execute(req.query, req.params)

        try:
            rows = [dict(row) for row in cursor.fetchall()]
        except Exception:
            rows = []

        if (
            not req.query.lstrip()
            .upper()
            .startswith(("SELECT", "PRAGMA", "WITH", "EXPLAIN"))
        ):
            conn.commit()

        rowcount = cursor.rowcount
        conn.close()

        return {"data": rows, "rows_affected": rowcount}
    except sqlite3.OperationalError as e:
        if "locked" in str(e) or "busy" in str(e):
            raise HTTPException(status_code=503, detail="Database busy – please retry in a moment")
        raise HTTPException(status_code=400, detail=str(e))
    except sqlite3.Error as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        _dec_active_queries()


class ToolRequest(BaseModel):
    tool_id: str


@app.post("/api/tools/execute")
def execute_tool(req: ToolRequest):
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
    """Trigger a full fundamentals sync (Morningstar + NSE) NOW."""
    try:
        _task_fundamentals_sync()
        return {"success": True, "message": "Fundamentals sync completed"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/tools/sync/etf")
async def force_etf_sync():
    """Trigger ETF blocklist sync NOW."""
    try:
        _task_etf_sync()
        return {"success": True, "message": "ETF sync completed"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/tools/sync/index")
async def force_index_sync():
    """Trigger NIFTY index constituents sync NOW."""
    try:
        _task_index_sync()
        return {"success": True, "message": "Index sync completed"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/tools/ingest")
async def force_daily_ingest():
    """Trigger daily bhavcopy ingest NOW."""
    try:
        _task_daily_ingest(force=True)
        return {"success": True, "message": "Daily ingest completed"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


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
        "fundamentals": _get_last_run("fundamentals_sync")
        if "_get_last_run" in globals()
        else "Never",
        "etf": _get_last_run("etf_sync") if "_get_last_run" in globals() else "Never",
        "index": _get_last_run("index_sync")
        if "_get_last_run" in globals()
        else "Never",
        "ingest": _get_last_run("daily_ingest")
        if "_get_last_run" in globals()
        else "Never",
        "db_doctor": _get_last_run("db_doctor")
        if "_get_last_run" in globals()
        else "Never",
    }


@app.get("/api/fundamentals/live/{symbol}")
async def get_live_fundamentals(symbol: str):
    import json, subprocess, sqlite3, os
    from myra_app.constants import DB_DIR
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
                "pe": funda.get("peRatio") or funda.get("pe"),
                "pb": funda.get("priceToBook"),
                "ps": funda.get("priceToSales"),
                "roe": funda.get("returnOnEquity") or funda.get("roe"),
                "eps": funda.get("earningsPerShare") or funda.get("eps"),
                "book_value": funda.get("bookValuePerShare") or funda.get("book_value"),
                "market_cap": funda.get("marketCap") or funda.get("market_cap"),
                "net_margin": funda.get("netMargin") or funda.get("net_margin"),
                "operating_margin": funda.get("operatingMargin"),
                "gross_margin": funda.get("grossMargin"),
                "debt_equity": funda.get("debtToEquity") or funda.get("debt_to_equity"),
                "current_ratio": funda.get("currentRatio"),
                "quick_ratio": funda.get("quickRatio"),
                "dividend_yield": funda.get("dividendYield")
                or funda.get("dividend_yield"),
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
                    "period_end": sh.get("headers", [None])[-1]
                    if sh.get("headers")
                    else None,
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
                "train_samples": meta.get("train_samples"),
                "test_samples": meta.get("test_samples"),
            }
        return {"exists": True, "message": "Model exists but metadata not found."}
    except Exception as e:
        return {"exists": False, "error": str(e)}


@app.post("/api/ml/train")
async def ml_train(config: dict = None):
    """Train a new model. Optionally pass a config dict to override defaults."""
    from myra_app.ml_trainer import MLTrainer

    trainer = MLTrainer(config)
    result = trainer.train()
    return result


@app.get("/api/ml/predict")
async def ml_predict():
    """Return today's predictions for all symbols."""
    from myra_app.ml_trainer import MLTrainer

    trainer = MLTrainer()
    return trainer.predict_today()


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
                            "SELECT COALESCE(marketCap, market_cap), sector FROM fundamentals WHERE symbol = ? LIMIT 1",
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


@app.post("/api/launchpad/scan")
async def launchpad_scan():
    """Run launchpad scan in the background and return immediately."""
    with _launchpad_scan_lock:
        if _launchpad_scan_state["scan_status"] == "scanning":
            return {"status": "already_scanning", "message": "Scan already in progress"}
        _launchpad_scan_state["scan_status"] = "scanning"
        _launchpad_scan_state["progress"] = 0
        _launchpad_scan_state["message"] = "Scanning..."
        _launchpad_scan_state["predictions"] = []

    def _run():
        import sqlite3
        import numpy as np
        import pandas as pd
        import joblib
        import os as _os
        from myra_app.librarian_core import LibrarianCore
        from myra_app.constants import DB_DIR as _DB_DIR

        try:
            model_path = "models/launchpad_xgb.joblib"
            if not _os.path.exists(model_path):
                with _launchpad_scan_lock:
                    _launchpad_scan_state["scan_status"] = "no_model"
                    _launchpad_scan_state["message"] = "Launchpad model not trained yet."
                    _launchpad_scan_state["progress"] = 0
                return

            with _launchpad_scan_lock:
                _launchpad_scan_state["progress"] = 10
                _launchpad_scan_state["message"] = "Loading data..."

            tech_db = _os.path.join(_DB_DIR, LibrarianCore.DB_MAP["technical"])
            val_db = _os.path.join(_DB_DIR, LibrarianCore.DB_MAP["valuation"])

            with sqlite3.connect(tech_db) as conn:
                events = conn.execute(
                    "SELECT symbol, trigger_date FROM launchpad_events WHERE success = 0 AND trigger_date >= date('now', '-180 days') ORDER BY trigger_date DESC"
                ).fetchall()

            if not events:
                with _launchpad_scan_lock:
                    _launchpad_scan_state["scan_status"] = "no_events"
                    _launchpad_scan_state["message"] = "No stocks in digestion phase."
                    _launchpad_scan_state["progress"] = 100
                    _launchpad_scan_state["predictions"] = []
                    _launchpad_scan_state["last_scan"] = _launchpad_dt.now().isoformat()
                _save_scan_cache()
                return

            with _launchpad_scan_lock:
                _launchpad_scan_state["progress"] = 20
                _launchpad_scan_state["message"] = f"Scanning {min(len(events), 20)} events..."

            model = joblib.load(model_path)
            results = []
            total = min(len(events), 20)
            for idx, (sym, trig) in enumerate(events[:20]):
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

                    sector = None
                    mcap = None
                    if _os.path.exists(val_db):
                        with sqlite3.connect(val_db) as vconn:
                            vrow = vconn.execute(
                                "SELECT COALESCE(marketCap, market_cap), sector FROM fundamentals WHERE symbol = ? LIMIT 1",
                                (sym,),
                            ).fetchone()
                            if vrow:
                                mcap = float(vrow[0]) if vrow[0] else None
                                sector = vrow[1]

                    results.append({
                        "symbol": sym,
                        "trigger_date": trig,
                        "predicted_return_pct": predicted_return_pct,
                        "predicted_days_to_breakout": round(float(preds[0, 1]), 1),
                        "current_digestion_days": len(row),
                        "sector": sector,
                        "market_cap": mcap,
                        "breakout_probability": breakout_probability,
                    })
                except Exception:
                    continue
                finally:
                    with _launchpad_scan_lock:
                        _launchpad_scan_state["progress"] = min(
                            20 + int((idx + 1) / total * 75), 95
                        )

            with _launchpad_scan_lock:
                _launchpad_scan_state["scan_status"] = "completed"
                _launchpad_scan_state["progress"] = 100
                _launchpad_scan_state["last_scan"] = _launchpad_dt.now().isoformat()
                _launchpad_scan_state["message"] = f"Found {len(results)} setups."
                _launchpad_scan_state["predictions"] = results
            _save_scan_cache()

        except Exception as e:
            with _launchpad_scan_lock:
                _launchpad_scan_state["scan_status"] = "error"
                _launchpad_scan_state["message"] = str(e)
                _launchpad_scan_state["progress"] = 0

    t = _launchpad_threading.Thread(target=_run, daemon=True)
    t.start()
    return {"status": "started", "message": "Scan started in background"}


@app.get("/api/launchpad/status")
async def launchpad_scan_status():
    """Return current scan status including predictions when available.
    Falls back to cached results on disk when no scan is running in this session."""
    import copy
    with _launchpad_scan_lock:
        state = copy.deepcopy(_launchpad_scan_state)

    # If idle (no scan in this session), try loading cached results from disk
    if state["scan_status"] == "idle":
        cache = _load_scan_cache()
        if cache and cache.get("predictions") is not None:
            return {
                "scan_status": "idle",
                "last_scan": cache.get("last_scan"),
                "progress": 100,
                "message": cache.get("message", f"Found {len(cache['predictions'])} setups."),
                "predictions": cache["predictions"],
            }

    state.pop("predictions", None)
    result = {
        **state,
        "predictions": list(_launchpad_scan_state["predictions"]),
    }
    return result


@app.get("/api/ml/launchpad/feature-importance")
async def launchpad_feature_importance():
    """Get feature importance from the launchpad model."""
    from myra_app.ml_trainer import LaunchpadPredictor

    predictor = LaunchpadPredictor()
    return predictor.get_feature_importance()


# --- Multibagger Pro Scanner State ---
_mb_scan_state: dict = {
    "scan_status": "idle",
    "last_scan": None,
    "progress": 0,
    "message": "Idle — click Scan to start",
    "candidates": [],
    "bear_market": False,
}
_mb_scan_lock = threading.Lock()


@app.get("/api/multibagger/status")
async def multibagger_status():
    return _mb_scan_state


@app.post("/api/multibagger/scan")
async def multibagger_scan(payload: dict = Body(default={})):
    with _mb_scan_lock:
        if _mb_scan_state["scan_status"] == "scanning":
            return {"detail": "Scan already in progress"}, 409

        _mb_scan_state.update({
            "scan_status": "scanning",
            "progress": 0,
            "message": "Initialising scanner...",
            "candidates": [],
        })

    base_days = int(payload.get("base_days", 21))
    min_dar = float(payload.get("min_dar", 0.2))
    target_dar = payload.get("target_dar")
    if target_dar is not None:
        target_dar = float(target_dar)
    tightness_full = payload.get("tightness_full_score_pct")
    if tightness_full is not None:
        tightness_full = float(tightness_full)
    tightness_zero = payload.get("tightness_zero_score_pct")
    if tightness_zero is not None:
        tightness_zero = float(tightness_zero)

    def _run():
        try:
            from myra_app.strategies.accumulation_base_scanner import AccumulationBaseScanner
            import math as _math

            scanner = AccumulationBaseScanner(
                base_days=base_days,
                min_dar=min_dar,
                target_dar=target_dar,
                tightness_full_score_pct=tightness_full,
                tightness_zero_score_pct=tightness_zero,
            )

            _mb_scan_state["message"] = "Loading universe..."
            _mb_scan_state["progress"] = 5

            universe = scanner._get_universe()
            total = max(len(universe), 1)
            _mb_scan_state["message"] = f"Scanning {total} symbols..."
            _mb_scan_state["progress"] = 10

            # Monkey-patch a progress hook into the scan loop
            original_get_tech = scanner._get_tech_data
            processed = [0]

            def _tracked_get_tech(symbol, min_date):
                processed[0] += 1
                if processed[0] % 40 == 0:
                    pct = 10 + int((processed[0] / total) * 82)
                    _mb_scan_state["progress"] = min(pct, 92)
                    _mb_scan_state["message"] = f"Scanning {processed[0]}/{total} symbols..."
                return original_get_tech(symbol, min_date)

            scanner._get_tech_data = _tracked_get_tech

            df = scanner.scan()

            _mb_scan_state["progress"] = 95
            _mb_scan_state["message"] = "Finalising results..."

            candidates = []
            if not df.empty:
                for _, row in df.iterrows():
                    rec = row.to_dict()
                    for key, val in list(rec.items()):
                        if isinstance(val, float) and (_math.isnan(val) or _math.isinf(val)):
                            rec[key] = None
                    candidates.append(rec)

            _mb_scan_state.update({
                "scan_status": "completed",
                "last_scan": datetime.now().isoformat(),
                "progress": 100,
                "message": f"Found {len(candidates)} candidates",
                "candidates": candidates,
                "bear_market": scanner.bear_market,
            })

        except Exception as e:
            logger.error("Multibagger scan failed: %s", e, exc_info=True)
            _mb_scan_state.update({
                "scan_status": "error",
                "progress": 0,
                "message": str(e),
            })

    threading.Thread(target=_run, daemon=True).start()
    return {"status": "started"}


# --- Darvas Box Pro Scanner State ---
_darvas_scan_state: dict = {
    "scan_status": "idle",
    "last_scan": None,
    "progress": 0,
    "message": "Idle — click Scan to start",
    "candidates": [],
    "bear_market": False,
}
_darvas_scan_lock = threading.Lock()
_DARVAS_SCAN_CACHE = "models/darvas_scan_cache.json"


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
    except Exception:
        pass


def _load_darvas_cache() -> dict | None:
    import json as _json
    import os as _os
    try:
        if _os.path.exists(_DARVAS_SCAN_CACHE):
            with open(_DARVAS_SCAN_CACHE) as _f:
                return _json.load(_f)
    except Exception:
        pass
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
                "message": cache.get("message", f"Found {len(cache['candidates'])} candidates."),
                "candidates": cache["candidates"],
                "bear_market": state.get("bear_market", False),
            }

    return state


@app.post("/api/darvas/scan")
async def darvas_scan(payload: dict = Body(default={})):
    with _darvas_scan_lock:
        if _darvas_scan_state["scan_status"] == "scanning":
            return {"detail": "Scan already in progress"}, 409

        _darvas_scan_state.update({
            "scan_status": "scanning",
            "progress": 0,
            "message": "Initialising scanner...",
            "candidates": [],
        })

    base_days = int(payload.get("base_days", 120))
    min_dar = float(payload.get("min_dar", 0.2))
    min_mcap = int(payload.get("min_mcap", 100))
    max_mcap = int(payload.get("max_mcap", 50000))

    def _run():
        try:
            from myra_app.strategies.darvas_box_scanner import DarvasBoxScanner
            import math as _math

            scanner = DarvasBoxScanner(
                base_days=base_days,
                min_dar=min_dar,
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

            def _tracked_get_tech(symbol, min_date):
                processed[0] += 1
                if processed[0] % 25 == 0:
                    pct = 10 + int((processed[0] / total) * 82)
                    _darvas_scan_state["progress"] = min(pct, 92)
                    _darvas_scan_state["message"] = f"Scanning {processed[0]}/{total} symbols..."
                return original_get_tech(symbol, min_date)

            scanner._get_tech_data = _tracked_get_tech

            df = scanner.scan()

            _darvas_scan_state["progress"] = 95
            _darvas_scan_state["message"] = "Finalising results..."

            candidates = []
            if not df.empty:
                for _, row in df.iterrows():
                    rec = row.to_dict()
                    for key, val in list(rec.items()):
                        if isinstance(val, float) and (_math.isnan(val) or _math.isinf(val)):
                            rec[key] = None
                    candidates.append(rec)

            _darvas_scan_state.update({
                "scan_status": "completed",
                "last_scan": datetime.now().isoformat(),
                "progress": 100,
                "message": f"Found {len(candidates)} candidates",
                "candidates": candidates,
                "bear_market": scanner.bear_market,
            })
            _save_darvas_cache()

        except Exception as e:
            logger.error("Darvas scan failed: %s", e, exc_info=True)
            _darvas_scan_state.update({
                "scan_status": "error",
                "progress": 0,
                "message": str(e),
            })

    threading.Thread(target=_run, daemon=True).start()
    return {"status": "started"}


@app.get("/api/ml/factor-importance")
async def factor_importance():
    from myra_app.ml_trainer import FactorDiscovery
    fd = FactorDiscovery()
    result = fd.discover_factors()
    return result


_search_librarian: object = None
_search_librarian_lock = threading.Lock()

def _get_search_librarian():
    global _search_librarian
    if _search_librarian is None:
        with _search_librarian_lock:
            if _search_librarian is None:
                from myra_app.librarian import Librarian
                _search_librarian = Librarian(read_only=True)
    return _search_librarian

@app.get("/api/search/symbols")
async def search_symbols(q: str = Query(..., min_length=1)):
    return _get_search_librarian().search_symbols(q)


def _validate_finstack(result: dict) -> dict:
    if "error" in result:
        raise HTTPException(status_code=500, detail=result["error"])
    if "_raw" in result:
        raise HTTPException(status_code=502, detail="FinStack MCP returned non-JSON response")
    return result


@app.get("/api/finstack/nifty-outlook")
async def finstack_nifty_outlook():
    cache_key = "nifty_outlook"
    now = time.time()
    if cache_key in _finstack_cache and (now - _finstack_cache[cache_key]["ts"]) < CACHE_TTL:
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
    if cache_key in _finstack_cache and (now - _finstack_cache[cache_key]["ts"]) < CACHE_TTL:
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
    if cache_key in _finstack_cache and (now - _finstack_cache[cache_key]["ts"]) < CACHE_TTL:
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
async def stock_brief(symbol: str = Query(..., description="Stock symbol, e.g., RELIANCE")):
    cache_key = f"stock_brief:{symbol}"
    now = time.time()
    if cache_key in _finstack_cache and (now - _finstack_cache[cache_key]["ts"]) < CACHE_TTL:
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
async def unusual_activity(symbol: str = Query(..., description="Stock symbol, e.g., RELIANCE")):
    cache_key = f"unusual_activity:{symbol}"
    now = time.time()
    if cache_key in _finstack_cache and (now - _finstack_cache[cache_key]["ts"]) < CACHE_TTL:
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
        raise HTTPException(status_code=400, detail="query parameter 'symbol' is required")
    cache_key = f"stock_timeline:{symbol}"
    now = time.time()
    if cache_key in _finstack_cache and (now - _finstack_cache[cache_key]["ts"]) < CACHE_TTL:
        return _finstack_cache[cache_key]["data"]
    from myra_app.utils.finstack_bridge import get_stock_timeline
    try:
        data = await get_stock_timeline(symbol)
        _finstack_cache[cache_key] = {"ts": now, "data": data}
        return data
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
