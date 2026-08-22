"""
Fund Traction API endpoints.
- /batch: returns traction data for a list of symbols
- /scanner: advanced scanner with filters, joins fundamentals
- /months: list of available months
"""

import logging
import os
import sqlite3
from collections import Counter

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from myra_app.constants import DB_DIR

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/fund-traction", tags=["fund-traction"])


def _get_db_path() -> str:
    return os.path.join(DB_DIR, "myra_valuation.db")


def _safe_float(val, default=None):
    if val is None:
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


@router.get("/batch")
def get_fund_traction_batch(
    symbols: str = Query("", description="Comma-separated list of symbols"),
):
    """Return fund traction data for the given symbols (latest month per symbol)."""
    if not symbols.strip():
        return JSONResponse(status_code=400, content={"error": "No symbols provided."})

    symbol_list = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    if not symbol_list:
        return JSONResponse(status_code=400, content={"error": "No valid symbols."})

    db_path = _get_db_path()
    if not os.path.exists(db_path):
        return JSONResponse(status_code=503, content={"error": "Database not available."})

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        latest_row = conn.execute("SELECT MAX(month) as m FROM fund_traction").fetchone()
        latest_month = latest_row["m"] if latest_row else None
        if not latest_month:
            return {"latest_month": None, "symbols": {s: None for s in symbol_list}}

        ph = ",".join("?" for _ in symbol_list)
        query = f"""
            SELECT ft.symbol, ft.month, ft.traction_score, ft.number_of_funds,
                   ft.adds_new, ft.reduces_closes, ft.sma_30, ft.month_end_close,
                   ft.close_latest, ft.pct_vs_sma
            FROM fund_traction ft
            INNER JOIN (
                SELECT symbol, MAX(month) as max_month
                FROM fund_traction WHERE symbol IN ({ph}) GROUP BY symbol
            ) latest ON ft.symbol = latest.symbol AND ft.month = latest.max_month
            WHERE ft.symbol IN ({ph})
            ORDER BY ft.traction_score DESC
        """
        rows = conn.execute(query, symbol_list + symbol_list).fetchall()
        result_map = {r["symbol"]: dict(r) for r in rows}
        return {
            "latest_month": latest_month,
            "symbols": {s: result_map.get(s) for s in symbol_list},
        }
    except Exception as e:
        logger.exception("Batch query failed")
        return JSONResponse(status_code=500, content={"error": str(e)})
    finally:
        conn.close()


@router.get("/months")
def get_available_months():
    """Return list of available months in fund_traction, newest first."""
    db_path = _get_db_path()
    if not os.path.exists(db_path):
        return {"months": []}
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT DISTINCT month FROM fund_traction ORDER BY month DESC"
        ).fetchall()
        return {"months": [r[0] for r in rows]}
    except Exception:
        return {"months": []}
    finally:
        conn.close()


