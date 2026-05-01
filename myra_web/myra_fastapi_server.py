import os
import sqlite3
import subprocess
import re
import logging
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional, Dict, Any

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="MYRA v3.2 API Bridge")

# CORS – allow React dev server
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Paths
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_DIR = os.path.abspath(os.path.join(BASE_DIR, "..", "myra_app", "db"))

# Map UI database names to actual filenames
DB_MAP = {
    "_tech_conn": "myra_technical.db",
    "_meta_conn": "myra_metadata.db",
    "_inst_conn": "myra_institutional.db",
    "_gov_conn": "myra_governance.db",
}


def get_db_path(db_key: str):
    filename = DB_MAP.get(db_key)
    if not filename:
        return None
    return os.path.join(DB_DIR, filename)

@app.get("/api/health")
def health_check():
    status = {}
    for key in DB_MAP:
        db_path = get_db_path(key)
        if db_path and os.path.exists(db_path):
            try:
                conn = sqlite3.connect(db_path)
                conn.execute("SELECT 1")
                conn.close()
                status[key] = True
            except Exception:
                status[key] = False
        else:
            status[key] = False
    return status

def rewrite_table_name(sql: str) -> str:
    """
    Replace FROM technical_data with FROM technical_data_ui
    """
    pattern = r'\bFROM\s+technical_data\b'
    new_sql = re.sub(pattern, 'FROM technical_data_ui', sql, flags=re.IGNORECASE)
    if new_sql != sql:
        logger.info(f"Rewrote table: {sql} -> {new_sql}")
    return new_sql

class QueryRequest(BaseModel):
    database: str
    query: str
    args: Dict[str, Any] = {}

@app.post("/api/query")
def execute_query(req: QueryRequest):
    logger.info(f"=== Query on {req.database} ===")
    logger.info(f"SQL: {req.query}")
    
    # Validate database
    if req.database not in DB_MAP:
        raise HTTPException(status_code=400, detail=f"Unknown database '{req.database}'")
    
    db_path = get_db_path(req.database)
    if not db_path or not os.path.exists(db_path):
        raise HTTPException(status_code=404, detail=f"Database not found: {db_path}")
    
    # Security: only SELECT/PRAGMA/WITH
    query_upper = req.query.strip().upper()
    if not (query_upper.startswith("SELECT") or query_upper.startswith("PRAGMA") or query_upper.startswith("WITH")):
        raise HTTPException(status_code=403, detail="Only SELECT, WITH, and PRAGMA queries allowed.")
    
    # Apply table name rewrite
    final_query = rewrite_table_name(req.query)
    
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        # Execute with parameters if any (assuming named placeholders not used; simple queries)
        if req.args:
            cursor.execute(final_query, list(req.args.values()))
        else:
            cursor.execute(final_query)
        rows = cursor.fetchall()
        data = [dict(row) for row in rows]
        conn.close()
        
        logger.info(f"Returned {len(data)} rows")
        if data:
            logger.info(f"Columns: {list(data[0].keys())}")
            logger.info(f"Sample first row: {data[0]}")
        else:
            logger.warning("No data returned.")
        return {"data": data}
    except Exception as e:
        logger.error(f"Query error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# Keep the rest of your endpoints (/api/tools/execute, /api/symbols) unchanged
class ToolRequest(BaseModel):
    tool_id: str

@app.post("/api/tools/execute")
def execute_tool(req: ToolRequest):
    PROJECT_ROOT = os.path.abspath(os.path.join(BASE_DIR, ".."))
    tool_map = {
        "force_sync": os.path.join(PROJECT_ROOT, "tools", "force_sync.py"),
        "force_backfill": os.path.join(PROJECT_ROOT, "tools", "force_backfill.py"),
        "train_aeon": os.path.join(PROJECT_ROOT, "research", "train_aeon.py"),
        "repair_indicators": os.path.join(PROJECT_ROOT, "tools", "repair_calculated_indicators.py"),
    }
    script_path = tool_map.get(req.tool_id)
    if not script_path or not os.path.exists(script_path):
        raise HTTPException(status_code=404, detail=f"Script '{req.tool_id}' not found")
    try:
        result = subprocess.run(["python", script_path], capture_output=True, text=True, timeout=120, cwd=PROJECT_ROOT)
        combined = result.stdout + "\n" + result.stderr
        return {"success": result.returncode == 0, "logs": combined.strip() or "Executed successfully."}
    except subprocess.TimeoutExpired:
        return {"success": False, "logs": "Timeout after 120s"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/symbols")
def get_symbols(limit: int = 1000):
    db_path = get_db_path("_tech_conn")
    if not db_path or not os.path.exists(db_path):
        raise HTTPException(status_code=404, detail="Technical database not found")
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT DISTINCT symbol FROM technical_data ORDER BY symbol LIMIT ?", (limit,))
        symbols = [row[0] for row in cursor.fetchall()]
        conn.close()
        return {"symbols": symbols}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=True)