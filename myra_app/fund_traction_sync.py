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

import json
import logging
import os
import sqlite3
import json
from datetime import date, datetime

import requests

from myra_app.constants import DB_DIR, TRACTION_BASE_URL

logger = logging.getLogger(__name__)


# â”€â”€ Month name â†’ ISO mapping â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
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

# â”€â”€ Table DDL â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Columns after pct_vs_sma are additive (ADD COLUMN only -- nothing existing is
# ever renamed, retyped or dropped). They carry the identity + breadth fields
# the Traction Board needs so its cards are filterable/sortable entirely from
# the DB instead of re-reading the upstream JSON.
_CREATE_FUND_TRACTION = """
CREATE TABLE IF NOT EXISTS fund_traction (
    symbol                  TEXT    NOT NULL,
    month                   TEXT    NOT NULL,   -- YYYY-MM
    traction_score          REAL,
    number_of_funds         INTEGER,
    adds_new                INTEGER,
    reduces_closes          INTEGER,
    sma_30                  REAL,
    month_end_close         REAL,
    close_latest            REAL,
    pct_vs_sma              REAL,
    stock_key               TEXT,
    name                    TEXT,
    nse                     TEXT,
    bse                     TEXT,
    sector                  TEXT,
    direction               TEXT,
    median_share_change_pct REAL,
    median_weight_delta_pp  REAL,
    breadth_active          INTEGER,
    breadth_hold            INTEGER,
    PRIMARY KEY (symbol, month)
)
"""

# Columns added after the original fund_traction DDL was first shipped. Kept as
# a list so SchemaRegistry (which is ALTER-TABLE-only) and this CREATE can be
# cross-checked and so a live DB missing any of them can be patched idempotently.
FUND_TRACTION_ADDED_COLUMNS: tuple[tuple[str, str], ...] = (
    ("stock_key", "TEXT"),
    ("name", "TEXT"),
    ("nse", "TEXT"),
    ("bse", "TEXT"),
    ("sector", "TEXT"),
    ("direction", "TEXT"),
    ("median_share_change_pct", "REAL"),
    ("median_weight_delta_pp", "REAL"),
    ("breadth_active", "INTEGER"),
    ("breadth_hold", "INTEGER"),
)

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

# â”€â”€ Traction Board tables (additive; fund_traction is never altered) â”€â”€â”€â”€â”€â”€
# Per-fund breakdown lines backing the Pages-parity board cards. One row per
# (symbol, month, fund). Re-sync is idempotent via INSERT OR REPLACE.
_CREATE_FUND_TRACTION_FUNDS = """
CREATE TABLE IF NOT EXISTS fund_traction_funds (
    symbol              TEXT    NOT NULL,
    month               TEXT    NOT NULL,
    fund_slug           TEXT,
    fund_name           TEXT    NOT NULL,
    activity            TEXT,
    share_change_pct    REAL,
    share_change_abs    REAL,
    weight_delta_pp     REAL,
    current_weight_pct  REAL,
    is_new              INTEGER,
    history_url         TEXT,
    PRIMARY KEY (symbol, month, fund_name)
)
"""

_CREATE_INDEX_FUND_TRACTION_FUNDS_MONTH = """
CREATE INDEX IF NOT EXISTS idx_fund_traction_funds_month
    ON fund_traction_funds(month)
"""

_CREATE_INDEX_FUND_TRACTION_FUNDS_SYMBOL_MONTH = """
CREATE INDEX IF NOT EXISTS idx_fund_traction_funds_symbol_month
    ON fund_traction_funds(symbol, month)
"""

# Grounded insights + top-traction rows per month. `kind` is 'insight' or
# 'top_traction'; narrative/metric payloads live in JSON columns so the
# schema stays stable as the upstream insight format evolves.
_CREATE_FUND_TRACTION_INSIGHTS = """
CREATE TABLE IF NOT EXISTS fund_traction_insights (
    month           TEXT    NOT NULL,
    kind            TEXT    NOT NULL,
    item_id         TEXT    NOT NULL,
    section         TEXT,
    headline        TEXT,
    action          TEXT,
    stock_keys      TEXT,
    body            TEXT,
    citations_json  TEXT,
    source          TEXT,
    score           REAL,
    fund_count      INTEGER,
    PRIMARY KEY (month, kind, item_id)
)
"""

_CREATE_INDEX_FUND_TRACTION_INSIGHTS_MONTH = """
CREATE INDEX IF NOT EXISTS idx_fund_traction_insights_month
    ON fund_traction_insights(month)
"""

# Manual watchlist pins. Keyed by stock_key so pins survive re-syncs;
# auto-pins are derived at read time and never stored here.
_CREATE_FUND_TRACTION_WATCHLIST = """
CREATE TABLE IF NOT EXISTS fund_traction_watchlist (
    stock_key   TEXT    NOT NULL PRIMARY KEY,
    name        TEXT,
    nse         TEXT,
    pinned_at   TEXT,
    source      TEXT
)
"""


