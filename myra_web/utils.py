"""MYRA web-layer shared utilities. Pure helpers extracted from myra_fastapi_server.py (Phase 1 of monolith refactor). No FastAPI state — deterministic functions and constants only."""

import json
import logging
import math
import os
import sqlite3
from datetime import datetime, timedelta, timezone

from myra_app.constants import DB_DIR, MODELS_DIR
from myra_app.librarian_core import LibrarianCore

logger = logging.getLogger(__name__)

# Single same-date confluence snapshot, written by
# myra_web.confluence_batch.run_confluence_batch(). Defined here (rather than
# in confluence_batch) so both modules can reference it without a cycle.
CONFLUENCE_SNAPSHOT_FILENAME = "confluence_snapshot.json"


def _df_to_safe_records(df) -> list[dict]:
    """Convert a DataFrame to a list of dicts, replacing NaN/Inf with None."""
    if df.empty:
        return []
    records = df.to_dict("records")
    for rec in records:
        for key, val in list(rec.items()):
            if isinstance(val, float) and (math.isnan(val) or math.isinf(val)):
                rec[key] = None
    return records


def _get_latest_trading_day_before(date_str: str) -> str:
    """Find the most recent trading day on or before date_str by querying technical_data."""
    target = datetime.strptime(date_str, "%Y-%m-%d")
    tech_db = os.path.join(DB_DIR, LibrarianCore.DB_MAP["technical"])
    conn = sqlite3.connect(tech_db)
    for offset in range(10):
        check = (target - timedelta(days=offset)).strftime("%Y-%m-%d")
        row = conn.execute(
            "SELECT COUNT(*) FROM technical_data WHERE date = ?", (check,)
        ).fetchone()
        if row and row[0] > 0:
            conn.close()
            return check
    conn.close()
    return date_str


# ---- tier rank helper (module-level for testability) ----
_TIER_RANK_MAP = {"HIGH": 0, "MOD": 1, "LOW": 2}


def _apply_tier_rank(candidates: list[dict]) -> list[dict]:
    """Add numeric ``tier_rank`` (0=HIGH, 1=MOD, 2=LOW) to every candidate dict."""
    for c in candidates:
        if "tier_rank" not in c:
            c["tier_rank"] = _TIER_RANK_MAP.get(c.get("tier"), 2)
    return candidates


# Map cache filenames to friendly display names
_SCANNER_CACHE_MAP: dict[str, str] = {
    "trigger_cache.json": "The Trigger",
    "bottom_hunter_cache.json": "Bottom Hunter",
    "invisible_hand_cache.json": "Invisible Hand",
    "wyckoff_cache.json": "Wyckoff Automaton",
    "liquidity_flip_cache.json": "Liquidity Flip",
    "operator_fingerprint_cache.json": "Operator Fingerprint",
    "float_exhaustion_cache.json": "Float Exhaustion",
    "seasonal_delivery_cache.json": "Seasonal Delivery",
    "darvas_cache.json": "Darvas Box Pro",
    "multibagger_cache.json": "Multibagger Pro",
    "climax_accumulation_cache.json": "Climax Accumulation",
    "bottom_hunter_m1_cache.json": "Recovery Ladder",
    "super_breakout_cache.json": "Super Breakout",
}

# Display-name → frontend route (for link column)
_SCANNER_ROUTES: dict[str, str] = {
    "The Trigger": "/trigger",
    "Bottom Hunter": "/bottom-hunter",
    "Recovery Ladder": "/recovery-ladder",
    "Super Breakout": "/super-breakout",
    "Invisible Hand": "/invisible-hand",
    "Wyckoff Automaton": "/wyckoff",
    "Liquidity Flip": "/liquidity-flip",
    "Operator Fingerprint": "/operator-fingerprint",
    "Float Exhaustion": "/float-exhaustion",
    "Seasonal Delivery": "/seasonal-delivery",
    "Darvas Box Pro": "/darvas-box-pro",
    "Multibagger Pro": "/multibagger-pro-scanner",
    "Climax Accumulation": "/climax-accumulation",
}

_GRADE_RANK: dict[str, float] = {
    "A+": 4.5,
    "A": 4,
    "B": 3,
    "C": 2,
    "D": 1,
}
_TIER_RANK: dict[str, float] = {
    "HIGH": 3.5,
    "MID": 2.5,
    "LOW": 1.5,
}


