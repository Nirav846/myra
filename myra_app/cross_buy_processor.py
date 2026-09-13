# cross_buy_processor.py – convert raw mutual-fund holdings CSVs into the
# `fund_cross_buy` table in myra_valuation.db.
#
# Cross-buy definition used here:
#   For every stock held by N distinct funds in a given month we compute
#       cross_buy_ratio = (total_funds - same_style_funds) / total_funds
#   where same_style_funds counts funds whose mandate classification equals the
#   stock's own size category ('Large'/'Mid'/'Small' from fundamentals.market_cap;
#   'Multi' and 'Other' mandates never match). A high ratio therefore means the
#   stock is bought across funds whose style differs from its own size bucket –
#   i.e. genuine cross-style accumulation rather than one style crowding in.
#   If the stock category is 'Unknown', same_style_funds = 0 (ratio == 1.0).
#
# Signal tags (on cross_buy_ratio):
#   total>=5 and ratio>=0.7 -> STRONG_CROSS_BUY
#   ratio>=0.5              -> CROSS_BUY
#   ratio>=0.25             -> MIXED
#   else                    -> STYLE_CONCENTRATED

import logging
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

from myra_app.constants import DB_DIR

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[1]
_TRACTION_DIR = REPO_ROOT / "cross-fund-holdings-traction"
RAW_HOLDINGS_DIR = _TRACTION_DIR / "temp_holdings"
NAME_TO_NSE_PATH = _TRACTION_DIR / "config" / "name_to_nse.csv"
TRACTION_SRC = _TRACTION_DIR / "src"
FUNDS_LIST_PATH = _TRACTION_DIR / "config" / "rupeevest_funds.txt"
DOWNLOAD_SCRIPT_PATH = _TRACTION_DIR / "scripts" / "download_rupeevest_funds.py"
KEEP_RAW = False
DEFAULT_MONTHS: list[str] = ["2026-04", "2026-05", "2026-06", "2026-07"]
DOWNLOAD_TIMEOUT_S = 600

# mf_screener is pure stdlib and lives outside the package tree; add once.
if str(TRACTION_SRC) not in sys.path:
    sys.path.insert(0, str(TRACTION_SRC))

VALUATION_DB_PATH = os.path.join(DB_DIR, "myra_valuation.db")

# Market-cap thresholds (absolute rupees), precedent: myra_web/routes/fund_traction.py
_SMALL_CAP_MAX = 5e10  # < Rs 5,000 Cr -> Small
_MID_CAP_MAX = 2e11  # < Rs 20,000 Cr -> Mid

_IST_TZ = timezone(timedelta(hours=5, minutes=30))

_MONTH_SUFFIX_RE = re.compile(r"_(\d{2})_(\d{2})\.csv$", re.IGNORECASE)
_NORMALIZE_PUNCT_RE = re.compile(r"[.,\'()\-@#]+")
_ABBREV_PERIOD_RE = re.compile(r"(?<=[A-Za-z])\.(?=[A-Za-z])")
_WS_RE = re.compile(r"\s+")

def normalize_company_name(name: str) -> str:
    """Normalize a company name for matching: lowercases, converts '&' to 'and',
    strips periods from abbreviations (J.K. → jk), removes punctuation,
    strips leading 'the', removes common suffixes (ltd, limited, inc,
    corporation, corp, pvt, private, company, co), collapses multiple spaces.
    """
    if not name:
        return ""
    text = str(name)
    text = text.replace("&", " and ")
    text = _ABBREV_PERIOD_RE.sub("", text)
    text = _NORMALIZE_PUNCT_RE.sub(" ", text)
    text = _WS_RE.sub(" ", text).strip()
    text = text.lower()
    if text.startswith("the "):
        text = text[4:]
    suffixes = {"ltd", "limited", "inc", "corporation", "corp", "pvt", "private", "company", "co"}
    while text:
        words = text.split()
        if len(words) == 1:
            break
        if words[-1] in suffixes:
            words = words[:-1]
            text = " ".join(words)
        else:
            break
    return text

