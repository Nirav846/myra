"""
MYRA SMC Calculator - High Performance Polars Implementation
Computes Smart Money Concepts indicators from OHLCV data.
Optimized for 2.4M+ rows using Polars vectorized operations.
"""

import polars as pl
from typing import Union

_FVG_NO_EVENT_SENTINEL = -1000  # No FVG event has occurred yet


def _normalize_input(
    df: Union["pd.DataFrame", pl.DataFrame], swing_length: int
) -> pl.DataFrame:
    """Normalize input DataFrame to Polars with lowercase columns and validation."""
    # Validate swing_length
    if swing_length < 1:
        raise ValueError(f"swing_length must be >= 1, got {swing_length}")

    # Convert pandas to polars and ensure lowercase columns
    if isinstance(df, pl.DataFrame):
        df_pl = df.clone()
        # Also ensure lowercase for safety
        if any(c.isupper() for c in df_pl.columns):
            df_pl = df_pl.rename({col: col.lower() for col in df_pl.columns})
    else:
        import pandas as pd

        df_pl = pl.from_pandas(df)
        # Ensure column names are lowercase
        df_pl = df_pl.rename({col: col.lower() for col in df_pl.columns})

    # Validate required columns
    REQUIRED_COLS = {"symbol", "date", "open", "high", "low", "close"}
    missing = REQUIRED_COLS - set(df_pl.columns)
    if missing:
        raise ValueError(f"Missing required columns: {missing}")

    # Sort for per-symbol operations
    df_pl = df_pl.sort(["symbol", "date"])

    # Cast price columns once at function entry
    df_pl = df_pl.with_columns(
        [pl.col("open", "high", "low", "close").cast(pl.Float64)]
    )

    return df_pl