# â”€â”€ Helpers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


def _get_db_path() -> str:
    return os.path.join(DB_DIR, "myra_valuation.db")


def resolved_symbol_sql(alias: str) -> str:
    """SQL expression for a row's canonical ticker: resolved ``nse`` else ``symbol``.

    ``fund_traction.symbol`` is a name-derived key for rows where upstream gave
    no NSE ticker; ``myra_app.traction_symbols`` fills ``nse`` with the real
    ticker. Joining market data on this expression keeps both the resolved rows
    and the not-yet-resolved ones (which fall back to ``symbol``).
    """
    return f"COALESCE(NULLIF({alias}.nse, ''), {alias}.symbol)"


def _ensure_tables(conn: sqlite3.Connection) -> None:
    """Create fund_traction and sync_metadata tables if they don't exist.

    Also creates the additive Traction Board tables (fund_traction_funds,
    fund_traction_insights, fund_traction_watchlist). The fund_traction
    table itself is never altered here.
    """
    conn.execute(_CREATE_FUND_TRACTION)
    conn.execute(_CREATE_SYNC_METADATA)
    conn.execute(_CREATE_INDEX_FUND_TRACTION_MONTH)
    _ensure_fund_traction_columns(conn)
    conn.execute(_CREATE_FUND_TRACTION_FUNDS)
    conn.execute(_CREATE_INDEX_FUND_TRACTION_FUNDS_MONTH)
    conn.execute(_CREATE_INDEX_FUND_TRACTION_FUNDS_SYMBOL_MONTH)
    conn.execute(_CREATE_FUND_TRACTION_INSIGHTS)
    conn.execute(_CREATE_INDEX_FUND_TRACTION_INSIGHTS_MONTH)
    conn.execute(_CREATE_FUND_TRACTION_WATCHLIST)
    conn.commit()


def _ensure_fund_traction_columns(conn: sqlite3.Connection) -> int:
    """Idempotently ADD COLUMN the Traction Board fields onto fund_traction.

    SchemaRegistry is the canonical migrator, but this sync can run before the
    API starts, so it patches its own table. Purely additive: an existing
    column is skipped, never dropped or redefined.
    """
    have = {
        row[1] for row in conn.execute("PRAGMA table_info(fund_traction)").fetchall()
    }
    added = 0
    for col, decl in FUND_TRACTION_ADDED_COLUMNS:
        if col not in have:
            conn.execute(f"ALTER TABLE fund_traction ADD COLUMN {col} {decl}")
            added += 1
    if added:
        logger.info("Fund traction: added %d board column(s) to fund_traction", added)
    return added


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


def _prev_month(today: date) -> list[tuple[int, int]]:
    """(year, month) tuple for the calendar month before ``today``'s month."""
    if today.month == 1:
        return [(today.year - 1, 12)]
    return [(today.year, today.month - 1)]


def has_unsynced_month(base_url: str | None = None) -> list[str]:
    """Read-only smart gate: months available upstream but not yet imported.

    Probes ONLY the two months that could possibly be new (the previous and
    the current calendar month) — a cheap HEAD-only check with no download and
    no DB writes. A month is "unsynced" when it responds 200 upstream AND is
    newer than our last-imported watermark.

    This exists so the scheduler (and the manual pipeline flag) can ask "is
    there anything new?" without paying for a full sync. It never mutates state
    and never reports 404s as failures — a missing prior month just means that
    month isn't published yet, which is normal.
    """
    url_base = base_url or TRACTION_BASE_URL
    watermark = None
    try:
        conn = sqlite3.connect(_get_db_path())
        try:
            watermark = _get_last_imported_month(conn)
        finally:
            conn.close()
    except sqlite3.Error:
        watermark = None

    # Only the previous + current month can be newly published since our last
    # run; probing the whole history would be wasteful for a gate.
    today = date.today()
    candidates: list[str] = []
    for year, month in ((today.year, today.month), *_prev_month(today)):
        candidates.append(f"{year}-{month:02d}")

    found, _missing, _errors = _probe_months(url_base, candidates)
    return [m for m in found if watermark is None or m > watermark]


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
        "stock_key": str(stock.get("stock_key") or ""),
        "name": str(stock.get("name") or ""),
        # entry_estimate.nse is the canonical ticker when upstream supplies one;
        # the top-level nse field is usually empty. Prefer the former so the
        # read path can join market data from day one.
        "nse": str(
            (stock.get("entry_estimate") or {}).get("nse") or stock.get("nse") or ""
        ),
        "bse": str(stock.get("bse") or ""),
        "sector": str(stock.get("sector") or ""),
        "direction": str(stock.get("direction") or ""),
        "median_share_change_pct": _float(stock.get("median_share_change_pct")),
        "median_weight_delta_pp": _float(stock.get("median_weight_delta_pp")),
        "breadth_active": _int(stock.get("breadth_active")),
        "breadth_hold": _int(stock.get("breadth_hold")),
    }