# Blocklist for foreign companies (they have no NSE symbol)
_FOREIGN_BLOCKLIST_RAW = {
    "Alphabet Inc",
    "Microsoft Corp",
    "Amazon Com Inc",
    "Meta Platforms",
    "Nvidia Corporation",
    "Adobe Inc",
    "Accenture Plc",
    "Epam Systems Inc",
    "Cognizant Tech Solutions",
    "LG Electronics Inc",
}
_FOREIGN_BLOCKLIST = {normalize_company_name(name) for name in _FOREIGN_BLOCKLIST_RAW}

# Path to manual overrides file (in project root config)
MANUAL_OVERRIDES_PATH = Path(__file__).resolve().parents[1] / "config" / "nse_manual_overrides.csv"

# Caches
_NAME_TO_NSE_CACHE: dict[str, str] | None = None
_MARKET_CAP_CACHE: dict[str, float] | None = None
_SYMBOL_NAME_CACHE: dict[str, str] | None = None  # normalized name -> symbol (for fuzzy fallback)
_NAMES_POPULATED: bool = False  # flag to indicate we have attempted to populate symbols_master.name

def _load_symbol_names_from_metadata() -> None:
    """Load symbol names from metadata.db into _SYMBOL_NAME_CACHE.
    Populates the cache with normalized name -> symbol for active equity symbols.
    If the name column is missing or no names, leaves cache as None.
    """
    global _SYMBOL_NAME_CACHE
    if _SYMBOL_NAME_CACHE is not None:
        # Already loaded
        return
    import sqlite3
    db_path = os.path.join(DB_DIR, "myra_metadata.db")
    if not os.path.exists(db_path):
        logger.warning("Metadata DB not found at %s", db_path)
        _SYMBOL_NAME_CACHE = None
        return
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.execute("PRAGMA query_only=ON")
        cursor = conn.cursor()
        # Check if name column exists
        cursor.execute("PRAGMA table_info(symbols_master)")
        columns = [row[1] for row in cursor.fetchall()]
        if "name" not in columns:
            logger.warning("symbols_master table lacks 'name' column; cannot load symbol names for fuzzy fallback")
            _SYMBOL_NAME_CACHE = None
            conn.close()
            return
        # Query for active equity symbols with non-empty name
        cursor.execute(
            """
            SELECT symbol, name
            FROM symbols_master
            WHERE (instrument_type='EQUITY' OR instrument_type IS NULL)
              AND (is_active=1 OR is_active IS NULL)
              AND name IS NOT NULL
              AND name != ''
            """
        )
        rows = cursor.fetchall()
        conn.close()
    except sqlite3.Error as exc:
        logger.warning("Failed to query symbol names from metadata: %s", exc)
        _SYMBOL_NAME_CACHE = None
        return

    if not rows:
        logger.warning("No symbol names found in metadata DB")
        _SYMBOL_NAME_CACHE = None
        return

    mapping: dict[str, str] = {}
    for symbol, name in rows:
        if not symbol or not name:
            continue
        key = normalize_company_name(name)
        if not key:
            continue
        if key in mapping:
            logger.warning("Duplicate normalized name '%s' from symbols_master: keeping first symbol '%s', ignoring '%s'", key, mapping[key], symbol)
        else:
            mapping[key] = symbol
    _SYMBOL_NAME_CACHE = mapping
    logger.debug("Loaded %d symbol names from metadata for fuzzy fallback", len(mapping))


