"""
RRG (Relative Rotation Graphs) API endpoints.

GET /api/rrg/indices  — list available indices
GET /api/rrg/         — compute RRG data for given params
"""

import logging
from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from myra_app.analysis.rrg import discover_indices, get_rrg_cached

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/rrg", tags=["rrg"])

MAX_SECTORS = 25


# ── Response models ───────────────────────────────────────────────────────────
class RRGPoint(BaseModel):
    id: str
    label: str
    x: float
    y: float
    quadrant: str


class RRGResponse(BaseModel):
    current: List[RRGPoint]
    trails: Dict[str, List[List[float]]]
    meta: Dict[str, Any]


class IndicesResponse(BaseModel):
    indices: List[Dict[str, str]]
    count: int


# ── Endpoints ─────────────────────────────────────────────────────────────────
@router.get("/indices", response_model=IndicesResponse)
def list_indices():
    """Return all discovered NSE index IDs and labels."""
    indices = discover_indices()
    return {"indices": indices, "count": len(indices)}


@router.get("/", response_model=RRGResponse)
def get_rrg(
    benchmark: str = Query("nifty 50", description="Benchmark index ID"),
    timeframe: str = Query("weekly", pattern="^(weekly|daily)$"),
    trail: int = Query(8, ge=2, le=26, description="Number of periods to show as trail"),
    sectors: str = Query(None, description="Comma-separated sector IDs (default: all)"),
    refresh: bool = Query(False, description="Bypass cache and recompute"),
):
    """
    Compute Relative Rotation Graph data.

    Returns current quadrant positions and historical trails for each sector
    relative to the chosen benchmark.
    """
    try:
        # Parse sectors
        sector_list = None
        if sectors:
            sector_list = [s.strip() for s in sectors.split(",") if s.strip()]
        if not sector_list:
            raise HTTPException(status_code=400, detail="At least one sector is required")

        # Normalize for comparison
        benchmark_low = benchmark.lower()
        sector_list_low = [s.lower() for s in sector_list]

        logger.debug("RRG request: benchmark=%s, sectors=%s, tf=%s, trail=%d, refresh=%s",
                      benchmark, sector_list, timeframe, trail, refresh)

        # Guard: benchmark not in sectors (case-insensitive)
        if benchmark_low in sector_list_low:
            raise HTTPException(status_code=400, detail="Benchmark cannot be included in sectors")

        # Server-side sector cap
        if len(sector_list) > MAX_SECTORS:
            raise HTTPException(
                status_code=400,
                detail=f"Too many sectors (max {MAX_SECTORS})",
            )

        # Validate benchmark exists
        all_indices = discover_indices()
        known_ids = {idx["id"] for idx in all_indices}
        if benchmark not in known_ids:
            raise HTTPException(status_code=400, detail=f"Benchmark '{benchmark}' not found")

        result = get_rrg_cached(
            benchmark_id=benchmark,
            sector_ids=sector_list,
            timeframe=timeframe,
            trail=trail,
            refresh=refresh,
        )
        return result

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("RRG computation failed")
        raise HTTPException(status_code=500, detail="RRG computation failed. Check logs for details.")
