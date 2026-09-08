"""Daily point-in-time market-cap rank builder (D1 — Bottom Hunter M1).

Builds ``mcap_rank_daily`` in the **scoring** sidecar
(``myra_scoring.db``): one row per (symbol, date) with the reconstructed
market cap in ₹ crore and a strict per-day rank (1 = largest).

Methodology (validated in Phase 4 / Phase 5 of the mcap work; the
ranking-fidelity gate passed CLEAN against the independent anchor):

    mcap(T) = stored_close(T) × shares_outstanding_current

* The stored close is already back-adjusted for splits and bonuses, and the
  current share count already counts those events' shares, so the
  split/bonus multiplier *cancels* for every date.
* Non-split share changes (rights, QIP, buybacks, mergers) are NOT captured
  by this formula — those events change the share count with no price
  adjustment.  This is the documented HDFCBANK limitation (HDFC-HDFC Bank
  merger, July 2023: pre-merger mcaps reconstructed with this formula are
  overstated by ~the merger ratio; the name stays in the top-20 regardless,
  so top-500 membership is unaffected).  A real fix requires a historical
  share-count time series, which MYRA does not keep.

Unadjusted corporate actions
----------------------------
Six split/bonus events in the 2015-2026 history hit stored series NOT
back-adjusted by the vendor (detected in the Phase-4 corporate-action pass:
435 events classified → 393 adjusted, 6 unadjusted, 22 ambiguous).  For
those, pre-``ex_date`` mcaps are divided by the event multiplier::

    UNSPLIT_CORRECTIONS = [
        # (symbol, ex_date, multiplier)
        ("AHLEAST",      "2022-10-06", 1.5),
        ("KOTYARK",      "2026-06-24", 11.0),
        ("VMARCIND_SME", "2026-07-07", 6.0),
        ("AILIMITED_SME","2026-08-24", 2.0),
        ("TDPOWERSYS",   "2026-08-24", 2.0),
        ("CORDELIA",     "2026-08-25", 10.0),
    ]

Embedded deliberately (provenance: ``.backtest_scratch/_phase4_mcap_corrections.pkl``)
so the production builder does not depend on a scratch artifact.  A future
automated detector that re-derives these from price-gap analysis can replace
this table; until then the table is the source of truth for corrections.

Rankable universe
-----------------
``rankable = symbols_master.instrument_type='EQUITY'`` AND
``fundamentals.shares_outstanding > 0`` AND present in ``technical_data``.
This mirrors the Phase-4 validated set (2,395 rankable symbols; the 22
missing-shares symbols are stale/ETF names, none in the NIFTY-500 snapshot).

Enrichment-pipeline placement — WIRED (decision: standalone table)
------------------------------------------------------------------
``feature_enrichment.py::process_enrichment_pipeline`` calls
:func:`update_mcap_rank_for_date` with the latest trading day at the end of
the enrichment run (after close prices land); the call is non-fatal and the
scanner falls back to the fundamentals snapshot if the table is absent or
stale.  Two design points were investigated and resolved:

1. Write engine — a SQL ``ROW_NUMBER()`` writer (matching the read-side
   window used in the scanner fallback) was REJECTED in favor of the
   existing pandas compute in this module: per-date rank via
   ``groupby("date").cumcount()+1`` (identical semantics), written with
   delete-then-append on the PK ``(symbol, date)``.  This keeps the
   enrichment pipeline's Python-side write idiom.
2. ``mcap_rank_daily`` stays a STANDALONE table in ``myra_scoring.db``.
   Folding it into ``ranking_history`` was rejected: that table has zero
   code references and a different rank concept (Nifty500-membership/sector
   ranks vs a raw PIT market-cap rank over all 2,395 rankable equities).
"""

from __future__ import annotations

import logging
import os
import sqlite3
from typing import Optional, Sequence

logger = logging.getLogger(__name__)

# (symbol, ex_date, multiplier) — see module docstring for provenance.
UNSPLIT_CORRECTIONS: tuple[tuple[str, str, float], ...] = (
    ("AHLEAST", "2022-10-06", 1.5),
    ("KOTYARK", "2026-06-24", 11.0),
    ("VMARCIND_SME", "2026-07-07", 6.0),
    ("AILIMITED_SME", "2026-08-24", 2.0),
    ("TDPOWERSYS", "2026-08-24", 2.0),
    ("CORDELIA", "2026-08-25", 10.0),
)

