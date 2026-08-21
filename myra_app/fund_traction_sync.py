"""
Fund Traction Sync
Downloads monthly cross-fund-holdings-traction JSON from GitHub Pages
and loads into myra_valuation.db.

Table: fund_traction
  - symbol, month, traction_score, number_of_funds, adds_new,
    reduces_closes, sma_30, month_end_close, close_latest, pct_vs_sma
"""

import logging
import os
import sqlite3
from datetime import date, datetime

import requests

from myra_app.constants import DB_DIR, TRACTION_BASE_URL

logger = logging.getLogger(__name__)

# ── Month name → ISO mapping ───────────────────────────────────────────────
_MONTH_MAP = {
    "january": "01", "february": "02", "march": "03", "april": "04",
    "may": "05", "june": "06", "july": "07", "august": "08",
    "september": "09", "october": "10", "november": "11", "december": "12",
}

# ── Table DDL ───────────────────────────────────────────────────────────────
_CREATE_FUND_TRACTION = """
CREATE TABLE IF NOT EXISTS fund_traction (
    symbol          TEXT    NOT NULL,
    month           TEXT    NOT NULL,   -- YYYY-MM
    traction_score  REAL,
    number_of_funds INTEGER,
    adds_new        INTEGER,
    reduces_closes  INTEGER,
    sma_30          REAL,
    month_end_close REAL,
    close_latest    REAL,
    pct_vs_sma      REAL,
    PRIMARY KEY (symbol, month)
)
"""

_CREATE_SYNC_METADATA = """
CREATE TABLE IF NOT EXISTS sync_metadata (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    updated_at  TEXT NOT NULL
)
"""


# ── Helpers ─────────────────────────────────────────────────────────────────

def _get_db_path() -> str:
    return os.path.join(DB_DIR, "myra_valuation.db")


def _ensure_tables(conn: sqlite3.Connection) -> None:
    """Create fund_traction and sync_metadata tables if they don't exist."""
    conn.execute(_CREATE_FUND_TRACTION)
    conn.execute(_CREATE_SYNC_METADATA)
    conn.commit()


def _get_last_imported_month(conn: sqlite3.Connection) -> str | None:
    """Return the last imported month (YYYY-MM) or None."""
    row = conn.execute(
        "SELECT value FROM sync_metadata WHERE key = ?",
        ("fund_traction_last_month",),
    ).fetchone()
    return row[0] if row else None


def _set_last_imported_month(conn: sqlite3.Connection, month: str) -> None:
    """Store the last imported month."""
    conn.execute(
        "INSERT OR REPLACE INTO sync_metadata (key, value, updated_at) VALUES (?, ?, ?)",
        ("fund_traction_last_month", month, datetime.now().isoformat()),
    )
    conn.commit()


def _filename_to_month(filename: str) -> str | None:
    """Convert a filename like 'june_traction.json' to 'YYYY-MM'.

    Tries current year first, then previous year.
    Returns None if the filename doesn't match the expected pattern.
    """
    name = filename.lower().replace(".json", "").replace("_traction", "").strip()
    month_num = _MONTH_MAP.get(name)
    if not month_num:
        return None

    current_year = date.today().year
    # Try current year first, then previous year
    return f"{current_year}-{month_num}"


def _list_available_months(base_url: str) -> list[str]:
    """Fetch the index page and extract available month filenames.

    Expects the GitHub Pages site to list files. Falls back to a
    known set of month names if the index isn't parseable.
    """
    try:
        resp = requests.get(base_url, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        logger.warning(f"Could not fetch index from {base_url}: {e}")
        return []

    # Try to extract .json filenames from the response
    import re
    # Match patterns like "june_traction.json" or "2026-06_traction.json"
    json_files = re.findall(r'href="([^"]*traction\.json)"', resp.text, re.IGNORECASE)
    if not json_files:
        # Fallback: try to find any JSON files
        json_files = re.findall(r'href="([^"]*\.json)"', resp.text, re.IGNORECASE)

    months = []
    for f in json_files:
        fname = os.path.basename(f)
        month = _filename_to_month(fname)
        if month:
            months.append(month)  # noqa: PG-APPEND

    return sorted(set(months))


def _download_and_parse(url: str) -> list[dict]:
    """Download a JSON file and return the parsed list of dicts."""
    try:
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, list):
            return data
        logger.warning(f"Expected JSON array from {url}, got {type(data).__name__}")
        return []
    except Exception as e:
        logger.warning(f"Failed to download {url}: {e}")
        return []