def _add_fvg(df: pl.DataFrame) -> pl.DataFrame:
    """Add FVG detection and related columns."""
    # Batch shift operations to avoid re-partitioning
    df = df.with_columns(
        [
            pl.col("high").shift(2).over("symbol").alias("high_shift_2"),
            pl.col("low").shift(2).over("symbol").alias("low_shift_2"),
        ]
    )
    df = df.with_columns(
        [
            (pl.col("low") > pl.col("high_shift_2")).alias("bullish_fvg"),
            (pl.col("high") < pl.col("low_shift_2")).alias("bearish_fvg"),
        ]
    )

    # --- FVG boundaries ---
    df = df.with_columns(
        [
            pl.when(pl.col("bullish_fvg"))
            .then(pl.col("low"))
            .when(pl.col("bearish_fvg"))
            .then(pl.col("high_shift_2"))
            .otherwise(None)
            .alias("fvg_top"),
            pl.when(pl.col("bullish_fvg"))
            .then(pl.col("high_shift_2"))
            .when(pl.col("bearish_fvg"))
            .then(pl.col("low"))
            .otherwise(None)
            .alias("fvg_bottom"),
        ]
    )
    df = df.drop(["high_shift_2", "low_shift_2"])

    # fvg_boundary is null when no FVG exists on that row — consumers
    # should check bullish_fvg/bearish_fvg before reading this column.
    df = df.with_columns(
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
    df = df.with_columns(
        [
            ((pl.col("bullish_fvg")) | (pl.col("bearish_fvg"))).alias("fvg_event"),
            pl.int_range(pl.len()).over("symbol").alias("row_idx"),
        ]
    )
    df = df.with_columns(
        [
            pl.when(pl.col("fvg_event"))
            .then(pl.col("row_idx"))
            .otherwise(None)
            .over("symbol")
            .forward_fill()
            .alias("last_event_idx")
        ]
    )
    df = df.with_columns(
        [
            (pl.col("row_idx") - pl.col("last_event_idx"))
            .fill_null(_FVG_NO_EVENT_SENTINEL)
            .alias("fvg_freshness")
        ]
    )
    df = df.drop(["fvg_event", "row_idx", "last_event_idx"])

    return df


def _add_swing_levels(df: pl.DataFrame, swing_length: int) -> pl.DataFrame:
    """Add swing high/low levels and liquidity distance."""
    # --- Swing Highs/Lows (using rolling max/min, much faster) ---
    window = 2 * swing_length + 1
    df = df.with_columns(
        [
            pl.col("high")
            .rolling_max(window_size=window, center=True)
            .over("symbol")
            .alias("rolling_high_max"),
            pl.col("low")
            .rolling_min(window_size=window, center=True)
            .over("symbol")
            .alias("rolling_low_min"),
        ]
    )
    df = df.with_columns(
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
    df = df.with_columns(
        [
            pl.col("swing_high").forward_fill().over("symbol").alias("swing_high"),
            pl.col("swing_low").forward_fill().over("symbol").alias("swing_low"),
        ]
    )
    df = df.drop(["rolling_high_max", "rolling_low_min"])

    # --- Liquidity Distance ---
    df = df.with_columns(
        [
            pl.min_horizontal(
                [
                    (pl.col("swing_high") - pl.col("close")).abs() / pl.col("close"),
                    (pl.col("close") - pl.col("swing_low")).abs() / pl.col("close"),
                ]
            ).alias("liquidity_distance")
        ]
    )

    return df


def _add_trend_alignment(df: pl.DataFrame) -> pl.DataFrame:
    """Add trend alignment based on SMAs."""
    # --- Trend Alignment (rolling SMAs) ---
    df = df.with_columns(
        [
            pl.col("close").rolling_mean(window_size=20).over("symbol").alias("sma20"),
            pl.col("close").rolling_mean(window_size=50).over("symbol").alias("sma50"),
            pl.col("close")
            .rolling_mean(window_size=200)
            .over("symbol")
            .alias("sma200"),
        ]
    )
    df = df.with_columns(
        [
            (pl.col("sma50") > pl.col("sma200")).cast(pl.Int32).alias("htf_bullish"),
            (pl.col("sma50") < pl.col("sma200")).cast(pl.Int32).alias("htf_bearish"),
            (pl.col("sma20") > pl.col("sma50")).cast(pl.Int32).alias("mtf_bullish"),
            (pl.col("sma20") < pl.col("sma50")).cast(pl.Int32).alias("mtf_bearish"),
        ]
    )
    df = df.with_columns(
        [
            (
                pl.col("htf_bullish")
                + pl.col("mtf_bullish")
                - pl.col("htf_bearish")
                - pl.col("mtf_bearish")
            ).alias("trend_alignment")
        ]
    )

    # Drop intermediate columns
    df = df.drop(
        [
            "htf_bullish",
            "htf_bearish",
            "mtf_bullish",
            "mtf_bearish",
            "sma20",
            "sma50",
            "sma200",
        ]
    )

    return df


def _add_delivery_ma(df: pl.DataFrame) -> pl.DataFrame:
    """Add delivery moving average if delivery columns exist."""
    # --- Delivery MA ---
    if "delivery_qty" in df.columns:
        df = df.with_columns(
            [
                pl.col("delivery_qty")
                .rolling_mean(window_size=60)
                .over("symbol")
                .alias("delivery_ma_60")
            ]
        )
    elif "delivery" in df.columns:
        df = df.with_columns(
            [
                pl.col("delivery")
                .rolling_mean(window_size=60)
                .over("symbol")
                .alias("delivery_ma_60")
            ]
        )
    else:
        df = df.with_columns([pl.lit(None).alias("delivery_ma_60")])

    return df


def calculate_smc_indicators(
    df: Union["pd.DataFrame", pl.DataFrame],
    swing_length: int = 5,
    use_polars: bool = True,
) -> Union["pd.DataFrame", pl.DataFrame]:
    """
    Calculate SMC indicators with Polars for speed.

    Returns DataFrame with added columns:
      bullish_fvg (Int32), bearish_fvg (Int32),
      fvg_top (Float64), fvg_bottom (Float64), fvg_boundary (Float64),
      fvg_freshness (Int64), swing_high (Float64), swing_low (Float64),
      liquidity_distance (Float64), trend_alignment (Int32),
      delivery_ma_60 (Float64 | null), has_bullish_fvg (Int32)
    """
    df_pl = _normalize_input(df, swing_length)
    df_pl = _add_fvg(df_pl)
    df_pl = _add_swing_levels(df_pl, swing_length)
    df_pl = _add_trend_alignment(df_pl)
    df_pl = _add_delivery_ma(df_pl)

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
