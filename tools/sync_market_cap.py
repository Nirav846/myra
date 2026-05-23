"""
Populate market_cap, sector, and pe columns in the fundamentals table
using niftyterminal as a live NSE fallback.

Usage:
    python tools/sync_market_cap.py

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


def main():
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


if __name__ == "__main__":
    main()
