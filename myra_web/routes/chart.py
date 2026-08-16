import os
import sqlite3

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api/chart", tags=["chart"])


@router.get("/{symbol}")
async def get_chart(symbol: str, limit: int = 500):
    """Return OHLCV data for a symbol, ordered ascending by date."""
    from myra_fastapi_server import get_db_path  # lazy: avoids circular import + preserves test monkeypatch

    db_path = get_db_path("technical")
    if not db_path or not os.path.exists(db_path):
        raise HTTPException(status_code=500, detail="Technical database not found")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT date, open, high, low, close, volume "
            "FROM technical_data WHERE symbol = ? ORDER BY date DESC LIMIT ?",
            (symbol.upper(), limit),
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        raise HTTPException(status_code=404, detail="Symbol not found")

    # Reverse to ascending date order
    data = [dict(r) for r in reversed(rows)]
    return {"symbol": symbol.upper(), "data": data}
