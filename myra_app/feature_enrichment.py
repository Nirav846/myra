import polars as pl


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


def process_enrichment_pipeline(lib, conn):
    """
    Handles the DB transaction and applies the enrichment logic.
    """
    ALLOWED_QUERIES = {
        "prices": "SELECT * FROM prices",
        "technical_data": "SELECT * FROM technical_data",
        "calculated_indicators": "SELECT * FROM calculated_indicators",
        "fundamentals": "SELECT * FROM fundamentals"
    }

    ALLOWED_DROPS = {
        "prices": "DROP TABLE IF EXISTS prices",
        "technical_data": "DROP TABLE IF EXISTS technical_data",
        "calculated_indicators": "DROP TABLE IF EXISTS calculated_indicators",
        "fundamentals": "DROP TABLE IF EXISTS fundamentals"
    }

    ALLOWED_RENAMES = {
        "prices": "ALTER TABLE stg_enriched_market_data RENAME TO prices",
        "technical_data": "ALTER TABLE stg_enriched_market_data RENAME TO technical_data",
        "calculated_indicators": "ALTER TABLE stg_enriched_market_data RENAME TO calculated_indicators",
        "fundamentals": "ALTER TABLE stg_enriched_market_data RENAME TO fundamentals"
    }

    try:
        import pandas as pd

        tables = [
            t[0]
            for t in lib.safe_execute(
                "SELECT name FROM sqlite_master WHERE type='table'", conn=conn
            ).fetchall()
        ]

        # Find the first valid table available in the DB
        table_name = None
        for tbl in ["technical_data", "prices", "calculated_indicators", "fundamentals"]:
            if tbl in tables:
                table_name = tbl
                break

        if not table_name:
            return

        if table_name not in ALLOWED_QUERIES:
            raise ValueError("Invalid table name")

        df_raw = pl.read_database(ALLOWED_QUERIES[table_name], conn)
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
            lib.safe_execute("BEGIN TRANSACTION;", conn=conn)
            lib.safe_execute(ALLOWED_DROPS[table_name], conn=conn)
            lib.safe_execute(ALLOWED_RENAMES[table_name], conn=conn)
            lib.safe_execute("COMMIT;", conn=conn)
        except Exception:
            lib.safe_execute("ROLLBACK", conn=conn)
            raise

    except Exception as e:
        import logging

        logging.getLogger(__name__).error(f"Enrichment pipeline failed: {e}")
