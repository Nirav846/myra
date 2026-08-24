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
_PUNCT_RE = re.compile(r"[,.'()\-]+")
_WS_RE = re.compile(r"\s+")

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


def _normalize_company(name: str) -> str:
    """Normalize a company name for matching: upper-case, collapse whitespace,
    strip punctuation like ,.'()- .

    Args:
        name: Raw company name.

    Returns:
        Normalized upper-case key.
    """
    text = _PUNCT_RE.sub(" ", (name or ""))
    return _WS_RE.sub(" ", text).strip().upper()


_CORP_SUFFIXES = {"LIMITED", "LTD", "PRIVATE", "PVT", "INDIA"}


def _fallback_name_keys(normalized: str) -> list[str]:
    """Build fallback lookup keys by dropping trailing corporate-suffix words.

    Args:
        normalized: Output of _normalize_company().

    Returns:
        List of progressively shorter keys (without duplicates).
    """
    tokens = normalized.split()
    keys: list[str] = []
    while len(tokens) > 1 and tokens[-1] in _CORP_SUFFIXES:
        tokens.pop()
        key = " ".join(tokens)
        if key not in keys:
            keys.append(key)
    return keys


def _load_name_to_nse() -> dict[str, str]:
    """Load config/name_to_nse.csv into {normalized_company_name: nse_symbol}.

    Each company is indexed under its full normalized name PLUS alias keys
    generated by progressively dropping trailing corporate suffixes
    (Limited/Ltd/Private/Pvt/India). Aliases use setdefault so an existing
    distinct entry always wins over an ambiguous shorter alias. Cached at
    module level after first load.

    Returns:
        Mapping dict; empty dict if the file is missing/unreadable.
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
                key = _normalize_company(company)
                if key:
                    mapping.setdefault(key, nse)
                for alias in _fallback_name_keys(key):
                    mapping.setdefault(alias, nse)
    except OSError as exc:
        logger.warning("Could not read %s: %s", NAME_TO_NSE_PATH, exc)
    _NAME_TO_NSE_CACHE = mapping
    return mapping


def _resolve_symbol(row: Any, name_map: dict[str, str]) -> str | None:
    """Resolve the NSE symbol for one holding row.

    Prefers row.nse; otherwise looks up row.name via the normalized mapping
    (with trailing-suffix fallback keys).

    Args:
        row: mf_screener.load.HoldingRow instance.
        name_map: Mapping from _load_name_to_nse().

    Returns:
        Uppercase NSE symbol, or None when unresolvable.
    """
    nse = (getattr(row, "nse", "") or "").strip()
    if nse:
        return nse.upper()
    name = getattr(row, "name", "") or ""
    key = _normalize_company(name)
    if not key:
        return None
    hit = name_map.get(key)
    if hit:
        return hit
    for fb_key in _fallback_name_keys(key):
        hit = name_map.get(fb_key)
        if hit:
            return hit
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
