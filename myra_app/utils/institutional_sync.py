"""
Institutional Data Sync using NSE-MCP
Syncs FII/DII activity, bulk deals, block deals, insider trades, and corporate actions.
"""

import asyncio
import json
import logging
import os
import sqlite3
from datetime import datetime, timezone, timedelta

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

logger = logging.getLogger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))

PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
NSE_MCP_DIR = os.path.join(PROJECT_ROOT, "nse-mcp")


class InstitutionalSync:
    """
    Syncs institutional data from NSE-MCP server.
    Retention: 5 years (1826 days) - historical institutional activity for pattern analysis.
    """

    def __init__(self, db_path: str = None, retention_days: int = 1826):
        if db_path is None:
            from myra_app.constants import DB_DIR

            db_path = os.path.join(DB_DIR, "myra_institutional.db")
        self.db_path = db_path
        self.retention_days = retention_days
        self.server_params = StdioServerParameters(
            command="node",
            args=["dist/index.js"],
            cwd=NSE_MCP_DIR,
        )

    async def _call_tool(self, tool_name: str, arguments: dict = None):
        """Connect to NSE-MCP over stdio, call the tool, and return parsed JSON."""
        if arguments is None:
            arguments = {}

        async with stdio_client(self.server_params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(tool_name, arguments)
                if result.content and result.content[0].type == "text":
                    text = result.content[0].text
                    if text.startswith("Error:"):
                        raise Exception(text)
                    return json.loads(text)
                return None

    def _call_sync(self, tool_name: str, arguments: dict = None):
        """Synchronous wrapper for _call_tool."""
        return asyncio.run(self._call_tool(tool_name, arguments))

    def _get_connection(self):
        """Get a connection to the institutional database."""
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _ensure_tables(self):
        """Create all required tables if they don't exist."""
        conn = self._get_connection()
        try:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS fii_dii_daily (
                date TEXT PRIMARY KEY,
                fii_net_buy REAL,
                dii_net_buy REAL,
                source TEXT DEFAULT 'NSE-MCP'
            )"""
            )

            conn.execute(
                """CREATE TABLE IF NOT EXISTS bulk_deals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                date TEXT NOT NULL,
                security_name TEXT,
                client_name TEXT,
                buy_sell TEXT,
                quantity INTEGER,
                price REAL,
                trade_value REAL,
                source TEXT DEFAULT 'NSE-MCP',
                UNIQUE(symbol, date, client_name, buy_sell)
            )"""
            )

            conn.execute(
                """CREATE TABLE IF NOT EXISTS block_deals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                date TEXT NOT NULL,
                security_name TEXT,
                client_name TEXT,
                buy_sell TEXT,
                quantity INTEGER,
                price REAL,
                trade_value REAL,
                source TEXT DEFAULT 'NSE-MCP',
                UNIQUE(symbol, date, client_name, buy_sell)
            )"""
            )

            conn.execute(
                """CREATE TABLE IF NOT EXISTS insider_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                acq_name TEXT,
                category TEXT,
                type TEXT,
                mode TEXT,
                value_cr REAL,
                avg_price REAL,
                date TEXT NOT NULL,
                source TEXT DEFAULT 'NSE-MCP'
            )"""
            )

            conn.execute(
                """CREATE TABLE IF NOT EXISTS corporate_actions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                date TEXT NOT NULL,
                security_name TEXT,
                action_type TEXT,
                ex_date TEXT,
                record_date TEXT,
                source TEXT DEFAULT 'NSE-MCP',
                UNIQUE(symbol, date, action_type)
            )"""
            )

            conn.execute(
                """CREATE TABLE IF NOT EXISTS sync_metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )"""
            )

            conn.commit()
        finally:
            conn.close()

    def _get_last_sync_date(self, key: str) -> str | None:
        """Get the last sync date for a given key (e.g., 'bulk_deals', 'block_deals').
        Returns ISO date string (YYYY-MM-DD) or None if never synced."""
        conn = sqlite3.connect(self.db_path)
        try:
            row = conn.execute(
                "SELECT value FROM sync_metadata WHERE key = ?", (key,)
            ).fetchone()
            return row[0] if row else None
        finally:
            conn.close()

    def _set_last_sync_date(self, key: str, value: str) -> None:
        """Set the last sync date for a given key. Uses INSERT OR REPLACE for idempotency."""
        from datetime import datetime

        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                "INSERT OR REPLACE INTO sync_metadata (key, value, updated_at) VALUES (?, ?, ?)",
                (key, value, datetime.now().isoformat()),
            )
            conn.commit()
        finally:
            conn.close()

    def sync_fii_dii(self, limit: int = 30):
        """Sync FII/DII daily activity."""
        logger.info("[InstitutionalSync] Syncing FII/DII activity...")
        try:
            data = self._call_sync("get_fii_dii_activity", {"limit": limit})
            if not data:
                logger.warning("[InstitutionalSync] No FII/DII data returned")
                return 0

            conn = self._get_connection()
            inserted = 0
            try:
                for item in data:
                    dt_raw = item.get("date", "")
                    try:
                        dt_obj = datetime.strptime(dt_raw, "%d-%b-%Y")
                        dt = f"{dt_obj:%Y-%m-%d}"
                    except Exception:
                        dt = dt_raw
                    fii = float(item.get("fiiNetValue", 0) or 0)
                    dii = float(item.get("diiNetValue", 0) or 0)
                    if dt:
                        conn.execute(  # noqa: PG-NPLUS1
                            """INSERT OR REPLACE INTO fii_dii_daily (date, fii_net_buy, dii_net_buy)
                            VALUES (?, ?, ?)""",
                            (dt, fii, dii),
                        )
                        inserted += 1
                conn.commit()
                logger.info(f"[InstitutionalSync] Inserted {inserted} FII/DII records")
            finally:
                conn.close()
            return inserted
        except Exception as e:
            logger.error(f"[InstitutionalSync] FII/DII sync failed: {e}")
            return 0

    def _upsert_deals(self, table: str, df, from_date, to_date) -> int:
        """Insert deal rows into the given table. Returns count of attempted inserts."""
        from datetime import datetime

        conn = sqlite3.connect(self.db_path)
        inserted = 0
        try:
            for _, row in df.iterrows():  # noqa: PG-ITERROWS
                symbol = str(row.get("Symbol", "")).strip()
                raw_date = str(row.get("Date", ""))
                # Parse date: nselib returns "01-Jan-2026" format
                try:
                    dt = datetime.strptime(raw_date, "%d-%b-%Y")
                    deal_date = f"{dt:%Y-%m-%d}"
                except (ValueError, TypeError):
                    deal_date = raw_date

                client = str(row.get("ClientName", "")).strip()
                buy_sell = str(row.get("Buy/Sell", "")).strip()
                qty = row.get("QuantityTraded", 0)
                price = row.get("TradePrice/Wght.Avg.Price", 0)

                if not symbol or not deal_date:
                    continue

                conn.execute(  # noqa: PG-NPLUS1
                    f"""INSERT OR IGNORE INTO {table}
                     (symbol, date, security_name, client_name, buy_sell, quantity, price, trade_value)
                     VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (symbol, deal_date, str(row.get("SecurityName", "")), client, buy_sell,
                     qty, price, round(qty * price / 10000000, 2) if qty and price else 0),
                )
                inserted += 1
            conn.commit()
        finally:
            conn.close()
        return inserted

    def sync_bulk_deals(self) -> int:
        """Fetch bulk deals from NSE using nselib with date-range tracking.
        Returns number of new rows inserted."""
        from datetime import date, timedelta

        last_sync = self._get_last_sync_date("bulk_deals")
        today = date.today()

        if last_sync:
            from_date = date.fromisoformat(last_sync) + timedelta(days=1)
        else:
            from_date = date(2020, 1, 1)  # default: fetch from 2020

        to_date = today - timedelta(days=1)  # yesterday (today's data may not be available)

        if from_date > to_date:
            logger.info("Bulk deals: no new data to fetch (already up to date)")
            return 0

        try:
            from nselib import capital_market
            # nselib expects dd-mm-YYYY format
            df = capital_market.bulk_deal_data(
                from_date=f"{from_date:%d-%m-%Y}",
                to_date=f"{to_date:%d-%m-%Y}",
            )
        except Exception as e:
            logger.warning(f"Bulk deals nselib fetch failed: {e}. Skipping.")
            return 0

        if df is None or df.empty:
            logger.info("Bulk deals: nselib returned no data")
            self._set_last_sync_date("bulk_deals", to_date.isoformat())
            return 0

        inserted = self._upsert_deals("bulk_deals", df, from_date, to_date)

        self._set_last_sync_date("bulk_deals", to_date.isoformat())
        logger.info(f"Bulk deals: inserted {inserted} rows (from {from_date} to {to_date})")
        return inserted

    def sync_block_deals(self) -> int:
        """Fetch block deals from NSE using nselib with date-range tracking.
        Returns number of new rows inserted."""
        from datetime import date, timedelta

        last_sync = self._get_last_sync_date("block_deals")
        today = date.today()

        if last_sync:
            from_date = date.fromisoformat(last_sync) + timedelta(days=1)
        else:
            from_date = date(2020, 1, 1)  # default: fetch from 2020

        to_date = today - timedelta(days=1)  # yesterday (today's data may not be available)

        if from_date > to_date:
            logger.info("Block deals: no new data to fetch (already up to date)")
            return 0

        try:
            from nselib import capital_market
            # nselib expects dd-mm-YYYY format; function is block_deals_data (plural)
            df = capital_market.block_deals_data(
                from_date=f"{from_date:%d-%m-%Y}",
                to_date=f"{to_date:%d-%m-%Y}",
            )
        except Exception as e:
            logger.warning(f"Block deals nselib fetch failed: {e}. Skipping.")
            return 0

        if df is None or df.empty:
            logger.info("Block deals: nselib returned no data")
            self._set_last_sync_date("block_deals", to_date.isoformat())
            return 0

        inserted = self._upsert_deals("block_deals", df, from_date, to_date)

        self._set_last_sync_date("block_deals", to_date.isoformat())
        logger.info(f"Block deals: inserted {inserted} rows (from {from_date} to {to_date})")
        return inserted

    def sync_insider_trades(self):
        """Sync insider trades."""
        logger.info("[InstitutionalSync] Syncing insider trades...")
        try:
            from datetime import datetime, timedelta

            to_date = f"{datetime.now(IST):%Y-%m-%d}"
            from_date = f"{(datetime.now(IST) - timedelta(days=30)):%Y-%m-%d}"

            data = self._call_sync(
                "get_insider_trading", {"fromDate": from_date, "toDate": to_date}
            )
            if not data:
                logger.warning("[InstitutionalSync] No insider trades data returned")
                return 0

            conn = self._get_connection()
            inserted = 0
            try:
                for item in data:
                    sym = item.get("symbol", "").strip().upper()
                    dt_raw = item.get("acquireFromDate", "")
                    try:
                        dt_obj = datetime.strptime(dt_raw, "%d-%b-%Y")
                        dt = f"{dt_obj:%Y-%m-%d}"
                    except Exception:
                        dt = dt_raw
                    acquirer = item.get("acquirerName", "")
                    category = item.get("personCategory", "")
                    bs = (
                        "Buy" if int(item.get("sharesAcquired", 0) or 0) > 0 else "Sell"
                    )
                    qty = int(item.get("sharesAcquired", 0) or 0)
                    if qty < 0:
                        qty = abs(qty)
                        bs = "Sell"
                    mode = item.get("modeOfAcquisition", "")
                    if sym and dt:
                        conn.execute(  # noqa: PG-NPLUS1
                            """INSERT OR IGNORE INTO insider_trades
                            (symbol, acq_name, category, type, mode, date)
                            VALUES (?, ?, ?, ?, ?, ?)""",
                            (sym, acquirer, category, bs, mode, dt),
                        )
                        inserted += 1
                conn.commit()
                logger.info(f"[InstitutionalSync] Inserted {inserted} insider trades")
            finally:
                conn.close()
            return inserted
        except Exception as e:
            logger.error(f"[InstitutionalSync] Insider trades sync failed: {e}")
            return 0

    def sync_corporate_actions(self):
        """Sync corporate actions."""
        logger.info("[InstitutionalSync] Syncing corporate actions...")
        try:
            from datetime import datetime, timedelta

            to_date = f"{datetime.now(IST):%Y-%m-%d}"
            from_date = f"{(datetime.now(IST) - timedelta(days=90)):%Y-%m-%d}"

            data = self._call_sync(
                "get_corporate_actions", {"fromDate": from_date, "toDate": to_date}
            )
            if not data:
                logger.warning("[InstitutionalSync] No corporate actions data returned")
                return 0

            conn = self._get_connection()
            inserted = 0
            try:
                for item in data:
                    sym = item.get("symbol", "").strip().upper()
                    ex_date_raw = item.get("exDate", "")
                    try:
                        dt_obj = datetime.strptime(ex_date_raw, "%d-%b-%Y")
                        dt = f"{dt_obj:%Y-%m-%d}"
                    except Exception:
                        dt = ex_date_raw
                    sec_name = item.get("company", "")
                    action = item.get("purpose", "")
                    record_date_raw = item.get("recordDate", "")
                    try:
                        record_date_obj = datetime.strptime(record_date_raw, "%d-%b-%Y")
                        record_date = f"{record_date_obj:%Y-%m-%d}"
                    except Exception:
                        record_date = record_date_raw
                    if sym and dt:
                        conn.execute(  # noqa: PG-NPLUS1
                            """INSERT OR IGNORE INTO corporate_actions
                            (symbol, date, security_name, action_type, ex_date, record_date)
                            VALUES (?, ?, ?, ?, ?, ?)""",
                            (sym, dt, sec_name, action, dt, record_date),
                        )
                        inserted += 1
                conn.commit()
                logger.info(
                    f"[InstitutionalSync] Inserted {inserted} corporate actions"
                )
            finally:
                conn.close()
            return inserted
        except Exception as e:
            logger.error(f"[InstitutionalSync] Corporate actions sync failed: {e}")
            return 0

    def _cleanup_old(self, table: str, retention_days: int = None):
        """Delete rows older than retention period. Default: 5 years (1826 days)."""
        if retention_days is None:
            retention_days = self.retention_days

        try:
            conn = self._get_connection()
            try:
                ist_now = datetime.now(IST)
                cutoff = ist_now - timedelta(days=retention_days)
                cutoff_str = f"{cutoff:%Y-%m-%d}"

                cursor = conn.execute(
                    f"DELETE FROM {table} WHERE date < ?", (cutoff_str,)
                )
                deleted = cursor.rowcount
                conn.commit()
                if deleted > 0:
                    logger.info(
                        f"[InstitutionalSync] Cleaned {deleted} old rows from {table}"
                    )
            finally:
                conn.close()
        except Exception as e:
            logger.warning(f"[InstitutionalSync] Cleanup failed for {table}: {e}")

    def run_all(self):
        """Run all sync methods sequentially."""
        logger.info("[InstitutionalSync] Starting full institutional sync...")

        self._ensure_tables()

        self.sync_fii_dii(limit=30)
        self._cleanup_old("fii_dii_daily")

        self.sync_bulk_deals()
        self._cleanup_old("bulk_deals")

        self.sync_block_deals()
        self._cleanup_old("block_deals")

        self.sync_insider_trades()
        self._cleanup_old("insider_trades")

        self.sync_corporate_actions()
        self._cleanup_old("corporate_actions")

        logger.info("[InstitutionalSync] Full institutional sync complete")


def sync_institutional_data(force: bool = False, task_id: int = None):
    """
    Wrapper function for backward compatibility with background_orchestrator.
    """
    from myra_app.task_tracker import update

    if task_id is not None:
        update(task_id, "Syncing institutional data via NSE-MCP...")

    sync = InstitutionalSync()
    sync.run_all()

    if task_id is not None:
        update(task_id, "Institutional sync complete")

    print("[MYRA] Institutional sync complete via NSE-MCP")
