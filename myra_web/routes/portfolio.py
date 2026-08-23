import json
import logging
import os
import sqlite3
import time as _time
from datetime import datetime

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from myra_app.constants import DB_DIR
from myra_app.librarian_core import LibrarianCore
from myra_web.background import _spawn_task

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/portfolio", tags=["portfolio"])

@router.post("/refresh")
async def refresh_portfolio():
    """Trigger a manual refresh of portfolio prices and fundamentals (async)."""

    def _run():
        from myra_app.portfolio_db import auto_refresh_portfolio

        return auto_refresh_portfolio()

    try:
        tid = _spawn_task("portfolio_refresh", _run)
        return JSONResponse(
            status_code=202, content={"status": "started", "task_id": tid}
        )
    except Exception as e:
        logger.exception("portfolio refresh failed")
        return JSONResponse(
            status_code=500, content={"status": "error", "message": "Internal server error"}
        )


@router.get("/live-prices")
async def get_live_prices():
    """Fetch live intraday prices from yfinance for all portfolio holdings.
    Cached for 5 minutes in live_price_cache table."""
    import yfinance as yf
    import time as _time

    try:
        from myra_app.portfolio_db import get_all_holdings, get_db_path
    except ImportError:
        return {"status": "error", "message": "portfolio module not available"}

    try:
        holdings = get_all_holdings()
    except Exception:
        return {"status": "error", "message": "Failed to read holdings"}

    if not holdings:
        return {"status": "ok", "prices": {}, "message": "No holdings in portfolio."}

    PORTFOLIO_DB = get_db_path()
    symbols = [h["symbol"] for h in holdings]

    # Create live_price_cache table if not exists
    try:
        lc = sqlite3.connect(PORTFOLIO_DB)
        lc.execute(
            """CREATE TABLE IF NOT EXISTS live_price_cache (
                 symbol TEXT PRIMARY KEY,
                 ltp REAL,
                 change REAL,
                 change_pct REAL,
                 previous_close REAL,
                 fetched_at TEXT DEFAULT (datetime('now','localtime'))
             )"""
        )
        lc.commit()
    except Exception:
        pass

    # Check cache freshness (5 min TTL)
    now = _time.time()
    use_cache = True
    try:
        cached_count = lc.execute("SELECT COUNT(*) FROM live_price_cache").fetchone()[0]
        if cached_count > 0:
            first = lc.execute(
                "SELECT fetched_at FROM live_price_cache LIMIT 1"
            ).fetchone()[0]
            if first:
                try:
                    cached_time = _time.mktime(
                        _time.strptime(first, "%Y-%m-%d %H:%M:%S")
                    )
                    if (now - cached_time) < 300:
                        # Return cached data
                        lc.row_factory = sqlite3.Row
                        rows = lc.execute("SELECT * FROM live_price_cache").fetchall()
                        prices = {}
                        for r in rows:
                            prices[r["symbol"]] = {
                                "ltp": r["ltp"],
                                "change": r["change"],
                                "change_pct": r["change_pct"],
                                "previous_close": r["previous_close"],
                                "fetched_at": r["fetched_at"],
                                "cached": True,
                            }
                        lc.close()
                        return {"status": "ok", "prices": prices, "source": "cache"}
                except Exception:
                    pass
        lc.close()
    except Exception:
        pass

    # Fetch from yfinance
    prices = {}
    warnings = []
    for sym in symbols:
        try:
            ticker = yf.Ticker(f"{sym}.NS")
            info = ticker.info
            ltp = info.get("currentPrice") or info.get("regularMarketPrice")
            prev_close = info.get("previousClose") or info.get(
                "regularMarketPreviousClose"
            )
            change = info.get("regularMarketChange")
            change_pct = info.get("regularMarketChangePercent")
            if ltp is None:
                warnings.append(f"{sym}: no live price available")
                continue
            prices[sym] = {
                "ltp": ltp,
                "change": change,
                "change_pct": change_pct,
                "previous_close": prev_close,
                "fetched_at": datetime.now().strftime("%H:%M:%S"),
                "cached": False,
            }
            _time.sleep(0.2)
        except Exception:
            warnings.append(f"{sym}: price unavailable")
            continue

    # Cache results
    if prices:
        try:
            lc = sqlite3.connect(PORTFOLIO_DB)
            for sym, p in prices.items():
                lc.execute(
                    """INSERT OR REPLACE INTO live_price_cache
                       (symbol, ltp, change, change_pct, previous_close, fetched_at)
                       VALUES (?, ?, ?, ?, ?, datetime('now','localtime'))""",
                    (sym, p["ltp"], p["change"], p["change_pct"], p["previous_close"]),
                )
            lc.commit()
            lc.close()
        except Exception:
            pass

    if not prices:
        return {
            "status": "error",
            "message": "Could not fetch any live prices",
            "warnings": warnings,
        }

    return {
        "status": "ok",
        "prices": prices,
        "source": "yfinance",
        "warnings": warnings if warnings else None,
    }