@router.get("/scanner")
def fund_traction_scanner(
    limit: int = Query(100, ge=1, le=500),
    month: str = Query(""),
    min_score: float = Query(0),
    max_score: float = Query(0),
    min_fund_count: int = Query(0),
    max_fund_count: int = Query(0),
    min_add_count: int = Query(0),
    sector: str = Query(""),
    market_cap_min: float = Query(0),
    market_cap_max: float = Query(0),
    nifty500: int = Query(0, description="If 1, only include Nifty500 constituents"),
    min_roe: float = Query(0),
    min_net_margin: float = Query(0),
    min_momentum: float = Query(0, description="Min month-over-month traction score change"),
):
    """Advanced fund traction scanner with fundamentals join."""
    db_path = _get_db_path()
    if not os.path.exists(db_path):
        return JSONResponse(status_code=503, content={"error": "Database not available."})

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        target_month = str(month).strip() if month else ""
        if not target_month:
            row = conn.execute("SELECT MAX(month) as m FROM fund_traction").fetchone()
            target_month = row["m"] if row and row["m"] else ""
            if not target_month:
                return {"month": None, "stocks": [], "total": 0, "summary": {}}

        conds = ["ft.month = ?"]
        params: list = [target_month]

        if min_score > 0:
            conds.append("ft.traction_score >= ?")
            params.append(min_score)
        if max_score > 0:
            conds.append("ft.traction_score <= ?")
            params.append(max_score)
        if min_fund_count > 0:
            conds.append("ft.number_of_funds >= ?")
            params.append(min_fund_count)
        if max_fund_count > 0:
            conds.append("ft.number_of_funds <= ?")
            params.append(max_fund_count)
        if min_add_count > 0:
            conds.append("ft.adds_new >= ?")
            params.append(min_add_count)
        if sector.strip():
            slist = [s.strip() for s in sector.split(",") if s.strip()]
            if slist:
                conds.append(f"f.sector IN ({','.join('?' for _ in slist)})")
                params.extend(slist)
        if market_cap_min > 0:
            conds.append("f.market_cap >= ?")
            params.append(market_cap_min)
        if market_cap_max > 0:
            conds.append("f.market_cap <= ?")
            params.append(market_cap_max)
        if min_roe > 0:
            conds.append("f.roe >= ?")
            params.append(min_roe)
        if min_net_margin > 0:
            conds.append("f.net_margin >= ?")
            params.append(min_net_margin)

        # Nifty500 filter — join against index_constituents in myra_metadata.db
        nifty500_join = ""
        if nifty500:
            meta_db = os.path.join(DB_DIR, "myra_metadata.db")
            if os.path.exists(meta_db):
                nifty500_join = "INNER JOIN index_constituents ic ON ic.symbol = ft.symbol AND ic.index_name = 'NIFTY 500'"
                conds.append("1=1")  # placeholder removed below, just needs a valid condition
            else:
                logger.warning("Nifty500 filter requested but myra_metadata.db not found")

        where = " AND ".join(c for c in conds if c != "1=1") if nifty500_join else " AND ".join(conds)

        # Momentum subquery: get previous month's score for each symbol
        momentum_sub = """
            LEFT JOIN (
                SELECT ft2.symbol,
                       ft2.traction_score AS prev_score,
                       ft2.month AS prev_month
                FROM fund_traction ft2
                INNER JOIN (
                    SELECT symbol, MAX(month) AS prev_max
                    FROM fund_traction
                    WHERE month < ?
                    GROUP BY symbol
                ) pm ON ft2.symbol = pm.symbol AND ft2.month = pm.prev_max
            ) prev ON prev.symbol = ft.symbol
        """
        params_with_prev = [target_month] + params  # prepend target_month for prev subquery

        query = f"""
            SELECT ft.symbol, ft.month, ft.traction_score, ft.number_of_funds,
                   ft.adds_new, ft.reduces_closes, ft.sma_30, ft.month_end_close,
                   ft.close_latest, ft.pct_vs_sma,
                   f.market_cap, f.sector, f.roe, f.net_margin, f.pe,
                   f.promoter_holding_pct, f.free_float_pct,
                   prev.prev_score, prev.prev_month,
                   ROUND(
                       COALESCE(f.roe, 0) * 0.4
                       + COALESCE(f.net_margin, 0) * 0.3
                       + (100 - MIN(COALESCE(f.pe, 100), 100)) * 0.3, 2
                   ) AS quality_score
            FROM fund_traction ft
            LEFT JOIN fundamentals f ON ft.symbol = f.symbol
            {momentum_sub}
            {nifty500_join}
            WHERE {where}
            ORDER BY ft.traction_score DESC
            LIMIT ?
        """
        params_with_prev.append(limit)
        rows = conn.execute(query, params_with_prev).fetchall()

        count_q = f"""
            SELECT COUNT(*) as cnt FROM fund_traction ft
            LEFT JOIN fundamentals f ON ft.symbol = f.symbol
            {momentum_sub}
            WHERE {where}
        """
        total = conn.execute(count_q, params_with_prev[:-1]).fetchone()["cnt"]

        stocks = []
        for r in rows:
            adds = r["adds_new"]
            reduces = r["reduces_closes"]
            score = _safe_float(r["traction_score"])
            prev_score = _safe_float(r["prev_score"])
            momentum = round(score - prev_score, 2) if score is not None and prev_score is not None else None

            # Apply min_momentum filter (server-side since it's a post-compute field)
            if min_momentum > 0 and (momentum is None or momentum < min_momentum):
                continue

            stocks.append({
                "symbol": r["symbol"],
                "month": r["month"],
                "traction_score": score,
                "fund_count": r["number_of_funds"],
                "adds_new": r["adds_new"],
                "reduces_closes": r["reduces_closes"],
                "net_adds": (adds or 0) - (reduces or 0),
                "momentum": momentum,
                "prev_month": r["prev_month"],
                "sma_30": _safe_float(r["sma_30"]),
                "month_end_close": _safe_float(r["month_end_close"]),
                "close_latest": _safe_float(r["close_latest"]),
                "pct_vs_sma": _safe_float(r["pct_vs_sma"]),
                "market_cap": _safe_float(r["market_cap"]),
                "sector": r["sector"] or None,
                "roe": _safe_float(r["roe"]),
                "net_margin": _safe_float(r["net_margin"]),
                "pe": _safe_float(r["pe"]),
                "promoter_holding_pct": _safe_float(r["promoter_holding_pct"]),
                "free_float_pct": _safe_float(r["free_float_pct"]),
                "quality_score": _safe_float(r["quality_score"]),
            })

        # Summary
        scores = [s["traction_score"] for s in stocks if s["traction_score"] is not None]
        fcounts = [s["fund_count"] for s in stocks if s["fund_count"] is not None]
        sector_counter = Counter(s["sector"] for s in stocks if s.get("sector"))
        top_sectors = [{"sector": sec, "count": cnt} for sec, cnt in sector_counter.most_common(5)]

        cap_dist = {"small": 0, "mid": 0, "large": 0, "unknown": 0}
        for s in stocks:
            mc = s.get("market_cap")
            if mc is None:
                cap_dist["unknown"] += 1
            elif mc < 5e10:       # < 5000 Cr
                cap_dist["small"] += 1
            elif mc < 2e11:       # < 20000 Cr
                cap_dist["mid"] += 1
            else:
                cap_dist["large"] += 1

        total_adds = sum(s["adds_new"] or 0 for s in stocks)
        total_reduces = sum(s["reduces_closes"] or 0 for s in stocks)

        summary = {
            "avg_score": round(sum(scores) / len(scores), 2) if scores else 0,
            "avg_fund_count": round(sum(fcounts) / len(fcounts), 1) if fcounts else 0,
            "total_adds": total_adds,
            "total_reduces": total_reduces,
            "total_net_adds": total_adds - total_reduces,
            "top_sectors": top_sectors,
            "cap_distribution": cap_dist,
        }

        return {"month": target_month, "stocks": stocks, "total": len(stocks), "summary": summary}

    except Exception as e:
        logger.exception("Scanner query failed")
        return JSONResponse(status_code=500, content={"error": str(e)})
    finally:
        conn.close()