def _populate_symbol_names_from_nse() -> None:
    """Populate missing names in symbols_master from the NSE EQUITY_L.csv.
    This is a one-time operation; we attempt to fill empty names for symbols that already exist.
    We do not insert new symbols.
    """
    import csv, urllib.request, os, sqlite3
    from io import StringIO

    url = "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as response:
            data = response.read().decode("utf-8", errors="replace")
    except Exception as exc:
        logger.warning("Failed to download NSE equity master: %s", exc)
        return

    # Parse CSV
    try:
        df_raw = csv.DictReader(StringIO(data))
        # Normalize column names: strip whitespace
        df_raw.fieldnames = [name.strip() if name else name for name in df_raw.fieldnames]
    except Exception as exc:
        logger.warning("Failed to parse NSE CSV: %s", exc)
        return

    # Find symbol and name columns
    symbol_col = None
    name_col = None
    for col in df_raw.fieldnames:
        if col.upper() == "SYMBOL":
            symbol_col = col
        if col.upper() in ("NAME OF COMPANY", "NAME"):
            name_col = col
    if symbol_col is None or name_col is None:
        logger.warning("Could not find SYMBOL and NAME columns in NSE CSV")
        return

    # Build mapping from symbol to name
    nse_name_map: dict[str, str] = {}
    for row in df_raw:
        sym = (row.get(symbol_col) or "").strip()
        name = (row.get(name_col) or "").strip()
        if sym and name:
            nse_name_map[sym] = name

    if not nse_name_map:
        logger.warning("No symbol-name pairs extracted from NSE CSV")
        return

    # Update symbols_master table
    db_path = os.path.join(DB_DIR, "myra_metadata.db")
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        # Update only existing symbols where name is NULL or empty
        # We'll batch update
        updated = 0
        for sym, name in nse_name_map.items():
            cursor.execute(
                "UPDATE symbols_master SET name=? WHERE symbol=? AND (name IS NULL OR name='')",
                (name, sym)
            )
            updated += cursor.rowcount
        conn.commit()
        conn.close()
        logger.info("Populated names for %d symbols in symbols_master from NSE master", updated)
    except sqlite3.Error as exc:
        logger.warning("Failed to update symbols_master with names from NSE: %s", exc)


def _ensure_symbol_names_populated() -> None:
    """Ensure that symbol names are populated in metadata DB for fuzzy fallback.
    Attempts to load names from metadata; if insufficient, tries to populate from NSE master.
    """
    global _NAMES_POPULATED
    if _NAMES_POPULATED:
        return
    # First, try to load names from metadata to see how many we have
    _load_symbol_names_from_metadata()
    if _SYMBOL_NAME_CACHE is not None:
        # If we have a reasonable fraction of symbols with names, consider it good.
        # We could check the count against total symbols, but for simplicity, if we have any, we assume it's enough.
        # However, we might want to populate if the cache is very small.
        # We'll skip population for now; we can add logic later if needed.
        _NAMES_POPULATED = True
        return
    # If we get here, _SYMBOL_NAME_CACHE is None (missing column or no names)
    logger.info("Attempting to populate symbol names from NSE master")
    _populate_symbol_names_from_nse()
    # After population, try loading again
    _load_symbol_names_from_metadata()
    _NAMES_POPULATED = True

_RE_SMALL_CAP = re.compile(r"small[\s_-]*cap", re.IGNORECASE)
_RE_MID_CAP = re.compile(r"mid[\s_-]*cap|large\s*(?:&|and)\s*mid", re.IGNORECASE)
_RE_LARGE_CAP = re.compile(r"large[\s_-]*cap|bluechip", re.IGNORECASE)
_RE_MULTI = re.compile(
    r"flexi|multi[\s_-]*cap|all[\s_-]*cap|focused|value|contra|elss|equity\s+savings",
    re.IGNORECASE,
)

_NAME_TO_NSE_CACHE: dict[str, str] | None = None
_MARKET_CAP_CACHE: dict[str, float] | None = None

_DDL = """
CREATE TABLE IF NOT EXISTS fund_cross_buy (
    symbol TEXT,
    month TEXT,
    total_funds INTEGER,
    large_funds INTEGER,
    mid_funds INTEGER,
    small_funds INTEGER,
    multi_funds INTEGER,
    other_funds INTEGER,
    cross_buy_ratio REAL,
    signal_tag TEXT,
    last_updated TEXT,
    PRIMARY KEY (symbol, month)
)
"""


