import polars as pl
import duckdb


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
    for col in ["delivery_qty", "high", "low", "volume", "close"]:
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
        # Fallback: Dynamic Market Average of all stocks
        df = df.with_columns(
            pl.col("stock_return").mean().over("date").alias("market_return")
        )

    # Core Institutional Metrics - Forced Calculation Block
    # We use min_periods=5 to allow calculations to start early in a stock's history
    df = df.with_columns(
        [
            (
                pl.col("delivery_qty")
                / pl.col("delivery_qty").rolling_mean(100, min_periods=5).over("symbol")
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
            pl.col("delivery_divergence_score").fill_nan(1.0).fill_null(1.0),
            pl.col("volatility_compression_score").fill_nan(1.0).fill_null(1.0),
            pl.col("relative_volume_score").fill_nan(1.0).fill_null(1.0),
            pl.col("nifty_outperformance_score").fill_nan(0.0).fill_null(0.0),
        ]
    )

    if "nifty_close" in df.columns:
        df = df.drop("nifty_close")

    return df


def process_enrichment_pipeline(conn):
    """
    Handles the DB transaction and applies the enrichment logic.
    """
    try:
        if isinstance(conn, duckdb.DuckDBPyConnection):
            tables = [t[0] for t in conn.execute("SHOW TABLES").fetchall()]
            table_name = "prices"
            valid_tables = ["prices", "technical_data", "calculated_indicators", "fundamentals"]
            if table_name not in valid_tables:
                raise ValueError("Invalid table name")
            if table_name not in tables:
                return

            df_raw = pl.from_arrow(conn.execute(f"SELECT * FROM {table_name}").arrow())  # noqa: S608
            if df_raw.is_empty():
                return

            nifty_df = pl.DataFrame({"date": [], "close": []})
            if "benchmarks" in tables:
                nifty_df = pl.from_arrow(
                    conn.execute(
                        "SELECT date, close FROM benchmarks WHERE symbol = '^NSEI'"
                    ).arrow()
                )

            df_enriched = enrich_features(df_raw, nifty_df)
            conn.register("df_enriched_view", df_enriched.to_arrow())

            conn.execute("BEGIN TRANSACTION")
            try:
                conn.execute("DROP TABLE IF EXISTS stg_enriched_market_data")
                conn.execute(
                    "CREATE TABLE stg_enriched_market_data AS SELECT * FROM df_enriched_view"
                )
                conn.execute(f"DROP TABLE {table_name}")  # noqa: S608
                conn.execute(
                    f"ALTER TABLE stg_enriched_market_data RENAME TO {table_name}"  # noqa: S608
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        else:  # SQLite fallback
            import pandas as pd

            cursor = conn.cursor()
            tables = [
                t[0]
                for t in cursor.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            ]
            table_name = "technical_data"
            valid_tables = ["prices", "technical_data", "calculated_indicators", "fundamentals"]
            if table_name not in valid_tables:
                raise ValueError("Invalid table name")
            if table_name not in tables:
                return

            df_raw = pl.read_database(f"SELECT * FROM {table_name}", conn)  # noqa: S608
            nifty_pd = pd.read_sql(
                "SELECT date, close FROM technical_data WHERE symbol LIKE '%NIFTY 50%'",
                conn,
            )
            nifty_df = pl.from_pandas(nifty_pd)

            df_enriched = enrich_features(df_raw, nifty_df)
            df_enriched.to_pandas().to_sql(
                "stg_enriched_market_data", conn, if_exists="replace", index=False
            )

            try:
                cursor.executescript(
                    f"""
                    BEGIN TRANSACTION;
                    DROP TABLE IF EXISTS {table_name};
                    ALTER TABLE stg_enriched_market_data RENAME TO {table_name};
                    COMMIT;
                    """  # noqa: S608
                )
            except Exception:
                cursor.execute("ROLLBACK")
                raise

    except Exception as e:
        import logging

        logging.getLogger(__name__).error(f"Enrichment pipeline failed: {e}")