@router.get("")
async def get_portfolio():
    """Returns full portfolio data: holdings, summary, sector allocation,
    scanner overlap, alerts, risk metrics, and freshness."""
    try:
        from myra_app.portfolio_db import (
            get_all_holdings,
            get_delivery_metrics,
            get_technical_position,
            get_sector_allocation as _get_sector_allocation,
            get_scanner_overlap as _get_scanner_overlap,
            get_delivery_alerts as _get_delivery_alerts,
            get_concentration_risk as _get_concentration_risk,
            get_drawdown_metrics as _get_drawdown_metrics,
            get_diversification_score as _get_diversification_score,
            _get_portfolio_meta,
            get_db_path,
        )
    except ImportError:
        return {"status": "error", "message": "portfolio module not available"}

    try:
        holdings = get_all_holdings()
    except Exception:
        return {"status": "error", "message": "Failed to read holdings"}

    if not holdings:
        return {
            "status": "empty",
            "message": "No portfolio data. Import your broker XLSX first: python tools/portfolio.py import <file>",
        }

    PORTFOLIO_DB = get_db_path()
    total_invested = 0.0
    total_current = 0.0
    total_day_pnl = 0.0
    enriched = []
    symbols = [h["symbol"] for h in holdings]

    price_map = {}
    prev_price_map = {}
    try:
        if os.path.exists(PORTFOLIO_DB):
            pc = sqlite3.connect(PORTFOLIO_DB)
            pc.row_factory = sqlite3.Row
            for row in pc.execute(
                "SELECT symbol, latest_close, previous_close, latest_date FROM price_data"
            ).fetchall():
                price_map[row["symbol"]] = row["latest_close"]
                prev_price_map[row["symbol"]] = row["previous_close"]
            pc.close()
    except Exception:
        pass

    funda_map = {}
    try:
        if os.path.exists(PORTFOLIO_DB):
            fc = sqlite3.connect(PORTFOLIO_DB)
            fc.row_factory = sqlite3.Row
            for row in fc.execute(
                "SELECT symbol, pe, sector FROM fundamental_data"
            ).fetchall():
                funda_map[row["symbol"]] = {
                    "pe": row["pe"],
                    "sector": row["sector"],
                }
            fc.close()
    except Exception:
        pass

    val_funda_map = {}
    VAL_DB = os.path.join(DB_DIR, LibrarianCore.DB_MAP["valuation"])
    if os.path.exists(VAL_DB):
        try:
            vc = sqlite3.connect(VAL_DB)
            vc.row_factory = sqlite3.Row
            placeholders = ",".join("?" for _ in symbols)
            for row in vc.execute(
                f"""SELECT symbol, pe, operatingMargin, grossMargin,
                            freeCashFlowYield, currentRatio, quickRatio,
                            payoutRatio, beta, promoter_holding_pct,
                            sector, market_cap
                     FROM fundamentals WHERE symbol IN ({placeholders})""",
                symbols,
            ).fetchall():
                val_funda_map[row["symbol"]] = dict(row)
            vc.close()
        except Exception:
            pass

    def compute_myra_quality_score(f):
        score = 1
        if f.get("operatingMargin") and f["operatingMargin"] > 0.15:
            score += 1
        if f.get("freeCashFlowYield") and f["freeCashFlowYield"] > 0.05:
            score += 1
        if f.get("promoter_holding_pct") and f["promoter_holding_pct"] > 50:
            score += 1
        if f.get("pe") and 0 < f["pe"] < 20:
            score += 1
        if f.get("currentRatio") and f["currentRatio"] > 1.5:
            score += 1
        return min(score, 5)

    FUNDA_FIELDS = [
        "operatingMargin",
        "grossMargin",
        "freeCashFlowYield",
        "currentRatio",
        "quickRatio",
        "payoutRatio",
        "beta",
        "promoter_holding_pct",
        "market_cap",
    ]

    for h in holdings:
        sym = h["symbol"]
        qty = h.get("net_qty", 0)
        avg = h.get("avg_price", 0)
        invested = qty * avg
        ltp = price_map.get(sym)
        current_value = qty * ltp if ltp else 0
        prev_close = prev_price_map.get(sym)
        day_change = (ltp - prev_close) if ltp and prev_close else 0
        day_pnl = qty * day_change
        overall_pnl = current_value - invested
        overall_pnl_pct = round((overall_pnl / invested * 100), 2) if invested else 0

        delivery = {}
        try:
            delivery = get_delivery_metrics(sym) or {}
        except Exception:
            pass

        tech_pos = {}
        try:
            tech_pos = get_technical_position(sym) or {}
        except Exception:
            pass

        funda = funda_map.get(sym, {})
        vf = val_funda_map.get(sym, {})

        morningstar_rating = compute_myra_quality_score(vf)
        morningstar_fields_available = sum(
            1 for f in FUNDA_FIELDS if vf.get(f) is not None
        )

        total_invested += invested
        total_current += current_value
        total_day_pnl += day_pnl

        enriched.append(
            {
                "symbol": sym,
                "category": h.get("category", "NSE EQ"),
                "net_qty": qty,
                "avg_price": round(avg, 2),
                "ltp": ltp,
                "current_value": round(current_value, 2),
                "current": round(current_value, 2),
                "overall_pnl": round(overall_pnl, 2),
                "overall_pnl_pct": overall_pnl_pct,
                "day_pnl": round(day_pnl, 2),
                "day_pnl_pct": (
                    round((day_pnl / (current_value - day_pnl) * 100), 2)
                    if (current_value - day_pnl)
                    else 0
                ),
                "delivery_pct": delivery.get("del_pct"),
                "delivery_trend": delivery.get("del_trend", "\u2014"),
                "vs_sma50_pct": tech_pos.get("vs_sma_pct"),
                "vs_52w_high_pct": tech_pos.get("vs_52w_high_pct"),
                "pe": funda.get("pe") or vf.get("pe"),
                "sector": funda.get("sector") or vf.get("sector") or "Other",
                "alert": None,
                "operating_margin": vf.get("operatingMargin"),
                "gross_margin": vf.get("grossMargin"),
                "free_cash_flow_yield": vf.get("freeCashFlowYield"),
                "current_ratio": vf.get("currentRatio"),
                "quick_ratio": vf.get("quickRatio"),
                "payout_ratio": vf.get("payoutRatio"),
                "promoter_holding": vf.get("promoter_holding_pct"),
                "market_cap": vf.get("market_cap"),
                "beta": vf.get("beta"),
                "morningstar_rating": morningstar_rating,
                "morningstar_fields_available": morningstar_fields_available,
            }
        )

    # Enrich with industry data from cache (yfinance)
    try:
        from myra_app.portfolio_db import get_cached_industries, refresh_industry_cache

        industry_data = get_cached_industries(symbols)
        missing = [s for s in symbols if s not in industry_data]
        if missing:
            logger.info(
                f"Fetching industry data for {len(missing)} symbols from yfinance"
            )
            fresh = refresh_industry_cache(missing)
            industry_data.update(fresh)
        for h in enriched:
            sym = h["symbol"]
            ind = industry_data.get(sym, {})
            h["industry"] = ind.get("industry")
            h["yf_sector"] = ind.get("yf_sector")
    except Exception as e:
        logger.warning(f"Industry enrichment failed: {e}")

    total_day_pnl_pct = (
        round((total_day_pnl / (total_current - total_day_pnl) * 100), 2)
        if (total_current - total_day_pnl)
        else 0
    )
    overall_pnl = total_current - total_invested
    overall_pnl_pct = (
        round((overall_pnl / total_invested * 100), 2) if total_invested else 0
    )

    summary = {
        "total_invested": round(total_invested, 2),
        "total_current": round(total_current, 2),
        "overall_pnl": round(overall_pnl, 2),
        "overall_pnl_pct": round(overall_pnl_pct, 2),
        "day_pnl": round(total_day_pnl, 2),
        "day_pnl_pct": round(total_day_pnl_pct, 2),
        "holdings_count": len(enriched),
        "last_refresh": _get_portfolio_meta("last_refresh") or "Not refreshed yet",
    }

    sector_allocation = []
    try:
        sector_allocation = _get_sector_allocation(enriched) or []
    except Exception:
        pass

    scanner_overlap = {}
    try:
        scanner_overlap = _get_scanner_overlap(enriched) or []
    except Exception:
        pass

    alerts = []
    try:
        alerts = _get_delivery_alerts(enriched) or []
    except Exception:
        pass

    concentration = {}
    try:
        concentration = _get_concentration_risk() or {}
    except Exception:
        pass

    drawdown = {}
    try:
        drawdown = _get_drawdown_metrics() or {}
    except Exception:
        pass

    diversification = {}
    try:
        diversification = _get_diversification_score() or {}
    except Exception:
        pass

    risk = {
        "concentration": {
            "top3_pct": concentration.get("top3_pct", 0),
            "holdings": concentration.get("top3_holdings", []),
        },
        "drawdown": {
            "peak_value": drawdown.get("peak_value", 0),
            "peak_date": drawdown.get("peak_date", ""),
            "current_value": drawdown.get("current_value", 0),
            "drawdown_pct": drawdown.get("drawdown_pct", 0),
            "days_from_peak": drawdown.get("days_from_peak", 0),
        },
        "diversification_score": diversification.get("score", 0),
        "diversification_rating": diversification.get("rating", ""),
    }

    _prices_from = _get_portfolio_meta("prices_updated_at")
    _funds_cached = _get_portfolio_meta("funds_updated_at")
    if not _prices_from or _prices_from == "unknown":
        try:
            pc = sqlite3.connect(PORTFOLIO_DB)
            row = pc.execute("SELECT MAX(latest_date) FROM price_data").fetchone()
            _prices_from = row[0] if row and row[0] else "unknown"
            pc.close()
        except Exception:
            _prices_from = "unknown"
    if not _funds_cached or _funds_cached == "unknown":
        try:
            fc = sqlite3.connect(PORTFOLIO_DB)
            row = fc.execute("SELECT MAX(fetched_at) FROM fundamental_data").fetchone()
            _funds_cached = row[0] if row and row[0] else "unknown"
            fc.close()
        except Exception:
            _funds_cached = "unknown"

    freshness = {
        "prices_from": _prices_from,
        "fundamentals_cached": _funds_cached,
        "fundamentals_coverage_pct": round(
            sum(1 for h in enriched if h.get("pe")) / max(len(enriched), 1) * 100
        ),
    }

    return {
        "status": "ok",
        "summary": summary,
        "holdings": enriched,
        "sector_allocation": sector_allocation,
        "scanner_overlap": scanner_overlap,
        "alerts": alerts,
        "risk": risk,
        "freshness": freshness,
    }


