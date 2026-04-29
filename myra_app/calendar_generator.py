import logging
import sqlite3
from datetime import datetime, timedelta

import pandas as pd

logger = logging.getLogger(__name__)


def generate_calendar(db_path="calendar.db", start_year=2021, end_year=2026):
    """
    Generates a market calendar for NSE trading days.
    Includes holiday logic and weekend handling.
    """
    logger.info(f"[MYRA] Generating Market Calendar at {db_path}...")
    print(f"[MYRA] Generating Market Calendar at {db_path}...")

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS market_calendar (
            date TEXT PRIMARY KEY,
            is_trading_day INTEGER NOT NULL,
            holiday_name TEXT
        )
    """)

    # Base list (approximate/simplified for demonstration)
    fixed_holidays = {
        "01-26": "Republic Day",
        "05-01": "Maharashtra Day",
        "08-15": "Independence Day",
        "10-02": "Gandhi Jayanti",
        "12-25": "Christmas",
    }

    start_date = datetime(start_year, 1, 1)
    end_date = datetime(end_year, 12, 31)

    records = []
    current_date = start_date

    while current_date <= end_date:
        date_str = current_date.date().isoformat()
        mm_dd = f"{current_date.month:02d}-{current_date.day:02d}"

        is_trading = True
        holiday_name = ""

        if current_date.weekday() >= 5:
            is_trading = False
            holiday_name = "Weekend"
        elif mm_dd in fixed_holidays:
            is_trading = False
            holiday_name = fixed_holidays[mm_dd]

        records.append((date_str, 1 if is_trading else 0, holiday_name))
        current_date += timedelta(days=1)

    cursor.executemany(
        "INSERT OR REPLACE INTO market_calendar VALUES (?, ?, ?)", records
    )
    conn.commit()
    conn.close()

    # Priority 4: Fallback Warning Log
    logger.warning(
        "[CALENDAR] Auto-generated calendar in use. This relies on approximations and does not include ad-hoc NSE holidays. For strict accuracy, update `market_calendar` table manually."
    )


if __name__ == "__main__":
    generate_calendar()
