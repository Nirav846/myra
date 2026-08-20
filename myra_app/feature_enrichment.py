import logging
import os
import sqlite3
import threading
import time

import polars as pl

from myra_app.constants import DB_DIR

_enrichment_paused = threading.Event()
_enrichment_paused.set()  # not paused initially


def pause_enrichment():
    """Called by screener when a scan starts."""
    _enrichment_paused.clear()  # blocking


def resume_enrichment():
    """Called by screener when a scan finishes."""
    _enrichment_paused.set()  # unblock


def wait_if_paused(timeout_seconds: float = 5.0):
    """
    Wait for enrichment to be resumed, but only for a limited time.
    If timeout occurs, log a warning and force resume to prevent permanent deadlock.
    """
    if not _enrichment_paused.wait(timeout=timeout_seconds):
        logging.getLogger(__name__).warning(
            "Enrichment paused for >%s seconds – forcing resume to avoid deadlock",
            timeout_seconds,
        )
        resume_enrichment()


def enrich_features(df: pl.DataFrame, nifty_df: pl.DataFrame) -> pl.DataFrame:
    """
    Enrich raw market data with institutional dynamic baselines using Vectorized Polars.
    Prioritizes raw calculation over aggressive defaulting to fix the '1.0' lock-in issue.
    """
    if df.is_empty():
        return df

    if "close" in nifty_df.columns:
        nifty_df = nifty_df.rename({"close": "nifty_close"})

    df = df.sort(["symbol", "date"])

    # Ensure critical columns exist to prevent crash
    for col in ["delivery", "high", "low", "volume", "close"]:
        if col not in df.columns:
            df = df.with_columns(pl.lit(1.0).alias(col))

    # Calculate 50-day stock return
    df = df.with_columns(
        (
            (
                pl.col("close")
                - pl.col("close")
                .shift(50)
                .fill_null(pl.col("close").first())
                .over("symbol")
            )
            / pl.col("close")
            .shift(50)
            .fill_null(pl.col("close").first())
            .over("symbol")
        ).alias("stock_return")
    )

    # Calculate Market Return (Benchmark)
    if not nifty_df.is_empty():
        nifty_df = nifty_df.sort("date")
        df = df.join(nifty_df, on="date", how="left")
        if "nifty_close" in df.columns:
            df = df.with_columns(
                pl.col("nifty_close").fill_null(strategy="forward").over("symbol")
            )
            df = df.with_columns(
                (
                    (
                        pl.col("nifty_close")
                        - pl.col("nifty_close")
                        .shift(50)
                        .fill_null(pl.col("nifty_close").first())
                        .over("symbol")
                    )
                    / pl.col("nifty_close")
                    .shift(50)
                    .fill_null(pl.col("nifty_close").first())
                    .over("symbol")
                ).alias("market_return")
            )
        else:
            df = df.with_columns(pl.lit(0.0).alias("market_return"))
    else:
        # No benchmark data available — skip outperformance
        df = df.with_columns(pl.lit(0.0).alias("market_return"))

    # Core Institutional Metrics - Forced Calculation Block
    # We use min_periods=5 to allow calculations to start early in a stock's history
    df = df.with_columns(
        [
            (
                pl.col("delivery")
                / pl.col("delivery").rolling_mean(100, min_periods=5).over("symbol")
            ).alias("delivery_divergence_score"),
            (
                (pl.col("high") - pl.col("low"))
                / (pl.col("high") - pl.col("low"))
                .rolling_mean(50, min_periods=5)
                .over("symbol")
            ).alias("volatility_compression_score"),
            (
                pl.col("volume")
                / pl.col("volume").rolling_mean(50, min_periods=5).over("symbol")
            ).alias("relative_volume_score"),
            (pl.col("stock_return") - pl.col("market_return")).alias(
                "nifty_outperformance_score"
            ),
        ]
    )

    # Minimalist Cleanup: Apply defaults only AFTER calculations are done
    df = df.with_columns(
        [
            pl.col("relative_volume_score").fill_nan(1.0).fill_null(1.0),
            pl.col("nifty_outperformance_score").fill_nan(0.0).fill_null(0.0),
        ]
    )

    if "nifty_close" in df.columns:
        df = df.drop("nifty_close")

    return df


