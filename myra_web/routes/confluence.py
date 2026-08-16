"""
MYRA Confluence Router.

Extracted from myra_fastapi_server.py (Phase 9 of monolith refactor).

GET /api/confluence — aggregated view of symbols flagged by 2+ scanners.
"""

import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse

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
        return JSONResponse(status_code=500, content={"error": str(e)})