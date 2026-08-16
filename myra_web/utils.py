"""MYRA web-layer shared utilities. Pure helpers extracted from myra_fastapi_server.py (Phase 1 of monolith refactor). No FastAPI state — deterministic functions and constants only."""

import json
import math
import os
import sqlite3
from datetime import datetime, timedelta, timezone

from myra_app.constants import DB_DIR, MODELS_DIR
from myra_app.librarian_core import LibrarianCore


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
    "launchpad_scan_cache.json": "Launchpad Scanner",
}

# Display-name → frontend route (for link column)
_SCANNER_ROUTES: dict[str, str] = {
    "The Trigger": "/trigger",
    "Bottom Hunter": "/bottom-hunter",
    "Invisible Hand": "/invisible-hand",
    "Wyckoff Automaton": "/wyckoff",
    "Liquidity Flip": "/liquidity-flip",
    "Operator Fingerprint": "/operator-fingerprint",
    "Float Exhaustion": "/float-exhaustion",
    "Seasonal Delivery": "/seasonal-delivery",
    "Darvas Box Pro": "/darvas-box-pro",
    "Multibagger Pro": "/multibagger-pro-scanner",
    "Climax Accumulation": "/climax-accumulation",
    "Launchpad Scanner": "/launchpad-scanner",
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
    """Aggregate all scanner cache files into a confluence report.

    Only symbols flagged by 2+ distinct scanners are included.
    """
    IST = timezone(timedelta(hours=5, minutes=30))

    # Collect all cache files that match our known names
    cache_files: dict[str, str] = {}  # display_name → filepath
    try:
        for fname in os.listdir(MODELS_DIR):
            if fname not in _SCANNER_CACHE_MAP:
                continue
            display = _SCANNER_CACHE_MAP[fname]
            # Handle darvas_scan_cache.json vs darvas_cache.json — prefer the
            # one with more candidates; if both exist we'll resolve below.
            fpath = os.path.join(MODELS_DIR, fname)
            if display in cache_files:
                # Already have one for this display name — keep the one with
                # more candidates (lazy: replace if new file is larger).
                try:
                    with open(cache_files[display], encoding="utf-8") as f:
                        existing = json.load(f)
                    with open(fpath, encoding="utf-8") as f:
                        new_data = json.load(f)
                    if len(new_data.get("candidates", [])) > len(
                        existing.get("candidates", [])
                    ):
                        cache_files[display] = fpath
                except Exception:
                    pass
            else:
                cache_files[display] = fpath
    except Exception:
        pass

    if len(cache_files) < 2:
        return {"generated_at": datetime.now(IST).isoformat(), "symbols": []}

    # --- Aggregate per-symbol data -------------------------------------------
    # symbol → { sector, scanners: { display_name: candidate }, last_scan str }
    agg: dict[str, dict] = {}

    for display_name, fpath in cache_files.items():
        try:
            with open(fpath, encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception:
            continue  # graceful degradation

        last_scan = data.get("last_scan")
        for cand in data.get("candidates", []):
            sym = cand.get("symbol")
            if not sym:
                continue
            if sym not in agg:
                agg[sym] = {
                    "sector": cand.get("sector", ""),
                    "scanners": {},
                    "last_scan": last_scan,
                }
            agg[sym]["scanners"][display_name] = cand
            # Track the latest scan timestamp across all scanners
            if last_scan and (
                agg[sym]["last_scan"] is None or last_scan > agg[sym]["last_scan"]
            ):
                agg[sym]["last_scan"] = last_scan
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
                "last_scan": info["last_scan"],
                "best_grade": _best_grade(cand_list),
            }
        )

    # Sort: scanner_count desc, then symbol asc
    symbols_out.sort(key=lambda x: (-x["scanner_count"], x["symbol"]))

    return {
        "generated_at": datetime.now(IST).isoformat(),
        "symbols": symbols_out,
    }


def get_db_path(db_key: str):
    """Safely construct the path to a specific SQLite sidecar."""
    filename = LibrarianCore.DB_MAP.get(db_key)
    if not filename:
        return None
    return os.path.join(DB_DIR, filename)
