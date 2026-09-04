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
    os.path.join(
        PROJECT_ROOT, "eod2", "src", "eod2_data", "daily"
    ),  # BhavDesk (primary)
    os.path.join(PROJECT_ROOT, "eod2_data", "daily"),  # legacy fallback
    r"D:\01screener\Myra\eod2\src\eod2_data\daily",  # hard-coded fallback
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


def _read_symbol_csv(
    csv_path: str, symbol: str
) -> tuple[pd.DataFrame, pd.DataFrame] | None:
    """Read a single eod2 CSV and return (valid_df, rejected_df), or ``None`` on
    read failure.

    ``valid_df`` contains rows that passed all checks and will be inserted into
    ``technical_data``. ``rejected_df`` contains rows where the close price was
    missing, NaN, or <= 0 after numeric coercion; they are mirrored to the
    existing ``ingestion_rejects`` table by the caller and never written to
    ``technical_data``. A non-empty rejected_df is normal — it corresponds to
    audit finding C1 (EOD2 zero-close fallback).

    Rejection is split into two distinct reason codes (assigned by the caller
    via ``_record_eod2_rejects``):

      - ``blank/unparseable close (eod2)`` for NaN-after-coerce rows
      - ``close<=0 (eod2)`` for explicit non-positive values
    """
    try:
        df = pd.read_csv(csv_path)
    except Exception:
        return None

    if df.empty:
        return None

    # Rename known columns
    df = df.rename(columns=_RENAME_MAP)

    # Keep only the columns we care about (ignore Series, QTY_PER_TRADE, etc.)
    keep = [
        c
        for c in [
            "date",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "delivery",
            "trades",
        ]
        if c in df.columns
    ]
    df = df[keep].copy()

    # Parse dates
    df["date"] = pd.to_datetime(df["date"], format="%Y-%m-%d", errors="coerce")
    df = df.dropna(subset=["date"])
    if df.empty:
        return None

    # Numeric coercion — coerce WITHOUT fill so we can detect unparseable close.
    for col in _NUMERIC_COLS:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    # Audit C1: identify rows whose close is missing/<=0 AFTER coercion.
    # These would otherwise have been silently written as 0 via the legacy
    # .fillna(0) fallback, contaminating technical_data and downstream rolling
    # metrics (sma_50, high_52w, low_52w). Drop them and route to ingestion_rejects.
    if "close" in df.columns:
        close_bad_mask = df["close"].isna() | (df["close"] <= 0)
    else:
        # No close column at all — every row is invalid for our purposes.
        close_bad_mask = pd.Series(True, index=df.index)

    rejected_df = df.loc[close_bad_mask].copy()
    valid_df = df.loc[~close_bad_mask].copy()

    # Now fillna(0) on the VALID frame only, for downstream derived columns.
    for col in _NUMERIC_COLS:
        if col in valid_df.columns:
            valid_df[col] = valid_df[col].fillna(0)

    if not valid_df.empty:
        valid_df["symbol"] = symbol.upper()
        dates = valid_df["date"]
        valid_df["date"] = dates.dt.strftime("%Y-%m-%d")  # noqa: PG-STRFTIME

    if not rejected_df.empty:
        rejected_df["symbol"] = symbol.upper()
        dates = rejected_df["date"]
        rejected_df["date"] = dates.dt.strftime("%Y-%m-%d")  # noqa: PG-STRFTIME

    return valid_df, rejected_df


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
    "symbol",
    "date",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "delivery",
    "trades",
    "delivery_pct",
    "delivery_ratio",
    "delivery_source",
    "sma_50",
    "high_52w",
    "low_52w",
    "delivery_ma_60",
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


