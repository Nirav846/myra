"""MYRA Fundamental Data Sync Module.

Fetches and stores fundamental data from Morningstar (bulk) and NSE (per-symbol).
Stores data in myra_valuation.db fundamentals table.
"""

import logging
import sqlite3
import time
from datetime import datetime, timedelta, timezone

import requests

from myra_app.constants import DB_DIR
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

# NSE API configuration
NSE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
    "Referer": "https://www.nseindia.com/",
}

NSE_QUOTE_URL = "https://www.nseindia.com/api/quote-equity?symbol={symbol}"
NSE_TRADE_INFO_URL = (
    "https://www.nseindia.com/api/quote-equity?symbol={symbol}&section=trade_info"
)


class FundamentalSync:
    """Syncs fundamental data from Morningstar and NSE into myra_valuation.db."""

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
                pe                  REAL,
                sector_pe           REAL,
                market_cap          REAL,
                face_value          REAL,
                issued_size         INTEGER,
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

                rows = data.get("rows", [])
                if not rows:
                    break

                for row in rows:
                    ticker = row.get("ticker")
                    if not ticker:
                        continue

                    result[ticker] = {
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

    @staticmethod
    def _retry_request(url, headers, timeout, max_retries=3):
        import time

        for attempt in range(max_retries):
            try:
                resp = requests.get(url, headers=headers, timeout=timeout)
                resp.raise_for_status()
                return resp
            except requests.exceptions.RequestException as e:
                if attempt == max_retries - 1:
                    raise
                time.sleep(2**attempt)
        return None

    def _fetch_nse_symbol(self, symbol: str) -> dict:
        """Fetch fundamental data for a single symbol from NSE.

        Args:
            symbol: The stock symbol to fetch data for.

        Returns:
            Dict with NSE fundamental data or empty dict on error.
        """
        result = {}

        try:
            # Fetch quote data (PE, sector PE, face value, issued size)
            url1 = NSE_QUOTE_URL.format(symbol=symbol)
            response1 = self._retry_request(url1, NSE_HEADERS, 30)
            response1.raise_for_status()
            data1 = response1.json()

            metadata = data1.get("metadata", {})
            security_info = data1.get("securityInfo", {})

            pe = metadata.get("pdSymbolPe")
            sector_pe = metadata.get("pdSectorPe")
            face_value = security_info.get("faceValue")
            issued_size = security_info.get("issuedSize")

            if pe is not None:
                result["pe"] = pe
            if sector_pe is not None:
                result["sector_pe"] = sector_pe
            if face_value is not None:
                result["face_value"] = face_value
            if issued_size is not None:
                result["issued_size"] = issued_size

            # Fetch trade info (market cap, volatility, impact cost)
            url2 = NSE_TRADE_INFO_URL.format(symbol=symbol)
            response2 = self._retry_request(url2, NSE_HEADERS, 30)
            response2.raise_for_status()
            data2 = response2.json()

            market_dept = data2.get("marketDeptOrderBook", {})
            trade_info = market_dept.get("tradeInfo", {})

            market_cap = trade_info.get("totalMarketCap")
            daily_vol = trade_info.get("cmDailyVolatility")
            annual_vol = trade_info.get("cmAnnualVolatility")
            impact_cost = trade_info.get("impactCost")

            if market_cap is not None:
                result["market_cap"] = market_cap
            if daily_vol is not None:
                result["daily_volatility"] = float(daily_vol)
            if annual_vol is not None:
                result["annual_volatility"] = float(annual_vol)
            if impact_cost is not None:
                result["impact_cost"] = impact_cost

        except requests.exceptions.RequestException as e:
            logger.warning(f"[FundamentalSync] NSE fetch failed for {symbol}: {e}")
            self.errors += 1
        except Exception as e:
            logger.warning(f"[FundamentalSync] Error processing {symbol}: {e}")
            self.errors += 1

        return result

    def _fetch_nse_all(self, symbols: list) -> dict:
        """Fetch fundamental data for all symbols from NSE.

        Args:
            symbols: List of symbols to fetch.

        Returns:
            Dict keyed by symbol: {symbol: {pe, sector_pe, ...}}
        """
        logger.info(
            f"[FundamentalSync] Starting NSE fetch for {len(symbols)} symbols..."
        )
        result = {}

        for i, symbol in enumerate(symbols):
            symbol_data = self._fetch_nse_symbol(symbol)
            if symbol_data:
                result[symbol] = symbol_data
                self.nse_fetched += 1

            # Rate limiting: sleep 500ms between requests
            if i < len(symbols) - 1:
                time.sleep(0.5)

            # Log progress every 50 symbols
            if (i + 1) % 50 == 0:
                logger.info(f"[FundamentalSync] NSE progress: {i + 1}/{len(symbols)}")

        logger.info(f"[FundamentalSync] NSE fetch complete: {self.nse_fetched} symbols")
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
                "daily_volatility": nse.get("daily_volatility"),
                "annual_volatility": nse.get("annual_volatility"),
                "impact_cost": nse.get("impact_cost"),
                # Morningstar fields – map camelCase API keys to DB columns
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
                "source_nse": "NSE" if nse else None,
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

    def _log_summary(self):
        """Log the sync summary."""
        logger.info(
            f"[FundamentalSync] Summary - MS fetched: {self.ms_fetched}, "
            f"NSE fetched: {self.nse_fetched}, Inserted: {self.inserted}, "
            f"Errors: {self.errors}"
        )

    def run_full_sync(self):
        """Run full sync: Morningstar bulk + NSE NIFTY 500.

        Morningstar is fetched first for all symbols, then NSE for NIFTY 500.
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

        # Step 2: Get NIFTY 500 symbols and fetch NSE data
        nifty_symbols = self._get_nifty_500_symbols()
        if nifty_symbols:
            nse_data = self._fetch_nse_all(nifty_symbols)
        else:
            nse_data = {}
            logger.warning(
                "[FundamentalSync] No NIFTY 500 symbols found, skipping NSE fetch"
            )

        # Step 3: Merge and insert
        self._merge_and_insert(ms_data, nse_data, today)

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

    def run_nse_only(self):
        """Run NSE NIFTY 500 only - can be called separately."""
        logger.info("[FundamentalSync] Starting NSE-only sync...")
        self.ms_fetched = 0
        self.nse_fetched = 0
        self.inserted = 0
        self.errors = 0

        today = datetime.now(IST).date().isoformat()

        nifty_symbols = self._get_nifty_500_symbols()
        if nifty_symbols:
            nse_data = self._fetch_nse_all(nifty_symbols)
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
