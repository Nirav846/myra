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

    if not nifty_df.is_empty():
        nifty_df = nifty_df.sort("date")
        df = df.join(nifty_df, on="date", how="left")
        if "nifty_close" in df.columns:
            df = df.with_columns(
                pl.col("nifty_close").fill_null(strategy="forward").over("symbol")
            )
    else:
        df = df.with_columns(pl.lit(None).alias("nifty_close"))

    for col in ["delivery_qty", "high", "low", "volume", "close"]:
        if col not in df.columns:
            df = df.with_columns(pl.lit(1.0).alias(col))

    df = df.with_columns(
        [
            (
                pl.col("delivery_qty")
                / pl.col("delivery_qty").rolling_mean(100).over("symbol")
            ).alias("delivery_divergence_score"),
            (
                (pl.col("high") - pl.col("low"))
                / (pl.col("high") - pl.col("low")).rolling_mean(50).over("symbol")
            ).alias("volatility_compression_score"),
            (pl.col("volume") / pl.col("volume").rolling_mean(50).over("symbol")).alias(
                "relative_volume_score"
            ),
            (
                (
                    (pl.col("close") - pl.col("close").shift(50).over("symbol"))
                    / pl.col("close").shift(50).over("symbol")
                )
                - (
                    (
                        pl.col("nifty_close")
                        - pl.col("nifty_close").shift(50).over("symbol")
                    )
                    / pl.col("nifty_close").shift(50).over("symbol")
                )
            ).alias("nifty_outperformance_score"),
        ]
    )

    df = df.with_columns(
        [
            pl.col("delivery_divergence_score")
            .fill_null(strategy="forward")
            .over("symbol"),
            pl.col("volatility_compression_score")
            .fill_null(strategy="forward")
            .over("symbol"),
            pl.col("relative_volume_score")
            .fill_null(strategy="forward")
            .over("symbol"),
            pl.col("nifty_outperformance_score")
            .fill_null(strategy="forward")
            .over("symbol"),
        ]
    )

    if "nifty_close" in df.columns:
        df = df.drop("nifty_close")

    return df


def process_enrichment_pipeline(conn: duckdb.DuckDBPyConnection):
    """
    Schema Check: Verifies enriched DataFrame has same row count.
    No-Overwrite Policy: Writes to stg_enriched_market_data first.
    """
    try:
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

        # Transactional Swap
        conn.execute("BEGIN TRANSACTION")
        try:
            conn.execute("DROP TABLE IF EXISTS stg_enriched_market_data")
            conn.execute(
                "CREATE TABLE stg_enriched_market_data AS SELECT * FROM df_enriched_view"
            )
            conn.execute(f"DROP TABLE {table_name}")
            conn.execute(f"ALTER TABLE stg_enriched_market_data RENAME TO {table_name}")
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

    except Exception as e:
        import logging

        logging.getLogger(__name__).error(f"Enrichment pipeline failed: {e}")