def _ensure_table(conn: sqlite3.Connection) -> None:
    """Create fund_cross_buy if absent; migrate legacy single-symbol PK.

    The API serves month history, so the table needs composite PK
    (symbol, month). An old table whose PK is not exactly (symbol, month)
    – e.g. PK on symbol alone – would overwrite rows across months and keep
    stale symbols from prior runs; it is dropped and recreated (data is fully
    reproducible via backfill_months).

    Args:
        conn: Open sqlite3 connection to myra_valuation.db.
    """
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='fund_cross_buy'"
    ).fetchone()
    if exists is None:
        conn.execute(_DDL)
        return
    pk_cols = [
        info[1]
        for info in conn.execute("PRAGMA table_info(fund_cross_buy)").fetchall()
        if int(info[5] or 0) > 0
    ]
    if pk_cols != ["symbol", "month"]:
        logger.warning(
            "Legacy fund_cross_buy schema detected (PK=%s); dropping and recreating "
            "with composite (symbol, month) PK. Data will be re-backfilled.",
            pk_cols or "<none>",
        )
        conn.execute("DROP TABLE fund_cross_buy")
        conn.execute(_DDL)


def detect_available_months() -> list[str]:
    """Detect months present in RAW_HOLDINGS_DIR from ``_MM_YY`` filename suffixes.

    Returns ascending unique list of "YYYY-MM" strings; falls back to
    DEFAULT_MONTHS when the folder is missing, unreadable, or empty.
    """
    try:
        months: set[str] = set()
        for path in RAW_HOLDINGS_DIR.glob("*.csv"):
            month = _month_from_filename(path)
            if month:
                months.add(month)
        if months:
            return sorted(months)
        logger.warning(
            "No month-tagged CSVs in %s; using default months.", RAW_HOLDINGS_DIR
        )
    except OSError as exc:
        logger.warning(
            "Could not read %s (%s); using default months.", RAW_HOLDINGS_DIR, exc
        )
    return list(DEFAULT_MONTHS)


def download_holdings(month: str) -> bool:
    """Run the RupeeVest downloader for the given month.

    Args:
        month: Month tag ("YYYY-MM"); passed through to the downloader via
            --out-dir/--funds-file which fetches current exports.

    Returns:
        True on success; False when the funds list/script is missing or the
        subprocess fails/times out. Never raises – processing continues with
        whatever CSVs already exist.
    """
    if not FUNDS_LIST_PATH.exists():
        logger.warning("Funds list missing (%s); skipping download.", FUNDS_LIST_PATH)
        return False
    if not DOWNLOAD_SCRIPT_PATH.exists():
        logger.warning(
            "Downloader script missing (%s); skipping download.", DOWNLOAD_SCRIPT_PATH
        )
        return False
    cmd = [
        sys.executable,
        str(DOWNLOAD_SCRIPT_PATH),
        "--out-dir",
        str(RAW_HOLDINGS_DIR),
        "--funds-file",
        str(FUNDS_LIST_PATH),
    ]
    try:
        proc = subprocess.run(  # noqa: S603 - fixed argv, no shell
            cmd,
            capture_output=True,
            text=True,
            timeout=DOWNLOAD_TIMEOUT_S,
            cwd=str(REPO_ROOT),
        )
    except subprocess.TimeoutExpired:
        logger.warning("Holdings download timed out after %ss.", DOWNLOAD_TIMEOUT_S)
        return False
    except OSError as exc:
        logger.warning("Holdings download failed to launch: %s", exc)
        return False
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "")[-500:]
        logger.warning("Holdings download failed (rc=%s): %s", proc.returncode, tail)
        return False
    logger.info("Holdings download succeeded for %s.", month)
    return True


def classify_fund(fund_name: str) -> str:
    """Classify a fund slug/display name into a mandate category.

    Order matters: "large & mid cap" contains both words and must resolve to Mid.

    Args:
        fund_name: Fund slug or display name.

    Returns:
        One of 'Large', 'Mid', 'Small', 'Multi', 'Other'.
    """
    if _RE_SMALL_CAP.search(fund_name):
        return "Small"
    if _RE_MID_CAP.search(fund_name):
        return "Mid"
    if _RE_LARGE_CAP.search(fund_name):
        return "Large"
    if _RE_MULTI.search(fund_name):
        return "Multi"
    return "Other"


