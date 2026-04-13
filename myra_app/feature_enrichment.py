import polars as pl
import duckdb


def enrich_features(df: pl.DataFrame, nifty_df: pl.DataFrame) -> pl.DataFrame:
    """
    Enrich raw market data with institutional dynamic baselines using Vectorized Polars.
    """
    if df.is_empty():
        return df

    if "close" in nifty_df.columns:
        nifty_df = nifty_df.rename({"close": "nifty_close"})

    df = df.sort(["symbol", "date"])

    for col in ["delivery_qty", "high", "low", "volume", "close"]:
        if col not in df.columns:
            df = df.with_columns(pl.lit(1.0).alias(col))

    # Calculate stock return first
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

    if not nifty_df.is_empty():
        nifty_df = nifty_df.sort("date")
        df = df.join(nifty_df, on="date", how="left")
        if "nifty_close" in df.columns:
            df = df.with_columns(
                pl.col("nifty_close").fill_null(strategy="forward").over("symbol")
            )
            # Calculate market return based on NIFTY index
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
        # Calculate daily average return of all stocks instead of averaging the price
        df = df.with_columns(
            pl.col("stock_return").mean().over("date").alias("market_return")
        )

    # To prevent division by zero or dropping values on short histories, fallback if shift/rolling is null
    df = df.with_columns(
        [
            (
                pl.col("delivery_qty")
                / pl.col("delivery_qty").rolling_mean(100, min_periods=1).over("symbol")
            )
            .fill_nan(1.0)
            .alias("delivery_divergence_score"),
            (
                (pl.col("high") - pl.col("low"))
                / (pl.col("high") - pl.col("low"))
                .rolling_mean(50, min_periods=1)
                .over("symbol")
            )
            .fill_nan(1.0)
            .alias("volatility_compression_score"),
            (
                pl.col("volume")
                / pl.col("volume").rolling_mean(50, min_periods=1).over("symbol")
            )
            .fill_nan(1.0)
            .alias("relative_volume_score"),
            (pl.col("stock_return") - pl.col("market_return"))
            .fill_nan(0.0)
            .alias("nifty_outperformance_score"),
        ]
    )

    df = df.with_columns(
        [
            pl.col("delivery_divergence_score")
            .fill_null(strategy="forward")
            .fill_null(1.0)
            .over("symbol"),
            pl.col("volatility_compression_score")
            .fill_null(strategy="forward")
            .fill_null(1.0)
            .over("symbol"),
            pl.col("relative_volume_score")
            .fill_null(strategy="forward")
            .fill_null(1.0)
            .over("symbol"),
            pl.col("nifty_outperformance_score")
            .fill_null(strategy="forward")
            .fill_null(0.0)
            .over("symbol"),
        ]
    )

    if "nifty_close" in df.columns:
        df = df.drop("nifty_close")

    return df


def process_enrichment_pipeline(conn):
    """
    Schema Check: Verifies enriched DataFrame has same row count.
    No-Overwrite Policy: Writes to stg_enriched_market_data first.
    """
    try:
        if isinstance(conn, duckdb.DuckDBPyConnection):
            tables = conn.execute("SHOW TABLES").fetchall()
            tables = [t[0] for t in tables]
            table_name = "prices"
            if table_name not in tables:
                return

            df_raw = pl.from_arrow(conn.execute(f"SELECT * FROM {table_name}").arrow())
            if df_raw.is_empty():
                return

            nifty_df = pl.DataFrame({"date": [], "close": []})
            if "benchmarks" in tables:
                nifty_df = pl.from_arrow(
                    conn.execute(
                        "SELECT date, close FROM benchmarks WHERE symbol = '^NSEI'"
                    ).arrow()
                )

            original_row_count = df_raw.height
            df_enriched = enrich_features(df_raw, nifty_df)

            if df_enriched.height != original_row_count:
                raise ValueError(
                    f"Row count mismatch! Expected {original_row_count}, "
                    f"got {df_enriched.height}."
                )

            conn.register("df_enriched_view", df_enriched.to_arrow())

            conn.execute("BEGIN TRANSACTION")
            try:
                conn.execute("DROP TABLE IF EXISTS stg_enriched_market_data")
                conn.execute(
                    "CREATE TABLE stg_enriched_market_data AS SELECT * FROM df_enriched_view"
                )
                conn.execute(f"DROP TABLE {table_name}")
                conn.execute(
                    f"ALTER TABLE stg_enriched_market_data RENAME TO {table_name}"
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        else:  # sqlite fallback
            import pandas as pd

            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = [t[0] for t in cursor.fetchall()]

            table_name = "technical_data"
            if table_name not in tables:
                return

            df_raw = pl.read_database(f"SELECT * FROM {table_name}", conn)
            if df_raw.is_empty():
                return

            nifty_df = pl.DataFrame({"date": [], "close": []})
            if "technical_data" in tables:
                nifty_pd = pd.read_sql(
                    "SELECT date, close FROM technical_data WHERE symbol LIKE '%NIFTY 50%'",
                    conn,
                )
                nifty_df = pl.from_pandas(nifty_pd)

            original_row_count = df_raw.height
            df_enriched = enrich_features(df_raw, nifty_df)

            if df_enriched.height != original_row_count:
                raise ValueError(
                    f"Row count mismatch! Expected {original_row_count}, "
                    f"got {df_enriched.height}."
                )

            df_enriched.to_pandas().to_sql(
                "stg_enriched_market_data", conn, if_exists="replace", index=False
            )

            cursor.execute("BEGIN TRANSACTION")
            try:
                cursor.execute(f"DROP TABLE IF EXISTS {table_name}")
                cursor.execute(
                    f"ALTER TABLE stg_enriched_market_data RENAME TO {table_name}"
                )
                cursor.execute("COMMIT")
            except Exception:
                cursor.execute("ROLLBACK")
                raise

    except Exception as e:
        import logging

        logging.getLogger(__name__).error(f"Enrichment pipeline failed: {e}")