def _grade_rank(value) -> float:
    """Convert a grade/tier/score value to a numeric rank (higher = better)."""
    if value is None:
        return -1
    if isinstance(value, (int, float)):
        return float(value) / 100 * 4  # normalise 0-100 to 0-4 scale
    s = str(value).strip()
    return _GRADE_RANK.get(s.upper(), _TIER_RANK.get(s.upper(), 0))


def _best_grade(candidates: list[dict]) -> str | None:
    """Return the best grade string from a list of candidate dicts."""
    best_rank: float = -1
    best_str: str | None = None

    for c in candidates:
        for key in ("grade", "score", "tier"):
            if key in c and c[key] is not None:
                rank = _grade_rank(c[key])
                if rank > best_rank:
                    best_rank = rank
                    best_str = str(c[key])
    return best_str


def build_confluence_report() -> dict:
    """Aggregate the single same-date confluence snapshot into a report.

    Source of truth is ``MODELS_DIR/confluence_snapshot.json``, written by
    ``myra_web.confluence_batch.run_confluence_batch()``. Every scanner in
    that snapshot ran against one identical ``as_on_date``, so a symbol
    appearing under N scanners genuinely means N scanners agree on that
    date — not "each flagged it at some point across a multi-week window of
    independently-written per-scanner caches", which is what the previous
    per-scanner-cache implementation actually computed.

    Only symbols flagged by 2+ distinct scanners are included.
    """
    IST = timezone(timedelta(hours=5, minutes=30))

    snapshot_path = os.path.join(MODELS_DIR, CONFLUENCE_SNAPSHOT_FILENAME)
    try:
        with open(snapshot_path, encoding="utf-8") as fh:
            snap = json.load(fh)
    except FileNotFoundError:
        return {
            "generated_at": datetime.now(IST).isoformat(),
            "as_on_date": None,
            "scanner_errors": {},
            "message": (
                "No confluence snapshot yet — run POST /api/confluence/refresh "
                "to scan every confluence scanner against the same date."
            ),
            "symbols": [],
        }
    except Exception as e:  # unreadable/corrupt snapshot — degrade, don't 500
        logger.error("Confluence snapshot unreadable: %s", e)
        return {
            "generated_at": datetime.now(IST).isoformat(),
            "as_on_date": None,
            "scanner_errors": {},
            "message": f"Confluence snapshot could not be read: {e}",
            "symbols": [],
        }

    as_on_date = snap.get("as_on_date")
    generated_at = snap.get("generated_at")
    scanners = snap.get("scanners") or {}

    # --- Aggregate per-symbol data -------------------------------------------
    # symbol → { sector, scanners: { display_name: candidate } }
    agg: dict[str, dict] = {}
    scanner_errors: dict[str, str] = {}

    for display_name, entry in scanners.items():
        if entry.get("error"):
            scanner_errors[display_name] = entry["error"]
            continue
        for cand in entry.get("candidates") or []:
            sym = cand.get("symbol")
            if not sym:
                continue
            if sym not in agg:
                agg[sym] = {"sector": cand.get("sector", ""), "scanners": {}}
            agg[sym]["scanners"][display_name] = cand
            # Update sector if the new candidate has a value
            if cand.get("sector") and not agg[sym]["sector"]:
                agg[sym]["sector"] = cand["sector"]

    # --- Filter to 2+ scanners and build output -----------------------------
    symbols_out: list[dict] = []
    for sym, info in agg.items():
        scanner_names = sorted(info["scanners"].keys())
        if len(scanner_names) < 2:
            continue
        cand_list = [info["scanners"][n] for n in scanner_names]
        symbols_out.append(
            {
                "symbol": sym,
                "sector": info["sector"],
                "scanner_count": len(scanner_names),
                "scanners": scanner_names,
                "last_scan": generated_at,
                "best_grade": _best_grade(cand_list),
            }
        )

    # Sort: scanner_count desc, then symbol asc
    symbols_out.sort(key=lambda x: (-x["scanner_count"], x["symbol"]))

    return {
        "generated_at": generated_at or datetime.now(IST).isoformat(),
        "as_on_date": as_on_date,
        "scanner_errors": scanner_errors,
        "symbols": symbols_out,
    }


def get_db_path(db_key: str):
    """Safely construct the path to a specific SQLite sidecar."""
    filename = LibrarianCore.DB_MAP.get(db_key)
    if not filename:
        return None
    return os.path.join(DB_DIR, filename)