@router.get("/benchmark")
async def get_portfolio_benchmark():
    """Compare portfolio returns vs Nifty benchmark using snapshot history."""
    from myra_app.portfolio_db import get_snapshots, get_db_path
    import sqlite3
    import os

    # Get portfolio snapshots
    snapshots = get_snapshots(limit=250)  # ~1 year of trading days
    if len(snapshots) < 2:
        return {
            "status": "ok",
            "benchmark": {
                "portfolio_return": 0,
                "nifty_return": 0,
                "alpha": 0,
                "message": "Not enough snapshot data for comparison",
            },
        }

    # Portfolio return from first to latest snapshot
    first = snapshots[-1]
    last = snapshots[0]
    portfolio_return = (
        ((last["total_current"] - first["total_current"]) / first["total_current"])
        * 100
        if first["total_current"]
        else 0
    )

    # Nifty benchmark from myra_metadata.db (benchmarks table, symbol ^NSEI)
    nifty_return = 0
    meta_db = os.path.join(DB_DIR, "myra_metadata.db")
    if os.path.exists(meta_db):
        try:
            mc = sqlite3.connect(meta_db)
            mc.row_factory = sqlite3.Row
            first_date = first["date"]
            last_date = last["date"]
            first_close = mc.execute(
                "SELECT close FROM benchmarks WHERE symbol=? AND date <= ? ORDER BY date DESC LIMIT 1",
                ("^NSEI", first_date),
            ).fetchone()
            last_close = mc.execute(
                "SELECT close FROM benchmarks WHERE symbol=? AND date <= ? ORDER BY date DESC LIMIT 1",
                ("^NSEI", last_date),
            ).fetchone()
            fc = first_close["close"] if first_close else None
            lc = last_close["close"] if last_close else None
            if fc and lc and fc > 0:
                nifty_return = ((lc - fc) / fc) * 100
            mc.close()
        except Exception:
            pass

    alpha = portfolio_return - nifty_return
    return {
        "status": "ok",
        "benchmark": {
            "portfolio_return": round(portfolio_return, 2),
            "nifty_return": round(nifty_return, 2),
            "alpha": round(alpha, 2),
            "period": f"{first['date']} to {last['date']}",
        },
    }


