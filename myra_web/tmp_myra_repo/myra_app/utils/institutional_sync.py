"""
Institutional Data Sync
Syncs bulk and block deals from NSE APIs.
Follows same pattern as index_sync.py for consistency.
"""

import logging
import os
import sqlite3
from datetime import date, datetime, timedelta

from myra_app.constants import DB_DIR
from myra_app.data_sources.nse_institutional import fetch_institutional_data

logger = logging.getLogger(__name__)

# NSE headers for API requests
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/",
}


def sync_institutional_data(force=False, task_id: int = None):
    """
    Sync institutional bulk/block deals from NSE.
    If force=True, always fetch. Otherwise only fetch if table is empty.
    """
    from myra_app.task_tracker import update

    if task_id is not None:
        update(task_id, "Checking institutional data freshness…")

    from datetime import datetime, timedelta, timezone

    IST = timezone(timedelta(hours=5, minutes=30))
    today = datetime.now(timezone.utc).astimezone(IST).date()

    inst_db = os.path.join(DB_DIR, "myra_institutional.db")
    conn = sqlite3.connect(inst_db, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS large_deals (
        symbol TEXT, date TEXT, client_name TEXT, buy_sell TEXT,
        qty INTEGER, price REAL, value REAL, deal_type TEXT,
        PRIMARY KEY (date, symbol, client_name, buy_sell)
    )"""
    )

    # Check if we need to sync
    if not force:
        count = conn.execute("SELECT COUNT(*) FROM large_deals").fetchone()[0]
        if count > 0:
            conn.close()
            print(
                f"[MYRA] Institutional data already exists ({count} deals). Use force=True to refresh."
            )
            return

    # Fetch data (default last 7 days)
    if task_id is not None:
        update(task_id, "Fetching bulk/block deals from NSE…")
    data = fetch_institutional_data()
    inserted = 0
    for label, df in data.items():
        if df.empty:
            continue
        for _, row in df.iterrows():  # noqa: PG-ITERROWS
            try:
                sym = str(row.get("Symbol", "")).strip().upper()
                dt = str(row.get("Date", "")).strip()
                client = str(row.get("ClientName", "")).strip()
                bs = str(row.get("Buy/Sell", "")).strip().upper()
                qty = int(float(str(row.get("QuantityTraded", 0)).replace(",", "")))
                price = float(
                    str(row.get("TradePrice/Wght.Avg.Price", 0)).replace(",", "")
                )
                value = round(qty * price / 100000, 2)
                conn.execute(  # noqa: PG-NPLUS1
                    """INSERT OR REPLACE INTO large_deals
                    (symbol, date, client_name, buy_sell, qty, price, value, deal_type)
                    VALUES (?,?,?,?,?,?,?,?)""",
                    (sym, dt, client, bs, qty, price, value, label.upper()),
                )
                inserted += 1
            except Exception:
                pass

    if task_id is not None:
        update(task_id, f"Inserting {inserted} deals…")

    conn.commit()
    conn.close()

    if task_id is not None:
        update(task_id, f"Institutional sync complete: {inserted} deals")

    print(f"[MYRA] Institutional sync complete: {inserted} deals inserted")
