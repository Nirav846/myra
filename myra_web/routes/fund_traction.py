"""
Fund Traction API endpoints.
- /batch: returns traction data for a list of symbols
- /scanner: advanced scanner with filters, joins fundamentals
- /months: list of available months
"""

import json
import logging
import os
import sqlite3
from collections import Counter

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from myra_app.constants import DB_DIR, MODELS_DIR

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/fund-traction", tags=["fund-traction"])


# Weights are the pre-existing policy and are unchanged: ROE 0.4, P/E 0.3.
# Only *which* components are allowed to contribute changed (Phase 12).
_QUALITY_WEIGHT_ROE = 0.4
_QUALITY_WEIGHT_PE = 0.3


def quality_score_sql(alias: str = "f") -> str:
    """SQL for the fund-traction quality score, with explicit missing-data rules.

    The previous expression was::

        ROUND(COALESCE(roe,0)*0.4 + COALESCE(net_margin,0)*0.3
              + (100 - MIN(COALESCE(pe,100),100))*0.3, 2)

    which had three defects, all reproduced against the live database:

    1. **Absent inputs silently scored zero.** The weights were never
       renormalised over the inputs that actually existed, so a stock with no
       ROE (2,769 of 3,246 rows with a market cap have none) lost the whole
       0.4 and a stock with none of the three could reach at most 0.3. Being
       unmeasured was therefore a de facto penalty.

    2. **Negative P/E inverted the meaning of the metric.** ``MIN(pe, 100)``
       capped the cheap end but had no floor, so a loss-making company
       (pe = -341.7, 14 such rows) received ``100 - (-341.7) = 441.7`` points -
       the *largest* score in the data, for the worst possible quality signal.

    3. **Mixed-scale ``net_margin`` swamped everything.** Observed values run
       from ~0.13 (fraction) to -26,570 (percent or corrupt); at 0.3 weight a
       single bad row contributed -7,971, overwhelming every other term.

    Corrected rules:

    * a component contributes only when its input is actually present;
    * a negative or zero P/E means "no positive earnings", i.e. an unavailable
      cheapness signal - it is *not* treated as maximum cheapness;
    * the cheapness term is clamped to [0, 100] so it can never run away;
    * ``net_margin`` is excluded outright because its scale is unresolved (see
      docs/PHASE12_MISSING_DATA_DECISION_SAFETY.md). Normalising it by guessing
      a per-row unit is exactly what this phase must not do, but continuing to
      rank on an incomparable column is worse. It is deferred to a data-repair
      phase, not silently guessed here;
    * the result is scaled so a row that has **every** component scores exactly
      what the old expression gave it. The renormalising factor is
      ``total_weight / present_weight``, so an absent input is redistributed
      across the inputs that exist instead of subtracting points, while
      well-measured rows keep their established magnitude and their existing
      UI bands (0-40 / 40-70 / 70+);
    * when no component is available the score is ``NULL`` - unavailable, not 0.

    The relative 4:3 emphasis between ROE and P/E is preserved.
    """
    roe = f"{alias}.roe"
    pe = f"{alias}.pe"
    roe_num = (
        f"CASE WHEN {roe} IS NOT NULL THEN {roe} * {_QUALITY_WEIGHT_ROE} ELSE 0 END"
    )
    roe_w = f"CASE WHEN {roe} IS NOT NULL THEN {_QUALITY_WEIGHT_ROE} ELSE 0 END"
    # A P/E that is absent, zero or negative carries no cheapness information.
    pe_num = (
        f"CASE WHEN {pe} IS NOT NULL AND {pe} > 0 "
        f"THEN (100 - MAX(0, MIN({pe}, 100))) * {_QUALITY_WEIGHT_PE} ELSE 0 END"
    )
    pe_w = (
        f"CASE WHEN {pe} IS NOT NULL AND {pe} > 0 THEN {_QUALITY_WEIGHT_PE} ELSE 0 END"
    )
    total_w = _QUALITY_WEIGHT_ROE + _QUALITY_WEIGHT_PE
    return f"ROUND(({roe_num} + {pe_num}) * {total_w} / NULLIF({roe_w} + {pe_w}, 0), 2)"


def _get_db_path() -> str:
    return os.path.join(DB_DIR, "myra_valuation.db")


