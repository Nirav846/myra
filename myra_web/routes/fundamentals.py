"""
MYRA Fundamentals Router — consolidated per-symbol fundamental snapshot.

Combines Screener.in metrics (PBV/ROCE) with valuation fundamentals, latest
technical price data and recent corporate actions into a single JSON payload.

Endpoints:
  GET /api/fundamentals/{symbol}         — cached snapshot from local DBs
  GET /api/fundamentals/live/{symbol}    — live snapshot (Screener.in CLI + DB)
"""

import json
import logging
import os
import sqlite3
import subprocess

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


@router.get("/live/{symbol}")
async def get_live_fundamentals(symbol: str):
    """Return a live fundamental snapshot (Screener.in CLI + valuation DB).

    Moved from myra_fastapi_server.py (Phase 10). Falls back to the local
    valuation DB when the `screener` CLI is unavailable or times out.
    """
    result = {
        "symbol": symbol.upper(),
        "source": "db",
        "fundamentals": {},
        "shareholding": None,
        "key_metrics": {},
        "pros_cons": {"pros": [], "cons": [], "about": ""},
        "ratios": {},
        "peer_comparison": [],
    }

    val_path = os.path.join(DB_DIR, LibrarianCore.DB_MAP["valuation"])
    if os.path.exists(val_path):
        conn = sqlite3.connect(val_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM fundamentals WHERE symbol = ?",
            (symbol.upper(),),
        ).fetchone()
        if row:
            funda = dict(row)
            merged = {
                "symbol": funda.get("symbol"),
                "sector": funda.get("sector"),
                "pe": funda.get("pe") or funda.get("peRatio"),
                "pb": funda.get("priceToBook"),
                "ps": funda.get("priceToSales"),
                "roe": funda.get("roe") or funda.get("returnOnEquity"),
                "eps": funda.get("eps") or funda.get("earningsPerShare"),
                "book_value": funda.get("book_value") or funda.get("bookValuePerShare"),
                "market_cap": funda.get("market_cap") or funda.get("marketCap"),
                "net_margin": funda.get("net_margin") or funda.get("netMargin"),
                "operating_margin": funda.get("operatingMargin"),
                "gross_margin": funda.get("grossMargin"),
                "debt_equity": funda.get("debt_to_equity") or funda.get("debtToEquity"),
                "current_ratio": funda.get("currentRatio"),
                "quick_ratio": funda.get("quickRatio"),
                "dividend_yield": funda.get("dividend_yield")
                or funda.get("dividendYield"),
                "free_cash_flow_yield": funda.get("freeCashFlowYield"),
                "revenue_growth": funda.get("revenueGrowth"),
                "earnings_growth": funda.get("earningsGrowth"),
                "payout_ratio": funda.get("payoutRatio"),
                "beta": funda.get("beta"),
                "source": funda.get("source_ms") or funda.get("source_nse"),
                "date": funda.get("date") or funda.get("last_updated"),
            }
            result["fundamentals"] = merged
        conn.close()

    try:
        proc = subprocess.run(
            ["screener", symbol.upper(), "all"],
            capture_output=True,
            text=True,
            timeout=25,
            encoding="utf-8",
            errors="replace",
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )
        if proc.returncode == 0:
            data = json.loads(proc.stdout)

            sh = data.get("sections", {}).get("shareholding", {})
            latest_sh = sh.get("latest", {})
            if latest_sh:
                result["shareholding"] = {
                    "promoter_pct": latest_sh.get("Promoters"),
                    "fii_pct": latest_sh.get("FIIs"),
                    "dii_pct": latest_sh.get("DIIs"),
                    "public_pct": latest_sh.get("Public"),
                    "government_pct": latest_sh.get("Government"),
                    "period_end": (
                        sh.get("headers", [None])[-1] if sh.get("headers") else None
                    ),
                }

            pc = data.get("sections", {}).get("pros_cons", {})
            km = pc.get("key_metrics", {})
            if km:
                result["key_metrics"] = {
                    "market_cap": km.get("Market Cap"),
                    "current_price": km.get("Current Price"),
                    "high_low": km.get("High / Low"),
                    "pe": km.get("Stock P/E"),
                    "book_value": km.get("Book Value"),
                    "dividend_yield": km.get("Dividend Yield"),
                    "roce": km.get("ROCE"),
                    "roe": km.get("ROE"),
                    "face_value": km.get("Face Value"),
                }
            result["pros_cons"] = {
                "pros": pc.get("pros", []),
                "cons": pc.get("cons", []),
                "about": pc.get("about", ""),
            }

            ratios = data.get("sections", {}).get("ratios", {})
            if ratios:
                result["ratios"] = ratios

            peers = data.get("sections", {}).get("peer_comparison", {})
            if peers:
                result["peer_comparison"] = peers.get("peers", [])

            result["source"] = "live"
    except Exception:
        pass

    return result