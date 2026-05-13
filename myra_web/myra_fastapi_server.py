import os
import sqlite3
import subprocess
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional
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

app = FastAPI(title="MYRA v3.2 API Bridge")

# Allow the React frontend to communicate with this local API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allows all origins for development
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Use the expected folder structure: Myra\myra_web (this project) side-by-side with Myra\myra_app
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_DIR = os.path.abspath(os.path.join(BASE_DIR, "..", "myra_app", "db"))

# Map your DB sidecars internally
DB_MAP = {
    "tech": "myra_technical.db",
    "meta": "myra_metadata.db",
    "inst": "myra_institutional.db",
    "gov": "myra_governance.db",
    "_tech_conn": "myra_technical.db",
    "_meta_conn": "myra_metadata.db",
    "_inst_conn": "myra_institutional.db",
    "_gov_conn": "myra_governance.db"
}

def get_db_path(db_key: str):
    """Safely construct the path to a specific SQLite sidecar."""
    filename = DB_MAP.get(db_key)
    if not filename:
        return None
    return os.path.join(DB_DIR, filename)

@app.get("/api/health")
def health_check():
    """
    Checks if the databases in myra_app/db exist and can be connected to.
    The React UI polls this endpoint to update the green/yellow status lights in the sidebar.
    """
    status = {}
    for key, filename in DB_MAP.items():
        db_path = get_db_path(key)
        if db_path and os.path.exists(db_path):
            try:
                # Fast heartbeat check
                conn = sqlite3.connect(db_path)
                conn.execute("SELECT 1")
                conn.close()
                status[key] = True
            except Exception:
                status[key] = False
        else:
            status[key] = False
    return status

class QueryRequest(BaseModel):
    db: str
    query: str
    params: list = []

@app.post("/api/query")
async def execute_query(req: QueryRequest):
    # Map frontend DB names to actual files
    db_map = {
        "_tech_conn": "myra_technical.db",
        "_meta_conn": "myra_metadata.db",
        "_val_conn": "myra_valuation.db",
        "_inst_conn": "myra_institutional.db",
        "_gov_conn": "myra_governance.db",
        "_cache_conn": "myra_cache_network.db",
        "_scoring_conn": "myra_scoring.db",
        "_cal_conn": "myra_calendar.db",
    }
    
    # Use explicitly defined mapping or fallback to globally defined DB_MAP
    db_file = db_map.get(req.db) or DB_MAP.get(req.db)
    if not db_file:
        raise HTTPException(status_code=400, detail=f"Unknown database: {req.db}")

    db_path = os.path.join(DB_DIR, db_file)
    if not os.path.exists(db_path):
        raise HTTPException(status_code=400, detail=f"Database file not found: {db_file}")

    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.execute(req.query, req.params)
        
        try:
            rows = [dict(row) for row in cursor.fetchall()]
        except Exception:
            rows = []
            
        if not req.query.lstrip().upper().startswith(("SELECT", "PRAGMA", "WITH", "EXPLAIN")):
            conn.commit()
            
        rowcount = cursor.rowcount
        conn.close()
        
        return {
            "data": rows,
            "rows_affected": rowcount
        }
    except sqlite3.Error as e:
        raise HTTPException(status_code=400, detail=str(e))

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
        "repair_indicators": "tools/repair_calculated_indicators.py"
    }
    
    script_path = tool_map.get(req.tool_id)
    if not script_path:
        raise HTTPException(status_code=400, detail="Tool mapping not found")
        
    full_script_path = os.path.join(BASE_DIR, script_path.replace("/", os.sep))
    
    if not os.path.exists(full_script_path):
        raise HTTPException(status_code=404, detail=f"Script not found at {full_script_path}")

    try:
        # Run the Python script synchronously, capturing output
        # For long-running scripts >1 minute, you would typically use Celery or BackgroundTasks here
        result = subprocess.run(["python", full_script_path], capture_output=True, text=True, timeout=120)
        
        combined_logs = result.stdout + "\n" + result.stderr
        return {
            "success": result.returncode == 0,
            "logs": combined_logs.strip() or "Script executed silently."
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
    tech_db = os.path.join(DB_DIR, DB_MAP["tech"])
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
            rows = conn.execute("""
                SELECT a.symbol, a.close as close_today, b.close as close_prev
                FROM technical_data a
                JOIN technical_data b ON a.symbol = b.symbol AND b.date = ?
                WHERE a.date = ?
                  AND a.close > 0 AND b.close > 0
            """, (prev_date, latest)).fetchall()
            
            advances = sum(1 for r in rows if r[1] > r[2])
            declines = sum(1 for r in rows if r[1] < r[2])
            total = advances + declines
            
            return {
                "advances": advances,
                "declines": declines,
                "total": total,
                "date": latest
            }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/db-size")
async def get_db_size():
    """Return size of the main technical database."""
    try:
        tech_path = os.path.join(DB_DIR, DB_MAP["tech"])
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
            "memory_total_gb": round(psutil.virtual_memory().total / (1024**3), 1)
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
        "fundamentals": _get_last_run("fundamentals_sync") if '_get_last_run' in globals() else "Never",
        "etf": _get_last_run("etf_sync") if '_get_last_run' in globals() else "Never",
        "index": _get_last_run("index_sync") if '_get_last_run' in globals() else "Never",
        "ingest": _get_last_run("daily_ingest") if '_get_last_run' in globals() else "Never",
        "db_doctor": _get_last_run("db_doctor") if '_get_last_run' in globals() else "Never",
    }

