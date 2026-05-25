"""
Populate market_cap, sector, and pe columns in the fundamentals table
using niftyterminal as a live NSE fallback.

Usage:
    python tools/sync_market_cap.py                    # market cap sync only
    python tools/sync_market_cap.py --shareholding     # shareholding + float sync
    python tools/sync_market_cap.py --shareholding --limit 10   # test on first 10

Updates use the market_cap column (not marketCap) to match what
_get_fundamentals_bulk in ml_trainer.py already queries.
"""

import asyncio
import logging
import os
import sqlite3
import sys
import time

from myra_app.constants import DB_DIR
from myra_app.librarian_core import LibrarianCore

from niftyterminal import get_stock_quote

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

SEMAPHORE_LIMIT = 10


# ──────────────────────────────────────────────
# MARKET CAP SYNC (existing)
# ──────────────────────────────────────────────

async def fetch_one(sym: str, sem: asyncio.Semaphore) -> dict:
    async with sem:
        try:
            data = await get_stock_quote(sym)
            return {
                "symbol": sym,
                "market_cap": data.get("marketCap"),
                "sector": data.get("sector"),
                "pe": data.get("pe"),
            }
        except Exception as exc:
            logger.warning("Failed to fetch %s: %s", sym, exc)
            return {"symbol": sym, "market_cap": None, "sector": None, "pe": None}


async def fetch_all(symbols: list[str]) -> list[dict]:
    sem = asyncio.Semaphore(SEMAPHORE_LIMIT)
    results = []
    total = len(symbols)
    for i in range(0, total, SEMAPHORE_LIMIT * 2):
        batch = symbols[i : i + SEMAPHORE_LIMIT * 2]
        tasks = [fetch_one(sym, sem) for sym in batch]
        batch_results = await asyncio.gather(*tasks)
        results.extend(batch_results)
        if (i + len(batch)) % 50 == 0 or (i + len(batch)) >= total:
            logger.info("Fetched %d / %d symbols", min(i + len(batch), total), total)
    return results


def sync_market_cap():
    """Fetch market_cap, sector, pe for all symbols via niftyterminal."""
    db_path = os.path.join(DB_DIR, LibrarianCore.DB_MAP["valuation"])
    if not os.path.exists(db_path):
        logger.error("Valuation database not found at %s", db_path)
        sys.exit(1)

    conn = sqlite3.connect(db_path, timeout=30)
    symbols = [
        row[0]
        for row in conn.execute("SELECT DISTINCT symbol FROM fundamentals").fetchall()
    ]
    logger.info("Found %d symbols in fundamentals table", len(symbols))

    if not symbols:
        logger.warning("No symbols to process.")
        conn.close()
        return

    t0 = time.time()
    results = asyncio.run(fetch_all(symbols))
    elapsed = time.time() - t0

    successes = 0
    failures = 0
    cur = conn.cursor()

    for r in results:
        if r["market_cap"] is not None:
            cur.execute(
                "UPDATE fundamentals SET market_cap = ?, sector = ?, pe = ? WHERE symbol = ?",
                (r["market_cap"], r["sector"], r["pe"], r["symbol"]),
            )
            successes += 1
        else:
            failures += 1

    conn.commit()
    conn.close()

    total = len(results)
    logger.info(
        "Done. %d / %d symbols updated (%.0fs, %d failures)",
        successes,
        total,
        elapsed,
        failures,
    )
    print(f"Total: {total}, Successes: {successes}, Failures: {failures}")


# ──────────────────────────────────────────────
# SHAREHOLDING + FREE FLOAT SYNC
# ──────────────────────────────────────────────

def _fetch_yf_info(symbol: str) -> dict:
    """
    Fetch shares_outstanding, insider holding, and float from yfinance.

    NOTE: heldPercentInsiders is US-style insider ownership (management,
    directors, 10%+ beneficial owners). This is NOT the same as SEBI promoter
    holding. The real promoter_holding_pct comes from screener.in or NSE
    filings via fundamental_manager.py.

    Returns dict with keys:
        shares_outstanding, insider_holding_pct, float_shares,
        market_cap, sector, industry, price
    """
    import yfinance as yf

    result = {
        "symbol": symbol,
        "shares_outstanding": None,
        "insider_holding_pct": None,
        "float_shares": None,
        "market_cap": None,
        "sector": None,
        "industry": None,
        "price": None,
    }

    def _try_suffix(suffix: str) -> bool:
        ticker = yf.Ticker(f"{symbol}{suffix}")
        info = ticker.info
        if not info:
            return False
        so = info.get("sharesOutstanding")
        insiders = info.get("heldPercentInsiders")
        fs = info.get("floatShares")
        mcap = info.get("marketCap")
        price = info.get("regularMarketPrice")
        result["shares_outstanding"] = float(so) if so else None
        result["insider_holding_pct"] = round(float(insiders) * 100, 2) if insiders else None
        result["float_shares"] = float(fs) if fs else None
        result["market_cap"] = float(mcap) if mcap else None
        result["price"] = float(price) if price else None
        result["sector"] = info.get("sector")
        result["industry"] = info.get("industry")
        return result["shares_outstanding"] is not None or result["market_cap"] is not None

    try:
        got_data = _try_suffix(".NS")
        if not got_data:
            _try_suffix(".BO")
    except Exception as exc:
        logger.debug("yfinance failed for %s: %s", symbol, exc)

    return result


