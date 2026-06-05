"""MYRA Fundamental Data Sync Module.

Fetches and stores fundamental data from Morningstar (bulk) and yfinance (per-symbol).
Stores data in myra_valuation.db fundamentals table.
"""

import logging
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone

import requests
import yfinance as yf

from myra_app.constants import DB_DIR
from myra_app.background_orchestrator import _shutdown_event
from myra_app.librarian_core import LibrarianCore

logger = logging.getLogger("myra.fundamental_sync")

IST = timezone(timedelta(hours=5, minutes=30))

# Morningstar API configuration
MORNINGSTAR_URL = "https://lt.morningstar.com/api/rest.svc/g9vi2nsqjb/security/screener"
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

    def _ensure_table_exists(self, conn: sqlite3.Connection):
        """Create fundamentals table if it doesn't exist."""
        conn.execute("""
            CREATE TABLE IF NOT EXISTS fundamentals (
                symbol              TEXT NOT NULL,
                date                TEXT NOT NULL,
                sector              TEXT,
                pe                  REAL,
                sector_pe           REAL,
                market_cap          REAL,
                face_value          REAL,
                issued_size         INTEGER,
                shares_outstanding  INTEGER,
                daily_volatility    REAL,
                annual_volatility   REAL,
                impact_cost         REAL,
                net_margin          REAL,
                roe_ttm             REAL,
                dividend_yield      REAL,
                peRatio             REAL,
                priceToBook         REAL,
                priceToSales        REAL,
                earningsPerShare    REAL,
                bookValuePerShare   REAL,
                revenueGrowth       REAL,
                earningsGrowth      REAL,
                marketCap           REAL,
                enterpriseValue     REAL,
                debtToEquity        REAL,
                returnOnEquity      REAL,
                returnOnAssets      REAL,
                operatingMargin     REAL,
                grossMargin         REAL,
                payoutRatio         REAL,
                currentRatio        REAL,
                quickRatio          REAL,
                freeCashFlowYield   REAL,
                beta                REAL,
                source_ms           TEXT,
                source_nse          TEXT,
                PRIMARY KEY (symbol, date)
            )
            """)
        # Add shares_outstanding if table pre-dates the column
        try:
            conn.execute("ALTER TABLE fundamentals ADD COLUMN shares_outstanding INTEGER")
        except sqlite3.OperationalError:
            pass
        conn.commit()

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
                        "netMargin": row.get("netMargin"),
                        "dividendYield": row.get("dividendYield"),
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

            if info.get("marketCap") is not None:
                result["market_cap"] = info["marketCap"]
            if info.get("trailingPE") is not None:
                result["pe"] = info["trailingPE"]
            if info.get("faceValue") is not None:
                result["face_value"] = info["faceValue"]
            if info.get("sharesOutstanding") is not None:
                result["issued_size"] = info["sharesOutstanding"]

        except Exception as e:
            logger.warning(
                f"[FundamentalSync] yfinance fetch failed for {symbol}: {e}"
            )
            self.errors += 1

        return result

    def _fetch_nse_all(self, symbols: list, cancel_event: threading.Event | None = None) -> dict:
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

        logger.info(f"[FundamentalSync] yfinance fetch complete: {self.nse_fetched} symbols")
        return result

    def _merge_and_insert(self, ms_data: dict, nse_data: dict, date_str: str):
        """Merge Morningstar and NSE data and insert into database."""
        all_symbols = set(ms_data.keys()) | set(nse_data.keys())
        db_path = self._get_valuation_db_path()
        records = []

        for symbol in all_symbols:
            ms = ms_data.get(symbol, {})
            nse = nse_data.get(symbol, {})

            record = {
                "symbol": symbol,
                "date": date_str,
                # NSE fields (already use DB column names)
                "pe": nse.get("pe"),
                "sector_pe": nse.get("sector_pe"),
                "market_cap": nse.get("market_cap"),
                "face_value": nse.get("face_value"),
                "issued_size": nse.get("issued_size"),
                "shares_outstanding": nse.get("issued_size"),
                "daily_volatility": nse.get("daily_volatility"),
                "annual_volatility": nse.get("annual_volatility"),
                "impact_cost": nse.get("impact_cost"),
                # Morningstar fields – map camelCase API keys to DB columns
                "sector": ms.get("sector"),
                "net_margin": ms.get("netMargin"),
                "roe_ttm": ms.get("roeTTM"),
                "dividend_yield": ms.get("dividendYield"),
                "peRatio": ms.get("peRatio"),
                "priceToBook": ms.get("priceToBook"),
                "priceToSales": ms.get("priceToSales"),
                "earningsPerShare": ms.get("earningsPerShare"),
                "bookValuePerShare": ms.get("bookValuePerShare"),
                "revenueGrowth": ms.get("revenueGrowth"),
                "earningsGrowth": ms.get("earningsGrowth"),
                "marketCap": ms.get("marketCap"),
                "enterpriseValue": ms.get("enterpriseValue"),
                "debtToEquity": ms.get("debtToEquity"),
                "returnOnEquity": ms.get("returnOnEquity"),
                "returnOnAssets": ms.get("returnOnAssets"),
                "operatingMargin": ms.get("operatingMargin"),
                "grossMargin": ms.get("grossMargin"),
                "payoutRatio": ms.get("payoutRatio"),
                "currentRatio": ms.get("currentRatio"),
                "quickRatio": ms.get("quickRatio"),
                "freeCashFlowYield": ms.get("freeCashFlowYield"),
                "beta": ms.get("beta"),
                "source_ms": "MORNINGSTAR" if ms else None,
                "source_nse": "YFINANCE" if nse else None,
            }
            records.append(record)

        if not records:
            logger.warning("[FundamentalSync] No records to insert")
            return

        try:
            with sqlite3.connect(db_path, timeout=30) as conn:
                self._ensure_table_exists(conn)
                # Build INSERT dynamically from record keys
                columns = list(records[0].keys())
                placeholders = [f":{c}" for c in columns]
                sql = f"INSERT OR REPLACE INTO fundamentals ({','.join(columns)}) VALUES ({','.join(placeholders)})"
                conn.executemany(sql, records)
                self.inserted = len(records)
                logger.info(f"[FundamentalSync] Inserted {self.inserted} records")
        except Exception as e:
            logger.error(f"[FundamentalSync] Insert failed: {e}")

    def _backfill_market_cap_from_yfinance(self, cancel_event: threading.Event | None = None):
        """Fetch market cap for all symbols that are missing it."""
        import yfinance as yf
        import time

        db_path = self._get_valuation_db_path()
        conn = sqlite3.connect(db_path, timeout=30)
        symbols = conn.execute(
            "SELECT symbol FROM fundamentals WHERE marketCap IS NULL OR marketCap = 0"
        ).fetchall()
        conn.close()

        total = len(symbols)
        logger.info(f"[FundamentalSync] Backfilling marketCap for {total} symbols via yfinance...")

        updated = 0
        for i, (symbol,) in enumerate(symbols):
            if _shutdown_event.is_set() or (cancel_event and cancel_event.is_set()):
                logger.info("[FundamentalSync] Market cap backfill cancelled by user.")
                break

            try:
                ticker = yf.Ticker(f"{symbol}.NS")
                info = ticker.info
                market_cap = info.get('marketCap')
                if market_cap:
                    conn = sqlite3.connect(db_path, timeout=30)
                    conn.execute(
                        "UPDATE fundamentals SET marketCap = ? WHERE symbol = ?",
                        (market_cap, symbol)
                    )
                    conn.commit()
                    conn.close()
                    updated += 1
                if (i + 1) % 100 == 0:
                    logger.info(f"[FundamentalSync] Market cap backfill: {i+1}/{total} ({updated} updated)")
                time.sleep(0.3)
            except Exception:
                pass

        logger.info(f"[FundamentalSync] Market cap backfill complete: {updated}/{total} updated")

    def _backfill_shares_outstanding_from_yfinance(self, cancel_event: threading.Event | None = None):
        """Fetch shares outstanding for all symbols that are missing it."""
        import time

        db_path = self._get_valuation_db_path()
        conn = sqlite3.connect(db_path, timeout=30)
        symbols = conn.execute(
            "SELECT symbol FROM fundamentals WHERE (shares_outstanding IS NULL OR shares_outstanding = 0) AND source_ms IS NOT NULL"
        ).fetchall()
        conn.close()

        total = len(symbols)
        logger.info(f"[FundamentalSync] Backfilling shares_outstanding for {total} symbols via yfinance...")

        updated = 0
        for i, (symbol,) in enumerate(symbols):
            if _shutdown_event.is_set() or (cancel_event and cancel_event.is_set()):
                logger.info("[FundamentalSync] Shares outstanding backfill cancelled by user.")
                break

            try:
                ticker = yf.Ticker(f"{symbol}.NS")
                info = ticker.info
                shares = info.get('sharesOutstanding')
                if shares:
                    conn = sqlite3.connect(db_path, timeout=30)
                    conn.execute(
                        "UPDATE fundamentals SET shares_outstanding = ? WHERE symbol = ?",
                        (shares, symbol)
                    )
                    conn.commit()
                    # Verify the UPDATE took effect
                    row = conn.execute(
                        "SELECT shares_outstanding FROM fundamentals WHERE symbol = ?",
                        (symbol,)
                    ).fetchone()
                    if not row or not row[0]:
                        logger.warning(f"[FundamentalSync] shares_outstanding NOT persisted for {symbol}")
                    conn.close()
                    updated += 1
                if (i + 1) % 100 == 0:
                    logger.info(f"[FundamentalSync] Shares outstanding backfill: {i+1}/{total} ({updated} updated)")
                time.sleep(0.3)
            except Exception:
                pass

        logger.info(f"[FundamentalSync] Shares outstanding backfill complete: {updated}/{total} updated")

    def _compute_market_cap_from_prices(self):
        """Compute marketCap = shares_outstanding × latest close for all symbols."""
        tech_db = f"{DB_DIR}/myra_technical.db"
        val_db = f"{DB_DIR}/myra_valuation.db"

        tech_conn = sqlite3.connect(tech_db)
        val_conn = sqlite3.connect(val_db)

        shares = {}
        for row in val_conn.execute(
            "SELECT symbol, shares_outstanding FROM fundamentals WHERE shares_outstanding IS NOT NULL AND shares_outstanding > 0"
        ):
            shares[row[0]] = row[1]

        logger.info(f"[FundamentalSync] Computing marketCap for {len(shares)} symbols")

        updated = 0
        for symbol, shares_out in shares.items():
            row = tech_conn.execute(
                "SELECT close FROM technical_data WHERE symbol = ? ORDER BY date DESC LIMIT 1",
                (symbol,)
            ).fetchone()
            if row and row[0] and shares_out > 0:
                market_cap = shares_out * row[0]
                val_conn.execute(
                    "UPDATE fundamentals SET marketCap = ? WHERE symbol = ?",
                    (market_cap, symbol)
                )
                updated += 1

        val_conn.commit()
        tech_conn.close()
        val_conn.close()
        logger.info(f"[FundamentalSync] marketCap updated for {updated} symbols")

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

        # Step 5: Compute marketCap from shares_outstanding × latest close
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
