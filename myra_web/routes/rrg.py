"""
RRG (Relative Rotation Graphs) API endpoints.

GET /api/rrg/indices  — list available indices
GET /api/rrg/         — compute RRG data for given params
"""

import logging

from fastapi import APIRouter, HTTPException, Query

from myra_app.analysis.rrg import discover_indices, get_rrg_cached

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/rrg", tags=["rrg"])


@router.get("/indices")
def list_indices():
    """Return all discovered NSE index IDs and labels."""
    indices = discover_indices()
    return {"indices": indices, "count": len(indices)}


@router.get("/")
def get_rrg(
    benchmark: str = Query("nifty 50", description="Benchmark index ID"),
    timeframe: str = Query("weekly", pattern="^(weekly|daily)$"),
    trail: int = Query(8, ge=2, le=26, description="Number of periods to show as trail"),
    sectors: str = Query(None, description="Comma-separated sector IDs (default: all)"),
):
    """
    Compute Relative Rotation Graph data.

    Returns current quadrant positions and historical trails for each sector
    relative to the chosen benchmark.
    """
    try:
        sector_list = None
        if sectors:
            sector_list = [s.strip() for s in sectors.split(",") if s.strip()]
        if not sector_list:
            raise ValueError("At least one sector is required")

        result = get_rrg_cached(
            benchmark_id=benchmark,
            sector_ids=sector_list,
            timeframe=timeframe,
            trail=trail,
        )
        return result

    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.exception("RRG computation failed")
        raise HTTPException(status_code=500, detail=f"RRG computation failed: {exc}")