def _insert_rows(conn: sqlite3.Connection, rows: list[dict], month: str) -> int:
    """Insert traction rows into fund_traction. Returns count inserted.

    Also persists the per-fund breakdown into fund_traction_funds
    (idempotent via INSERT OR REPLACE). A stock whose payload carries no
    funds leaves its existing breakdown rows untouched (failed/partial
    fetch must not clobber valid rows).
    """
    inserted = 0
    for stock in rows:  # noqa: PG-ITERROWS
        parsed = _parse_stock(stock, month)
        if not parsed:
            continue

        conn.execute(  # noqa: PG-NPLUS1
            """INSERT OR REPLACE INTO fund_traction
               (symbol, month, traction_score, number_of_funds, adds_new,
                reduces_closes, sma_30, month_end_close, close_latest, pct_vs_sma,
                stock_key, name, nse, bse, sector, direction,
                median_share_change_pct, median_weight_delta_pp,
                breadth_active, breadth_hold)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                       ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                parsed["stock_key"],
                parsed["name"],
                parsed["nse"],
                parsed["bse"],
                parsed["sector"],
                parsed["direction"],
                parsed["median_share_change_pct"],
                parsed["median_weight_delta_pp"],
                parsed["breadth_active"],
                parsed["breadth_hold"],
            ),
        )
        inserted += 1
        _insert_stock_funds(conn, parsed["symbol"], month, stock.get("funds"))
    conn.commit()
    return inserted


def _num_or_none(val):
    """Float coercion that returns None instead of raising."""
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _insert_stock_funds(
    conn: sqlite3.Connection, symbol: str, month: str, funds
) -> int:
    """Replace one stock's per-fund breakdown rows. Returns rows written.

    No-op (0) when the payload carries no funds, so a partial fetch never
    wipes previously synced breakdown rows.
    """
    if not isinstance(funds, list):
        return 0
    conn.execute(
        "DELETE FROM fund_traction_funds WHERE symbol = ? AND month = ?",
        (symbol, month),
    )
    written = 0
    for f in funds:
        if not isinstance(f, dict):
            continue
        name = str(f.get("fund_display_name") or f.get("fund_name") or "").strip()
        if not name:
            continue
        conn.execute(
            """INSERT OR REPLACE INTO fund_traction_funds
               (symbol, month, fund_slug, fund_name, activity,
                share_change_pct, share_change_abs, weight_delta_pp,
                current_weight_pct, is_new, history_url)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                symbol,
                month,
                str(f.get("fund_slug") or ""),
                name,
                str(f.get("activity") or ""),
                _num_or_none(f.get("share_change_pct")),
                _num_or_none(f.get("share_change_abs")),
                _num_or_none(f.get("weight_delta_pp")),
                _num_or_none(f.get("current_weight_pct")),
                1 if f.get("is_new") else 0,
                str(f.get("history_url") or ""),
            ),
        )
        written += 1
    return written


