import asyncio
import os
import sqlite3
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from myra_web.utils import get_db_path

router = APIRouter(prefix="/api/chart", tags=["chart"])


def _run_chart_query(
    db_path: str,
    symbol: str,
    limit: int,
    from_date: Optional[str],
    to_date: Optional[str],
):
    """Execute a chart SQL query synchronously. Called via asyncio.to_thread."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        if from_date and to_date:
            rows = conn.execute(
                "SELECT date, open, high, low, close, volume, delivery, delivery_pct, vwap "
                "FROM technical_data WHERE symbol = ? AND date >= ? AND date <= ? "
                "ORDER BY date ASC",
                (symbol.upper(), from_date, to_date),
            ).fetchall()
        elif from_date:
            rows = conn.execute(
                "SELECT date, open, high, low, close, volume, delivery, delivery_pct, vwap "
                "FROM technical_data WHERE symbol = ? AND date >= ? "
                "ORDER BY date ASC LIMIT ?",
                (symbol.upper(), from_date, limit),
            ).fetchall()
        elif to_date:
            rows = conn.execute(
                "SELECT date, open, high, low, close, volume, delivery, delivery_pct, vwap "
                "FROM technical_data WHERE symbol = ? AND date <= ? "
                "ORDER BY date DESC LIMIT ?",
                (symbol.upper(), to_date, limit),
            ).fetchall()
            rows = list(reversed(rows))
        else:
            rows = conn.execute(
                "SELECT date, open, high, low, close, volume, delivery, delivery_pct, vwap "
                "FROM technical_data WHERE symbol = ? ORDER BY date DESC LIMIT ?",
                (symbol.upper(), limit),
            ).fetchall()
            rows = list(reversed(rows))
        return [dict(r) for r in rows]
    finally:
        conn.close()


@router.get("/{symbol}")
async def get_chart(
    symbol: str,
    limit: int = 500,
    from_date: Optional[str] = Query(None),
    to_date: Optional[str] = Query(None),
):
    """Return OHLCV + delivery + VWAP data for a symbol, ordered ascending by date."""
    db_path = get_db_path("technical")
    if not db_path or not os.path.exists(db_path):
        raise HTTPException(status_code=500, detail="Technical database not found")

    data = await asyncio.to_thread(
        _run_chart_query, db_path, symbol, limit, from_date, to_date
    )

    if not data:
        raise HTTPException(status_code=404, detail="Symbol not found")

    return {"symbol": symbol.upper(), "data": data}
