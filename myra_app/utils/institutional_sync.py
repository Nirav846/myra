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


def sync_institutional_data(force=False):
    """
    Sync institutional bulk/block deals from NSE.
    If force=True, always fetch. Otherwise only fetch if table is empty.
    """
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

    # Fetch insider trades from BSE (reliable ZIP endpoint)
    try:
        import io
        import zipfile
        from datetime import timedelta

        import pandas as pd

        from myra_app.data_sources.nse_institutional import _nse_session

        bse_insider_url = (
            "https://www.bseindia.com/download/BSEInsider/Equity/Insider_{}.zip"
        )
        insider_rows = []

        # Try last 5 trading days
        for d in range(5):
            check_date = today - timedelta(days=d)
            if check_date.weekday() >= 5:
                continue
            zip_url = bse_insider_url.format(check_date.strftime("%Y-%m-%d"))  # noqa: PG-STRFTIME
            resp = _nse_session().get(zip_url, headers=HEADERS, timeout=15)
            if resp.status_code != 200:
                continue

            # Extract CSV from ZIP
            with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
                csv_name = [n for n in zf.namelist() if n.lower().endswith(".csv")]
                if not csv_name:
                    continue
                df = pd.read_csv(zf.open(csv_name[0]))
                df.columns = [c.strip() for c in df.columns]

            # Map BSE columns to our schema
            for _, row in df.iterrows():  # noqa: PG-ITERROWS
                try:
                    sym = str(row.get("SYMBOL", row.get("Symbol", ""))).strip().upper()
                    if not sym or sym in ("NA", "-"):
                        continue
                    acq = str(row.get("ACQ_NAME", row.get("Acquirer Name", ""))).strip()
                    cat = str(row.get("CATEGORY", row.get("Category", ""))).strip()
                    typ = str(row.get("TYPE", row.get("Trade Type", ""))).strip()
                    mod = str(row.get("MODE", row.get("Mode", ""))).strip()
                    val = (
                        float(
                            str(row.get("VALUE_CR", row.get("Value", 0))).replace(
                                ",", ""
                            )
                        )
                        if "VALUE" in row or "Value" in row
                        else 0.0
                    )
                    prc = (
                        float(
                            str(row.get("AVG_PRICE", row.get("Avg Price", 0))).replace(
                                ",", ""
                            )
                        )
                        if "AVG" in row or "Avg" in row
                        else 0.0
                    )

                    insider_rows.append(  # noqa: PG-APPEND
                        (
                            sym,
                            acq,
                            cat,
                            typ,
                            mod,
                            round(val, 2) if val else 0.0,
                            round(prc, 2) if prc else 0.0,
                            check_date.strftime("%Y-%m-%d"),  # noqa: PG-STRFTIME
                        )
                    )
                except Exception:
                    pass

        if insider_rows:
            with sqlite3.connect(inst_db, timeout=10) as conn:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS insider_trades (
                        symbol TEXT, acq_name TEXT, category TEXT, type TEXT,
                        mode TEXT, value_cr REAL, avg_price REAL, date TEXT,
                        PRIMARY KEY (date, symbol, acq_name, type)
                    )
                """
                )
                conn.executemany(
                    "INSERT OR REPLACE INTO insider_trades VALUES (?,?,?,?,?,?,?,?)",
                    insider_rows,
                )
                conn.commit()
            print(f"[INST SYNC] Inserted {len(insider_rows)} insider trades from BSE.")
        else:
            print("[INST SYNC] No insider trades found in recent BSE files.")
    except Exception as e:
        logger.warning(f"BSE insider trade sync failed: {e}")

    conn.commit()
    conn.close()
    print(f"[MYRA] Institutional sync complete: {inserted} deals inserted")
