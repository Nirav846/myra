"""MYRA Fundamental Data Sync Module.

Fetches and stores fundamental data from Morningstar (bulk) and yfinance (per-symbol).
Stores data in myra_valuation.db fundamentals table.
"""

import logging
import os

# Load .env for MORNINGSTAR_TOKEN
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone

import requests
import yfinance as yf

from myra_app.constants import DB_DIR, DISABLE_FUNDAMENTAL_WRITERS
from myra_app.background_orchestrator import _shutdown_event
from myra_app.librarian_core import LibrarianCore

logger = logging.getLogger("myra.fundamental_sync")

IST = timezone(timedelta(hours=5, minutes=30))

# Morningstar API configuration
MORNINGSTAR_TOKEN = os.environ["MORNINGSTAR_TOKEN"]  # Required — set in .env
MORNINGSTAR_URL = (
    f"https://lt.morningstar.com/api/rest.svc/{MORNINGSTAR_TOKEN}/security/screener"
)
MORNINGSTAR_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
    "Referer": "https://www.morningstar.in/",
}


class FundamentalSync:
    """Syncs fundamental data from Morningstar and yfinance into myra_valuation.db."""

    def __init__(self):
        self.ms_fetched = 0
        self.nse_fetched = 0
        self.inserted = 0
        self.errors = 0

    def _get_valuation_db_path(self) -> str:
        """Get the valuation database path from DB_MAP."""
        db_file = LibrarianCore.DB_MAP["valuation"]
        return f"{DB_DIR}/{db_file}"

    # REMOVED – _ensure_table_exists() removed; librarian_schema.py is the sole authority

    def _fetch_morningstar_bulk(self) -> dict:
        """Fetch all symbols' fundamental data from Morningstar.

        Returns:
            Dict keyed by ticker: {ticker: {net_margin, roe_ttm, dividend_yield}}
        """
        logger.info("[FundamentalSync] Starting Morningstar bulk fetch...")
        result = {}
        page = 1
        page_size = 1000

        while True:
            params = {
                "page": page,
                "pageSize": page_size,
                "sortOrder": "LegalName asc",
                "outputType": "json",
                "version": "1",
                "languageId": "en-IN",
                "currencyId": "INR",
                "universeIds": "E0EXG$XNSE",
                "securityDataPoints": "ticker,sectorName,industryName,peRatio,priceToBook,priceToSales,earningsPerShare,bookValuePerShare,revenueGrowth,earningsGrowth,marketCap,enterpriseValue,debtToEquity,returnOnEquity,returnOnAssets,operatingMargin,grossMargin,netMargin,dividendYield,payoutRatio,currentRatio,quickRatio,freeCashFlowYield,beta",
                "filters": "",
            }

            try:
                response = requests.get(
                    MORNINGSTAR_URL,
                    headers=MORNINGSTAR_HEADERS,
                    params=params,
                    timeout=60,
                )
                response.raise_for_status()
                data = response.json()

                # Morningstar returns a list of {group, items} objects.
                # Flatten into a single list of row dicts.
                if isinstance(data, list):
                    flat_rows = []
                    for group_obj in data:
                        items = group_obj.get("items", [])
                        flat_rows.extend(items)
                    data = {"rows": flat_rows}

                rows = data.get("rows", [])
                if not rows:
                    break

                for row in rows:
                    ticker = row.get("ticker")
                    if not ticker:
                        continue

                    result[ticker] = {
                        "sector": row.get("sectorName") or row.get("industryName"),
                        "netMargin": row.get("netMargin"),
                        "roe_ttm": row.get("roeTTM"),
                        "dividendYield": row.get("dividendYield"),
                        # New fields from expanded Morningstar data
                        "peRatio": row.get("peRatio"),
                        "priceToBook": row.get("priceToBook"),
                        "priceToSales": row.get("priceToSales"),
                        "earningsPerShare": row.get("earningsPerShare"),
                        "bookValuePerShare": row.get("bookValuePerShare"),
                        "revenueGrowth": row.get("revenueGrowth"),
                        "earningsGrowth": row.get("earningsGrowth"),
                        "marketCap": row.get("marketCap"),
                        "enterpriseValue": row.get("enterpriseValue"),
                        "debtToEquity": row.get("debtToEquity"),
                        "returnOnEquity": row.get("returnOnEquity"),
                        "returnOnAssets": row.get("returnOnAssets"),
                        "operatingMargin": row.get("operatingMargin"),
                        "grossMargin": row.get("grossMargin"),
                        "payoutRatio": row.get("payoutRatio"),
                        "currentRatio": row.get("currentRatio"),
                        "quickRatio": row.get("quickRatio"),
                        "freeCashFlowYield": row.get("freeCashFlowYield"),
                        "beta": row.get("beta"),
                    }

                logger.debug(
                    f"[FundamentalSync] Morningstar page {page}: {len(rows)} rows"
                )
                page += 1

            except requests.exceptions.RequestException as e:
                logger.error(
                    f"[FundamentalSync] Morningstar fetch failed on page {page}: {e}"
                )
                self.errors += 1
                break

        self.ms_fetched = len(result)
        logger.info(
            f"[FundamentalSync] Morningstar bulk fetch complete: {self.ms_fetched} symbols"
        )
        return result

    def _get_nifty_500_symbols(self) -> list:
        """Read NIFTY 500 symbols from myra_metadata.db."""
        meta_db_file = LibrarianCore.DB_MAP["meta"]
        meta_db_path = f"{DB_DIR}/{meta_db_file}"

        try:
            with sqlite3.connect(meta_db_path, timeout=10) as conn:
                cursor = conn.execute(
                    "SELECT symbol FROM index_constituents WHERE index_name = ?",
                    ("NIFTY 500",),
                )
                symbols = [row[0] for row in cursor.fetchall()]
                return symbols
        except Exception as e:
            logger.error(f"[FundamentalSync] Failed to read NIFTY 500 symbols: {e}")
            return []

    def _fetch_yfinance_symbol(self, symbol: str) -> dict:
        """Fetch fundamental data for a single symbol from yfinance.

        Args:
            symbol: The stock symbol to fetch data for.

        Returns:
            Dict with yfinance fundamental data or empty dict on error.
        """
        result = {}
        try:
            ticker = yf.Ticker(f"{symbol}.NS")
            info = ticker.info
            if not info:
                return result

            if info.get("trailingPE") is not None:
                result["pe"] = info["trailingPE"]
            if info.get("faceValue") is not None:
                result["face_value"] = info["faceValue"]

        except Exception as e:
            logger.warning(f"[FundamentalSync] yfinance fetch failed for {symbol}: {e}")
            self.errors += 1

        return result

    def _fetch_nse_all(
        self, symbols: list, cancel_event: threading.Event | None = None
    ) -> dict:
        """Fetch fundamental data for all symbols via yfinance.

        Args:
            symbols: List of symbols to fetch.
            cancel_event: Optional threading.Event to check for cancellation.

        Returns:
            Dict keyed by symbol: {symbol: {pe, sector_pe, ...}}
        """
        logger.info(
            f"[FundamentalSync] Starting yfinance fetch for {len(symbols)} symbols..."
        )
        result = {}
        total = len(symbols)

        for i, symbol in enumerate(symbols):
            if _shutdown_event.is_set() or (cancel_event and cancel_event.is_set()):
                logger.info("[FundamentalSync] Cancelled by user.")
                break

            symbol_data = self._fetch_yfinance_symbol(symbol)
            if symbol_data:
                result[symbol] = symbol_data
                self.nse_fetched += 1

            # Rate limiting: sleep 200ms between requests
            if i < total - 1:
                time.sleep(0.2)

            # Log progress every 100 symbols
            if (i + 1) % 100 == 0:
                logger.info(f"[FundamentalSync] yfinance progress: {i + 1}/{total}")

        logger.info(
            f"[FundamentalSync] yfinance fetch complete: {self.nse_fetched} symbols"
        )
        return result

    def _merge_and_insert(self, ms_data: dict, nse_data: dict, date_str: str):
        """Merge Morningstar and NSE data and insert into database."""
        # DISABLE_FUNDAMENTAL_WRITERS: upstox_fetcher now owns fundamentals table
        if DISABLE_FUNDAMENTAL_WRITERS:
            logger.info(
                "[FundamentalSync] _merge_and_insert skipped: "
                "DISABLE_FUNDAMENTAL_WRITERS=True (upstox_fetcher owns fundamentals)"
            )
            return
        all_symbols = set(ms_data.keys()) | set(nse_data.keys())
        db_path = self._get_valuation_db_path()
        records = []

        # Fetch existing values that we must NOT overwrite
        existing_values = {}
        try:
            with sqlite3.connect(db_path, timeout=10) as conn:
                for row in conn.execute(  # noqa: PG-NPLUS1
                    "SELECT symbol, shares_outstanding, market_cap, promoter_holding_pct, free_float_pct, free_float_market_cap FROM fundamentals"
                ):
                    existing_values[row[0]] = (row[1], row[2], row[3], row[4], row[5])
        except Exception:
            pass

        # Canonical mapping: Morningstar camelCase keys → snake_case column names
        # Columns with a canonical equivalent are consolidated here.
        # Columns without a canonical equivalent are dropped to prevent schema drift.
        MS_CANONICAL_MAP = {
            "peRatio": "pe",
            "earningsPerShare": "eps",
            "bookValuePerShare": "book_value",
            "revenueGrowth": "sales_growth",
            "earningsGrowth": "profit_growth",
            "marketCap": "market_cap",
            "debtToEquity": "debt_to_equity",
            "returnOnEquity": "roe",
            "netMargin": "net_margin",
            "dividendYield": "dividend_yield",
        }

        for symbol in all_symbols:
            ms = ms_data.get(symbol, {})
            nse = nse_data.get(symbol, {})

            # Build MS fields using canonical snake_case names only
            ms_fields: dict[str, object] = {
                "sector": ms.get("sector"),
                "roe_ttm": ms.get("roeTTM"),
            }
            # Apply canonical mapping — camelCase values go into canonical columns
            for camel_key, canonical_key in MS_CANONICAL_MAP.items():
                val = ms.get(camel_key)
                if val is not None:
                    ms_fields[canonical_key] = val

            record = {
                "symbol": symbol,
                "date": date_str,
                # NSE fields
                "pe": nse.get("pe"),
                "sector_pe": nse.get("sector_pe"),
                "market_cap": nse.get("market_cap"),
                "face_value": nse.get("face_value"),
                "issued_size": nse.get("issued_size"),
                "shares_outstanding": nse.get("issued_size"),
                "daily_volatility": nse.get("daily_volatility"),
                "annual_volatility": nse.get("annual_volatility"),
                "impact_cost": nse.get("impact_cost"),
                "source_ms": "MORNINGSTAR" if ms else None,
                "source_nse": "YFINANCE" if nse else None,
                "last_updated": datetime.now().isoformat(),
                # MS fields (canonical snake_case)
                **ms_fields,
            }
            # Do NOT overwrite columns managed by separate backfills
            existing = existing_values.get(symbol)
            if existing:
                if existing[0] is not None and existing[0] > 0:
                    record["shares_outstanding"] = existing[0]
                if existing[1] is not None and existing[1] > 0:
                    record["market_cap"] = existing[1]
                if existing[2] is not None and existing[2] > 0:
                    record["promoter_holding_pct"] = existing[2]
                if existing[3] is not None and existing[3] > 0:
                    record["free_float_pct"] = existing[3]
                if existing[4] is not None and existing[4] > 0:
                    record["free_float_market_cap"] = existing[4]
            records.append(record)  # noqa: PG-APPEND

        if not records:
            logger.warning("[FundamentalSync] No records to insert")
            return

        try:
            with sqlite3.connect(db_path, timeout=30) as conn:
                # Schema managed by librarian_schema.py — no _ensure_table_exists call
                # Build INSERT dynamically from ALL record keys
                # (some symbols may lack certain Morningstar fields)
                all_columns = set()
                for r in records:
                    all_columns.update(r.keys())
                columns = sorted(all_columns)
                # Pad missing fields with None
                for r in records:
                    for c in columns:
                        if c not in r:
                            r[c] = None
                placeholders = [f":{c}" for c in columns]
                sql = f"INSERT OR REPLACE INTO fundamentals ({','.join(columns)}) VALUES ({','.join(placeholders)})"
                conn.executemany(sql, records)
                self.inserted = len(records)
                logger.info(f"[FundamentalSync] Inserted {self.inserted} records")
        except Exception as e:
            logger.error(f"[FundamentalSync] Insert failed: {e}")

    def _backfill_market_cap_from_yfinance(
        self, cancel_event: threading.Event | None = None
    ):
        """Fetch market cap for all symbols that are missing it."""
        # DISABLE_FUNDAMENTAL_WRITERS: upstox_fetcher now owns fundamentals table
        if DISABLE_FUNDAMENTAL_WRITERS:
            logger.info(
                "[FundamentalSync] _backfill_market_cap_from_yfinance skipped: "
                "DISABLE_FUNDAMENTAL_WRITERS=True"
            )
            return
        import yfinance as yf
        import time

        db_path = self._get_valuation_db_path()
        conn = sqlite3.connect(db_path, timeout=30)
        symbols = conn.execute(
            "SELECT symbol FROM fundamentals WHERE market_cap IS NULL OR market_cap = 0"
        ).fetchall()
        conn.close()

        total = len(symbols)
        logger.info(
            f"[FundamentalSync] Backfilling market_cap for {total} symbols via yfinance..."
        )

        updated = 0
        for i, (symbol,) in enumerate(symbols):
            if _shutdown_event.is_set() or (cancel_event and cancel_event.is_set()):
                logger.info("[FundamentalSync] Market cap backfill cancelled by user.")
                break

            try:
                ticker = yf.Ticker(f"{symbol}.NS")
                info = ticker.info
                market_cap = info.get("marketCap")
                if market_cap:
                    conn = sqlite3.connect(db_path, timeout=30)
                    conn.execute(  # noqa: PG-NPLUS1
                        "UPDATE fundamentals SET market_cap = ? WHERE symbol = ?",
                        (market_cap, symbol),
                    )
                    conn.commit()
                    conn.close()
                    updated += 1
                if (i + 1) % 100 == 0:
                    logger.info(
                        f"[FundamentalSync] Market cap backfill: {i+1}/{total} ({updated} updated)"
                    )
                time.sleep(0.3)
            except Exception:
                pass

        logger.info(
            f"[FundamentalSync] Market cap backfill complete: {updated}/{total} updated"
        )

    def _backfill_shares_outstanding_from_yfinance(
        self, cancel_event: threading.Event | None = None
    ):
        """Fetch shares outstanding for all symbols that are missing it."""
        # DISABLE_FUNDAMENTAL_WRITERS: upstox_fetcher now owns fundamentals table
        if DISABLE_FUNDAMENTAL_WRITERS:
            logger.info(
                "[FundamentalSync] _backfill_shares_outstanding_from_yfinance skipped: "
                "DISABLE_FUNDAMENTAL_WRITERS=True"
            )
            return
        import time

        db_path = self._get_valuation_db_path()
        conn = sqlite3.connect(db_path, timeout=30)
        symbols = conn.execute(
            "SELECT symbol FROM fundamentals WHERE (shares_outstanding IS NULL OR shares_outstanding = 0) AND source_ms IS NOT NULL"
        ).fetchall()
        conn.close()

        total = len(symbols)
        logger.info(
            f"[FundamentalSync] Backfilling shares_outstanding for {total} symbols via yfinance..."
        )

        updated = 0
        for i, (symbol,) in enumerate(symbols):
            if _shutdown_event.is_set() or (cancel_event and cancel_event.is_set()):
                logger.info(
                    "[FundamentalSync] Shares outstanding backfill cancelled by user."
                )
                break

            try:
                ticker = yf.Ticker(f"{symbol}.NS")
                info = ticker.info
                shares = info.get("sharesOutstanding")
                if shares:
                    conn = sqlite3.connect(db_path, timeout=30)
                    conn.execute(  # noqa: PG-NPLUS1
                        "UPDATE fundamentals SET shares_outstanding = ? WHERE symbol = ?",
                        (shares, symbol),
                    )
                    conn.commit()
                    # Verify the UPDATE took effect
                    row = conn.execute(  # noqa: PG-NPLUS1
                        "SELECT shares_outstanding FROM fundamentals WHERE symbol = ?",
                        (symbol,),
                    ).fetchone()
                    if not row or not row[0]:
                        logger.warning(
                            f"[FundamentalSync] shares_outstanding NOT persisted for {symbol}"
                        )
                    conn.close()
                    updated += 1
                if (i + 1) % 100 == 0:
                    logger.info(
                        f"[FundamentalSync] Shares outstanding backfill: {i+1}/{total} ({updated} updated)"
                    )
                time.sleep(0.3)
            except Exception:
                pass

        logger.info(
            f"[FundamentalSync] Shares outstanding backfill complete: {updated}/{total} updated"
        )

    def _compute_market_cap_from_prices(self):
        """Compute market_cap = shares_outstanding × latest close for all symbols."""
        # DISABLE_FUNDAMENTAL_WRITERS: upstox_fetcher now owns fundamentals table
        if DISABLE_FUNDAMENTAL_WRITERS:
            logger.info(
                "[FundamentalSync] _compute_market_cap_from_prices skipped: "
                "DISABLE_FUNDAMENTAL_WRITERS=True"
            )
            return
        tech_db = f"{DB_DIR}/myra_technical.db"
        val_db = f"{DB_DIR}/myra_valuation.db"

        tech_conn = sqlite3.connect(tech_db)
        val_conn = sqlite3.connect(val_db)

        shares = {}
        for row in val_conn.execute(  # noqa: PG-NPLUS1
            "SELECT symbol, shares_outstanding FROM fundamentals WHERE shares_outstanding IS NOT NULL AND shares_outstanding > 0"
        ):
            shares[row[0]] = row[1]

        logger.info(f"[FundamentalSync] Computing market_cap for {len(shares)} symbols")

        updated = 0

        # Batch fetch latest close for all symbols at once instead of N+1
        if shares:
            placeholders = ",".join("?" * len(shares))
            syms = list(shares.keys())
            close_rows = tech_conn.execute(
                f"SELECT symbol, close FROM technical_data "
                f"WHERE symbol IN ({placeholders}) AND close IS NOT NULL "
                f"ORDER BY symbol, date DESC",
                syms,
            ).fetchall()

            # Keep only the latest close per symbol
            latest_close: dict[str, float] = {}
            for sym, close in close_rows:
                if sym not in latest_close:
                    latest_close[sym] = close

            # Batch update: single executemany instead of N individual UPDATEs
            updates = [
                (shares[sym] * close_price, sym)
                for sym, close_price in latest_close.items()
                if close_price and shares.get(sym, 0) > 0
            ]
            if updates:
                val_conn.executemany(
                    "UPDATE fundamentals SET market_cap = ? WHERE symbol = ?",
                    updates,
                )
                updated = len(updates)

        val_conn.commit()
        tech_conn.close()
        val_conn.close()
        logger.info(f"[FundamentalSync] market_cap updated for {updated} symbols")

    def _refresh_stale_shares_outstanding(self, cancel_event=None):
        """
        Fetch shares_outstanding from yfinance ONLY for symbols where it is
        NULL or hasn't been updated in 90 days.  Typically <50 symbols.
        Retries once with a 2-second delay on transient failures.
        """
        # DISABLE_FUNDAMENTAL_WRITERS: upstox_fetcher now owns fundamentals table
        if DISABLE_FUNDAMENTAL_WRITERS:
            logger.info(
                "[FundamentalSync] _refresh_stale_shares_outstanding skipped: "
                "DISABLE_FUNDAMENTAL_WRITERS=True"
            )
            return {"updated": 0, "total": 0, "skipped": "flag_disabled"}
        import yfinance as yf

        val_db = os.path.join(DB_DIR, "myra_valuation.db")
        conn = sqlite3.connect(val_db)
        try:
            stale = conn.execute(
                """SELECT symbol FROM fundamentals
                   WHERE shares_outstanding IS NULL
                      OR shares_outstanding = 0
                      OR last_fundamental_update IS NULL
                      OR last_fundamental_update < date('now', '-90 days')"""
            ).fetchall()
        finally:
            conn.close()

        total = len(stale)
        if total == 0:
            logger.info(
                "[shares_outstanding] No stale symbols found — nothing to update"
            )
            return {"updated": 0, "total": 0}

        logger.info(
            f"[shares_outstanding] Found {total} stale/missing symbols to update"
        )

        updated = 0
        for i, (symbol,) in enumerate(stale):
            if cancel_event and cancel_event.is_set():
                logger.info("[shares_outstanding] Cancelled by user")
                break
            shares = None
            for attempt in range(2):
                try:
                    info = yf.Ticker(f"{symbol}.NS").info
                    shares = info.get("sharesOutstanding")
                    break
                except Exception as e:
                    if attempt == 0:
                        logger.debug(
                            f"[shares_outstanding] {symbol} fetch failed, retrying in 2s: {e}"
                        )
                        time.sleep(2.0)
                    else:
                        logger.warning(
                            f"[shares_outstanding] {symbol} fetch failed after retry: {e}"
                        )
            if shares and shares > 0:
                try:
                    conn = sqlite3.connect(val_db)
                    try:
                        conn.execute(  # noqa: PG-NPLUS1
                            """UPDATE fundamentals
                               SET shares_outstanding = ?, last_fundamental_update = date('now')
                               WHERE symbol = ?""",
                            (shares, symbol),
                        )
                        conn.commit()
                    finally:
                        conn.close()
                    updated += 1
                except Exception as e:
                    logger.warning(
                        f"[shares_outstanding] Failed to persist {symbol}: {e}"
                    )
            if (i + 1) % 25 == 0:
                logger.info(
                    f"[shares_outstanding] Progress: {i+1}/{total} — updated {updated}"
                )
            time.sleep(0.3)

        logger.info(f"[shares_outstanding] Complete — updated {updated}/{total}")
        if total > 0 and updated == 0:
            raise RuntimeError(
                f"Shares refresh failed: {total} stale symbols found but yfinance returned no data. "
                "Check network or retry later."
            )
        return {"updated": updated, "total": total}

    def _log_summary(self):
        """Log the sync summary."""
        logger.info(
            f"[FundamentalSync] Summary - MS fetched: {self.ms_fetched}, "
            f"yfinance fetched: {self.nse_fetched}, Inserted: {self.inserted}, "
            f"Errors: {self.errors}"
        )

    def run_full_sync(self, cancel_event: threading.Event | None = None):
        """Run full sync: Morningstar bulk + yfinance NIFTY 500.

        Morningstar is fetched first for all symbols, then yfinance for NIFTY 500.
        Data is merged and inserted with today's date.
        """
        # DISABLE_FUNDAMENTAL_WRITERS: upstox_fetcher now owns fundamentals table
        if DISABLE_FUNDAMENTAL_WRITERS:
            logger.info(
                "[FundamentalSync] run_full_sync skipped: "
                "DISABLE_FUNDAMENTAL_WRITERS=True (upstox_fetcher owns fundamentals)"
            )
            return {
                "ms_fetched": 0,
                "nse_fetched": 0,
                "inserted": 0,
                "errors": 0,
                "skipped": "flag_disabled",
            }
        logger.info("[FundamentalSync] Starting full sync...")
        self.ms_fetched = 0
        self.nse_fetched = 0
        self.inserted = 0
        self.errors = 0

        today = datetime.now(IST).date().isoformat()

        # Step 1: Morningstar bulk fetch
        ms_data = self._fetch_morningstar_bulk()

        # Step 2: Get NIFTY 500 symbols and fetch yfinance data
        nifty_symbols = self._get_nifty_500_symbols()
        if nifty_symbols:
            nse_data = self._fetch_nse_all(nifty_symbols, cancel_event)
        else:
            nse_data = {}
            logger.warning(
                "[FundamentalSync] No NIFTY 500 symbols found, skipping per-symbol fetch"
            )

        # Step 3: Merge and insert
        self._merge_and_insert(ms_data, nse_data, today)

        # Step 4: Backfill shares_outstanding from yfinance for all symbols
        self._backfill_shares_outstanding_from_yfinance(cancel_event)

        # Step 5: Compute market_cap from shares_outstanding × latest close
        self._compute_market_cap_from_prices()

        self._log_summary()
        return {
            "ms_fetched": self.ms_fetched,
            "nse_fetched": self.nse_fetched,
            "inserted": self.inserted,
            "errors": self.errors,
        }

    def run_ms_only(self):
        """Run Morningstar bulk only - for daily lightweight refresh."""
        # DISABLE_FUNDAMENTAL_WRITERS: upstox_fetcher now owns fundamentals table
        if DISABLE_FUNDAMENTAL_WRITERS:
            logger.info(
                "[FundamentalSync] run_ms_only skipped: "
                "DISABLE_FUNDAMENTAL_WRITERS=True"
            )
            return {
                "ms_fetched": 0,
                "nse_fetched": 0,
                "inserted": 0,
                "errors": 0,
                "skipped": "flag_disabled",
            }
        logger.info("[FundamentalSync] Starting Morningstar-only sync...")
        self.ms_fetched = 0
        self.nse_fetched = 0
        self.inserted = 0
        self.errors = 0

        today = datetime.now(IST).date().isoformat()

        ms_data = self._fetch_morningstar_bulk()
        self._merge_and_insert(ms_data, {}, today)

        self._log_summary()
        return {
            "ms_fetched": self.ms_fetched,
            "nse_fetched": self.nse_fetched,
            "inserted": self.inserted,
            "errors": self.errors,
        }

    def run_nse_only(self, cancel_event: threading.Event | None = None):
        """Run per-symbol yfinance fetch for NIFTY 500 symbols."""
        # DISABLE_FUNDAMENTAL_WRITERS: upstox_fetcher now owns fundamentals table
        if DISABLE_FUNDAMENTAL_WRITERS:
            logger.info(
                "[FundamentalSync] run_nse_only skipped: "
                "DISABLE_FUNDAMENTAL_WRITERS=True"
            )
            return {
                "ms_fetched": 0,
                "nse_fetched": 0,
                "inserted": 0,
                "errors": 0,
                "skipped": "flag_disabled",
            }
        logger.info(f"[FundamentalSync] Starting yfinance-only sync...")
        self.ms_fetched = 0
        self.nse_fetched = 0
        self.inserted = 0
        self.errors = 0

        today = datetime.now(IST).date().isoformat()

        nifty_symbols = self._get_nifty_500_symbols()
        if nifty_symbols:
            nse_data = self._fetch_nse_all(nifty_symbols, cancel_event)
            self._merge_and_insert({}, nse_data, today)
        else:
            logger.warning("[FundamentalSync] No NIFTY 500 symbols found")

        self._log_summary()
        return {
            "ms_fetched": self.ms_fetched,
            "nse_fetched": self.nse_fetched,
            "inserted": self.inserted,
            "errors": self.errors,
        }
