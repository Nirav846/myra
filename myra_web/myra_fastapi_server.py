"""
MYRA API Bridge — application wiring only.

All endpoints live in per-domain routers under ``myra_web/routes/``.
This file only creates the FastAPI app, middleware, exception handling,
and includes the routers. Names re-exported at the bottom are kept so
existing tests (which import from ``myra_fastapi_server``) keep working.
"""

import logging

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

logger = logging.getLogger(__name__)

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


# ---------------------------------------------------------------------------
# Backward-compatible re-exports (used by the existing test suite).
# ---------------------------------------------------------------------------
from myra_web.security import MYRA_API_SECRET, verify_myra_auth  # noqa: E402
from myra_web.utils import _apply_tier_rank, get_db_path  # noqa: E402
from myra_web.background import _spawn_task  # noqa: E402
from myra_web.routes.query import _run_query  # noqa: E402