def _insert_insights_doc(
    conn: sqlite3.Connection, doc: dict, month: str
) -> dict[str, int]:
    """Persist one upstream insights JSON doc for a month. Idempotent.

    Doc shape: {"monthId": ..., "source": ..., "topTraction": [...],
    "insights": [...]}. A kind with no rows leaves existing rows for that
    kind untouched, so a partial fetch never clobbers valid rows.
    """
    counts = {"insights": 0, "top_traction": 0}
    if not isinstance(doc, dict):
        return counts

    insights = doc.get("insights") or []
    if insights:
        conn.execute(
            "DELETE FROM fund_traction_insights WHERE month = ? AND kind = ?",
            (month, "insight"),
        )
        for i, ins in enumerate(insights):
            if not isinstance(ins, dict):
                continue
            keys = ins.get("stockKeys") or []
            conn.execute(
                """INSERT OR REPLACE INTO fund_traction_insights
                   (month, kind, item_id, section, headline, action,
                    stock_keys, body, citations_json, source, score, fund_count)
                   VALUES (?, 'insight', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    month,
                    str(ins.get("id") or f"ins_{i + 1:02d}"),
                    str(ins.get("section") or ins.get("type") or ""),
                    str(ins.get("headline") or ""),
                    str(ins.get("action") or ""),
                    json.dumps(keys),
                    str(ins.get("body") or ""),
                    json.dumps(ins.get("citations") or []),
                    str(doc.get("source") or ""),
                    None,
                    None,
                ),
            )
            counts["insights"] += 1

    top = doc.get("topTraction") or []
    if top:
        conn.execute(
            "DELETE FROM fund_traction_insights WHERE month = ? AND kind = ?",
            (month, "top_traction"),
        )
        for i, row in enumerate(top):
            if not isinstance(row, dict):
                continue
            key = str(row.get("stockKey") or row.get("name") or f"tt_{i + 1:02d}")
            keys = [row["stockKey"]] if row.get("stockKey") else []
            conn.execute(
                """INSERT OR REPLACE INTO fund_traction_insights
                   (month, kind, item_id, section, headline, action,
                    stock_keys, body, citations_json, source, score, fund_count)
                   VALUES (?, 'top_traction', ?, 'top_traction', ?, '',
                           ?, '', '', ?, ?, ?)""",
                (
                    month,
                    key,
                    str(row.get("name") or key),
                    json.dumps(keys),
                    str(doc.get("source") or ""),
                    _num_or_none(row.get("score")),
                    None
                    if row.get("fundCount") is None
                    else _int_or_none(row.get("fundCount")),
                ),
            )
            counts["top_traction"] += 1

    conn.commit()
    return counts


def _int_or_none(val):
    """Int coercion that returns None instead of raising."""
    if val is None:
        return None
    try:
        return int(val)
    except (ValueError, TypeError):
        return None


def _download_insights_doc(url: str) -> dict | None:
    """GET an insights JSON doc; None on any failure (never raises)."""
    try:
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.warning(f"Failed to download insights {url}: {e}")
        return None
    return data if isinstance(data, dict) else None


# â”€â”€ Main sync function â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


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
        "insights_rows": 0,
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
        insights_by_month: dict[str, int] = {}
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
            logger.info(f"Fund traction: month {month} â€” {count} stocks inserted")

            # Insights/top-traction are a separate upstream artifact. A miss here
            # is non-fatal and must not roll back the traction rows, but it is
            # surfaced so the board never silently renders an empty panel.
            ins_doc = _download_insights_doc(
                f"{TRACTION_BASE_URL}{month_name}_insights.json"
            )
            if ins_doc:
                ic = _insert_insights_doc(conn, ins_doc, month)
                insights_by_month[month] = ic["insights"] + ic["top_traction"]
            else:
                insights_by_month[month] = 0
                logger.warning(
                    f"Fund traction: no insights doc for month {month}; "
                    "board insights panel will be empty"
                )

        result["insights_rows"] = sum(insights_by_month.values())

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


# â”€â”€ Traction SMA updater â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


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


# â”€â”€ Traction Board read API â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Read-side helpers backing /api/traction-board/*. These are pure reads over
# the three additive tables plus fund_traction; nothing here writes.

# Pages-parity filters. 'all' is the unfiltered set; everything else is derived
# from direction / breadth / is_new / persistence status so no extra columns are
# needed.
BOARD_FILTERS = (
    "all",
    "added",
    "reduced",
    "new",
    "mixed",
    "still_adding",
    "reversed",
    "new_this_month",
    "watchlist",
)

BOARD_SORTS = (
    "score",
    "funds",
    "share_change",
    "weight_delta",
    "name",
    "pct_vs_sma",
    "mcap",
)

# Market-cap buckets for the board filter. Thresholds (in rupees) reuse the
# cross-buy scanner's stock_category precedent: Large >= 20,000 Cr, Mid >=
# 5,000 Cr, below that Small. 'unknown' = no fundamentals row / NULL mcap.
MCAP_BUCKETS = ("all", "large", "mid", "small", "unknown")
_MCAP_LARGE_MIN = 2e11  # 20,000 Cr
_MCAP_MID_MIN = 5e10  # 5,000 Cr


def _mcap_bucket(mcap: float | None) -> str:
    """Classify a rupees market cap into large/mid/small/unknown."""
    if not mcap or mcap <= 0:
        return "unknown"
    if mcap >= _MCAP_LARGE_MIN:
        return "large"
    if mcap >= _MCAP_MID_MIN:
        return "mid"
    return "small"


def _prev_month_label(year: int, month: int) -> str:
    """Return the previous month in YYYY-MM format."""
    if month == 1:
        return f"{year - 1:04d}-12"
    else:
        return f"{year:04d}-{month - 1:02d}"


def _enrich_board_market_data(
    rows: list[dict], target_month: str | None = None
) -> None:
    """Attach latest close + market cap to board rows, in bulk. Mutates in place.

    Price comes from technical_data (latest close, one windowed query — the
    same idiom as update_traction_sma); mcap from fundamentals (same DB, one
    query). Both are read-only joins — no extra sync, no schema change.

    Best-effort by design: any DB miss or error leaves price / market_cap_cr
    as None so the board always renders (enrichment must never break it).
    """
    symbols = sorted(
        {(r.get("resolved") or r["symbol"]) for r in rows if r.get("symbol")}
    )
    if not symbols:
        return

    # Market cap from fundamentals (valuation DB — same connection family).
    mcap_map: dict[str, float] = {}
    try:
        conn = sqlite3.connect(_get_db_path())
        try:
            for i in range(0, len(symbols), 500):
                chunk = symbols[i : i + 500]
                ph = ",".join("?" for _ in chunk)
                for sym, mc in conn.execute(
                    "SELECT symbol, market_cap FROM fundamentals "
                    f"WHERE symbol IN ({ph}) AND market_cap IS NOT NULL "
                    f"AND market_cap > 0",
                    chunk,
                ).fetchall():
                    mcap_map[sym] = mc
        finally:
            conn.close()
    except sqlite3.Error:
        mcap_map = {}
    # Latest close + prior-month close from technical_data (read-only).
    price_map: dict[str, dict[str, float | None]] = {}
    try:
        tech_path = os.path.join(DB_DIR, "myra_technical.db")
        if os.path.exists(tech_path):
            tconn = sqlite3.connect(f"file:{tech_path}?mode=ro", uri=True)
            try:
                prior_month = None
                if target_month:
                    y, m = (int(x) for x in target_month.split("-"))
                    prior_month = _prev_month_label(y, m)
                for i in range(0, len(symbols), 500):
                    chunk = symbols[i : i + 500]
                    ph = ",".join("?" for _ in chunk)
                    base = f"""
                        SELECT symbol, close FROM (
                            SELECT symbol, close,
                                   ROW_NUMBER() OVER (
                                       PARTITION BY symbol ORDER BY date DESC
                                   ) AS rn_latest,
                                   ROW_NUMBER() OVER (
                                       PARTITION BY symbol,
                                           strftime('%Y-%m', date) ORDER BY date DESC
                                   ) AS rn_prior
                            FROM technical_data
                            WHERE symbol IN ({ph}) AND close IS NOT NULL
                        ) WHERE rn_latest = 1"""
                    if prior_month:
                        base += f""" OR rn_prior = 1 AND strftime('%Y-%m', date) = {prior_month!r}"""
                    for sym, close in tconn.execute(base, chunk).fetchall():
                        if sym not in price_map:
                            price_map[sym] = {}
                        price_map[sym]["latest"] = close
                        if prior_month and "prior" not in price_map[sym]:
                            price_map[sym]["prior"] = close
            finally:
                tconn.close()
    except sqlite3.Error:
        price_map = {}

    for r in rows:
        key = r.get("resolved") or r["symbol"]
        mc = mcap_map.get(key)
        r["market_cap_cr"] = round(mc / 1e7, 1) if mc else None
        r["mcap_bucket"] = _mcap_bucket(mc)
        closes = price_map.get(key, {})
        r["price"] = closes.get("latest")
        r["prev_month_close"] = closes.get("prior")
        latest = r["price"]
        prior = r["prev_month_close"]
        if latest and prior:
            r["pct_vs_prev"] = round((latest - prior) / prior * 100, 2)
        else:
            r["pct_vs_prev"] = None


def board_months(conn: sqlite3.Connection) -> list[str]:
    """Months that have traction rows, newest first."""
    try:
        return [
            r[0]
            for r in conn.execute(
                "SELECT DISTINCT month FROM fund_traction ORDER BY month DESC"
            ).fetchall()
        ]
    except sqlite3.Error:
        return []


def _prior_month(months: list[str], month: str) -> str | None:
    """Chronologically previous *available* month (not calendar arithmetic)."""
    if month not in months:
        return None
    idx = months.index(month)
    return months[idx + 1] if idx + 1 < len(months) else None


def _activity_side(adds: int, reduces: int) -> str:
    if adds > 0 and reduces > 0:
        return "mixed"
    if adds > 0:
        return "adding"
    if reduces > 0:
        return "reducing"
    return "none"


def _persistence_status(prior_side: str | None, current_side: str) -> str:
    """Mirror of cross-fund-holdings-traction persistence._persistence_status.

    Duplicated deliberately: the board must render the SAME label the Pages
    report shows, and importing that package would couple Myra to its venv.
    """
    if prior_side is None or prior_side == "none":
        return "new_this_month"
    if current_side == "adding" and prior_side in ("adding", "mixed"):
        return "still_adding"
    if current_side == "reducing" and prior_side == "reducing":
        return "still_reducing"
    if current_side == "reducing" and prior_side == "mixed":
        return "continued_mixed"
    if current_side in ("adding", "reducing") and prior_side in ("adding", "reducing"):
        return "reversed"
    if prior_side == "mixed" and current_side == "adding":
        return "still_adding"
    return "continued_mixed"


# Downstream activity tokens: "fund added" vs "exited".
_ADD_TOKENS = ("add", "increase", "new", "bought")
_REDUCE_TOKENS = ("reduce", "decrease", "exit", "close", "sold")


def _board_rows(
    conn: sqlite3.Connection, month: str, prior_month: str | None
) -> dict[str, dict]:
    """All traction rows for `month`, enriched with per-fund lines + persistence.

    One SELECT for the month, one for prior-month sides, one bulk SELECT for the
    month's fund breakdown -- no per-symbol queries.
    """
    rows = conn.execute(
        """SELECT t.symbol, t.stock_key, t.name, t.nse, t.bse, t.sector,
                  t.direction, t.traction_score, t.number_of_funds,
                  t.adds_new, t.reduces_closes, t.breadth_active, t.breadth_hold,
                  t.median_share_change_pct, t.median_weight_delta_pp,
                  t.pct_vs_sma, t.month_end_close, t.close_latest
           FROM fund_traction t WHERE t.month = ?""",
        (month,),
    ).fetchall()

    prior_sides: dict[str, str] = {}
    if prior_month:
        for sym, a, r in conn.execute(
            "SELECT symbol, adds_new, reduces_closes FROM fund_traction WHERE month = ?",
            (prior_month,),
        ).fetchall():
            prior_sides[sym] = _activity_side(int(a or 0), int(r or 0))

    funds_by_symbol: dict[str, list[dict]] = {}
    for rec in conn.execute(
        """SELECT symbol, fund_slug, fund_name, activity, share_change_pct,
                  share_change_abs, weight_delta_pp, current_weight_pct,
                  is_new, history_url
           FROM fund_traction_funds WHERE month = ?""",
        (month,),
    ).fetchall():
        funds_by_symbol.setdefault(rec[0], []).append(
            {
                "fund_slug": rec[1],
                "fund_name": rec[2],
                "activity": rec[3],
                "share_change_pct": rec[4],
                "share_change_abs": rec[5],
                "weight_delta_pp": rec[6],
                "current_weight_pct": rec[7],
                "is_new": bool(rec[8]),
                "history_url": rec[9],
            }
        )

    def _is(activity: str | None, tokens: tuple[str, ...]) -> bool:
        a = (activity or "").strip().lower()
        return any(t in a for t in tokens)

    out: dict[str, dict] = {}
    for rec in rows:
        (
            symbol,
            stock_key,
            name,
            nse,
            bse,
            sector,
            direction,
            score,
            fund_count,
            adds,
            reduces,
            breadth_active,
            breadth_hold,
            med_share,
            med_weight,
            pct_vs_sma,
            month_end_close,
            close_latest,
        ) = rec
        adds_i = int(adds or 0)
        reduces_i = int(reduces or 0)
        funds = funds_by_symbol.get(symbol, [])
        add_lines = [f for f in funds if _is(f["activity"], _ADD_TOKENS)]
        reduce_lines = [f for f in funds if _is(f["activity"], _REDUCE_TOKENS)]
        # The per-fund breakdown is the authoritative add/reduce source (it is
        # what the Pages report filters on). Fall back to the summary columns
        # only when a stock's breakdown was never synced, so filters still work
        # on partially-enriched data.
        add_count = len(add_lines) if funds else adds_i
        reduce_count = len(reduce_lines) if funds else reduces_i
        out[symbol] = {
            "symbol": symbol,
            # canonical ticker for market-data joins (nse once resolved, else the
            # raw key so an unresolved stock still shows, just without market data)
            "resolved": (nse or symbol),
            "stock_key": stock_key or "",
            "name": name or symbol,
            "nse": nse or "",
            "bse": bse or "",
            "sector": sector or "",
            "direction": direction or "",
            "score": score,
            "fund_count": int(fund_count or 0),
            "add_count": add_count,
            "reduce_count": reduce_count,
            "breadth_active": int(breadth_active or 0),
            "breadth_hold": int(breadth_hold or 0),
            "median_share_change_pct": med_share,
            "median_weight_delta_pp": med_weight,
            "pct_vs_sma": pct_vs_sma,
            "month_end_close": month_end_close,
            "close_latest": close_latest,
            "new_entry_count": sum(1 for f in funds if f["is_new"]),
            "adds": add_lines,
            "reduces": reduce_lines,
            "holds": [f for f in funds if (f["activity"] or "").lower() == "hold"],
            "funds": funds,
            "persistence": {
                "status": _persistence_status(
                    prior_sides.get(symbol), _activity_side(add_count, reduce_count)
                ),
                "prior_month_id": prior_month or "",
            },
        }
    return out


def apply_board_filter(
    rows: list[dict], board_filter: str, watchlist: set[str]
) -> list[dict]:
    """Apply one of BOARD_FILTERS. 'all' and unknown values are pass-through."""
    f = (board_filter or "all").strip().lower()
    if f in ("all", ""):
        return rows

    def key(r):
        return r.get("stock_key") or r["symbol"]

    if f == "added":
        return [r for r in rows if r["add_count"] > 0 and r["reduce_count"] == 0]
    if f == "reduced":
        return [r for r in rows if r["reduce_count"] > 0 and r["add_count"] == 0]
    if f == "new":
        return [r for r in rows if r["new_entry_count"] > 0]
    if f == "mixed":
        return [r for r in rows if r["add_count"] > 0 and r["reduce_count"] > 0]
    if f == "still_adding":
        return [r for r in rows if r["persistence"]["status"] == "still_adding"]
    if f == "reversed":
        return [r for r in rows if r["persistence"]["status"] == "reversed"]
    if f == "new_this_month":
        return [r for r in rows if r["persistence"]["status"] == "new_this_month"]
    if f == "watchlist":
        return [r for r in rows if key(r) in watchlist]
    return rows


def apply_board_sort(
    rows: list[dict], sort_by: str, descending: bool = True
) -> list[dict]:
    """Sort by a public key. Name always sorts ascending for stable scanning."""
    key = (sort_by or "score").strip().lower()
    if key not in BOARD_SORTS:
        key = "score"
    if key == "name":
        return sorted(rows, key=lambda r: (r.get("name") or "").lower())
    col = {
        "score": "score",
        "funds": "fund_count",
        "share_change": "median_share_change_pct",
        "weight_delta": "median_weight_delta_pp",
        "pct_vs_sma": "pct_vs_sma",
        "mcap": "market_cap_cr",
    }[key]
    return sorted(
        rows, key=lambda r: (r.get(col) is None, r.get(col) or 0), reverse=descending
    )


def get_traction_board(
    month: str | None = None,
    board_filter: str = "all",
    sort_by: str = "score",
    search: str = "",
    include_funds: bool = True,
    mcap_bucket: str = "",
) -> dict:
    """Assemble the Traction Board payload for one month (default: newest).

    Stats are computed on the full month set so chips stay stable regardless of
    the active filter; rows are then filtered + searched + sorted. ``mcap_bucket``
    (one of MCAP_BUCKETS, empty = all) filters by the stock's market-cap class.
    """
    result: dict = {
        "success": False,
        "month": None,
        "months": [],
        "prior_month": None,
        "rows": [],
        "stats": {},
        "count": 0,
        "filter": board_filter,
        "sort": sort_by,
        "mcap": (mcap_bucket or "").strip().lower(),
        "error": None,
    }
    conn = sqlite3.connect(_get_db_path())
    try:
        _ensure_tables(conn)
        months = board_months(conn)
        result["months"] = months
        if not months:
            result["error"] = "No fund traction data. Run the fund traction sync first."
            return result

        target = month or months[0]
        if target not in months:
            result[
                "error"
            ] = f"Month {target} has no traction data. Available: {months}"
            return result

        prior = _prior_month(months, target)
        result["month"] = target
        result["prior_month"] = prior

        rows = list(_board_rows(conn, target, prior).values())
        # Read-only enrichment: latest close + market cap (best-effort, bulk).
        _enrich_board_market_data(rows, target_month=target)
        pins = get_watchlist(conn)

        # Stats on the FULL month set (stable chips), before any filtering.
        result["stats"] = {
            "total": len(rows),
            "adding": sum(
                1 for r in rows if r["add_count"] > 0 and r["reduce_count"] == 0
            ),
            "reducing": sum(
                1 for r in rows if r["reduce_count"] > 0 and r["add_count"] == 0
            ),
            "mixed": sum(
                1 for r in rows if r["add_count"] > 0 and r["reduce_count"] > 0
            ),
            "new_this_month": sum(1 for r in rows if r["new_entry_count"] > 0),
            "still_adding": sum(
                1 for r in rows if r["persistence"]["status"] == "still_adding"
            ),
            "reversed": sum(
                1 for r in rows if r["persistence"]["status"] == "reversed"
            ),
            "watchlisted": sum(
                1 for r in rows if (r["stock_key"] or r["symbol"]) in pins
            ),
            "large": sum(1 for r in rows if r.get("mcap_bucket") == "large"),
            "mid": sum(1 for r in rows if r.get("mcap_bucket") == "mid"),
            "small": sum(1 for r in rows if r.get("mcap_bucket") == "small"),
            "mcap_unknown": sum(1 for r in rows if r.get("mcap_bucket") == "unknown"),
        }

        filtered = apply_board_filter(rows, board_filter, pins)

        bucket = result["mcap"]
        if bucket and bucket != "all":
            filtered = [r for r in filtered if r.get("mcap_bucket") == bucket]

        q = (search or "").strip().lower()
        if q:
            filtered = [
                r
                for r in filtered
                if q in (r["name"] or "").lower()
                or q in (r["symbol"] or "").lower()
                or q in (r["nse"] or "").lower()
                or q in (r["sector"] or "").lower()
            ]

        filtered = apply_board_sort(filtered, sort_by)

        if not include_funds:
            for r in filtered:
                r["adds"] = []
                r["reduces"] = []
                r["holds"] = []
                r["funds"] = []

        result["rows"] = filtered
        result["count"] = len(filtered)
        result["success"] = True
        return result

    except sqlite3.Error as e:
        result["error"] = str(e)
        logger.exception("Traction board read failed")
        return result
    finally:
        conn.close()


def get_traction_insights(month: str | None = None) -> dict:
    """Insights + top-traction rows for one month (default: newest)."""
    result: dict = {
        "success": False,
        "month": None,
        "source": "",
        "insights": [],
        "top_traction": [],
        "count": 0,
        "error": None,
    }
    conn = sqlite3.connect(_get_db_path())
    try:
        _ensure_tables(conn)
        months = board_months(conn)
        if not months:
            result["error"] = "No fund traction data. Run the fund traction sync first."
            return result
        target = month or months[0]
        if target not in months:
            result["error"] = f"Month {target} has no traction data."
            return result
        result["month"] = target

        for kind, out_key in (
            ("insight", "insights"),
            ("top_traction", "top_traction"),
        ):
            for rec in conn.execute(
                """SELECT item_id, section, headline, action, stock_keys, body,
                          citations_json, source, score, fund_count
                   FROM fund_traction_insights
                   WHERE month = ? AND kind = ? ORDER BY rowid""",
                (target, kind),
            ).fetchall():
                try:
                    keys = json.loads(rec[4]) if rec[4] else []
                except (ValueError, TypeError):
                    keys = []
                try:
                    cites = json.loads(rec[6]) if rec[6] else []
                except (ValueError, TypeError):
                    cites = []
                result[out_key].append(
                    {
                        "id": rec[0],
                        "section": rec[1] or "",
                        "headline": rec[2] or "",
                        "action": rec[3] or "",
                        "stock_keys": keys,
                        "body": rec[5] or "",
                        "citations": cites,
                        "source": rec[7] or "",
                        "score": rec[8],
                        "fund_count": rec[9],
                    }
                )
                if rec[7] and not result["source"]:
                    result["source"] = rec[7]

        result["count"] = len(result["insights"]) + len(result["top_traction"])
        result["success"] = True
        return result

    except sqlite3.Error as e:
        result["error"] = str(e)
        logger.exception("Traction insights read failed")
        return result
    finally:
        conn.close()


# ── Watchlist pins ──────────────────────────────────────────────────────────


def get_watchlist(conn: sqlite3.Connection | None = None) -> set:
    """Pinned stock_keys. Read-only; safe on a missing table."""
    own = conn is None
    c = conn or sqlite3.connect(_get_db_path())
    try:
        _ensure_tables(c)
        return {
            r[0]
            for r in c.execute(
                "SELECT stock_key FROM fund_traction_watchlist"
            ).fetchall()
            if r[0]
        }
    except sqlite3.Error:
        return set()
    finally:
        if own:
            c.close()


def pin_watchlist(
    stock_key: str, name: str = "", nse: str = "", source: str = "manual"
) -> dict:
    """Pin one stock_key. Idempotent; re-pinning refreshes pinned_at."""
    key = (stock_key or "").strip()
    if not key:
        return {"success": False, "pinned": False, "error": "stock_key is required"}
    conn = sqlite3.connect(_get_db_path())
    try:
        _ensure_tables(conn)
        conn.execute(
            """INSERT OR REPLACE INTO fund_traction_watchlist
               (stock_key, name, nse, pinned_at, source) VALUES (?, ?, ?, ?, ?)""",
            (key, name, nse, datetime.now().isoformat(), source),
        )
        conn.commit()
        return {"success": True, "pinned": True, "stock_key": key}
    except sqlite3.Error as e:
        return {"success": False, "pinned": False, "error": str(e)}
    finally:
        conn.close()


def unpin_watchlist(stock_key: str) -> dict:
    """Remove one pin. Unpinning an absent key is a no-op success."""
    key = (stock_key or "").strip()
    if not key:
        return {"success": False, "unpinned": False, "error": "stock_key is required"}
    conn = sqlite3.connect(_get_db_path())
    try:
        _ensure_tables(conn)
        cur = conn.execute(
            "DELETE FROM fund_traction_watchlist WHERE stock_key = ?", (key,)
        )
        conn.commit()
        return {"success": True, "unpinned": cur.rowcount > 0, "stock_key": key}
    except sqlite3.Error as e:
        return {"success": False, "unpinned": False, "error": str(e)}
    finally:
        conn.close()


# â”€â”€ CLI entry point â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
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