def _insert_rows(conn: sqlite3.Connection, rows: list[dict], month: str) -> int:
    """Insert traction rows into the fund_traction table. Returns count inserted."""
    inserted = 0
    for row in rows:  # noqa: PG-ITERROWS
        symbol = str(row.get("symbol", "")).strip().upper()
        if not symbol:
            continue

        conn.execute(  # noqa: PG-NPLUS1
            """INSERT OR REPLACE INTO fund_traction
               (symbol, month, traction_score, number_of_funds, adds_new,
                reduces_closes, sma_30, month_end_close, close_latest, pct_vs_sma)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                symbol,
                month,
                row.get("traction_score"),
                row.get("number_of_funds"),
                row.get("adds_new"),
                row.get("reduces_closes"),
                row.get("sma_30"),
                row.get("month_end_close"),
                row.get("close_latest"),
                row.get("pct_vs_sma"),
            ),
        )
        inserted += 1
    conn.commit()
    return inserted


# ── Main sync function ──────────────────────────────────────────────────────

def sync_fund_traction(force: bool = False) -> dict:
    """Sync fund traction data from GitHub Pages.

    Args:
        force: If True, re-download all months regardless of last sync.

    Returns:
        dict with keys: success, months_synced, rows_inserted, last_month, error
    """
    result = {
        "success": False,
        "months_synced": 0,
        "rows_inserted": 0,
        "last_month": None,
        "error": None,
    }

    db_path = _get_db_path()
    conn = sqlite3.connect(db_path)
    try:
        _ensure_tables(conn)

        # Get available months from the remote index
        available = _list_available_months(TRACTION_BASE_URL)
        if not available:
            result["error"] = "No months found at remote URL or URL not configured"
            logger.warning("Fund traction: no months available at remote")
            return result

        # Filter to only new months (unless force=True)
        last_imported = _get_last_imported_month(conn)
        if not force and last_imported:
            available = [m for m in available if m > last_imported]

        if not available:
            result["success"] = True
            result["last_month"] = last_imported
            logger.info(f"Fund traction: already up to date (last: {last_imported})")
            return result

        logger.info(f"Fund traction: syncing {len(available)} months: {available}")

        for month in available:
            # Build the download URL
            # Try both formats: "june_traction.json" and "2026-06_traction.json"
            month_num = int(month.split("-")[1])
            month_name = list(_MONTH_MAP.keys())[month_num - 1]
            year = month.split("-")[0]

            # Try month_name format first, then numeric format
            urls_to_try = [
                f"{TRACTION_BASE_URL}{month_name}_traction.json",
                f"{TRACTION_BASE_URL}{year}-{month_num:02d}_traction.json",
                f"{TRACTION_BASE_URL}{month}_traction.json",
            ]

            rows = []
            for url in urls_to_try:
                rows = _download_and_parse(url)
                if rows:
                    break

            if not rows:
                logger.warning(f"Fund traction: no data for month {month}")
                continue

            count = _insert_rows(conn, rows, month)
            result["rows_inserted"] += count
            result["months_synced"] += 1
            result["last_month"] = month
            logger.info(f"Fund traction: month {month} — {count} rows inserted")

        # Update the last imported month
        if result["last_month"]:
            _set_last_imported_month(conn, result["last_month"])

        result["success"] = True
        return result

    except Exception as e:
        result["error"] = str(e)
        logger.exception("Fund traction sync failed")
        return result
    finally:
        conn.close()


# ── CLI entry point ─────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    force = "--force" in sys.argv
    print(f"Running fund traction sync (force={force})...")
    result = sync_fund_traction(force=force)
    print(f"Result: {result}")