def get_stock_category(symbol: str) -> str:
    """Map a symbol to its size bucket from fundamentals.market_cap.

    Thresholds: < 5e10 -> Small, < 2e11 -> Mid, else Large (absolute rupees).
    Missing/error -> 'Unknown' (debug-logged, never raised). Market caps are
    bulk-cached on first call to avoid per-symbol N+1 queries.

    Args:
        symbol: NSE symbol.

    Returns:
        'Large', 'Mid', 'Small', or 'Unknown'.
    """
    caps = _get_market_cap_map()
    mc = caps.get(symbol)
    if mc is None:
        logger.debug("No market_cap for %s in fundamentals; category Unknown.", symbol)
        return "Unknown"
    if mc < _SMALL_CAP_MAX:
        return "Small"
    if mc < _MID_CAP_MAX:
        return "Mid"
    return "Large"


def _get_market_cap_map() -> dict[str, float]:
    """Load {symbol: market_cap} from fundamentals once per process.

    Returns:
        Dict of symbol -> market_cap (absolute rupees); empty dict on error.
    """
    global _MARKET_CAP_CACHE
    if _MARKET_CAP_CACHE is not None:
        return _MARKET_CAP_CACHE
    caps: dict[str, float] = {}
    try:
        conn = sqlite3.connect(VALUATION_DB_PATH, timeout=30)
        try:
            rows = conn.execute(
                "SELECT symbol, market_cap FROM fundamentals WHERE market_cap IS NOT NULL"
            ).fetchall()
        finally:
            conn.close()
        for sym, mc in rows:
            try:
                caps[str(sym)] = float(mc)
            except (TypeError, ValueError):
                continue
    except sqlite3.Error as exc:
        logger.warning("Could not load fundamentals market caps: %s", exc)
    _MARKET_CAP_CACHE = caps
    return caps





def _load_name_to_nse() -> dict[str, str]:
    """Load config/name_to_nse.csv into {normalized_company_name: nse_symbol}.
    Also loads manual overrides from MANUAL_OVERRIDES_PATH (if exists) and
    merges them (overrides win). Uses the normalize_company_name helper
    which lowercases, removes punctuation .,&'()-,
    removes common suffixes (ltd, limited, inc, corporation, corp, pvt,
    private, company, co), collapses multiple spaces.
    Returns mapping dict; empty if the file is missing/unreadable.
    """
    global _NAME_TO_NSE_CACHE
    if _NAME_TO_NSE_CACHE is not None:
        return _NAME_TO_NSE_CACHE
    mapping: dict[str, str] = {}
    try:
        import csv
        with NAME_TO_NSE_PATH.open("r", encoding="utf-8-sig", newline="") as fh:
            for rec in csv.DictReader(fh):
                company = (rec.get("company_name") or "").strip()
                nse = (rec.get("nse") or "").strip().upper()
                if not company or not nse:
                    continue
                key = normalize_company_name(company)
                if key:
                    if key in mapping:
                        logger.debug("Duplicate normalized key '%s' in name_to_nse.csv: keeping existing symbol '%s', ignoring '%s'", key, mapping[key], nse)
                    else:
                        mapping[key] = nse
    except OSError as exc:
        logger.warning("Could not read %s: %s", NAME_TO_NSE_PATH, exc)
    # Load manual overrides (if file exists)
    try:
        if MANUAL_OVERRIDES_PATH.exists():
            with MANUAL_OVERRIDES_PATH.open("r", encoding="utf-8-sig", newline="") as fh:
                for rec in csv.DictReader(fh):
                    company = (rec.get("company_name") or "").strip()
                    nse = (rec.get("nse") or rec.get("nse_symbol") or "").strip().upper()  # support both column names
                    if not company or not nse:
                        continue
                    key = normalize_company_name(company)
                    if key:
                        if key in mapping:
                            logger.info("Manual override for '%s' (%s) replaces existing symbol '%s' with '%s'", company, key, mapping[key], nse)
                        else:
                            logger.info("Added manual override for '%s' (%s) -> '%s'", company, key, nse)
                        mapping[key] = nse
                    else:
                        logger.warning("Manual override company name '%s' normalizes to empty; skipping", company)
        else:
            logger.debug("Manual overrides file %s not found", MANUAL_OVERRIDES_PATH)
    except Exception as exc:
        logger.warning("Failed to load manual overrides from %s: %s", MANUAL_OVERRIDES_PATH, exc)
    _NAME_TO_NSE_CACHE = mapping
    return mapping


