"""
MYRA tools router — sync, ingest, execute, db-doctor, refresh-industry.

Extracted from myra_fastapi_server.py (Phase 4 of monolith refactor).
"""

import logging
import os
import subprocess

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from myra_web.background import _spawn_task
from myra_web.security import verify_myra_auth

logger = logging.getLogger(__name__)

try:
    # Phase 3: orchestrator wrappers removed — call task modules directly.
    from myra_app.tasks.context import default_context
    from myra_app.tasks.doctor import run as _run_db_doctor
    from myra_app.tasks.etf_sync import run as _run_etf_sync
    from myra_app.tasks.fundamentals import run as _run_fundamentals_sync
    from myra_app.tasks.index_sync import run as _run_index_sync
    from myra_app.tasks.ingest import run as _run_daily_ingest

    def _task_fundamentals_sync():
        return _run_fundamentals_sync(default_context())

    def _task_etf_sync():
        return _run_etf_sync(default_context())

    def _task_index_sync():
        return _run_index_sync(default_context())

    def _task_daily_ingest(force: bool = False):
        return _run_daily_ingest(default_context(), force=force)

    def _task_db_doctor():
        return _run_db_doctor(default_context())

except ImportError:
    pass

router = APIRouter(prefix="/api/tools", tags=["tools"])

# The server's BASE_DIR is the `myra_web` directory. tools.py lives in
# myra_web/routes/, so two dirname() calls yield the same path and keep the
# existing script-path resolution behavior identical.
_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class ToolRequest(BaseModel):
    tool_id: str


@router.post("/execute")
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

    full_script_path = os.path.join(_BASE_DIR, script_path.replace("/", os.sep))

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
        logger.exception("execute_tool failed")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/sync/fundamentals")
async def force_fundamentals_sync():
    """Trigger a full fundamentals sync (Morningstar + NSE) NOW (async)."""
    try:
        tid = _spawn_task("fundamentals_sync", _task_fundamentals_sync)
        return JSONResponse(
            status_code=202, content={"status": "started", "task_id": tid}
        )
    except Exception as e:
        logger.exception("force_fundamentals_sync failed")
        return JSONResponse(
            status_code=500,
            content={"status": "error", "message": "Internal server error"},
        )


@router.post("/sync/etf")
async def force_etf_sync():
    """Trigger ETF blocklist sync NOW (async)."""
    try:
        tid = _spawn_task("etf_sync", _task_etf_sync)
        return JSONResponse(
            status_code=202, content={"status": "started", "task_id": tid}
        )
    except Exception as e:
        logger.exception("force_etf_sync failed")
        return JSONResponse(
            status_code=500,
            content={"status": "error", "message": "Internal server error"},
        )


@router.post("/sync/index")
async def force_index_sync():
    """Trigger NIFTY index constituents sync NOW (async)."""
    try:
        tid = _spawn_task("index_sync", _task_index_sync)
        return JSONResponse(
            status_code=202, content={"status": "started", "task_id": tid}
        )
    except Exception as e:
        logger.exception("force_index_sync failed")
        return JSONResponse(
            status_code=500,
            content={"status": "error", "message": "Internal server error"},
        )


@router.post("/ingest")
async def force_daily_ingest():
    """Trigger daily bhavcopy ingest NOW (async)."""
    try:
        tid = _spawn_task("daily_ingest", _task_daily_ingest, force=True)
        return JSONResponse(
            status_code=202, content={"status": "started", "task_id": tid}
        )
    except Exception as e:
        logger.exception("force_daily_ingest failed")
        return JSONResponse(
            status_code=500,
            content={"status": "error", "message": "Internal server error"},
        )


@router.post("/db-doctor")
async def run_db_doctor():
    """Run DB Doctor health check NOW."""
    try:
        _task_db_doctor()
        return {"success": True, "message": "DB Doctor completed"}
    except Exception as e:
        logger.exception("run_db_doctor failed")
        raise HTTPException(status_code=500, detail="Internal server error")


# ---------------------------------------------------------------------------
# Portfolio tools (different prefix)
# ---------------------------------------------------------------------------

portfolio_tools_router = APIRouter(prefix="/api/portfolio", tags=["tools"])


@portfolio_tools_router.post("/refresh-industry")
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
        logger.exception("refresh_portfolio_industry failed")
        return {"status": "error", "message": "Internal server error"}