@router.post("/holdings")
async def add_portfolio_holding(req: Request):
    """Add a new holding or append to existing. Body: {symbol, qty, avg_price, category?}"""
    try:
        body = await req.json()
    except Exception:
        return JSONResponse(
            status_code=400, content={"status": "error", "message": "Invalid JSON body"}
        )
    symbol = body.get("symbol", "").upper().strip()
    qty = body.get("qty")
    avg_price = body.get("avg_price")
    category = body.get("category", "NSE EQ")
    if not symbol or qty is None or avg_price is None:
        return JSONResponse(
            status_code=400,
            content={
                "status": "error",
                "message": "symbol, qty, avg_price are required",
            },
        )
    try:
        qty = int(qty)
        avg_price = float(avg_price)
    except (ValueError, TypeError):
        return JSONResponse(
            status_code=400,
            content={
                "status": "error",
                "message": "qty must be int, avg_price must be number",
            },
        )
    if qty <= 0 or avg_price <= 0:
        return JSONResponse(
            status_code=400,
            content={
                "status": "error",
                "message": "qty and avg_price must be positive",
            },
        )
    if qty <= 0 or avg_price <= 0:
        return JSONResponse(
            status_code=400,
            content={
                "status": "error",
                "message": "qty and avg_price must be positive",
            },
        )
    from myra_app.portfolio_db import add_holding, get_holding

    existing = get_holding(symbol)
    if existing:
        old_qty = existing["net_qty"]
        old_avg = existing["avg_price"]
        new_qty = old_qty + qty
        new_avg = ((old_qty * old_avg) + (qty * avg_price)) / new_qty
        from myra_app.portfolio_db import update_holding

        update_holding(symbol, net_qty=new_qty, avg_price=round(new_avg, 2))
        return {
            "status": "ok",
            "message": f"Added {qty} to {symbol}. New qty: {new_qty}, new avg: \u20b9{new_avg:.2f}",
            "action": "updated",
            "holding": {
                "symbol": symbol,
                "net_qty": new_qty,
                "avg_price": round(new_avg, 2),
            },
        }
    else:
        add_holding(symbol, qty, avg_price, category)
        return {
            "status": "ok",
            "message": f"Added {symbol}: {qty} @ \u20b9{avg_price}",
            "action": "created",
            "holding": {
                "symbol": symbol,
                "net_qty": qty,
                "avg_price": avg_price,
            },
        }


