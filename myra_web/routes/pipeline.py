"""MYRA Pipeline Router.

Extracted from myra_fastapi_server.py (Phase 10 of monolith refactor).

Read-only background task status/events endpoints backed by
``myra_app.task_tracker``. No auth (matches original).
"""

from fastapi import APIRouter
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/pipeline", tags=["pipeline"])


@router.get("/status")
async def pipeline_status():
    """Return background pipeline task statuses."""
    try:
        from myra_app.task_tracker import list_tasks

        tasks = list_tasks(limit=50)
        return {"tasks": tasks, "status": "ok"}
    except Exception as e:
        logger.exception("pipeline_status failed")
        return {"status": "error", "message": "Internal server error"}


@router.get("/events")
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
        logger.exception("pipeline_events failed")
        return {"status": "error", "message": "Internal server error"}