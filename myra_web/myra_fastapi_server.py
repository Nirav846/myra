"""
MYRA API Bridge — application wiring only.

All endpoints live in per-domain routers under ``myra_web/routes/``.
This file only creates the FastAPI app, middleware, exception handling,
and includes the routers. Names re-exported at the bottom are kept so
existing tests (which import from ``myra_fastapi_server``) keep working.
"""

import contextlib
import logging
import os
import threading

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from myra_web.routes.fundamentals import router as fundamentals_router
from myra_web.routes.full_fundamentals import router as full_fundamentals_router
from myra_web.routes.sentiment import router as sentiment_router
from myra_web.routes.ai_opinion import router as ai_opinion_router
from myra_web.routes.chart import router as chart_router
from myra_web.routes.search import router as search_router
from myra_web.routes.finstack import router as finstack_router
from myra_web.routes.ml import router as ml_router
from myra_web.routes.tools import router as tools_router
from myra_web.routes.tools import portfolio_tools_router
from myra_web.routes.portfolio import router as portfolio_router
from myra_web.routes.health import router as health_router
from myra_web.routes.scanners import router as scanners_router
from myra_web.routes.query import router as query_router
from myra_web.routes.confluence import router as confluence_router
from myra_web.routes.pipeline import router as pipeline_router
from myra_web.routes.rrg import router as rrg_router
from myra_web.routes.fund_traction import router as fund_traction_router
from myra_web.routes.cross_buy import router as cross_buy_router

logger = logging.getLogger(__name__)


def _embedded_scheduler_enabled() -> bool:
    """Whether this API process should own the background scheduler.

    Opt-in via ``MYRA_EMBED_SCHEDULER`` (the launcher, ``run_fastapi.py``, sets
    it). Default off so test collections and read-only embeddings never spawn
    background tasks against live databases.
    """
    return str(os.environ.get("MYRA_EMBED_SCHEDULER", "0")).strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _start_embedded_scheduler() -> None:
    """Bind operator controls, then start the one-and-only scheduler."""
    try:
        from myra_app import background_orchestrator as bo
        from myra_app.pipeline_control import control

        # Share the operator pause control with the scheduled tasks so a
        # frontend Pause reaches them, and vice-versa.
        bo.attach_pause_event(control._pause_event)
        started = bo.start(register_signals=False)
        logger.info("[MYRA] Embedded background scheduler started=%s", started)
    except Exception as exc:  # noqa: BLE001 - API must still come up
        logger.error(
            "[MYRA] Embedded scheduler failed to start: %s", exc, exc_info=True
        )


def _stop_embedded_scheduler() -> None:
    try:
        from myra_app.background_orchestrator import request_shutdown

        request_shutdown()
    except Exception as exc:  # noqa: BLE001
        logger.warning("[MYRA] Embedded scheduler shutdown failed: %s", exc)


@contextlib.asynccontextmanager
async def _lifespan(_app: FastAPI):
    """Own the background scheduler for the lifetime of the API server.

    One process, one scheduler, one operator control plane. Startup runs in a
    daemon thread so the (potentially slow) DB doctor / catch-up ingest never
    delays the API becoming available.
    """
    embed = _embedded_scheduler_enabled()
    if embed:
        threading.Thread(
            target=_start_embedded_scheduler,
            name="myra-scheduler-boot",
            daemon=True,
        ).start()
    try:
        yield
    finally:
        if embed:
            stopper = threading.Thread(
                target=_stop_embedded_scheduler, name="myra-scheduler-stop", daemon=True
            )
            stopper.start()
            stopper.join(timeout=30)


app = FastAPI(title="MYRA v3.2 API Bridge", lifespan=_lifespan)

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
app.include_router(ml_router)
app.include_router(tools_router)
app.include_router(portfolio_tools_router)
app.include_router(portfolio_router)
app.include_router(health_router)
app.include_router(scanners_router)
app.include_router(query_router)
app.include_router(confluence_router)
app.include_router(pipeline_router)
app.include_router(rrg_router)
app.include_router(fund_traction_router)
app.include_router(cross_buy_router)


# ---------------------------------------------------------------------------
# Backward-compatible re-exports (used by the existing test suite).
# ---------------------------------------------------------------------------
from myra_web.security import MYRA_API_SECRET, verify_myra_auth  # noqa: E402
from myra_web.utils import _apply_tier_rank, get_db_path  # noqa: E402
from myra_web.background import _spawn_task  # noqa: E402
from myra_web.routes.query import _run_query  # noqa: E402
