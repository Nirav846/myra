"""
Shareholding sync for Indian stocks.

Primary: NSE corporate filings via dalal library (cookie-managed session).
Fallback: BSE shpSecSummery_New API (scripcode-based).

Populates promoter_holding_pct, public_holding_pct in fundamentals table.
"""

import asyncio
import logging
import os
import sqlite3
import time
import urllib.request
import json
from html.parser import HTMLParser

from myra_app.constants import DISABLE_FUNDAMENTAL_WRITERS

logger = logging.getLogger(__name__)

DB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "db")


# ──────────────────────────────────────────────
# NSE path (primary) — uses dalal library
# ──────────────────────────────────────────────


def fetch_nse_shareholding(symbol: str) -> dict | None:
    """
    Fetch promoter & public holding from NSE quarterly filings via dalal.

    Returns dict with keys:
        promoter_pct, public_pct, date (quarter ending)
    or None if not found.
    """
    import dalal

    try:
        rows = dalal.shareholding(symbol)
        if not rows:
            return None
        latest = rows[0]
        raw_p = latest.get("pr_and_prgrp")
        raw_pub = latest.get("public_val")
        date = latest.get("date", "")
        if raw_p is None or raw_pub is None:
            return None
        return {
            "promoter_pct": float(raw_p),
            "public_pct": float(raw_pub),
            "date": date,
        }
    except Exception as exc:
        logger.debug("NSE shareholding failed for %s: %s", symbol, exc)
        return None


# ──────────────────────────────────────────────
# BSE path (fallback) — uses shpSecSummery_New
# ──────────────────────────────────────────────


def resolve_bse_scrip(symbol: str) -> str | None:
    """
    Resolve NSE symbol to BSE scrip code.

    1. Check cache in symbols_master.bse_scrip_code
    2. If not cached, try BSE search (or known mapping)
    3. Cache result for future use

    Returns scripcode string or None.
    """
    # Step 1: check cache
    meta_path = os.path.join(DB_DIR, "myra_metadata.db")
    if os.path.exists(meta_path):
        try:
            conn = sqlite3.connect(meta_path)
            row = conn.execute(
                "SELECT bse_scrip_code FROM symbols_master WHERE symbol = ? AND bse_scrip_code IS NOT NULL",
                (symbol,),
            ).fetchone()
            conn.close()
            if row and row[0]:
                return str(row[0])
        except Exception:
            pass

    # Step 2: try to find from known BSE scrip mapping
    # Use BSE stock page: https://www.bseindia.com/stock-share-price/{name}/{symbol}/{scripcode}/
    # For now, use the BSE scrip master (downloaded from bhavcopy)
    code = _search_bse_scripcode(symbol)
    if code:
        _cache_scrip_code(symbol, code)
    return code


def _search_bse_scripcode(symbol: str) -> str | None:
    """Try to find BSE scrip code by scanning BSE scrip master or known endpoints."""
    # Method: BSE search API with proper browser headers
    try:
        url = f"https://api.bseindia.com/BseIndiaAPI/api/Search/w?text={symbol}"
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
                "Accept": "application/json",
                "Referer": "https://www.bseindia.com/",
            },
        )
        with urllib.request.urlopen(req, timeout=10) as res:
            data = json.loads(res.read().decode())
            if isinstance(data, list):
                for item in data:
                    name = (item.get("scripName") or "").upper()
                    ticker = (item.get("bseticker") or "").upper()
                    if symbol.upper() in name or symbol.upper() == ticker:
                        return str(item.get("scripCode"))
    except Exception:
        pass
    return None


def _cache_scrip_code(symbol: str, scripcode: str):
    """Cache BSE scrip code in symbols_master."""
    meta_path = os.path.join(DB_DIR, "myra_metadata.db")
    if not os.path.exists(meta_path):
        return
    try:
        conn = sqlite3.connect(meta_path)
        conn.execute(
            "UPDATE symbols_master SET bse_scrip_code = ? WHERE symbol = ?",
            (scripcode, symbol),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


class _ShareholdingHTMLParser(HTMLParser):
    """Minimal parser to extract promoter pct and total shares from BSE HTML table."""

    def __init__(self):
        super().__init__()
        self.in_table = False
        self.in_row = False
        self.in_cell = False
        self.cells = []
        self.rows = []
        self.promoter_pct = None
        self.public_pct = None
        self.total_shares = None

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self.in_table = True
            self.rows = []
        elif tag == "tr" and self.in_table:
            self.in_row = True
            self.cells = []
        elif tag in ("td", "th") and self.in_row:
            self.in_cell = True

    def handle_endtag(self, tag):
        if tag == "table":
            self.in_table = False
        elif tag == "tr" and self.in_row:
            self.in_row = False
            if self.cells:
                self.rows.append(self.cells)
        elif tag in ("td", "th") and self.in_cell:
            self.in_cell = False

    def handle_data(self, data):
        if self.in_cell:
            self.cells.append(data.strip())


def fetch_bse_shareholding(scripcode: str) -> dict | None:
    """
    Fetch promoter & public holding from BSE shareholding pattern API.

    Returns dict with keys:
        promoter_pct, public_pct, total_shares, date
    or None if not found.
    """
    url = f"https://api.bseindia.com/BseIndiaAPI/api/shpSecSummery_New/w?qtrid=&scripcode={scripcode}"
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
                "Accept": "application/json",
                "Referer": f"https://www.bseindia.com/stock-share-price/?scripcode={scripcode}",
            },
        )
        with urllib.request.urlopen(req, timeout=15) as res:
            raw = json.loads(res.read().decode())
            data_html = raw.get("Data", "")
            if not data_html:
                return None

        # Parse HTML tables
        parser = _ShareholdingHTMLParser()
        parser.feed(data_html)

        # Find the main shareholding table (Table 3 in our test - has Category/Promoter/Public/Grand Total)
        for row in parser.rows:
            if not row:
                continue
            first_cell = row[0].strip() if row else ""
            if (
                first_cell == "(A) Promoter & Promoter Group"
                or first_cell == "Promoter & Promoter Group"
            ):
                if len(row) >= 5:
                    promoter_pct = _parse_pct(row[4])
            elif first_cell == "(B) Public" or first_cell == "Public":
                if len(row) >= 5:
                    public_pct = _parse_pct(row[4])
            elif first_cell in ("Grand Total", "Total"):
                if len(row) >= 4:
                    total_shares = _parse_num(row[3])

        return {
            "promoter_pct": promoter_pct,
            "public_pct": public_pct,
            "total_shares": total_shares,
            "date": None,
        }

    except Exception as exc:
        logger.debug("BSE shareholding failed for scripcode %s: %s", scripcode, exc)
        return None


