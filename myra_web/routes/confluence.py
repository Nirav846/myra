"""
MYRA Confluence Router.

Extracted from myra_fastapi_server.py (Phase 9 of monolith refactor).

GET  /api/confluence          — aggregated view of symbols flagged by 2+ scanners,
                                computed from ONE same-date batch snapshot.
POST /api/confluence/refresh  — run every confluence scanner against the same
                                as_on_date and write that snapshot.

Confluence is deliberately NOT computed from the individual per-scanner cache
files: those are written independently whenever a user happens to click Scan
on a scanner page, so their dates routinely span weeks and "flagged by 6
scanners" would not mean the 6 scanners agree on any single date. See
myra_web/confluence_batch.py.
"""

import logging

from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import JSONResponse

from myra_web.confluence_batch import (
    _confluence_lock,
    _confluence_state,
    run_confluence_batch,
)
from myra_web.utils import build_confluence_report

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["confluence"])


@router.get("/confluence")
async def confluence_endpoint():
    """Return an aggregated view of symbols flagged by 2+ scanners."""
    try:
        return build_confluence_report()
    except Exception as e:
        logger.error("Confluence report failed: %s", e, exc_info=True)
        return JSONResponse(status_code=500, content={"error": "Internal server error"})


@router.get("/confluence/status")
async def confluence_status():
    """Refresh state for the UI's progress indicator."""
    with _confluence_lock:
        return dict(_confluence_state)


@router.post("/confluence/refresh")
async def confluence_refresh(payload: dict = Body(default={})):
    """Re-run every confluence scanner against one as_on_date.

    Runs in a worker thread because a full batch is 13 sequential full-universe
    scans and would block the event loop otherwise. The (lock + scanning
    guard) pattern mirrors register_scanner's _scan_handler in scanners.py.
    """
    as_on_date = payload.get("as_on_date") or None

    with _confluence_lock:
        if _confluence_state["scan_status"] == "scanning":
            raise HTTPException(status_code=409, detail="Refresh already in progress")
        _confluence_state.update(
            {
                "scan_status": "scanning",
                "message": "Running all confluence scanners against one date...",
            }
        )

    def _run() -> None:
        try:
            result = run_confluence_batch(as_on_date=as_on_date)
            with _confluence_lock:
                _confluence_state.update(
                    {
                        "scan_status": "completed",
                        "message": (
                            f"Snapshot for {result['as_on_date']}: "
                            f"{result['scanners_ok']} scanners ok, "
                            f"{result['scanners_failed']} failed "
                            f"({result['total_seconds']}s)"
                        ),
                        "last_result": result,
                    }
                )
        except Exception as e:  # noqa: BLE001 - surface failure, never hang the UI
            logger.error("Confluence refresh failed: %s", e, exc_info=True)
            with _confluence_lock:
                _confluence_state.update(
                    {
                        "scan_status": "error",
                        "message": f"Refresh failed: {type(e).__name__}: {e}",
                        "last_result": None,
                    }
                )

    import threading

    threading.Thread(target=_run, daemon=True, name="confluence-batch").start()

    return {
        "scan_status": "scanning",
        "as_on_date": as_on_date or "latest trading day",
        "message": "Batch started — this runs 13 full scans and takes a while.",
    }