def _resolve_symbol(row: Any, name_map: dict[str, str]) -> str | None:
    """Resolve the NSE symbol for one holding row.

    Steps:
    1. Check blocklist (foreign companies) -> skip.
    2. Use row.nse if present.
    3. Normalize the name and look up in the provided name_map (from CSV).
    4. If not found, attempt fuzzy fallback against symbols_master (requires name column).
       - Exact normalized match.
       - Prefix match (one string is a prefix of the other).
       - Token overlap >= 80% (intersection / max token count).
    Returns uppercase NSE symbol or None.

    Args:
        row: mf_screener.load.HoldingRow instance.
        name_map: Mapping from _load_name_to_nse().

    Returns:
        Uppercase NSE symbol, or None when unresolvable.
    """
    # Blocklist check
    name_raw = getattr(row, "name", "") or ""
    if not name_raw:
        return None
    key = normalize_company_name(name_raw)
    if key in _FOREIGN_BLOCKLIST:
        logger.debug("Foreign company name blocked: %s (normalized: %s)", name_raw, key)
        return None

    # Use nse from row if available
    nse = getattr(row, "nse", "") or ""
    if nse:
        return nse.strip().upper()

    # Lookup in the CSV-based mapping
    if key:
        hit = name_map.get(key)
        if hit:
            return hit

    # Fuzzy fallback against symbols_master (requires name column)
    # Ensure we have symbol names populated (from metadata, possibly via NSE master)
    _ensure_symbol_names_populated()
    if _SYMBOL_NAME_CACHE is None:
        logger.debug("Symbol name cache not available; skipping fuzzy fallback")
        return None

    # Exact match
    if key in _SYMBOL_NAME_CACHE:
        symbol = _SYMBOL_NAME_CACHE[key]
        logger.debug("Fuzzy exact match: %s -> %s", name_raw, symbol)
        return symbol

    # Collect candidates for prefix and token overlap
    input_tokens = set(key.split())
    candidates = []  # list of (symbol, score_type, score_value)
    # Prefix match
    for cached_name, symbol in _SYMBOL_NAME_CACHE.items():
        if cached_name == key:
            continue  # already handled exact
        if cached_name.startswith(key) or key.startswith(cached_name):
            # Avoid overly short prefixes: require both strings length >= 2 and the shorter length >= 2?
            # We'll accept any prefix for now but could add a minimum length.
            candidates.append((symbol, "prefix", 0))  # score_value not used for now
    # Token overlap match
    for cached_name, symbol in _SYMBOL_NAME_CACHE.items():
        if cached_name == key:
            continue
        cached_tokens = set(cached_name.split())
        if not input_tokens or not cached_tokens:
            continue
        intersection = len(input_tokens & cached_tokens)
        max_len = max(len(input_tokens), len(cached_tokens))
        if max_len > 0 and intersection / max_len >= 0.8:
            candidates.append((symbol, "token", intersection / max_len))

    # Prioritize: exact already handled, then prefix, then token.
    # We'll choose the first candidate if we have exactly one candidate of any type.
    # If multiple candidates, we log ambiguity and return None.
    if len(candidates) == 1:
        symbol, match_type, _ = candidates[0]
        if match_type == "prefix":
            logger.warning("Fuzzy prefix match: %s -> %s", name_raw, symbol)
        else:  # token
            logger.warning("Fuzzy token match (%.0f%%): %s -> %s", candidates[0][2]*100, name_raw, symbol)
        return symbol
    elif len(candidates) > 1:
        logger.warning("Ambiguous fuzzy match for '%s': %d candidates (prefix/token)", name_raw, len(candidates))
        return None
    else:
        return None


def _month_from_filename(path: Path) -> str | None:
    """Parse trailing ``_MM_YY.csv`` filename suffix into "20YY-MM".

    Args:
        path: Candidate CSV path.

    Returns:
        "YYYY-MM" string, or None when the suffix is absent/malformed.
    """
    match = _MONTH_SUFFIX_RE.search(path.name)
    if not match:
        return None
    mm, yy = match.group(1), match.group(2)
    try:
        month_num = int(mm)
        if not 1 <= month_num <= 12:
            return None
    except ValueError:
        return None
    return f"20{yy}-{month_num:02d}"


