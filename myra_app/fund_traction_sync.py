"""
Fund Traction Sync
Downloads monthly cross-fund-holdings-traction JSON from GitHub Pages
and loads into myra_valuation.db.

Actual JSON structure (from GitHub Pages):
{
  "stocks": [
    {
      "stock_key": "NAME:torrent pharmaceuticals",
      "name": "Torrent Pharmaceuticals Limited",
      "nse": "", "bse": "", "sector": "",
      "direction": "mixed",
      "score": 492.65,
      "fund_count": 17,
      "new_entry_count": 13,
      "breadth_exit": 1,
      "breadth_hold": 1,
      "funds": [...],
      "entry_estimate": {
        "nse": "TORNTPHARM",
        "month": "2026-07",
        "month_end_close": 5122.80,
        "sma_30": 4826.40,
        "close_latest": 4896.50,
        "pct_vs_sma": 1.45,
        ...
      }
    }, ...
  ]
}
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
    "january": "01",
    "february": "02",
    "march": "03",
    "april": "04",
    "may": "05",
    "june": "06",
    "july": "07",
    "august": "08",
    "september": "09",
    "october": "10",
    "november": "11",
    "december": "12",
}
_MONTH_NAMES = list(_MONTH_MAP.keys())

# Earliest month this sync will import. Previously a local inside
# _list_available_months(); promoted here so the expected-month range can be
# computed and so a 404 can be reported. VALUE UNCHANGED.
MIN_MONTH = "2026-04"

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

_CREATE_INDEX_FUND_TRACTION_MONTH = """
CREATE INDEX IF NOT EXISTS idx_fund_traction_month ON fund_traction(month)
"""


# ── Helpers ─────────────────────────────────────────────────────────────────


def _get_db_path() -> str:
    return os.path.join(DB_DIR, "myra_valuation.db")


def _ensure_tables(conn: sqlite3.Connection) -> None:
    """Create fund_traction and sync_metadata tables if they don't exist."""
    conn.execute(_CREATE_FUND_TRACTION)
    conn.execute(_CREATE_SYNC_METADATA)
    conn.execute(_CREATE_INDEX_FUND_TRACTION_MONTH)
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


def _expected_months() -> list[str]:
    """Months this run expects to find, from MIN_MONTH to the current month.

    MIN_MONTH is deliberately unchanged; this only names the range so that a
    month which is 404 can be reported instead of silently skipped.
    """
    today = date.today()
    min_year, min_m = (int(x) for x in MIN_MONTH.split("-"))
    candidates = []
    for year in range(min_year, today.year + 1):
        start_m = min_m if year == min_year else 1
        end_month = 12 if year < today.year else today.month
        for m in range(start_m, end_month + 1):
            candidates.append(f"{year}-{m:02d}")
    return candidates


def _probe_months(base_url: str, months: list[str]) -> tuple:
    """HEAD-probe each month and report found / missing / network-error.

    Returns (found, missing, errors) where missing is a list of
    (month, status_code) and errors a list of (month, message).

    The previous implementation returned only the months that responded 200
    and discarded every 404 and every network error. That is what allowed a
    fully-dead upstream to look like a clean, up-to-date run.
    """
    found: list[str] = []
    missing: list[tuple] = []
    errors: list[tuple] = []
    for month in months:
        year, m = month.split("-")
        month_name = _MONTH_NAMES[int(m) - 1]
        url = f"{base_url}{month_name}_traction.json"
        try:
            r = requests.head(url, timeout=5)
        except Exception as exc:
            errors.append((month, f"{type(exc).__name__}: {exc}"))
            continue
        if r.status_code == 200:
            found.append(month)
        else:
            missing.append((month, r.status_code))
        logger.debug(f"Probed {month} -> {r.status_code}")
    return found, missing, errors


def _list_available_months(base_url: str) -> list[str]:
    """Months that responded 200. Kept for backwards compatibility.

    Callers that need to know what was MISSING should use _probe_months
    directly; this helper cannot express a 404.
    """
    found, _missing, _errors = _probe_months(base_url, _expected_months())
    return found


