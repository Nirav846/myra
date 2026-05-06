import os
import sqlite3
import subprocess
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional

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
def execute_query(req: QueryRequest):
    """
    Allows the React frontend to run read queries directly against the sidecars.
    """
    if req.db not in DB_MAP:
        raise HTTPException(status_code=400, detail="Invalid database specified.")
    
    db_path = get_db_path(req.db)
    if not db_path or not os.path.exists(db_path):
        raise HTTPException(status_code=404, detail=f"Database file not found: {DB_MAP.get(req.db)}")

    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(req.query, req.params)
        
        # If it's a SELECT query, return the rows
        if req.query.strip().upper().startswith(("SELECT", "PRAGMA", "WITH")):
            rows = cursor.fetchall()
            data = [dict(ix) for ix in rows]
            conn.close()
            return {"data": data}
        else:
            # If it's an UPDATE/INSERT (though UI is mostly read-only)
            conn.commit()
            rowcount = cursor.rowcount
            conn.close()
            return {"data": [{"rows_affected": rowcount}]}
            
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

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