def _month_tag_tuple(month: str) -> tuple[int, int]:
    """Convert "YYYY-MM" into an int tuple for ordering/comparisons.

    Args:
        month: Month tag "YYYY-MM".

    Returns:
        (year, month) ints; (0, 0) when unparseable.
    """
    parts = month.split("-")
    if len(parts) != 2:
        return (0, 0)
    try:
        return (int(parts[0]), int(parts[1]))
    except ValueError:
        return (0, 0)


def _previous_month(month: str) -> tuple[int, int]:
    """Compute the previous calendar month tuple for a "YYYY-MM" tag.

    Args:
        month: Month tag "YYYY-MM".

    Returns:
        (year, month) of the preceding calendar month.
    """
    year, mon = _month_tag_tuple(month)
    if mon <= 1:
        return (year - 1, 12)
    return (year, mon - 1)


def _cleanup_old_csvs(month: str) -> None:
    """Delete month-tagged CSVs older than the previous calendar month.

    Keeps the current and previous month relative to ``month``. Never raises.

    Args:
        month: Reference month tag "YYYY-MM".
    """
    if KEEP_RAW:
        return
    cutoff = _previous_month(month)
    try:
        removed = 0
        for path in RAW_HOLDINGS_DIR.glob("*.csv"):
            tag = _month_from_filename(path)
            if tag is None:
                continue
            if _month_tag_tuple(tag) < cutoff:
                try:
                    path.unlink()
                    removed += 1
                except OSError as exc:
                    logger.warning("Could not delete old CSV %s: %s", path.name, exc)
        if removed:
            logger.info("Cleaned %d raw CSV(s) older than %s.", removed, cutoff)
    except OSError as exc:
        logger.warning("Raw CSV cleanup failed: %s", exc)


