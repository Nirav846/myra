"""
Cross-Buy API endpoints.
- /scanner: advanced scanner with filters, joins fundamentals
- /months: list of available months
"""

import logging
import os
import sqlite3

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from myra_app.constants import DB_DIR

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/cross-buy", tags=["cross-buy"])


def _get_db_path() -> str:
    return os.path.join(DB_DIR, "myra_valuation.db")


def _safe_float(val, default=None):
    if val is None:
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


@router.get("/months")
def get_available_months():
    """Return list of available months in fund_cross_buy, newest first."""
    db_path = _get_db_path()
    if not os.path.exists(db_path):
        return {"months": []}
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT DISTINCT month FROM fund_cross_buy ORDER BY month DESC"
        ).fetchall()
        return {"months": [r[0] for r in rows]}
    except Exception:
        return {"months": []}
    finally:
        conn.close()


@router.get("/scanner")
def cross_buy_scanner(
    month: str = Query(""),
    min_cross_buy_ratio: float = Query(0),
    signal_tag: str = Query(""),
    min_total_funds: int = Query(0),
    stock_category: str = Query("", description="Optional filter: Large / Mid / Small"),
    limit: int = Query(500, ge=1, le=2000),
):
    """Cross-buy scanner over fund_cross_buy joined with fundamentals.

    Args:
        month: Month tag "YYYY-MM"; empty resolves to the latest month.
        min_cross_buy_ratio: Minimum cross_buy_ratio (SQL filter).
        signal_tag: Optional exact signal_tag match (SQL filter).
        min_total_funds: Minimum distinct funds holding the stock (SQL filter).
        stock_category: Optional post-compute size filter (Large/Mid/Small).
        limit: Max rows returned (1-2000).
    """
    db_path = _get_db_path()
    if not os.path.exists(db_path):
        return JSONResponse(
            status_code=503, content={"error": "Database not available."}
        )

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        target_month = str(month).strip() if month else ""
        if not target_month:
            row = conn.execute("SELECT MAX(month) as m FROM fund_cross_buy").fetchone()
            target_month = row["m"] if row and row["m"] else ""
            if not target_month:
                return {"month": None, "stocks": [], "total": 0}

        conds = ["cb.month = ?"]
        params: list = [target_month]

        if min_cross_buy_ratio > 0:
            conds.append("cb.cross_buy_ratio >= ?")
            params.append(min_cross_buy_ratio)
        if signal_tag.strip():
            conds.append("cb.signal_tag = ?")
            params.append(signal_tag.strip())
        if min_total_funds > 0:
            conds.append("cb.total_funds >= ?")
            params.append(min_total_funds)
        where = " AND ".join(conds)

        query = f"""
            SELECT cb.symbol, cb.month, cb.total_funds,
                   cb.large_funds, cb.mid_funds, cb.small_funds,
                   cb.multi_funds, cb.other_funds,
                   cb.cross_buy_ratio, cb.signal_tag,
                   CASE
                       WHEN f.market_cap IS NULL THEN 'Unknown'
                       WHEN f.market_cap >= 2e11 THEN 'Large'
                       WHEN f.market_cap >= 5e10 THEN 'Mid'
                       ELSE 'Small'
                   END AS stock_category,
                   f.market_cap, f.sector
            FROM fund_cross_buy cb
            LEFT JOIN fundamentals f ON cb.symbol = f.symbol
            WHERE {where}
            ORDER BY cb.cross_buy_ratio DESC, cb.total_funds DESC
            LIMIT ?
        """
        params.append(limit)
        rows = conn.execute(query, params).fetchall()

        cat_filter = (
            stock_category.strip().capitalize() if stock_category.strip() else ""
        )

        stocks = []
        for r in rows:
            item = {
                "symbol": r["symbol"],
                "month": r["month"],
                "total_funds": r["total_funds"],
                "large_funds": r["large_funds"],
                "mid_funds": r["mid_funds"],
                "small_funds": r["small_funds"],
                "multi_funds": r["multi_funds"],
                "other_funds": r["other_funds"],
                "cross_buy_ratio": _safe_float(r["cross_buy_ratio"]),
                "signal_tag": r["signal_tag"] or None,
                "stock_category": r["stock_category"] or "Unknown",
                "market_cap": _safe_float(r["market_cap"]),
                "sector": r["sector"] or None,
            }
            # Post-compute filter (computed field, cannot go in SQL WHERE)
            if cat_filter and item["stock_category"] != cat_filter:
                continue
            stocks.append(item)

        return {"month": target_month, "stocks": stocks, "total": len(stocks)}

    except Exception:
        logger.exception("Scanner query failed")
        return JSONResponse(status_code=500, content={"error": "Internal server error"})
    finally:
        conn.close()
