"""
Backfill SMC enrichment for all historical dates starting from 2024-01-01.

Usage:
    python tools/enrich_history.py

Processes one date at a time, commits after each, and logs progress every 20 dates.
"""

import logging
import os
import sqlite3
import sys
import time
from datetime import date, datetime, timedelta

from myra_app.constants import DB_DIR
from myra_app.librarian import Librarian
from myra_app.librarian_core import LibrarianCore

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


def main():
    tech_db = os.path.join(DB_DIR, LibrarianCore.DB_MAP["technical"])
    if not os.path.exists(tech_db):
        logger.error("Technical database not found at %s", tech_db)
        sys.exit(1)

    lib = Librarian(read_only=False)
    lib.connect()
    conn = sqlite3.connect(tech_db, timeout=60)

    distinct_dates = [
        row[0]
        for row in conn.execute(
            "SELECT DISTINCT date FROM technical_data WHERE date >= '2024-01-01' ORDER BY date"
        ).fetchall()
    ]

    if not distinct_dates:
        logger.warning("No dates found from 2024-01-01 onward.")
        return

    total = len(distinct_dates)
    logger.info(
        "Processing %d dates from %s to %s",
        total,
        distinct_dates[0],
        distinct_dates[-1],
    )

    from myra_app.feature_enrichment import process_enrichment_pipeline

    errors = 0
    start_global = time.time()

    for idx, d in enumerate(distinct_dates, start=1):
        try:
            t0 = time.time()
            process_enrichment_pipeline(lib, conn, target_date=d)
            conn.commit()
            elapsed = time.time() - t0

            if idx % 20 == 0 or idx == total or idx == 1:
                pct = idx / total * 100
                elapsed_total = time.time() - start_global
                rate = idx / elapsed_total if elapsed_total > 0 else 0
                eta = (total - idx) / rate if rate > 0 else 0
                logger.info(
                    "[%d/%d] %s done in %.1fs (%.1f%%, %.1f dates/s, ETA %.0fs)",
                    idx,
                    total,
                    d,
                    elapsed,
                    pct,
                    rate,
                    eta,
                )
        except Exception as e:
            errors += 1
            logger.error("Failed on date %s: %s", d, e)
            conn.rollback()

    elapsed_total = time.time() - start_global
    logger.info(
        "Finished processing %d dates in %.1fs with %d errors.",
        total,
        elapsed_total,
        errors,
    )

    conn.close()
    lib.close()


if __name__ == "__main__":
    main()
