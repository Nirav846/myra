"""
Optimised historical enrichment backfill (~22 min vs ~20 h).

Loads ALL *technical_data* once into Polars, processes each date using the
pre-loaded DataFrame, and writes results in batches of 50 dates.

Usage
-----
    python tools/enrich_history.py                        # full backfill
    python tools/enrich_history.py --single-date 2024-06-24  # test one
    python tools/enrich_history.py --dry-run                  # dry run
"""

import argparse
import logging
import os
import sqlite3
import sys
import time
from collections import defaultdict

import polars as pl

# Ensure project root is on sys.path so `myra_app` is importable
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from myra_app.constants import DB_DIR
from myra_app.feature_enrichment import enrich_from_dataframe
from myra_app.librarian_core import LibrarianCore

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("myra.backfill_enrichment")

# All enrichment columns the backfill writes
ALL_COLS = (
    [
        "delivery_divergence_score",
        "volatility_compression_score",
        "relative_volume_score",
        "nifty_outperformance_score",
    ]
    + [
        "bullish_fvg",
        "bearish_fvg",
        "fvg_top",
        "fvg_bottom",
        "fvg_boundary",
        "fvg_freshness",
        "swing_high",
        "swing_low",
        "liquidity_distance",
        "htf_bullish",
        "htf_bearish",
        "mtf_bullish",
        "mtf_bearish",
        "trend_alignment",
        "delivery_ma_60",
        "has_bullish_fvg",
    ]
    + ["sma_50", "high_52w", "low_52w"]
)


def load_nifty(full_df: pl.DataFrame) -> pl.DataFrame:
    """Load Nifty 50 benchmark, reindex to *full_df* dates, forward‑fill gaps."""
    meta_path = os.path.join(DB_DIR, LibrarianCore.DB_MAP["meta"])
    if not os.path.exists(meta_path):
        logger.warning("Meta DB not found — using empty benchmark")
        return pl.DataFrame(
            {
                "date": pl.Series([], dtype=pl.Utf8),
                "close": pl.Series([], dtype=pl.Float64),
            }
        )

    meta_conn = sqlite3.connect(meta_path)
    try:
        nifty = pl.read_database(
            "SELECT date, close FROM benchmarks WHERE symbol = '^NSEI' ORDER BY date",
            meta_conn,
        )
    finally:
        meta_conn.close()

    if nifty.is_empty():
        return pl.DataFrame(
            {
                "date": pl.Series([], dtype=pl.Utf8),
                "close": pl.Series([], dtype=pl.Float64),
            }
        )

    all_dates = full_df.select(pl.col("date").unique()).sort("date")
    nifty = all_dates.join(nifty, on="date", how="left").with_columns(
        pl.col("close").fill_null(strategy="forward")
    )
    return nifty


def ensure_columns(conn: sqlite3.Connection):
    """CREATE missing enrichment columns to avoid ALTER failures mid‑batch."""
    existing = {row[1] for row in conn.execute("PRAGMA table_info(technical_data)")}
    for col in ALL_COLS:
        if col not in existing:
            try:
                conn.execute(
                    f"ALTER TABLE technical_data ADD COLUMN {col} REAL"
                )  # noqa: PG-NPLUS1
            except sqlite3.OperationalError:
                pass
    conn.commit()


def write_batch(conn: sqlite3.Connection, batch: list[tuple[str, str, str, float]]):
    """
    Write one batch of ``(symbol, date, column, value)`` triples.

    Uses ``UPDATE … WHERE col IS NULL`` for idempotency.
    """
    by_col: dict[str, list[tuple[float, str, str]]] = defaultdict(list)
    for symbol, date_str, col, val in batch:
        by_col[col].append((val, symbol, date_str))  # noqa: PG-APPEND

    for col, rows in by_col.items():
        conn.executemany(
            f"UPDATE technical_data SET {col}=? WHERE symbol=? AND date=? AND {col} IS NULL",
            rows,
        )


def get_null_dates(conn: sqlite3.Connection) -> list[str]:
    """Return sorted list of dates where any enrichment column is NULL."""
    rows = conn.execute(
        "SELECT DISTINCT date FROM technical_data "
        "WHERE delivery_divergence_score IS NULL "
        "ORDER BY date"
    ).fetchall()
    return [r[0] for r in rows]