def process_month(month: str) -> dict:
    """Process one month of raw holdings CSVs into the fund_cross_buy table.

    Orchestrates: ensure raw CSVs exist (download if needed), load that month's
    rows via mf_screener, resolve symbols, aggregate per-symbol fund counts by
    mandate category, classify stocks by market-cap bucket, compute
    cross_buy_ratio, and INSERT OR REPLACE rows in one transaction. Old raw
    CSVs are pruned unless KEEP_RAW. Whole body is fail-safe: any exception is
    logged and returned, never raised.

    Args:
        month: Month tag "YYYY-MM".

    Returns:
        Summary dict: {success, month, symbols, unresolved, funds} on success;
        {success: False, reason: ...} or {success: False, error: ...} otherwise.
    """

    def _fail(payload: dict) -> dict:
        payload.setdefault("success", False)
        payload.setdefault("month", month)
        return payload

    try:
        month_files = [
            p
            for p in RAW_HOLDINGS_DIR.glob("*.csv")
            if _month_from_filename(p) == month
        ]
        if not month_files:
            logger.info("No CSVs for %s; attempting download...", month)
            download_holdings(month)
            month_files = [
                p
                for p in RAW_HOLDINGS_DIR.glob("*.csv")
                if _month_from_filename(p) == month
            ]
        if not month_files:
            logger.warning(
                "No holdings CSVs for %s even after download attempt.", month
            )
            return _fail({"reason": "no csvs"})

        # Load ONLY this month's rows through the existing parser.
        temp_dir = tempfile.mkdtemp(prefix=f"crossbuy_{month.replace('-', '_')}_")
        try:
            for src in month_files:
                shutil.copy2(src, Path(temp_dir) / src.name)
            from mf_screener.load import load_holdings_from_folder

            rows = load_holdings_from_folder(Path(temp_dir))
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

        if not rows:
            logger.warning("Parser returned 0 rows for %s.", month)
            return _fail({"reason": "no rows"})

        name_map = _load_name_to_nse()

        # Fund category map keyed by slug (filename stem carries the mandate words).
        fund_categories: dict[str, str] = {}
        for row in rows:
            slug = row.fund_slug
            if slug not in fund_categories:
                fund_categories[slug] = classify_fund(slug)

        # Aggregate per symbol: distinct fund slugs + bucket counts per category.
        symbol_funds: dict[str, set[str]] = {}
        unresolved_names: list[str] = []
        unresolved_count = 0
        for row in rows:
            symbol = _resolve_symbol(row, name_map)
            if not symbol:
                unresolved_count += 1
                if len(unresolved_names) < 10:
                    unresolved_names.append(row.name)
                continue
            bucket = symbol_funds.setdefault(symbol, set())
            bucket.add(row.fund_slug)

        if unresolved_count:
            logger.warning(
                "Month %s: %d holding row(s) unresolved to NSE symbols "
                "(sample: %s%s); skipped.",
                month,
                unresolved_count,
                unresolved_names[:3],
                "..." if unresolved_count > 3 else "",
            )

        unknown_warned = False
        out_rows: list[tuple[Any, ...]] = []
        for symbol, slugs in symbol_funds.items():
            total = len(slugs)
            large = mid = small = multi = other = 0
            same_style = 0
            stock_cat = get_stock_category(symbol)
            if stock_cat == "Unknown":
                if not unknown_warned:
                    logger.debug(
                        "Stock categories unavailable for some symbols "
                        "(no fundamentals row); same_style treated as 0."
                    )
                    unknown_warned = True
            for slug in slugs:
                cat = fund_categories[slug]
                if cat == "Large":
                    large += 1
                elif cat == "Mid":
                    mid += 1
                elif cat == "Small":
                    small += 1
                elif cat == "Multi":
                    multi += 1
                else:
                    other += 1
                if cat == stock_cat:  # 'Multi'/'Other' can never equal a size bucket
                    same_style += 1
            ratio = round((total - same_style) / total, 4) if total else 0.0
            if total >= 5 and ratio >= 0.7:
                tag = "STRONG_CROSS_BUY"
            elif ratio >= 0.5:
                tag = "CROSS_BUY"
            elif ratio >= 0.25:
                tag = "MIXED"
            else:
                tag = "STYLE_CONCENTRATED"
            last_updated = datetime.now(_IST_TZ).isoformat()
            out_rows.append(
                (
                    symbol,
                    month,
                    total,
                    large,
                    mid,
                    small,
                    multi,
                    other,
                    ratio,
                    tag,
                    last_updated,
                )
            )

        conn = sqlite3.connect(VALUATION_DB_PATH, timeout=30)
        try:
            _ensure_table(conn)
            conn.executemany(
                "INSERT OR REPLACE INTO fund_cross_buy "
                "(symbol, month, total_funds, large_funds, mid_funds, small_funds, "
                "multi_funds, other_funds, cross_buy_ratio, signal_tag, last_updated) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                out_rows,
            )
            conn.commit()
        finally:
            conn.close()

        _cleanup_old_csvs(month)

        summary = {
            "success": True,
            "month": month,
            "symbols": len(out_rows),
            "unresolved": unresolved_count,
            "funds": len(fund_categories),
        }
        logger.info("Month %s processed: %s", month, summary)
        return summary
    except Exception as exc:  # noqa: BLE001 - pipeline must never crash on one month
        logger.exception("process_month(%s) failed: %s", month, exc)
        return _fail({"error": str(exc)})


def backfill_months(months: list[str] | None = None) -> dict:
    """Process several months ascending and aggregate per-month results.

    Args:
        months: Explicit month tags to process; defaults to
            detect_available_months() output.

    Returns:
        Aggregated summary dict with per-month results. Never raises.
    """
    try:
        target_months = sorted(months) if months else detect_available_months()
        results: list[dict] = []
        ok = 0
        for month in target_months:
            logger.info("Cross-buy backfill: processing %s ...", month)
            result = process_month(month)
            results.append(result)
            if result.get("success"):
                ok += 1
        summary = {
            "success": ok > 0,
            "requested": len(target_months),
            "processed_ok": ok,
            "results": results,
        }
        logger.info("Cross-buy backfill complete: %s", summary)
        return summary
    except Exception as exc:  # noqa: BLE001 - batch entry point must not raise
        logger.exception("backfill_months failed: %s", exc)
        return {"success": False, "error": str(exc)}


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)-18s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    backfill_months()