def _download_and_parse(url: str) -> list[dict]:
    """Download a JSON file and return the stocks list.

    Handles both:
    - Direct list: [{...}, ...]
    - Dict with stocks key: {"stocks": [{...}, ...], ...}
    """
    try:
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.warning(f"Failed to download {url}: {e}")
        return []

    # Extract stocks list from various structures
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("stocks", "topTraction", "data", "items"):
            if key in data and isinstance(data[key], list):
                return data[key]
        logger.warning(
            f"JSON from {url} is a dict but has no stocks/topTraction/data/items key. "
            f"Keys found: {list(data.keys())}"
        )
        return []

    logger.warning(f"Unexpected JSON type from {url}: {type(data).__name__}")
    return []


def _extract_symbol(stock: dict) -> str:
    """Extract the NSE symbol from a stock entry.

    Priority:
    1. entry_estimate.nse (clean NSE symbol like "TORNTPHARM")
    2. stock.nse (if non-empty)
    3. stock_key parsed (strip "NAME:" prefix, uppercase)
    4. stock.name (uppercase, spaces removed)
    """
    # Best source: entry_estimate.nse
    entry = stock.get("entry_estimate") or {}
    nse = str(entry.get("nse", "")).strip()
    if nse:
        return nse.upper()

    # Fallback: stock-level nse field
    nse_direct = str(stock.get("nse", "")).strip()
    if nse_direct:
        return nse_direct.upper()

    # Fallback: parse stock_key "NAME:torrent pharmaceuticals"
    stock_key = str(stock.get("stock_key", ""))
    if stock_key.startswith("NAME:"):
        name_part = stock_key[5:].strip()
        if name_part:
            return name_part.upper().replace(" ", "")

    # Last resort: stock name
    name = str(stock.get("name", "")).strip()
    if name:
        return name.upper().replace(" ", "").replace(".", "").replace(",", "")

    return ""


def _parse_stock(stock: dict, month: str) -> dict | None:
    """Parse a stock entry into our schema. Returns None if symbol is empty."""
    symbol = _extract_symbol(stock)
    if not symbol:
        return None

    entry = stock.get("entry_estimate") or {}

    # Robust field extraction with fallbacks
    def _float(val, default=None):
        if val is None:
            return default
        try:
            return float(val)
        except (ValueError, TypeError):
            return default

    def _int(val, default=None):
        if val is None:
            return default
        try:
            return int(val)
        except (ValueError, TypeError):
            return default

    return {
        "symbol": symbol,
        "month": month,
        "traction_score": _float(stock.get("score")),
        "number_of_funds": _int(stock.get("fund_count")),
        "adds_new": _int(stock.get("new_entry_count")),
        "reduces_closes": _int(stock.get("breadth_exit")),
        "sma_30": _float(entry.get("sma_30")),
        "month_end_close": _float(entry.get("month_end_close")),
        "close_latest": _float(entry.get("close_latest")),
        "pct_vs_sma": _float(entry.get("pct_vs_sma")),
    }


