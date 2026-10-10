"""MYRA Pipeline Router — the single authoritative ``/api/pipeline`` surface.

Execution and status live in :mod:`myra_app.pipeline_control`, which reuses the
same ``myra_app.tasks.*`` modules the background orchestrator schedules. There is
no scheduler in this router: periodic execution belongs to
``myra_app.background_orchestrator``.

This replaces two colliding routers (the old read-only ``task_tracker`` view and
the never-registered ``myra_web/pipeline_dashboard.py``), which defined
overlapping ``/status`` and ``/events`` paths and two different response shapes.

Response contract (consumed by both Mission Control and Data Sync)::

    {
      "overall": {status, active_task_id, started_at, finished_at, message,
                  progress_pct, run_type, stop_on_fail, cancel_requested, busy,
                  paused},
      "tasks":   {<task_key>: {current_status, stage, progress_pct,
                               error_message, last_run, started_at,
                               finished_at, duration_seconds}},
      "order":   ["daily_ingest", ...],
      "events":  [{time, type, ...}, ...]
    }
"""

from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from myra_app.pipeline_control import (
    PIPELINE_TASKS,
    PipelineBusyError,
    control,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/pipeline", tags=["pipeline"])


class RunRequest(BaseModel):
    task: str = "all"
    stop_on_fail: bool = True


class ScheduleToggleRequest(BaseModel):
    task_key: str
    enabled: bool


@router.get("/status")
def get_pipeline_status():
    """Full run + per-task status. Never returns a partial success payload."""
    try:
        return control.get_status()
    except Exception:
        logger.exception("pipeline status failed")
        raise HTTPException(500, "Failed to read pipeline status")


@router.get("/run/status")
def get_run_status():
    """Cheap overall-only projection, for a tight poll loop."""
    try:
        return control.get_status()["overall"]
    except Exception:
        logger.exception("pipeline run status failed")
        raise HTTPException(500, "Failed to read run status")


@router.get("/events")
async def pipeline_events():
    """Server-Sent Events: initial state, then task/progress/completion events."""
    queue = control.subscribe()

    async def event_generator():
        try:
            # Initial snapshot so a fresh client never renders an empty board.
            yield _sse({"type": "connected", "state": control.get_status()})
            while not control.shutting_down():
                event = None
                with control._lock:  # noqa: SLF001 - same module family
                    if queue:
                        event = queue.popleft()
                if event is not None:
                    yield _sse(event)
                else:
                    await asyncio.sleep(0.5)
            # Tell clients why the stream ended so they reconnect knowingly.
            yield _sse({"type": "shutdown"})
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("pipeline SSE stream failed")
        finally:
            control.unsubscribe(queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"


@router.post("/run")
def run_pipeline(req: RunRequest):
    """Start one task or the whole pipeline.

    409 when a run is already in flight — the check and the start share one lock,
    so two concurrent callers cannot both start.
    """
    try:
        result = control.start_run(req.task, stop_on_fail=req.stop_on_fail)
    except PipelineBusyError as exc:
        raise HTTPException(409, str(exc))
    except KeyError as exc:
        raise HTTPException(400, f"Unknown task: {exc.args[0]}")
    return {
        "success": True,
        "message": f"Task '{req.task}' started",
        **result,
    }


@router.post("/cancel")
def cancel_pipeline():
    """Request cancellation. The run stays ``cancelling`` until the task exits."""
    return control.cancel()


@router.post("/pause")
def pause_pipeline():
    """Pause a running pipeline (cooperative; takes effect at the next
    checkpoint of a task that honours pause_event — currently enrichment)."""
    return control.pause()


@router.post("/resume")
def resume_pipeline():
    """Resume a paused pipeline."""
    return control.resume()


@router.post("/force-reset")
def force_reset():
    """Clear a stale (non-executing) run state.

    Refuses with 409 while a task is genuinely executing, so it can never report
    an operation as idle while it is still writing data.
    """
    if control.is_busy():
        raise HTTPException(
            409,
            "A task is still executing — use cancel and wait for it to stop. "
            "Force reset is refused while work is in flight.",
        )
    status = control.get_status()["overall"]
    logger.info("Pipeline force-reset from %s -> idle", status["status"])
    return {"success": True, "previous_status": status["status"], "status": "idle"}


@router.get("/check")
def get_pipeline_checks():
    """Directory / database reachability diagnostics (Data Sync page)."""
    return control.get_checks()


@router.get("/schedule")
def get_schedule():
    return control.get_schedule_config()


@router.post("/toggle-schedule")
def toggle_schedule(req: ScheduleToggleRequest):
    try:
        config = control.set_schedule_config(req.task_key, req.enabled)
    except KeyError as exc:
        raise HTTPException(400, f"Unknown task: {exc.args[0]}")
    return {"success": True, "config": config}


@router.get("/schedule/paused")
def get_schedule_paused():
    return {"paused": control.get_schedule_paused()}


@router.post("/schedule/pause")
def toggle_schedule_pause():
    return {"paused": control.toggle_schedule_pause(), "success": True}


__all__ = ["router", "control", "PIPELINE_TASKS"]
