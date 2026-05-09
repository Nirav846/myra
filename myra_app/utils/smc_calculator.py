"""
MYRA SMC Calculator - High Performance Polars Implementation
Computes Smart Money Concepts indicators from OHLCV data.
Optimized for 2.4M+ rows using Polars vectorized operations.
"""

import polars as pl
import pandas as pd
from typing import Union


def calculate_smc_indicators(
    df: Union[pd.DataFrame, pl.DataFrame],
    swing_length: int = 5,
    use_polars: bool = True,
) -> Union[pd.DataFrame, pl.DataFrame]:
    """Calculate SMC indicators with Polars for speed."""

    # Convert pandas to polars and ensure lowercase columns
    if isinstance(df, pd.DataFrame):
        df_pl = pl.from_pandas(df)
        # Ensure column names are lowercase
        df_pl = df_pl.rename({col: col.lower() for col in df_pl.columns})
    else:
        df_pl = df.clone()
        # Also ensure lowercase for safety
        if any(c.isupper() for c in df_pl.columns):
            df_pl = df_pl.rename({col: col.lower() for col in df_pl.columns})

    # Sort for per-symbol operations
    df_pl = df_pl.sort(["symbol", "date"])

    # --- FVG Detection ---
    df_pl = df_pl.with_columns(
        [
            (pl.col("low") > pl.col("high").shift(2).over("symbol")).alias(
                "bullish_fvg"
            ),
            (pl.col("high") < pl.col("low").shift(2).over("symbol")).alias(
                "bearish_fvg"
            ),
        ]
    )

    # --- FVG boundaries ---
    df_pl = df_pl.with_columns(
        [
            pl.when(pl.col("bullish_fvg"))
            .then(pl.col("low"))
            .when(pl.col("bearish_fvg"))
            .then(pl.col("high").shift(2).over("symbol"))
            .otherwise(None)
            .alias("fvg_top"),
            pl.when(pl.col("bullish_fvg"))
            .then(pl.col("high").shift(2).over("symbol"))
            .when(pl.col("bearish_fvg"))
            .then(pl.col("low"))
            .otherwise(None)
            .alias("fvg_bottom"),
        ]
    )

    df_pl = df_pl.with_columns(
        [
            pl.when(pl.col("close") > pl.col("fvg_top"))
            .then(pl.col("fvg_top"))
            .when(pl.col("close") < pl.col("fvg_bottom"))
            .then(pl.col("fvg_bottom"))
            .otherwise((pl.col("fvg_top") + pl.col("fvg_bottom")) / 2)
            .alias("fvg_boundary")
        ]
    )

    # --- FVG Freshness (using cumulative count) ---
    df_pl = df_pl.with_columns(
        [
            ((pl.col("bullish_fvg")) | (pl.col("bearish_fvg"))).alias("fvg_event"),
            pl.int_range(pl.len()).over("symbol").alias("row_idx"),
        ]
    )
    df_pl = df_pl.with_columns(
        [
            pl.when(pl.col("fvg_event"))
            .then(pl.col("row_idx"))
            .otherwise(None)
            .over("symbol")
            .forward_fill()
            .alias("last_event_idx")
        ]
    )
    df_pl = df_pl.with_columns(
        [
            (pl.col("row_idx") - pl.col("last_event_idx"))
            .fill_null(-1000)
            .alias("fvg_freshness")
        ]
    )
    df_pl = df_pl.drop(["fvg_event", "row_idx", "last_event_idx"])

    # --- Swing Highs/Lows (using rolling max/min, much faster) ---
    window = 2 * swing_length + 1
    df_pl = df_pl.with_columns(
        [
            pl.col("high")
            .cast(pl.Float64)
            .rolling_max(window_size=window, center=True)
            .over("symbol")
            .alias("rolling_high_max"),
            pl.col("low")
            .cast(pl.Float64)
            .rolling_min(window_size=window, center=True)
            .over("symbol")
            .alias("rolling_low_min"),
        ]
    )
    df_pl = df_pl.with_columns(
        [
            pl.when(pl.col("high") == pl.col("rolling_high_max"))
            .then(pl.col("high"))
            .otherwise(None)
            .alias("swing_high"),
            pl.when(pl.col("low") == pl.col("rolling_low_min"))
            .then(pl.col("low"))
            .otherwise(None)
            .alias("swing_low"),
        ]
    )
    # Forward fill swing values
    df_pl = df_pl.with_columns(
        [
            pl.col("swing_high").forward_fill().over("symbol").alias("swing_high"),
            pl.col("swing_low").forward_fill().over("symbol").alias("swing_low"),
        ]
    )
    df_pl = df_pl.drop(["rolling_high_max", "rolling_low_min"])

    # --- Liquidity Distance ---
    df_pl = df_pl.with_columns(
        [
            pl.min_horizontal(
                [
                    (pl.col("swing_high") - pl.col("close")).abs() / pl.col("close"),
                    (pl.col("close") - pl.col("swing_low")).abs() / pl.col("close"),
                ]
            ).alias("liquidity_distance")
        ]
    )

    # --- Trend Alignment (rolling SMAs) ---
    df_pl = df_pl.with_columns(
        [
            pl.col("close")
            .cast(pl.Float64)
            .rolling_mean(window_size=20)
            .over("symbol")
            .alias("sma20"),
            pl.col("close")
            .cast(pl.Float64)
            .rolling_mean(window_size=50)
            .over("symbol")
            .alias("sma50"),
            pl.col("close")
            .cast(pl.Float64)
            .rolling_mean(window_size=200)
            .over("symbol")
            .alias("sma200"),
        ]
    )
    df_pl = df_pl.with_columns(
        [
            (pl.col("sma50") > pl.col("sma200")).cast(pl.Int32).alias("htf_bullish"),
            (pl.col("sma50") < pl.col("sma200")).cast(pl.Int32).alias("htf_bearish"),
            (pl.col("sma20") > pl.col("sma50")).cast(pl.Int32).alias("mtf_bullish"),
            (pl.col("sma20") < pl.col("sma50")).cast(pl.Int32).alias("mtf_bearish"),
        ]
    )
    df_pl = df_pl.with_columns(
        [
            (
                pl.col("htf_bullish")
                + pl.col("mtf_bullish")
                - pl.col("htf_bearish")
                - pl.col("mtf_bearish")
            ).alias("trend_alignment")
        ]
    )

    # --- Delivery MA ---
    if "delivery_qty" in df_pl.columns:
        df_pl = df_pl.with_columns(
            [
                pl.col("delivery_qty")
                .cast(pl.Float64)
                .rolling_mean(window_size=60)
                .over("symbol")
                .alias("delivery_ma_60")
            ]
        )
    elif "delivery" in df_pl.columns:
        df_pl = df_pl.with_columns(
            [
                pl.col("delivery")
                .cast(pl.Float64)
                .rolling_mean(window_size=60)
                .over("symbol")
                .alias("delivery_ma_60")
            ]
        )
    else:
        df_pl = df_pl.with_columns([pl.lit(None).alias("delivery_ma_60")])

    # --- Active bullish FVG ---
    df_pl = df_pl.with_columns(
        [
            (pl.col("bullish_fvg") & (pl.col("fvg_freshness") <= 10))
            .cast(pl.Int32)
            .alias("has_bullish_fvg")
        ]
    )

    # Convert boolean to int for consistency
    df_pl = df_pl.with_columns(
        [pl.col("bullish_fvg").cast(pl.Int32), pl.col("bearish_fvg").cast(pl.Int32)]
    )

    return df_pl if use_polars else df_pl.to_pandas()
