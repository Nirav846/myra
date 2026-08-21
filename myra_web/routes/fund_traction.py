"""
Fund Traction batch API endpoint.
Returns traction data for a list of symbols from the fund_traction table.
"""

import logging
import os
import sqlite3

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from myra_app.constants import DB_DIR

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/fund-traction", tags=["fund-traction"])


def _get_db_path() -> str:
    return os.path.join(DB_DIR, "myra_valuation.db")


@router.get("/batch")
def get_fund_traction_batch(
    symbols: str = Query("", description="Comma-separated list of symbols"),
):
    """Return fund traction data for the given symbols.

    Uses the latest month available per symbol.
    If a symbol has no traction data, returns null for its fields.
    """
    if not symbols.strip():
        return JSONResponse(
            status_code=400,
            content={"error": "No symbols provided. Pass ?symbols=RELIANCE,TCS,..."},
        )

    symbol_list = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    if not symbol_list:
        return JSONResponse(
            status_code=400,
            content={"error": "No valid symbols provided."},
        )

    db_path = _get_db_path()
    if not os.path.exists(db_path):
        return JSONResponse(
            status_code=503,
            content={"error": "Database not available."},
        )

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        # Get the latest month available in the table
        latest_month_row = conn.execute(
            "SELECT MAX(month) as max_month FROM fund_traction"
        ).fetchone()
        latest_month = latest_month_row["max_month"] if latest_month_row else None

        if not latest_month:
            return {
                "latest_month": None,
                "symbols": {s: None for s in symbol_list},
            }

        # For each symbol, get the latest month's data
        # Use a single query with WHERE IN (...) and a subquery for latest month per symbol
        placeholders = ",".join("?" for _ in symbol_list)
        query = f"""
            SELECT ft.symbol, ft.month, ft.traction_score, ft.number_of_funds,
                   ft.adds_new, ft.reduces_closes, ft.sma_30, ft.month_end_close,
                   ft.close_latest, ft.pct_vs_sma
            FROM fund_traction ft
            INNER JOIN (
                SELECT symbol, MAX(month) as max_month
                FROM fund_traction
                WHERE symbol IN ({placeholders})
                GROUP BY symbol
            ) latest ON ft.symbol = latest.symbol AND ft.month = latest.max_month
            WHERE ft.symbol IN ({placeholders})
            ORDER BY ft.traction_score DESC
        """

        rows = conn.execute(query, symbol_list + symbol_list).fetchall()

        # Build result map
        result_map = {}
        for row in rows:
            result_map[row["symbol"]] = {
                "symbol": row["symbol"],
                "month": row["month"],
                "traction_score": row["traction_score"],
                "fund_count": row["number_of_funds"],
                "adds_new": row["adds_new"],
                "reduces_closes": row["reduces_closes"],
                "sma_30": row["sma_30"],
                "month_end_close": row["month_end_close"],
                "close_latest": row["close_latest"],
                "pct_vs_sma": row["pct_vs_sma"],
            }

        # Return results for all requested symbols (null for missing)
        symbols_result = {}
        for s in symbol_list:
            symbols_result[s] = result_map.get(s)

        return {
            "latest_month": latest_month,
            "symbols": symbols_result,
        }

    except Exception as e:
        logger.exception("Fund traction batch query failed")
        return JSONResponse(
            status_code=500,
            content={"error": f"Query failed: {e}"},
        )
    finally:
        conn.close()