async def process_shareholding_one(sym: str, sem: asyncio.Semaphore) -> dict:
    """Fetch shareholding + float data for one symbol using yfinance."""
    async with sem:
        try:
            await asyncio.sleep(0.3)  # rate-limit buffer
            yf_data = await asyncio.to_thread(_fetch_yf_info, sym)

            shares_out = yf_data.get("shares_outstanding")
            insider_pct = yf_data.get("insider_holding_pct")
            float_shares = yf_data.get("float_shares")
            mcap = yf_data.get("market_cap")
            industry = yf_data.get("industry") or yf_data.get("sector")

            if shares_out is not None:
                shares_out = max(shares_out, 0)
            else:
                shares_out = None

            free_float_pct = None
            if float_shares is not None and shares_out is not None and shares_out > 0:
                free_float_pct = round(float_shares / shares_out * 100, 2)

            free_float_market_cap = None
            if mcap is not None and free_float_pct is not None:
                free_float_market_cap = mcap * free_float_pct / 100

            return {
                "symbol": sym,
                "shares_outstanding": shares_out,
                "insider_holding_pct": insider_pct,
                "public_holding_pct": None,
                "promoter_holding_pct": None,
                "free_float_pct": free_float_pct,
                "free_float_market_cap": free_float_market_cap,
                "free_float_shares": float_shares,
                "industry": industry,
                "sector": yf_data.get("sector"),
            }

        except Exception as exc:
            logger.warning("Failed to fetch shareholding for %s: %s", sym, exc)
            return {
                "symbol": sym,
                "shares_outstanding": None,
                "insider_holding_pct": None,
                "public_holding_pct": None,
                "promoter_holding_pct": None,
                "free_float_pct": None,
                "free_float_market_cap": None,
                "free_float_shares": None,
                "industry": None,
                "sector": None,
            }


async def fetch_shareholding_all(symbols: list[str]) -> list[dict]:
    """Batch-fetch shareholding data for all symbols."""
    sem = asyncio.Semaphore(3)  # conservative for yfinance rate limits
    results = []
    total = len(symbols)
    for i in range(0, total, 6):  # 2 batches per semaphore cycle
        batch = symbols[i : i + 6]
        tasks = [process_shareholding_one(sym, sem) for sym in batch]
        batch_results = await asyncio.gather(*tasks)
        results.extend(batch_results)
        done = min(i + len(batch), total)
        if done % 20 == 0 or done >= total:
            logger.info("Processed %d / %d symbols", done, total)
    return results


def sync_shareholding_and_float(limit: int | None = None):
    """
    Sync shares_outstanding, promoter_holding_pct, public_holding_pct,
    free_float fields, and industry for all symbols via yfinance.
    """
    db_path = os.path.join(DB_DIR, LibrarianCore.DB_MAP["valuation"])
    if not os.path.exists(db_path):
        logger.error("Valuation database not found at %s", db_path)
        sys.exit(1)

    conn = sqlite3.connect(db_path, timeout=30)
    symbols = [
        row[0]
        for row in conn.execute("SELECT DISTINCT symbol FROM fundamentals ORDER BY symbol").fetchall()
    ]
    logger.info("Found %d symbols in fundamentals table", len(symbols))

    if not symbols:
        conn.close()
        logger.warning("No symbols to process.")
        return

    if limit:
        symbols = symbols[:limit]
        logger.info("LIMIT: processing first %d symbols", limit)

    t0 = time.time()
    results = asyncio.run(fetch_shareholding_all(symbols))
    elapsed = time.time() - t0

    update_sql = """
        UPDATE fundamentals SET
            shares_outstanding = COALESCE(?, shares_outstanding),
            insider_holding_pct = COALESCE(?, insider_holding_pct),
            free_float_pct = COALESCE(?, free_float_pct),
            free_float_market_cap = COALESCE(?, free_float_market_cap),
            free_float_shares = COALESCE(?, free_float_shares),
            industry = COALESCE(?, industry)
        WHERE symbol = ?
    """

    cur = conn.cursor()
    updated = 0
    skipped = 0
    for r in results:
        has_data = any(
            r.get(k) is not None
            for k in ["shares_outstanding", "insider_holding_pct", "free_float_shares", "industry"]
        )
        if has_data:
            cur.execute(
                update_sql,
                (
                    r.get("shares_outstanding"),
                    r.get("insider_holding_pct"),
                    r.get("free_float_pct"),
                    r.get("free_float_market_cap"),
                    r.get("free_float_shares"),
                    r.get("industry"),
                    r["symbol"],
                ),
            )
            updated += 1
        else:
            skipped += 1

    conn.commit()
    conn.close()

    # Summary
    with_so = sum(1 for r in results if r.get("shares_outstanding") is not None)
    with_insider = sum(1 for r in results if r.get("insider_holding_pct") is not None)
    with_industry = sum(1 for r in results if r.get("industry") is not None)
    with_ff = sum(1 for r in results if r.get("free_float_pct") is not None)

    print(f"\n{'='*60}")
    print(f"  SYNC COMPLETE")
    print(f"{'='*60}")
    print(f"  Total processed: {len(results)}")
    print(f"  Updated:         {updated}")
    print(f"  Skipped:         {skipped}")
    print(f"  Time:            {elapsed:.0f}s")
    print(f"{'='*60}")
    print(f"  Coverage:")
    print(f"    shares_outstanding:   {with_so}/{len(results)}")
    print(f"    insider_holding_pct:  {with_insider}/{len(results)}")
    print(f"    industry:             {with_industry}/{len(results)}")
    print(f"    free_float_pct:       {with_ff}/{len(results)}")
    print(f"{'='*60}\n")


# ──────────────────────────────────────────────
# CLI ENTRY POINT
# ──────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="MYRA fundamentals sync")
    parser.add_argument(
        "--shareholding",
        action="store_true",
        help="Run shareholding + free float sync instead of market cap sync",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit to first N symbols (for testing)",
    )
    args = parser.parse_args()

    if args.shareholding:
        sync_shareholding_and_float(limit=args.limit)
    else:
        sync_market_cap()
