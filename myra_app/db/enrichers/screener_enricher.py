"""
Screener.in Fundamentals Enricher

Fetches PBV and ROCE from Screener.in API for all symbols in the universe.
Caches results for 7 days to avoid rate limiting.
"""

import os
import sqlite3
import requests
import logging
import time
import re
from datetime import datetime, timedelta
from typing import Optional, Dict, List

from myra_app.constants import DB_DIR
from bs4 import BeautifulSoup  # requires beautifulsoup4

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": "https://www.screener.in/company/",
}
METRICS = {
    "pbv": "Price to book value",
    "roce": "Return on capital employed",
}
CACHE_DAYS = 7


def _get_company_id(symbol: str) -> Optional[str]:
    """Scrape Screener.in company page to get the numeric company ID."""
    url = f"https://www.screener.in/company/{symbol}/consolidated/"
    session = requests.Session()
    try:
        resp = session.get(url, headers=HEADERS, timeout=10)
        if resp.status_code != 200:
            logger.debug(
                f"Failed to fetch company page for {symbol}: {resp.status_code}"
            )
            return None
        soup = BeautifulSoup(resp.text, "html.parser")
        # Look for data-company-id attribute
        div = soup.find("div", {"data-company-id": True})
        if div:
            return div["data-company-id"]
        # Fallback: search script tags
        scripts = soup.find_all("script")
        for script in scripts:
            if script.string and "company_id" in script.string:
                match = re.search(r'company_id["\']?\s*[:=]\s*["\']?(\d+)', script.string)
                if match:
                    return match.group(1)
        return None
    except Exception as e:
        logger.error(f"Error getting company ID for {symbol}: {e}")
        return None


def _fetch_metric(company_id: str, metric_query: str) -> Optional[float]:
    """Fetch a single metric from Screener.in chart API."""
    q = metric_query.replace(" ", "+")
    url = (
        f"https://www.screener.in/api/company/{company_id}/chart/"
        f"?q={q}&days=1825&consolidated=true"
    )
    session = requests.Session()
    # Prime session with main page to get cookies
    session.get("https://www.screener.in/company/", headers=HEADERS)
    try:
        resp = session.get(url, headers=HEADERS, timeout=10)
        if resp.status_code != 200:
            return None
        data = resp.json()
        datasets = data.get("datasets", [])
        if not datasets:
            return None
        values = datasets[0].get("values", [])
        if not values:
            return None
        latest = values[-1]
        if isinstance(latest, list) and len(latest) >= 2:
            return float(latest[1])
        return float(latest) if latest is not None else None
    except Exception as e:
        logger.debug(f"Error fetching metric {metric_query}: {e}")
        return None


def _fetch_symbol_data(symbol: str) -> Dict[str, Optional[float]]:
    """Fetch all metrics for a single symbol."""
    company_id = _get_company_id(symbol)
    if not company_id:
        return {}
    result = {}
    for key, query in METRICS.items():
        val = _fetch_metric(company_id, query)
        if val is not None:
            result[key] = val
    return result


def _ensure_table(conn: sqlite3.Connection):
    """Create screener_fundamentals table if it doesn't exist."""
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS screener_fundamentals (
            symbol TEXT PRIMARY KEY,
            pbv REAL,
            roce REAL,
            last_updated TEXT
        )
    """
    )
    conn.commit()


def enrich_screener_fundamentals(force: bool = False):
    """
    Enrich screener_fundamentals table with PBV and ROCE.
    If force=True, update all symbols regardless of cache.
    Otherwise, skip symbols updated within CACHE_DAYS.
    """
    db_path = os.path.join(DB_DIR, "myra_valuation.db")
    conn = sqlite3.connect(db_path)
    _ensure_table(conn)

    cur = conn.cursor()
    # Get all symbols from fundamentals table (universe)
    cur.execute("SELECT symbol FROM fundamentals WHERE market_cap IS NOT NULL")
    symbols = [row[0] for row in cur.fetchall()]
    logger.info(f"Found {len(symbols)} symbols to enrich.")

    # Get last_updated for each symbol
    cur.execute("SELECT symbol, last_updated FROM screener_fundamentals")
    existing = {row[0]: row[1] for row in cur.fetchall()}

    cutoff = (datetime.now() - timedelta(days=CACHE_DAYS)).isoformat()

    updated = 0
    failed = 0
    for idx, sym in enumerate(symbols):
        # Check cache
        if not force and sym in existing and existing[sym] >= cutoff:
            continue

        logger.debug(f"Fetching data for {sym} ({idx+1}/{len(symbols)})")
        data = _fetch_symbol_data(sym)
        if data:
            pbv = data.get("pbv")
            roce = data.get("roce")
            cur.execute(
                """
                INSERT OR REPLACE INTO screener_fundamentals (symbol, pbv, roce, last_updated)
                VALUES (?, ?, ?, ?)
            """,
                (sym, pbv, roce, datetime.now().isoformat()),
            )
            updated += 1
            logger.debug(f"Updated {sym}: PBV={pbv}, ROCE={roce}")
        else:
            failed += 1
            logger.debug(f"No data for {sym}")

        # Be polite to Screener.in
        time.sleep(1.5)

    conn.commit()
    conn.close()
    logger.info(f"Enrichment complete. Updated: {updated}, Failed: {failed}")


if __name__ == "__main__":
    # Manual testing
    logging.basicConfig(level=logging.INFO)
    enrich_screener_fundamentals(force=True)