def process_enrichment_pipeline(lib, conn, target_date=None):
    """
    Handles the DB transaction and applies the enrichment logic.
    If target_date is provided, only data from that date backward is processed.
    """
    from datetime import datetime
    from myra_app.task_tracker import register, update, unregister

    tid = register("Enrichment pipeline", task_type="batch")
    start_time = datetime.now()
    try:
        date_ref = (
            f"'{target_date}'"
            if target_date
            else "(SELECT MAX(date) FROM technical_data)"
        )
        ALLOWED_QUERIES = {
            "technical_data": f"SELECT * FROM technical_data WHERE date >= date({date_ref}, '-365 days')",
            "calculated_indicators": "SELECT * FROM calculated_indicators",
            "fundamentals": "SELECT * FROM fundamentals",
        }

        import pandas as pd

        tables = [
            t[0]
            for t in lib.safe_execute(
                "SELECT name FROM sqlite_master WHERE type='table'", conn=conn
            ).fetchall()
        ]

        # Find the first valid table available in the DB
        table_name = None
        for tbl in [
            "technical_data",
            "calculated_indicators",
            "fundamentals",
        ]:
            if tbl in tables:
                table_name = tbl
                break

        if not table_name:
            return

        if table_name not in ALLOWED_QUERIES:
            raise ValueError("Invalid table name")

        df_raw = pl.read_database(
            ALLOWED_QUERIES[table_name],
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

        # Add data loading stats
        import logging

        logger = logging.getLogger(__name__)
        logger.info(
            f"Loaded {df_raw.shape[0]} rows for {df_raw['symbol'].n_unique()} symbols over {df_raw['date'].n_unique()} days"
        )
        # Read from dedicated benchmarks table in myra_metadata.db

        meta_path = os.path.join(DB_DIR, "myra_metadata.db")
        nifty_pd = pd.DataFrame(columns=["date", "close"])
        meta_conn = None
        try:
            meta_conn = sqlite3.connect(meta_path)
            nifty_pd = pd.read_sql(
                "SELECT date, close FROM benchmarks WHERE symbol = '^NSEI' ORDER BY date",
                meta_conn,
            )
        except Exception as e:
            logger.warning(f"Could not read Nifty benchmark from meta.db: {e}")
        finally:
            if meta_conn:
                meta_conn.close()

        # If benchmark data ends before the dates we need (likely), forward-fill
        if not nifty_pd.empty:
            nifty_pd["date"] = pd.to_datetime(nifty_pd["date"])
            all_dates = pd.to_datetime(df_raw["date"].unique())
            nifty_pd = (
                nifty_pd.set_index("date")
                .reindex(all_dates, method="ffill")
                .reset_index()
            )
            nifty_pd.columns = ["date", "close"]
            nifty_pd["date"] = nifty_pd["date"].map(lambda x: f"{x:%Y-%m-%d}")

        nifty_df = pl.from_pandas(nifty_pd)

        df_enriched = enrich_features(df_raw, nifty_df)

        # Add SMC indicators for fusion engine
        from myra_app.utils.smc_calculator import calculate_smc_indicators

        # Convert to pandas for SMC calculation
        price_df = df_enriched.to_pandas()
        if (
            not price_df.empty
            and "symbol" in price_df.columns
            and "date" in price_df.columns
        ):
            update(tid, "Computing SMC indicators…")
            print("[MYRA Enrichment] Computing SMC indicators...")

            # Add max_rows parameter to limit data size for robustness
            max_rows = 500000
            if len(price_df) > max_rows:
                price_df = price_df.tail(max_rows)
                logger.info(f"Limited to last {max_rows} rows for SMC calculation")

            # Process all symbols at once with vectorized SMC calculation
            import time

            t0 = time.time()
            smc_df = calculate_smc_indicators(
                price_df.rename(
                    columns={
                        "open": "Open",
                        "high": "High",
                        "low": "Low",
                        "close": "Close",
                        "volume": "Volume",
                    }
                )
            )
            print(f"SMC calculation took {time.time()-t0:.1f}s")

            # Pre-filter to only target date (or latest date) to reduce update data size
            latest_date = target_date if target_date else price_df["date"].max()
            smc_today = smc_df.filter(pl.col("date") == str(latest_date))
            logger.info(
                f"Filtered to {len(smc_today)} rows for latest date {latest_date}"
            )

            # Write SMC columns to technical_data using efficient batch updates
            smc_columns = [
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

            score_columns = [
                "delivery_divergence_score",
                "volatility_compression_score",
                "relative_volume_score",
                "nifty_outperformance_score",
            ]

            # Add missing columns to technical_data table
            for i, col in enumerate(smc_columns):
                if col in smc_df.columns:
                    try:
                        conn.execute(  # noqa: PG-NPLUS1
                            f"ALTER TABLE technical_data ADD COLUMN {col} REAL"
                        )
                    except sqlite3.OperationalError:
                        pass  # Column already exists

                # Update progress
                pct = (i + 1) * 100 // len(smc_columns)
                elapsed = (datetime.now() - start_time).total_seconds()
                if pct > 0:
                    eta_seconds = (elapsed / pct) * (100 - pct)
                    eta_str = f"~{int(eta_seconds // 60)}m {int(eta_seconds % 60)}s"
                else:
                    eta_str = "calculating…"
                update(tid, progress=pct, eta=eta_str)

            # Add missing score columns to technical_data table
            for col in score_columns:
                try:
                    conn.execute(f"ALTER TABLE technical_data ADD COLUMN {col} REAL")  # noqa: PG-NPLUS1
                except sqlite3.OperationalError:
                    pass  # Column already exists

            # Batch update using executemany for performance
            for i, col in enumerate(smc_columns):
                if col in smc_today.columns:
                    # Add progress print every 5 columns
                    if i % 5 == 0:
                        print(f"Enrichment column {i+1}/{len(smc_columns)} done")

                    # Prepare batch data
                    update_data = [
                        (
                            float(row[col]) if pd.notna(row[col]) else None,
                            row["symbol"],
                            str(row["date"]),
                            target_date,
                        )
                        if target_date
                        else (
                            float(row[col]) if pd.notna(row[col]) else None,
                            row["symbol"],
                            str(row["date"]),
                        )
                        for row in smc_today.to_pandas().to_dict("records")
                        if pd.notna(row[col])
                    ]

                    if update_data:
                        if target_date:
                            date_filter = "AND date = ?"
                        else:
                            date_filter = (
                                "AND date = (SELECT MAX(date) FROM technical_data)"
                            )

                        conn.executemany(
                            f"UPDATE technical_data SET {col} = ? WHERE symbol = ? AND date = ? AND {col} IS NULL {date_filter}",
                            update_data,
                        )
                        conn.commit()

            # Write enrichment score columns in batches (executemany + single commit)
            enriched_today = df_enriched.filter(pl.col("date") == str(latest_date))
            # Group rows by the set of columns they update, so each group uses one
            # executemany with a single commit (mirrors the SMA-50 batch pattern).
            updates_by_cols: dict = {}
            for row in enriched_today.iter_rows(named=True):
                symbol = row["symbol"]
                date_str = str(row["date"])
                score_values = {}
                for col in score_columns:
                    if col in row:
                        val = row[col]
                        if val is not None:
                            score_values[col] = float(val)
                if score_values:
                    col_tuple = tuple(score_values.keys())
                    updates_by_cols.setdefault(col_tuple, []).append(  # noqa: PG-APPEND
                        list(score_values.values()) + [symbol, date_str]
                    )
            for col_tuple, batch in updates_by_cols.items():
                set_clauses = [f"{c}=?" for c in col_tuple]
                conn.executemany(
                    f"UPDATE technical_data SET {','.join(set_clauses)} WHERE symbol=? AND date=?",
                    batch,
                )
            conn.commit()

            # Check if enrichment should pause after processing all symbols
            wait_if_paused()

            # --- 52-week high/low and SMA-50 computation ---
            update(tid, "Computing SMA-50 and 52-week metrics…")
            print("[MYRA Enrichment] Computing SMA-50 and 52-week high/low...")
            for col in ["sma_50", "high_52w", "low_52w"]:
                try:
                    conn.execute(f"ALTER TABLE technical_data ADD COLUMN {col} REAL")  # noqa: PG-NPLUS1
                except sqlite3.OperationalError:
                    pass

            df_roll = df_enriched.sort(["symbol", "date"])
            df_roll = df_roll.with_columns(
                [
                    pl.col("close")
                    .rolling_mean(50, min_periods=1)
                    .over("symbol")
                    .alias("sma_50"),
                    pl.col("high")
                    .rolling_max(252, min_periods=1)
                    .over("symbol")
                    .alias("high_52w"),
                    pl.col("low")
                    .rolling_min(252, min_periods=1)
                    .over("symbol")
                    .alias("low_52w"),
                ]
            )

            df_latest = df_roll.filter(pl.col("date") == str(latest_date))

            update_rows = []
            for row in df_latest.iter_rows(named=True):
                sma = float(row["sma_50"]) if row["sma_50"] is not None else None
                h52 = float(row["high_52w"]) if row["high_52w"] is not None else None
                l52 = float(row["low_52w"]) if row["low_52w"] is not None else None
                if sma is not None or h52 is not None or l52 is not None:
                    update_rows.append((sma, h52, l52, row["symbol"], str(row["date"])))  # noqa: PG-APPEND

            if update_rows:
                conn.executemany(
                    "UPDATE technical_data SET sma_50 = ?, high_52w = ?, low_52w = ? WHERE symbol = ? AND date = ?",
                    update_rows,
                )
                conn.commit()
                print(
                    f"[MYRA Enrichment] Updated {len(update_rows)} symbols with 52-week/SMA-50 metrics"
                )

        update(tid, "Enrichment complete")

        # Print total elapsed time
        total_elapsed = (datetime.now() - start_time).total_seconds()
        print(
            f"Enrichment completed in {total_elapsed:.1f}s ({int(total_elapsed // 60)}m {int(total_elapsed % 60)}s)"
        )

    except Exception as e:
        import logging

        logging.getLogger(__name__).error(f"Enrichment pipeline failed: {e}")
    finally:
        unregister(tid)


# --- Columns used by enrich_from_dataframe --------------------------------
_SCORE_COLS = [
    "delivery_divergence_score",
    "volatility_compression_score",
    "relative_volume_score",
    "nifty_outperformance_score",
]
_SMC_COLS = [
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
_ROLL_COLS = ["sma_50", "high_52w", "low_52w"]


def enrich_from_dataframe(
    full_df: pl.DataFrame,
    nifty_df: pl.DataFrame,
    target_date: str,
) -> dict[str, dict[str, float]]:
    """
    Compute enrichment for a single *target_date* using a pre-loaded Polars
    DataFrame instead of hitting the database.

    Parameters
    ----------
    full_df : pl.DataFrame
        Complete *technical_data* table (all dates, all symbols).
    nifty_df : pl.DataFrame
        Nifty 50 benchmark with columns ``["date", "close"]``, indexed to
        every date present in *full_df* (forward-filled for gaps).
    target_date : str
        ISO date (``YYYY-MM-DD``) to enrich.

    Returns
    -------
    dict[str, dict[str, float]]
        ``{symbol: {column_name: value, …}}`` for the target date.
        Only non‑null values are included.
    """
    from datetime import datetime, timedelta

    logger = logging.getLogger(__name__)

    # 1. Slice a 365‑day look‑back window ending at target_date
    td = datetime.strptime(target_date, "%Y-%m-%d")
    window_start = f"{(td - timedelta(days=365)):%Y-%m-%d}"

    window_df = full_df.filter(
        (pl.col("date") >= window_start) & (pl.col("date") <= target_date)
    )

    if window_df.is_empty():
        logger.warning("Empty window for %s — skipping", target_date)
        return {}

    # 2. Scores + rolling metrics (idempotent by construction)
    enriched = enrich_features(window_df, nifty_df)

    # 3. SMC indicators — pass Polars directly (avoids pandas round‑trip)
    from myra_app.utils.smc_calculator import calculate_smc_indicators

    smc_result = calculate_smc_indicators(enriched)  # returns pl.DataFrame

    # 4. Keep only the target date from each result
    enriched_today = enriched.filter(pl.col("date") == target_date)
    smc_today = smc_result.filter(pl.col("date") == target_date)

    # 5. Merge into {symbol: {col: val}}
    results: dict[str, dict[str, float]] = {}

    for row in enriched_today.iter_rows(named=True):
        sym: str = row["symbol"]
        entry: dict[str, float] = {}
        for col in _SCORE_COLS + _ROLL_COLS:
            val = row.get(col)
            if val is not None:
                try:
                    entry[col] = float(val)
                except (TypeError, ValueError):
                    pass
        results[sym] = entry

    for row in smc_today.iter_rows(named=True):
        sym: str = row["symbol"]
        entry = results.setdefault(sym, {})
        for col in _SMC_COLS:
            val = row.get(col)
            if val is not None:
                try:
                    entry[col] = float(val)
                except (TypeError, ValueError):
                    pass

    return results
