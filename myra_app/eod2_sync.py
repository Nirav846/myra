"""
EOD2 Data Sync — incremental sync from eod2_data/daily/ CSVs.

When ``constants.USE_EOD2_DATA`` is True the background orchestrator calls
:func:`sync_eod2_data` instead of the NSE bhavcopy fetcher.  Only rows whose
date is newer than the current DB maximum are inserted.  Derived columns
(delivery_pct, delivery_ratio, sma_50, high_52w, low_52w, delivery_ma_60) are
computed per-symbol during the sync so historical rows stay correct even when
the enrichment pipeline only touches the latest date.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd

from myra_app.constants import DB_DIR, PROJECT_ROOT

logger = logging.getLogger(__name__)

# ── Candidate locations for the eod2_data/daily/ folder ──────────────────────
# BhavDesk syncs NSE data daily into the eod2 submodule.  That is the preferred
# source (most recent, auto-updated).  The standalone eod2_data/ folder is a
# legacy fallback.
_EOD2_DAILY_CANDIDATES = [
    os.path.join(PROJECT_ROOT, "eod2", "src", "eod2_data", "daily"),   # BhavDesk (primary)
    os.path.join(PROJECT_ROOT, "eod2_data", "daily"),                   # legacy fallback
    r"D:\01screener\Myra\eod2\src\eod2_data\daily",                    # hard-coded fallback
]


def _find_eod2_daily() -> str | None:
    """Return the first existing eod2_data/daily/ path, or ``None``."""
    for p in _EOD2_DAILY_CANDIDATES:
        if os.path.isdir(p):
            logger.info(f"[EOD2] Using data source: {p}")
            return p
    return None


def _get_db_path() -> str:
    """Canonical path to myra_technical.db."""
    from myra_app.librarian_core import LibrarianCore

    return os.path.join(DB_DIR, LibrarianCore.DB_MAP["technical"])


def _get_meta_latest(eod2_daily: str) -> str | None:
    """Read ``meta.json`` and return the ``lastUpdate`` date as YYYY-MM-DD, or
    ``None`` if unavailable."""
    meta_path = os.path.join(os.path.dirname(eod2_daily), "meta.json")
    if not os.path.exists(meta_path):
        return None
    try:
        with open(meta_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        raw = data.get("lastUpdate", "")
        # Format: "2026-08-14T00:00:00+05:30"
        return raw[:10] if raw else None
    except Exception:
        return None


def _db_latest_date(conn: sqlite3.Connection) -> str | None:
    """Return the max(date) from technical_data, or None if empty."""
    row = conn.execute("SELECT MAX(date) FROM technical_data").fetchone()
    return row[0] if row and row[0] else None


# ── Column mapping (eod2 CSV headers → canonical DB columns) ────────────────
_RENAME_MAP = {
    "Date": "date",
    "Open": "open",
    "High": "high",
    "Low": "low",
    "Close": "close",
    "Volume": "volume",
    "DLV_QTY": "delivery",
    "TOTAL_TRADES": "trades",
}

_NUMERIC_COLS = ["open", "high", "low", "close", "volume", "delivery", "trades"]


def _read_symbol_csv(csv_path: str, symbol: str) -> pd.DataFrame | None:
    """Read a single eod2 CSV and return a normalised DataFrame, or ``None`` on
    failure."""
    try:
        df = pd.read_csv(csv_path)
    except Exception:
        return None

    if df.empty:
        return None

    # Rename known columns
    df = df.rename(columns=_RENAME_MAP)

    # Keep only the columns we care about (ignore Series, QTY_PER_TRADE, etc.)
    keep = [c for c in ["date", "open", "high", "low", "close", "volume",
                         "delivery", "trades"] if c in df.columns]
    df = df[keep].copy()

    # Parse dates
    df["date"] = pd.to_datetime(df["date"], format="%Y-%m-%d", errors="coerce")
    df = df.dropna(subset=["date"])
    if df.empty:
        return None

    # Numeric coercion
    for col in _NUMERIC_COLS:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

    df["symbol"] = symbol.upper()
    df["date"] = df["date"].dt.strftime("%Y-%m-%d")

    return df


def _compute_derived(df: pd.DataFrame) -> pd.DataFrame:
    """Add delivery_pct, delivery_ratio, delivery_source and the rolling
    enrichment columns (sma_50, high_52w, low_52w, delivery_ma_60)."""
    df = df.sort_values("date").copy()

    # delivery_pct
    vol = df["volume"].replace(0, np.nan)
    df["delivery_pct"] = (df["delivery"] / vol * 100).fillna(0)

    # delivery_ratio
    df["delivery_ratio"] = (df["delivery"] / vol).fillna(0)
    df.loc[df["volume"] <= 0, "delivery_ratio"] = 0

    # delivery_source
    df["delivery_source"] = "eod2_adjusted"

    # Rolling enrichments (need ≥2 rows to be meaningful)
    if len(df) >= 2:
        df["sma_50"] = df["close"].rolling(50, min_periods=1).mean()
        df["high_52w"] = df["high"].rolling(252, min_periods=1).max()
        df["low_52w"] = df["low"].rolling(252, min_periods=1).min()
        df["delivery_ma_60"] = df["delivery_pct"].rolling(60, min_periods=1).mean()
    else:
        df["sma_50"] = df["close"]
        df["high_52w"] = df["high"]
        df["low_52w"] = df["low"]
        df["delivery_ma_60"] = df["delivery_pct"]

    return df


# Columns written by the sync (order must match the INSERT statement)
_INSERT_COLS = [
    "symbol", "date", "open", "high", "low", "close", "volume",
    "delivery", "trades", "delivery_pct", "delivery_ratio",
    "delivery_source", "sma_50", "high_52w", "low_52w", "delivery_ma_60",
]

_INSERT_SQL = (
    "INSERT OR REPLACE INTO technical_data "
    "(" + ", ".join(_INSERT_COLS) + ") "
    "VALUES (" + ", ".join(["?"] * len(_INSERT_COLS)) + ")"
)


def _insert_rows(conn: sqlite3.Connection, rows: list[tuple]) -> int:
    """Bulk-insert rows and return count."""
    if not rows:
        return 0
    conn.executemany(_INSERT_SQL, rows)
    return len(rows)


# ── Enrichment helper ────────────────────────────────────────────────────────
def _run_enrichment(conn: sqlite3.Connection) -> None:
    """Best-effort call to the existing enrichment pipeline for SMC / score
    columns.  Silently skipped on failure so the sync is never blocked."""
    try:
        from myra_app.feature_enrichment import process_enrichment_pipeline
        from myra_app.librarian import Librarian

        lib = Librarian(read_only=False)
        lib.connect()
        process_enrichment_pipeline(lib, conn)
        conn.commit()
        logger.info("[EOD2] Enrichment pipeline completed.")
    except Exception as exc:
        logger.warning(f"[EOD2] Enrichment pipeline skipped: {exc}")


# ── Public API ────────────────────────────────────────────────────────────────
def sync_eod2_data() -> dict[str, Any]:
    """Incremental sync from eod2_data/daily/ CSVs into myra_technical.db.

    Returns a dict with keys ``rows_inserted``, ``symbols_updated``,
    ``skipped``, and ``error`` (``None`` on success).
    """
    result: dict[str, Any] = {
        "rows_inserted": 0,
        "symbols_updated": 0,
        "skipped": 0,
        "error": None,
    }

    eod2_daily = _find_eod2_daily()
    if eod2_daily is None:
        result["error"] = "eod2_data/daily/ folder not found"
        logger.error(f"[EOD2] {result['error']}")
        return result

    db_path = _get_db_path()
    if not os.path.exists(db_path):
        result["error"] = f"DB not found: {db_path}"
        logger.error(f"[EOD2] {result['error']}")
        return result

    # Determine the cutoff: any CSV row with date > DB max(date) is new.
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA busy_timeout = 30000")
    try:
        db_max = _db_latest_date(conn)
        meta_latest = _get_meta_latest(eod2_daily)
        logger.info(
            f"[EOD2] DB latest={db_max}  meta.json lastUpdate={meta_latest}  "
            f"source={eod2_daily}"
        )

        # Quick bail: if meta.json is older than or equal to DB, nothing new.
        if meta_latest and db_max and meta_latest <= db_max:
            logger.info("[EOD2] No new data — meta.json ≤ DB max date.")
            conn.close()
            return result

        csv_files = [f for f in os.listdir(eod2_daily) if f.endswith(".csv")]
        logger.info(f"[EOD2] Found {len(csv_files)} CSV files.")

        for fname in csv_files:
            symbol = fname.replace(".csv", "")  # e.g. "20microns"
            csv_path = os.path.join(eod2_daily, fname)

            try:
                df = _read_symbol_csv(csv_path, symbol)
                if df is None or df.empty:
                    result["skipped"] += 1
                    continue

                # Filter to new rows only
                if db_max:
                    df = df[df["date"] > db_max]
                if df.empty:
                    result["skipped"] += 1
                    continue

                df = _compute_derived(df)

                rows = [tuple(row) for row in df[_INSERT_COLS].itertuples(index=False, name=None)]
                inserted = _insert_rows(conn, rows)
                result["rows_inserted"] += inserted
                result["symbols_updated"] += 1
            except Exception as exc:
                logger.debug(f"[EOD2] Error processing {symbol}: {exc}")
                result["skipped"] += 1

        if result["rows_inserted"] > 0:
            conn.commit()
            logger.info(
                f"[EOD2] Inserted {result['rows_inserted']} rows for "
                f"{result['symbols_updated']} symbols."
            )
            # Run enrichment for SMC / score columns
            _run_enrichment(conn)
        else:
            logger.info("[EOD2] No new rows to insert — DB is current.")

    except Exception as exc:
        result["error"] = str(exc)
        logger.error(f"[EOD2] Sync failed: {exc}")
        try:
            conn.rollback()
        except Exception:
            pass
    finally:
        conn.close()

    return result