def _insert_rows(conn: sqlite3.Connection, rows: list[dict], month: str) -> int:
    """Insert traction rows into the fund_traction table. Returns count inserted."""
    inserted = 0
    for stock in rows:  # noqa: PG-ITERROWS
        parsed = _parse_stock(stock, month)
        if not parsed:
            continue

        conn.execute(  # noqa: PG-NPLUS1
            """INSERT OR REPLACE INTO fund_traction
               (symbol, month, traction_score, number_of_funds, adds_new,
                reduces_closes, sma_30, month_end_close, close_latest, pct_vs_sma)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                parsed["symbol"],
                parsed["month"],
                parsed["traction_score"],
                parsed["number_of_funds"],
                parsed["adds_new"],
                parsed["reduces_closes"],
                parsed["sma_30"],
                parsed["month_end_close"],
                parsed["close_latest"],
                parsed["pct_vs_sma"],
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

        # Probe the months we expect, keeping what is MISSING so a dead
        # upstream cannot masquerade as an up-to-date run.
        logger.info("Fund traction: probing available months...")
        expected = _expected_months()
        found, missing, errors = _probe_months(TRACTION_BASE_URL, expected)
        available = list(found)

        problems: list[str] = []
        if missing:
            detail = ", ".join(f"{m}({c})" for m, c in missing[:8])
            more = f" +{len(missing) - 8} more" if len(missing) > 8 else ""
            problems.append(
                f"{len(missing)} of {len(expected)} expected month(s) are "
                f"unavailable upstream (non-200): {detail}{more}"
            )
        if errors:
            detail = ", ".join(f"{m}({e})" for m, e in errors[:5])
            problems.append(f"{len(errors)} month probe(s) errored: {detail}")

        if not available:
            msg = "; ".join(problems) or "no months found at remote URL"
            result["error"] = (
                f"Fund traction upstream unusable: {msg}. Expected months "
                f"{expected[0]}..{expected[-1]}."
            )
            logger.error("Fund traction: %s", result["error"])
            return result

        logger.info(
            f"Fund traction: found {len(available)} of {len(expected)} "
            f"expected months: {available}"
        )
        if problems:
            # Import what IS reachable so no data is lost, but do not report a
            # clean success while expected months are missing.
            logger.error(
                "Fund traction: upstream incomplete -- %s", "; ".join(problems)
            )

        # Filter to only new months (unless force=True)
        last_imported = _get_last_imported_month(conn)
        if not force and last_imported:
            available = [m for m in available if m > last_imported]

        if not available:
            if problems:
                result["success"] = False
                result["error"] = (
                    "Fund traction: no new months imported AND upstream is "
                    "incomplete -- " + "; ".join(problems)
                )
                return result
            result["success"] = True
            result["last_month"] = last_imported
            logger.info(f"Fund traction: already up to date (last: {last_imported})")
            return result

        logger.info(f"Fund traction: syncing {len(available)} months: {available}")

        failed_downloads: list[str] = []
        for month in available:
            year, m = month.split("-")
            month_name = _MONTH_NAMES[int(m) - 1]
            url = f"{TRACTION_BASE_URL}{month_name}_traction.json"

            stocks = _download_and_parse(url)
            if not stocks:
                logger.warning(f"Fund traction: no data for month {month} at {url}")
                failed_downloads.append(month)
                continue

            count = _insert_rows(conn, stocks, month)
            result["rows_inserted"] += count
            result["months_synced"] += 1
            result["last_month"] = month
            logger.info(f"Fund traction: month {month} — {count} stocks inserted")

        # Update the last imported month
        if result["last_month"]:
            _set_last_imported_month(conn, result["last_month"])

        if problems or failed_downloads:
            bits = list(problems)
            if failed_downloads:
                bits.append(
                    f"download returned no usable rows for: "
                    f"{', '.join(failed_downloads)}"
                )
            result["success"] = False
            result["error"] = "Fund traction sync incomplete -- " + "; ".join(bits)
            logger.error("Fund traction: %s", result["error"])
            return result

        result["success"] = True
        return result

    except Exception as e:
        result["error"] = str(e)
        logger.exception("Fund traction sync failed")
        return result
    finally:
        conn.close()


# ── Traction SMA updater ────────────────────────────────────────────────────


def update_traction_sma() -> dict:
    """Recompute pct_vs_sma for the latest fund_traction month from raw closes.

    ``technical_data`` has no sma_30 column, so the SMA is computed here using
    the reference methodology (cross-fund-holdings-traction prices.py):
      - mean of the last 30 available closes if >=30 closes exist;
      - else mean of all available closes if >=15;
      - else None (row skipped, existing pct_vs_sma preserved).

    Batch implementation: one temp-table join against technical_data and one
    bulk UPDATE transaction (no per-symbol queries).

    Returns:
        dict with keys: success, month, updated, skipped_no_sma, error
    """
    result = {
        "success": False,
        "month": None,
        "updated": 0,
        "skipped_no_sma": 0,
        "error": None,
    }

    val_conn = None
    tech_conn = None
    try:
        val_conn = sqlite3.connect(os.path.join(DB_DIR, "myra_valuation.db"))
        tech_conn = sqlite3.connect(os.path.join(DB_DIR, "myra_technical.db"))

        # 1. Latest traction month
        row = val_conn.execute("SELECT MAX(month) FROM fund_traction").fetchone()
        latest_month = row[0] if row else None
        if not latest_month:
            result["success"] = True
            logger.info("Traction SMA update: no fund_traction rows, nothing to do")
            return result
        result["month"] = latest_month

        # 2. Target symbols for that month
        symbols = [
            r[0]
            for r in val_conn.execute(
                "SELECT symbol FROM fund_traction WHERE month = ?", (latest_month,)
            ).fetchall()
        ]
        if not symbols:
            result["success"] = True
            logger.info("Traction SMA update: no symbols for month %s", latest_month)
            return result

        # 3. Temp table + ONE batched window query (perf guard: no N+1)
        tech_conn.execute(
            "CREATE TEMP TABLE IF NOT EXISTS _ft_symbols (symbol TEXT PRIMARY KEY)"
        )
        tech_conn.execute("DELETE FROM _ft_symbols")
        tech_conn.executemany(
            "INSERT OR IGNORE INTO _ft_symbols (symbol) VALUES (?)",
            [(s,) for s in symbols],
        )
        rows = tech_conn.execute(
            """
            SELECT symbol, date, close
            FROM (
                SELECT symbol, date, close,
                       ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY date DESC) AS rn
                FROM technical_data
                WHERE symbol IN (SELECT symbol FROM _ft_symbols) AND close IS NOT NULL
            )
            WHERE rn <= 35
            ORDER BY symbol, date ASC
            """
        ).fetchall()
        logger.info(
            "Traction SMA update: month=%s symbols=%d fetched_rows=%d",
            latest_month,
            len(symbols),
            len(rows),
        )

        # Group fetched closes per symbol (rows already ordered by date ASC)
        closes_by_symbol: dict[str, list[float]] = {}
        for sym, _dt, close in rows:
            if sym not in closes_by_symbol:
                closes_by_symbol[sym] = []
            closes_by_symbol[sym].append(close)  # noqa: PG-APPEND

        # 4. Reference SMA methodology in Python
        updates = []
        skipped = 0
        for sym in symbols:
            closes = closes_by_symbol.get(sym, [])
            n = len(closes)
            if n >= 30:
                window = closes[-30:]
            elif n >= 15:
                window = closes
            else:
                skipped += 1
                continue
            sma = sum(window) / len(window)
            latest_close = closes[-1]
            if sma and sma > 0 and latest_close:
                pct = round((latest_close - sma) / sma * 100, 4)
                updates.append((pct, sym, latest_month))  # noqa: PG-APPEND
            else:
                skipped += 1
        result["skipped_no_sma"] = skipped

        # 5. Bulk UPDATE in ONE transaction (NULL sma rows never touched)
        val_conn.executemany(
            "UPDATE fund_traction SET pct_vs_sma = ? WHERE symbol = ? AND month = ?",
            updates,
        )

        # 6. Single commit + summary log
        val_conn.commit()
        result["updated"] = len(updates)
        result["success"] = True
        logger.info(
            "Traction SMA update: month=%s updated=%d skipped(no sma)=%d",
            latest_month,
            result["updated"],
            skipped,
        )
        return result

    except Exception as e:
        result["error"] = str(e)
        logger.exception("Traction SMA update failed")
        return result
    finally:
        if val_conn is not None:
            val_conn.close()
        if tech_conn is not None:
            tech_conn.close()


# ── CLI entry point ─────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    if "--update-sma" in sys.argv:
        print("Running traction SMA update...")
        print(f"Result: {update_traction_sma()}")
    else:
        force = "--force" in sys.argv
        print(f"Running fund traction sync (force={force})...")
        result = sync_fund_traction(force=force)
        print(f"Result: {result}")
