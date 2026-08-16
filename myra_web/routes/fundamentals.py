"""
MYRA Fundamentals Router — consolidated per-symbol fundamental snapshot.

Combines Screener.in metrics (PBV/ROCE) with valuation fundamentals, latest
technical price data and recent corporate actions into a single JSON payload.

Endpoint: GET /api/fundamentals/{symbol}
"""

import logging
import os
import sqlite3

from fastapi import APIRouter, HTTPException

from myra_app.constants import DB_DIR
from myra_app.librarian_core import LibrarianCore

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/fundamentals", tags=["fundamentals"])


def _db_path(db_key: str):
    """Resolve the SQLite sidecar path for a DB_MAP key (or None)."""
    filename = LibrarianCore.DB_MAP.get(db_key)
    if not filename:
        return None
    return os.path.join(DB_DIR, filename)


def _open(db_key: str):
    """Open a read connection to a sidecar DB, or None if the file is missing."""
    path = _db_path(db_key)
    if not path or not os.path.exists(path):
        return None
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


@router.get("/{symbol}")
def get_fundamentals(symbol: str):
    """Return all fundamental data for a symbol, with nulls for missing fields."""
    sym = symbol.strip().upper()
    if not sym:
        raise HTTPException(status_code=400, detail="Symbol is required")

    result = {
        "symbol": sym,
        "market_cap": None,
        "pe": None,
        "net_margin": None,
        "promoter_holding_pct": None,
        "sector": None,
        "free_float_mcap": None,
        "free_float_pct": None,
        "pbv": None,
        "roce": None,
        "last_updated": None,
        "close": None,
        "52w_high": None,
        "52w_low": None,
        "corporate_actions": [],
    }

    found = False

    # 1. Valuation fundamentals
    conn = _open("valuation")
    if conn is not None:
        try:
            row = conn.execute(
                "SELECT market_cap, pe, net_margin, promoter_holding_pct, sector, "
                "free_float_market_cap, free_float_pct "
                "FROM fundamentals WHERE symbol = ?",
                (sym,),
            ).fetchone()
            if row:
                found = True
                result["market_cap"] = row["market_cap"]
                result["pe"] = row["pe"]
                result["net_margin"] = row["net_margin"]
                result["promoter_holding_pct"] = row["promoter_holding_pct"]
                result["sector"] = row["sector"]
                result["free_float_mcap"] = row["free_float_market_cap"]
                result["free_float_pct"] = row["free_float_pct"]
        except Exception as e:
            logger.warning("[fundamentals] valuation query failed for %s: %s", sym, e)
        finally:
            conn.close()

    # 2. Screener.in (PBV / ROCE)
    conn = _open("valuation")
    if conn is not None:
        try:
            row = conn.execute(
                "SELECT pbv, roce, last_updated FROM screener_fundamentals WHERE symbol = ?",
                (sym,),
            ).fetchone()
            if row:
                found = True
                result["pbv"] = row["pbv"]
                result["roce"] = row["roce"]
                result["last_updated"] = row["last_updated"]
        except Exception as e:
            logger.warning("[fundamentals] screener query failed for %s: %s", sym, e)
        finally:
            conn.close()

    # 3. Technical (latest price + 52-week high/low)
    conn = _open("technical")
    if conn is not None:
        try:
            row = conn.execute(
                "SELECT close, high_52w, low_52w FROM technical_data "
                "WHERE symbol = ? ORDER BY date DESC LIMIT 1",
                (sym,),
            ).fetchone()
            if row:
                found = True
                result["close"] = row["close"]
                result["52w_high"] = row["high_52w"]
                result["52w_low"] = row["low_52w"]
                # Fall back to computing from recent trading data when the
                # pre-computed 52-week columns are unavailable.
                if result["52w_high"] is None or result["52w_low"] is None:
                    win = conn.execute(
                        "SELECT MAX(high) AS h, MIN(low) AS l FROM ("
                        "  SELECT high, low FROM technical_data "
                        "  WHERE symbol = ? ORDER BY date DESC LIMIT 252"
                        ")",
                        (sym,),
                    ).fetchone()
                    if win is not None and win["h"] is not None:
                        result["52w_high"] = win["h"]
                        result["52w_low"] = win["l"]
        except Exception as e:
            logger.warning("[fundamentals] technical query failed for %s: %s", sym, e)
        finally:
            conn.close()

    # 4. Corporate actions (last 1 year)
    conn = _open("institutional")
    if conn is not None:
        try:
            rows = conn.execute(
                "SELECT action_type, ex_date, date FROM corporate_actions "
                "WHERE symbol = ? AND date >= date('now', '-1 year', '+5 hours', '+30 minutes') "
                "ORDER BY date DESC",
                (sym,),
            ).fetchall()
            result["corporate_actions"] = [
                {
                    "action_type": r["action_type"],
                    "ex_date": r["ex_date"],
                    "date": r["date"],
                }
                for r in rows
            ]
            if rows:
                found = True
        except Exception as e:
            logger.warning(
                "[fundamentals] corporate actions query failed for %s: %s", sym, e
            )
        finally:
            conn.close()

    if not found:
        raise HTTPException(status_code=404, detail=f"No data found for symbol {sym}")

    return result