@router.put("/holdings/{symbol}")
async def update_portfolio_holding(symbol: str, req: Request):
    """Update a holding's quantity or average price."""
    try:
        body = await req.json()
    except Exception:
        return JSONResponse(
            status_code=400, content={"status": "error", "message": "Invalid JSON body"}
        )
    if not body:
        return JSONResponse(
            status_code=400,
            content={"status": "error", "message": "No fields to update"},
        )
    kwargs = {}
    if "net_qty" in body:
        try:
            kwargs["net_qty"] = int(body["net_qty"])
            if kwargs["net_qty"] <= 0:
                return JSONResponse(
                    status_code=400,
                    content={
                        "status": "error",
                        "message": "net_qty must be positive",
                    },
                )
        except (ValueError, TypeError):
            return JSONResponse(
                status_code=400,
                content={
                    "status": "error",
                    "message": "net_qty must be an integer",
                },
            )
    if "avg_price" in body:
        try:
            kwargs["avg_price"] = float(body["avg_price"])
            if kwargs["avg_price"] <= 0:
                return JSONResponse(
                    status_code=400,
                    content={
                        "status": "error",
                        "message": "avg_price must be positive",
                    },
                )
        except (ValueError, TypeError):
            return JSONResponse(
                status_code=400,
                content={
                    "status": "error",
                    "message": "avg_price must be a number",
                },
            )
    if not kwargs:
        return JSONResponse(
            status_code=400,
            content={"status": "error", "message": "No valid fields to update"},
        )
    from myra_app.portfolio_db import update_holding, get_holding

    sym = symbol.upper().strip()
    existing = get_holding(sym)
    if not existing:
        return JSONResponse(
            status_code=404,
            content={"status": "error", "message": f"'{sym}' not found in portfolio"},
        )
    update_holding(sym, **kwargs)
    updated = get_holding(sym)
    return {
        "status": "ok",
        "message": f"Updated {sym}",
        "holding": dict(updated),
    }


@router.delete("/holdings/{symbol}")
async def delete_portfolio_holding(symbol: str):
    """Remove a holding."""
    from myra_app.portfolio_db import delete_holding, get_holding

    sym = symbol.upper().strip()
    existing = get_holding(sym)
    if not existing:
        return JSONResponse(
            status_code=404,
            content={"status": "error", "message": f"'{sym}' not found in portfolio"},
        )
    delete_holding(sym)
    return {"status": "ok", "message": f"Removed {sym}"}