def _parse_pct(val: str) -> float | None:
    """Parse percentage string like '50.00' from HTML table cell."""
    if not val:
        return None
    val = val.replace(",", "").replace("%", "").strip()
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _parse_num(val: str) -> int | None:
    """Parse number string like '13,53,24,72,634' from HTML table cell."""
    if not val:
        return None
    val = val.replace(",", "").strip()
    try:
        return int(float(val))
    except (ValueError, TypeError):
        return None


# ──────────────────────────────────────────────
# Backfill orchestrator
# ──────────────────────────────────────────────


async def backfill_shareholding(max_symbols: int | None = None):
    """
    Backfill promoter_holding_pct and public_holding_pct for all symbols
    where promoter_holding_pct IS NULL.

    Primary path: NSE quarterly filings via dalal.
    If NSE fails for a symbol, falls back to BSE.

    Args:
        max_symbols: If set, limit to N symbols (for testing).
    """
    # DISABLE_FUNDAMENTAL_WRITERS: upstox_fetcher now owns the fundamentals table
    if DISABLE_FUNDAMENTAL_WRITERS:
        logger.info(
            "backfill_shareholding skipped: DISABLE_FUNDAMENTAL_WRITERS=True "
            "(upstox_fetcher owns fundamentals)"
        )
        return
    db_path = os.path.join(DB_DIR, "myra_valuation.db")
    if not os.path.exists(db_path):
        logger.error("Valuation DB not found at %s", db_path)
        return

    conn = sqlite3.connect(db_path)
    symbols = [
        row[0]
        for row in conn.execute(
            "SELECT symbol FROM fundamentals "
            "WHERE promoter_holding_pct IS NULL OR public_holding_pct IS NULL "
            "ORDER BY symbol"
        ).fetchall()
    ]

    if max_symbols:
        symbols = symbols[:max_symbols]

    logger.info("Found %d symbols with NULL promoter_holding_pct", len(symbols))
    if not symbols:
        conn.close()
        return

    updated_nse = 0
    updated_bse = 0
    skipped = 0
    t0 = time.time()

    for i, sym in enumerate(symbols):
        result = None

        # Primary: try NSE via dalal
        try:
            result = await asyncio.to_thread(fetch_nse_shareholding, sym)
        except Exception as exc:
            logger.debug("NSE fetch failed for %s: %s", sym, exc)

        # Fallback: try BSE
        if not result or result.get("promoter_pct") is None:
            try:
                scripcode = resolve_bse_scrip(sym)
                if scripcode:
                    result = await asyncio.to_thread(fetch_bse_shareholding, scripcode)
                    if result:
                        updated_bse += 1
            except Exception as exc:
                logger.debug("BSE fetch failed for %s: %s", sym, exc)
        else:
            updated_nse += 1

        # Store result
        if result and result.get("promoter_pct") is not None:
            promoter = result["promoter_pct"]
            public = result.get("public_pct")
            if public is None and promoter is not None:
                public = round(100.0 - promoter, 2)

            conn.execute(  # noqa: PG-NPLUS1
                """UPDATE fundamentals SET
                    promoter_holding_pct = COALESCE(?, promoter_holding_pct),
                    public_holding_pct = COALESCE(?, public_holding_pct)
                WHERE symbol = ?""",
                (promoter, public, sym),
            )
        else:
            skipped += 1

        if (i + 1) % 50 == 0 or i == len(symbols) - 1:
            conn.commit()
            elapsed = time.time() - t0
            logger.info(
                "[%d/%d] NSE=%d BSE=%d skipped=%d (%.1f sec)",
                i + 1,
                len(symbols),
                updated_nse,
                updated_bse,
                skipped,
                elapsed,
            )

        await asyncio.sleep(0.3)  # rate-limit

    conn.commit()
    conn.close()

    elapsed = time.time() - t0
    logger.info(
        "Backfill complete: %d NSE, %d BSE, %d skipped (%.1f sec)",
        updated_nse,
        updated_bse,
        skipped,
        elapsed,
    )

    print(f"\n{'='*60}")
    print(f"  SHAREHOLDING BACKFILL COMPLETE")
    print(f"{'='*60}")
    print(f"  Total processed: {len(symbols)}")
    print(f"  Updated via NSE: {updated_nse}")
    print(f"  Updated via BSE: {updated_bse}")
    print(f"  Skipped:         {skipped}")
    print(f"  Time:            {elapsed:.1f}s")
    print(f"{'='*60}\n")


def run_backfill(max_symbols: int | None = None):
    """Synchronous entry point for backfill."""
    asyncio.run(backfill_shareholding(max_symbols))


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Backfill shareholding data")
    parser.add_argument("--limit", type=int, default=None, help="Limit symbols")
    args = parser.parse_args()
    run_backfill(args.limit)