TARGET_TABLE = "mcap_rank_daily"

DDL = f"""
CREATE TABLE IF NOT EXISTS {TARGET_TABLE} (
    symbol     TEXT NOT NULL,
    date       TEXT NOT NULL,
    mcap_cr    REAL,
    mcap_rank  INTEGER,
    PRIMARY KEY (symbol, date)
)
"""


def _db_path(key: str) -> str:
    from myra_app.constants import DB_DIR
    from myra_app.librarian_core import LibrarianCore

    return os.path.join(DB_DIR, LibrarianCore.DB_MAP[key])


def _shares_map(conn_val: sqlite3.Connection) -> dict[str, float]:
    """Latest shares_outstanding per symbol (INR-denominated raw count)."""
    rows = conn_val.execute(
        """
        SELECT f.symbol, f.shares_outstanding
        FROM fundamentals f
        INNER JOIN (
            SELECT symbol, MAX(date) AS max_date
            FROM fundamentals
            WHERE shares_outstanding IS NOT NULL AND shares_outstanding > 0
            GROUP BY symbol
        ) latest ON f.symbol = latest.symbol AND f.date = latest.max_date
        """
    ).fetchall()
    return {r[0]: float(r[1]) for r in rows}


def _rankable_symbols(
    conn_meta: sqlite3.Connection, conn_val: sqlite3.Connection
) -> set[str]:
    """EQUITY master symbols that have a positive share count (Phase-4 set)."""
    equity = {
        r[0]
        for r in conn_meta.execute(
            "SELECT symbol FROM symbols_master WHERE instrument_type = 'EQUITY'"
        ).fetchall()
    }
    return equity & set(_shares_map(conn_val).keys())


def _fetch_closes(
    conn_tech: sqlite3.Connection,
    symbols: Sequence[str],
    start_date: Optional[str],
    end_date: Optional[str],
) -> list[tuple[str, str, float]]:
    """(symbol, date, close) rows for the rankable symbols in window."""
    if not symbols:
        return []
    ph = ",".join("?" for _ in symbols)
    sql = (
        f"SELECT symbol, date, close FROM technical_data "
        f"WHERE symbol IN ({ph}) AND close IS NOT NULL AND close > 0"
    )
    params: list = list(symbols)
    if start_date:
        sql += " AND date >= ?"
        params.append(start_date)
    if end_date:
        sql += " AND date <= ?"
        params.append(end_date)
    return conn_tech.execute(sql, params).fetchall()  # type: ignore[arg-type]


def _build_frame(
    conn_tech: sqlite3.Connection,
    conn_meta: sqlite3.Connection,
    conn_val: sqlite3.Connection,
    start_date: Optional[str],
    end_date: Optional[str],
) -> "pd.DataFrame | None":
    """Compute the (symbol, date, mcap_cr, mcap_rank) frame for the window.

    Returns None when there is nothing to rank.  Rankable set is fixed by
    the CURRENT fundamentals/meta snapshot (see module docstring); per-day
    ranks are therefore PIT-correct with respect to price, and use the
    latest share count (the validated approximation).
    """
    import pandas as pd

    rankable = _rankable_symbols(conn_meta, conn_val)
    shares = _shares_map(conn_val)
    if not rankable:
        return None

    rows = _fetch_closes(conn_tech, sorted(rankable), start_date, end_date)
    if not rows:
        return None

    df = pd.DataFrame(rows, columns=["symbol", "date", "close"])
    df = df[df["symbol"].isin(shares)]
    if df.empty:
        return None
    df["mcap_cr"] = df["close"] * df["symbol"].map(shares) / 1e7

    # Apply un-split corrections: pre-ex-date mcaps /mult.
    for symbol, ex_date, mult in UNSPLIT_CORRECTIONS:
        mask = (df["symbol"] == symbol) & (df["date"] < ex_date)
        n = int(mask.sum())
        if n:
            df.loc[mask, "mcap_cr"] = df.loc[mask, "mcap_cr"] / mult
            logger.info(
                "Correction applied: %s pre-%s mcap /%.2f (%d rows)",
                symbol,
                ex_date,
                mult,
                n,
            )

    # Strict per-day rank: largest mcap first; ties broken by symbol for
    # determinism (ROW_NUMBER semantics).
    df = df.sort_values(["date", "mcap_cr", "symbol"], ascending=[True, False, True])
    df["mcap_rank"] = df.groupby("date").cumcount() + 1
    return df