def _record_eod2_rejects(conn: sqlite3.Connection, rejected_df: pd.DataFrame) -> int:
    """Mirror rows whose close was missing/<=0 into the existing
    ``ingestion_rejects`` table (lives in myra_technical.db).

    Schema is fixed by librarian_schema.py / schema_registry.py — columns are
    ``(symbol, date, reason, raw_values, timestamp)``. No schema change here.

    Idempotency: the ``ingestion_rejects`` table has NO unique constraint
    (see librarian_schema.py:193-202 — no PRIMARY KEY, no UNIQUE index). A
    plain INSERT therefore duplicates on every rerun. To keep a same-day
    catch-up rerun from accumulating duplicates for this same symbol/date
    (re, reason) tuple, we pre-check via SELECT LIMIT 1 before each insert.
    Two distinct reason strings are used so the "blank/unparseable" and
    "explicitly <= 0" cases can be told apart later:

      - ``blank/unparseable close (eod2)`` for NaN-after-coerce rows
      - ``close<=0 (eod2)`` for rows whose close is an explicit non-positive value

    Args:
        conn: open sqlite3 connection to myra_technical.db.
        rejected_df: per-symbol rows with missing/NaN/<=0 close.

    Returns:
        Number of reject rows actually inserted (0 on any error).
    """
    if rejected_df is None or rejected_df.empty:
        return 0
    raw_cols = [
        c
        for c in ("open", "high", "low", "close", "volume", "delivery")
        if c in rejected_df.columns
    ]

    REASON_BLANK = "blank/unparseable close (eod2)"
    REASON_LE_ZERO = "close<=0 (eod2)"

    inserted = 0
    try:
        for _, r in rejected_df.iterrows():  # noqa: PG-ITERROWS
            raw_close = r.get("close")
            if pd.isna(raw_close):
                reason = REASON_BLANK
            else:
                reason = REASON_LE_ZERO
            sym = r.get("symbol", "")
            date = r.get("date", "")
            # Idempotency: skip if (symbol, date, reason) already recorded.
            # ingestion_rejects has no unique constraint, so SELECT-based
            # dedup is required to prevent unbounded duplicates on reruns.
            existing = conn.execute(
                "SELECT 1 FROM ingestion_rejects WHERE symbol = ? AND date = ? AND reason = ? LIMIT 1",
                (sym, date, reason),
            ).fetchone()
            if existing is not None:
                continue
            raw_values = {c: r.get(c) for c in raw_cols}
            conn.execute(
                "INSERT INTO ingestion_rejects (symbol, date, reason, raw_values) VALUES (?, ?, ?, ?)",
                (sym, date, reason, str(raw_values)),
            )
            inserted += 1
    except Exception as exc:
        logger.warning(f"[EOD2] Failed to record {len(rejected_df)} reject(s): {exc}")
    return inserted


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
        "rejected_rows": 0,
        "rejected_symbols": 0,
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
                read = _read_symbol_csv(csv_path, symbol)
                if read is None:
                    result["skipped"] += 1
                    continue
                valid_df, rejected_df = read

                if rejected_df is not None and not rejected_df.empty:
                    result["rejected_rows"] += int(len(rejected_df))
                    result["rejected_symbols"] += 1
                    inserted_rejects = _record_eod2_rejects(conn, rejected_df)
                    if inserted_rejects != len(rejected_df):
                        # Don't double-count: only credit what actually landed.
                        result["rejected_rows"] -= len(rejected_df) - inserted_rejects

                if valid_df is None or valid_df.empty:
                    result["skipped"] += 1
                    continue

                # Filter to new rows only
                if db_max:
                    valid_df = valid_df[valid_df["date"] > db_max]
                if valid_df.empty:
                    result["skipped"] += 1
                    continue

                valid_df = _compute_derived(valid_df)

                rows = [
                    tuple(row)
                    for row in valid_df[_INSERT_COLS].itertuples(index=False, name=None)
                ]
                inserted = _insert_rows(conn, rows)
                result["rows_inserted"] += inserted
                result["symbols_updated"] += 1
            except Exception as exc:
                logger.debug(f"[EOD2] Error processing {symbol}: {exc}")
                result["skipped"] += 1

        # Commit any reject rows we accumulated before final outcome logging.
        # If no rows were inserted we still want the rejects to land, so this
        # commit is independent of the "rows_inserted > 0" branch below.
        try:
            conn.commit()
        except Exception:
            pass

        if result["rows_inserted"] > 0:
            logger.info(
                f"[EOD2] Inserted {result['rows_inserted']} rows for "
                f"{result['symbols_updated']} symbols."
            )
            # Run enrichment for SMC / score columns
            _run_enrichment(conn)
        else:
            logger.info("[EOD2] No new rows to insert — DB is current.")

        # Audit C1: surfaced summary so zero-close contamination is no longer silent.
        if result["rejected_rows"] > 0:
            logger.info(
                f"[EOD2 REJECT] {result['rejected_rows']} rows rejected "
                f"(close<=0 or unparseable) across {result['rejected_symbols']} symbols."
            )

        # Audit H9: surface parse-error skips at INFO, not only DEBUG.
        if result["skipped"] > 0:
            logger.info(
                f"[EOD2] skipped {result['skipped']} symbols due to parse errors"
            )

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
