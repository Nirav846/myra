"""
Corporate Actions Enricher

Fetches corporate actions (Bonus, Split, Rights, Buy Back) from NSE API
and stores them in myra_institutional.db.
"""

import os
import sqlite3
import requests
import logging
from datetime import datetime, timedelta
from typing import Optional

from myra_app.constants import DB_DIR

logger = logging.getLogger(__name__)

NSE_BASE = "https://www.nseindia.com"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/companies-listing/corporate-filings-actions",
}


def _get_session():
    """Get a requests session with NSE cookies."""
    session = requests.Session()
    session.headers.update(HEADERS)
    # First hit the reference page to set cookies
    session.get(
        "https://www.nseindia.com/companies-listing/corporate-filings-actions",
        timeout=10,
    )
    return session


def fetch_corporate_actions(from_date: str, to_date: str):
    """
    Fetch corporate actions from NSE API for date range (DD-MM-YYYY).
    Returns list of dicts.
    """
    session = _get_session()
    url = "https://www.nseindia.com/api/corporates-corporateActions"
    params = {"index": "equities", "from_date": from_date, "to_date": to_date}
    resp = session.get(url, params=params, timeout=30)
    if resp.status_code != 200:
        logger.error(f"NSE API returned {resp.status_code}: {resp.text[:200]}")
        return []
    return resp.json()


def _filter_actions(records):
    """Keep only Bonus, Split, Rights, Buy Back (case-insensitive)."""
    keywords = ["bonus", "split", "rights", "buy back"]
    filtered = []
    for rec in records:
        subject = rec.get("subject", "").lower()
        if any(kw in subject for kw in keywords):
            filtered.append(rec)
    return filtered


def _insert_records(conn, records):
    """Insert records into corporate_actions table."""
    cur = conn.cursor()
    inserted = 0
    for rec in records:
        symbol = rec.get("symbol", "").strip()
        ex_date_str = rec.get("exDate", "")
        if not symbol or not ex_date_str:
            continue
        # Parse date
        try:
            dt = datetime.strptime(ex_date_str, "%d-%b-%Y").date()
        except ValueError:
            try:
                dt = datetime.strptime(ex_date_str, "%d-%m-%Y").date()
            except ValueError:
                continue
        date_iso = dt.isoformat()
        action_type = rec.get("subject", "").strip()
        security_name = rec.get("comp", "").strip()
        record_date = rec.get("recDate", "")
        # Insert with ignore
        cur.execute(
            """
            INSERT OR IGNORE INTO corporate_actions
            (symbol, date, security_name, action_type, ex_date, record_date, source)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
            (
                symbol,
                date_iso,
                security_name,
                action_type,
                ex_date_str,
                record_date,
                "NSE-API",
            ),
        )
        inserted += cur.rowcount
    conn.commit()
    return inserted


def enrich_corporate_actions(force: bool = False, days_back: int = 90):
    """
    Enrich the corporate_actions table.
    If force=False, only fetch from the last recorded date to yesterday.
    If force=True, fetch last `days_back` days (useful for backfill).
    """
    db_path = os.path.join(DB_DIR, "myra_institutional.db")
    conn = sqlite3.connect(db_path)

    # Get current max date
    cur = conn.cursor()
    cur.execute("SELECT MAX(date) FROM corporate_actions")
    row = cur.fetchone()
    max_date = row[0] if row and row[0] else None

    today = datetime.now().date()
    if force:
        start_date = today - timedelta(days=days_back)
        from_dt = start_date.strftime("%d-%m-%Y")
        to_dt = today.strftime("%d-%m-%Y")
        logger.info(f"Force fetch: {from_dt} to {to_dt}")
    elif max_date is None:
        # No data – fetch last year
        start_date = today - timedelta(days=365)
        from_dt = start_date.strftime("%d-%m-%Y")
        to_dt = today.strftime("%d-%m-%Y")
        logger.info(f"First run, fetching last 365 days: {from_dt} to {to_dt}")
    else:
        # Incremental: fetch from max_date+1 to yesterday
        start = datetime.strptime(max_date, "%Y-%m-%d").date() + timedelta(days=1)
        if start > today:
            logger.info("Data is already up to date.")
            conn.close()
            return
        from_dt = start.strftime("%d-%m-%Y")
        to_dt = today.strftime("%d-%m-%Y")
        logger.info(f"Incremental fetch: {from_dt} to {to_dt}")

    records = fetch_corporate_actions(from_dt, to_dt)
    if not records:
        logger.warning("No records fetched.")
        conn.close()
        return

    filtered = _filter_actions(records)
    logger.info(f"Fetched {len(records)} total, {len(filtered)} after filtering.")
    if filtered:
        inserted = _insert_records(conn, filtered)
        logger.info(f"Inserted {inserted} new records.")
    else:
        logger.info("No relevant actions to insert.")

    conn.close()


if __name__ == "__main__":
    # For testing
    logging.basicConfig(level=logging.INFO)
    enrich_corporate_actions(force=True, days_back=90)