def build_mcap_rank_daily(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    top_n: int = 500,
) -> dict:
    """Full rebuild of ``mcap_rank_daily`` (idempotent; table replaced).

    Returns a small stats dict (rows, days, symbols, top-n snapshot) for the
    caller / CLI.  The full 2015-2026 history is ~2.2M rows and rebuilds in
    well under a minute.
    """
    conn_tech = sqlite3.connect(_db_path("technical"))
    conn_meta = sqlite3.connect(_db_path("meta"))
    conn_val = sqlite3.connect(_db_path("valuation"))
    conn_score = sqlite3.connect(_db_path("scoring"))
    try:
        conn_score.execute(DDL)
        df = _build_frame(conn_tech, conn_meta, conn_val, start_date, end_date)
        if df is None:
            return {"rows": 0, "days": 0, "symbols": 0}

        conn_score.execute("BEGIN")
        df[["symbol", "date", "mcap_cr", "mcap_rank"]].to_sql(
            TARGET_TABLE, conn_score, if_exists="replace", index=False
        )
        conn_score.commit()

        days = int(df["date"].nunique())
        syms = int(df["symbol"].nunique())
        latest = df["date"].max()
        latest_snapshot = df[df["date"] == latest]
        top_rows = int((latest_snapshot["mcap_rank"] <= top_n).sum())
        if top_rows:
            cutoff_cr = float(
                latest_snapshot.loc[
                    latest_snapshot["mcap_rank"] == top_n, "mcap_cr"
                ].iloc[0]
            )
        else:
            cutoff_cr = None
        stats = {
            "rows": int(len(df)),
            "days": days,
            "symbols": syms,
            "latest_date": latest,
            f"top_{top_n}_on_latest": top_rows,
            f"top_{top_n}_cutoff_cr": cutoff_cr,
        }
        logger.info("mcap_rank_daily rebuilt: %s", stats)
        return stats
    finally:
        for c in (conn_score, conn_val, conn_meta, conn_tech):
            c.close()


def update_mcap_rank_for_date(date_str: str) -> int:
    """Upsert a single date's ranks into the existing table.

    The daily-pipeline hook: called by
    ``feature_enrichment.process_enrichment_pipeline`` after close prices
    land for *date_str*, it recomputes only that date and deletes-then-appends
    the rows (the table's PRIMARY KEY (symbol, date) makes the upsert exact).
    """
    conn_tech = sqlite3.connect(_db_path("technical"))
    conn_meta = sqlite3.connect(_db_path("meta"))
    conn_val = sqlite3.connect(_db_path("valuation"))
    conn_score = sqlite3.connect(_db_path("scoring"))
    try:
        conn_score.execute(DDL)
        df = _build_frame(conn_tech, conn_meta, conn_val, date_str, date_str)
        if df is None or df.empty:
            logger.warning("update_mcap_rank_for_date(%s): no rows", date_str)
            return 0
        conn_score.execute("BEGIN")
        # Exact upsert for the date: delete-then-append inside the same
        # transaction (to_sql has no INSERT OR REPLACE; PK is (symbol, date)).
        conn_score.execute(f"DELETE FROM {TARGET_TABLE} WHERE date = ?", (date_str,))
        df[["symbol", "date", "mcap_cr", "mcap_rank"]].to_sql(
            TARGET_TABLE, conn_score, if_exists="append", index=False
        )
        conn_score.commit()
        logger.info("mcap_rank_daily updated for %s: %d rows", date_str, len(df))
        return int(len(df))
    finally:
        for c in (conn_score, conn_val, conn_meta, conn_tech):
            c.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(build_mcap_rank_daily())