def _safe_float(val, default=None):
    if val is None:
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def _load_dcb_discounts() -> dict[str, float]:
    """Load DCB discount_pct from the scanner cache. Returns {symbol: discount_pct}."""
    cache_path = os.path.join(MODELS_DIR, "dcb_bargain_cache.json")
    if not os.path.exists(cache_path):
        return {}
    try:
        with open(cache_path) as f:
            data = json.load(f)
        candidates = data.get("candidates", [])
        return {
            c["symbol"]: c["discount_pct"]
            for c in candidates
            if c.get("discount_pct") is not None
        }
    except Exception as e:
        logger.debug("DCB cache load for discounts failed: %s", e)
        return {}


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
        return JSONResponse(
            status_code=503, content={"error": "Database not available."}
        )

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        latest_row = conn.execute(
            "SELECT MAX(month) as m FROM fund_traction"
        ).fetchone()
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

        # Enrich with DCB discount from scanner cache
        dcb_map = _load_dcb_discounts()
        for sym, entry in result_map.items():
            if entry is not None and sym in dcb_map:
                entry["dcb_discount"] = round(dcb_map[sym], 2)

        return {
            "latest_month": latest_month,
            "symbols": {s: result_map.get(s) for s in symbol_list},
        }
    except Exception:
        logger.exception("Batch query failed")
        return JSONResponse(status_code=500, content={"error": "Internal server error"})
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
    min_momentum: float = Query(
        0, description="Min month-over-month traction score change"
    ),
):
    """Advanced fund traction scanner with fundamentals join."""
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
                conds.append(
                    "1=1"
                )  # placeholder removed below, just needs a valid condition
            else:
                logger.warning(
                    "Nifty500 filter requested but myra_metadata.db not found"
                )

        where = (
            " AND ".join(c for c in conds if c != "1=1")
            if nifty500_join
            else " AND ".join(conds)
        )

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
        params_with_prev = [
            target_month
        ] + params  # prepend target_month for prev subquery

        query = f"""
            SELECT ft.symbol, ft.month, ft.traction_score, ft.number_of_funds,
                   ft.adds_new, ft.reduces_closes, ft.sma_30, ft.month_end_close,
                   ft.close_latest, ft.pct_vs_sma,
                   f.market_cap, f.sector, f.roe, f.net_margin, f.pe,
                   f.promoter_holding_pct, f.free_float_pct,
                   prev.prev_score, prev.prev_month,
                   {quality_score_sql("f")} AS quality_score
            FROM fund_traction ft
            LEFT JOIN fundamentals f
                ON f.symbol = COALESCE(NULLIF(ft.nse, ''), ft.symbol)
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
            LEFT JOIN fundamentals f ON f.symbol = COALESCE(NULLIF(ft.nse, ''), ft.symbol)
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
            momentum = (
                round(score - prev_score, 2)
                if score is not None and prev_score is not None
                else None
            )

            # Apply min_momentum filter (server-side since it's a post-compute field)
            if min_momentum > 0 and (momentum is None or momentum < min_momentum):
                continue

            stocks.append(
                {
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
                }
            )

        # Summary
        scores = [
            s["traction_score"] for s in stocks if s["traction_score"] is not None
        ]
        fcounts = [s["fund_count"] for s in stocks if s["fund_count"] is not None]
        sector_counter = Counter(s["sector"] for s in stocks if s.get("sector"))
        top_sectors = [
            {"sector": sec, "count": cnt} for sec, cnt in sector_counter.most_common(5)
        ]

        cap_dist = {"small": 0, "mid": 0, "large": 0, "unknown": 0}
        for s in stocks:
            mc = s.get("market_cap")
            if mc is None:
                cap_dist["unknown"] += 1
            elif mc < 5e10:  # < 5000 Cr
                cap_dist["small"] += 1
            elif mc < 2e11:  # < 20000 Cr
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

        return {
            "month": target_month,
            "stocks": stocks,
            "total": len(stocks),
            "summary": summary,
        }

    except Exception:
        logger.exception("Scanner query failed")
        return JSONResponse(status_code=500, content={"error": "Internal server error"})
    finally:
        conn.close()


# ── Traction Board (Pages-parity) ───────────────────────────────────────────
# Additive endpoints; they do not touch /batch or /scanner. They delegate all
# data shaping to fund_traction_sync's read API so the DB stays the single
# source of truth (no re-reading upstream JSON at request time).


@router.get("/board")
def get_traction_board(
    month: str | None = Query(None, description="YYYY-MM; defaults to newest"),
    filter: str = Query("all", description="One of fund_traction_sync.BOARD_FILTERS"),
    sort: str = Query(
        "score", description="score|funds|share_change|weight_delta|name|pct_vs_sma"
    ),
    search: str = Query("", description="Match name/symbol/NSE/sector"),
    include_funds: bool = Query(True, description="Include per-fund breakdown lines"),
    mcap_bucket: str = Query(
        "", description="Market cap bucket: large|mid|small|unknown"
    ),
):
    """Traction Board payload: month tabs, filters, per-fund cards, stats."""
    from myra_app import fund_traction_sync as fts

    return fts.get_traction_board(
        month=month,
        board_filter=filter,
        sort_by=sort,
        search=search,
        include_funds=include_funds,
        mcap_bucket=mcap_bucket,
    )


@router.get("/board/insights")
def get_traction_board_insights(
    month: str | None = Query(None, description="YYYY-MM; defaults to newest"),
):
    """Insights + top-traction panel for one month."""
    from myra_app import fund_traction_sync as fts

    return fts.get_traction_insights(month=month)


@router.get("/board/intelligence")
def get_traction_board_intelligence(
    month: str | None = Query(None, description="YYYY-MM; defaults to newest"),
):
    """Month-over-month cohorts + delivery-weighted entry price per symbol."""
    from myra_app import traction_intelligence as ti

    return ti.get_traction_intelligence(month=month)


@router.get("/board/watchlist")
def get_traction_board_watchlist():
    """Pinned stock_keys."""
    from myra_app import fund_traction_sync as fts

    conn = sqlite3.connect(_get_db_path())
    try:
        pins = sorted(fts.get_watchlist(conn))
        return {"success": True, "watchlist": pins, "count": len(pins)}
    finally:
        conn.close()


@router.post("/board/watchlist/pin")
def pin_traction_watchlist(payload: dict):
    """Pin a stock. Body: {stock_key, name?, nse?, source?}."""
    from myra_app import fund_traction_sync as fts

    return fts.pin_watchlist(
        stock_key=payload.get("stock_key", ""),
        name=payload.get("name", ""),
        nse=payload.get("nse", ""),
        source=payload.get("source", "manual"),
    )


@router.post("/board/watchlist/unpin")
def unpin_traction_watchlist(payload: dict):
    """Unpin a stock. Body: {stock_key}."""
    from myra_app import fund_traction_sync as fts

    return fts.unpin_watchlist(stock_key=payload.get("stock_key", ""))