def _progress_line(
    idx: int, total: int, date_str: str, elapsed: float, start_time: float
) -> str:
    """Return a formatted progress string with ETA."""
    pct = idx / total * 100
    elapsed_total = time.time() - start_time
    rate = idx / elapsed_total if elapsed_total > 0 else 0
    eta = (total - idx) / rate if rate > 0 else 0
    return (
        f"[{idx}/{total}] {date_str} done in {elapsed:.1f}s "
        f"({pct:.1f}%, {rate:.2f} dates/s, ETA {eta:.0f}s)"
    )


def main():
    parser = argparse.ArgumentParser(description="Optimised enrichment backfill")
    parser.add_argument(
        "--dry-run", action="store_true", help="Only discover dates, do not write"
    )
    parser.add_argument(
        "--single-date",
        type=str,
        default=None,
        help="Process only this single date (YYYY-MM-DD) for testing",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=50,
        help="Number of dates per write batch (default: 50)",
    )
    args = parser.parse_args()

    tech_path = os.path.join(DB_DIR, LibrarianCore.DB_MAP["technical"])
    if not os.path.exists(tech_path):
        logger.error("Technical database not found at %s", tech_path)
        sys.exit(1)

    logger.info("Connecting to technical database …")
    conn = sqlite3.connect(tech_path, timeout=60)

    if args.single_date:
        dates = [args.single_date]
    else:
        dates = get_null_dates(conn)

    if not dates:
        logger.info("No dates require backfill — everything is up to date.")
        conn.close()
        return

    total = len(dates)
    logger.info(
        "Found %d dates to process (range: %s … %s)", total, dates[0], dates[-1]
    )

    if args.dry_run:
        logger.info("DRY RUN — would process %d dates", total)
        logger.info("First 5: %s", dates[:5])
        conn.close()
        return

    # --- Load data once ----------------------------------------------------
    logger.info("Loading technical_data into Polars …")
    t0 = time.time()
    full_df = pl.read_database(
        "SELECT * FROM technical_data ORDER BY date",
        conn,
        infer_schema_length=None,
        schema_overrides={
            "volume": pl.Int64,
            "delivery": pl.Float64,
            "trades": pl.Int64,
            "delivery_pct": pl.Float64,
            "open": pl.Float64,
            "high": pl.Float64,
            "low": pl.Float64,
            "close": pl.Float64,
            "vwap": pl.Float64,
        },
    )
    logger.info(
        "Loaded %d rows for %d symbols over %d dates in %.1fs",
        full_df.shape[0],
        full_df["symbol"].n_unique(),
        full_df["date"].n_unique(),
        time.time() - t0,
    )

    logger.info("Loading Nifty benchmark …")
    nifty_df = load_nifty(full_df)
    logger.info("Nifty benchmark: %d rows", nifty_df.shape[0])

    ensure_columns(conn)
    logger.info("Schema verified")

    # --- Process dates in batches ------------------------------------------
    batch_size = args.batch_size
    batch_buffer: list[tuple[str, str, str, float]] = []
    errors = 0
    start_time = time.time()

    for idx, d in enumerate(dates, start=1):
        t0 = time.time()
        try:
            result = enrich_from_dataframe(full_df, nifty_df, d)
            elapsed = time.time() - t0

            for symbol, values in result.items():
                for col, val in values.items():
                    batch_buffer.append((symbol, d, col, val))  # noqa: PG-APPEND

            if (idx % batch_size == 0 or idx == total) and batch_buffer:
                write_batch(conn, batch_buffer)
                conn.commit()
                logger.info(
                    "Batch written: %d column-values across %d dates",
                    len(batch_buffer),
                    min(batch_size, idx % batch_size or batch_size),
                )
                batch_buffer.clear()

            if idx % 20 == 0 or idx == total or idx == 1:
                logger.info(_progress_line(idx, total, d, elapsed, start_time))

        except Exception as e:
            errors += 1
            logger.error("Failed on date %s: %s", d, e)
            # Skip this date; keep buffer intact for rest of batch

    if batch_buffer:
        write_batch(conn, batch_buffer)
        conn.commit()
        batch_buffer.clear()

    elapsed_total = time.time() - start_time
    logger.info(
        "Finished processing %d dates in %.1fs with %d errors.",
        total,
        elapsed_total,
        errors,
    )

    conn.close()


if __name__ == "__main__":
    main()
