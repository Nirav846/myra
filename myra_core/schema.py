"""
MYRA Schema Definition v1.0
Central source of truth for all DataFrame column names.
Use these constants everywhere instead of hardcoded strings.
"""

from typing import List

# ═══════════════════════════════════════════════════════════════
# CORE OHLCV (CamelCase as used in DataFrames, lowercase in DB)
# ═══════════════════════════════════════════════════════════════
CORE_COLS = ["Open", "High", "Low", "Close", "Volume"]
CORE_COLS_DB = ["open", "high", "low", "close", "volume"]

# ═══════════════════════════════════════════════════════════════
# DELIVERY & VOLUME METRICS
# ═══════════════════════════════════════════════════════════════
DELIVERY_COLS = [
    "delivery",
    "delivery_pct",
    "delivery_ratio",
    "delivery_qty",
    "delivery_source",
]
DELIVERY_COLS_CAMEL = ["DeliveryPct", "delivery_percent"]

# ═══════════════════════════════════════════════════════════════
# ENRICHMENT / INDICATORS (computed by feature_enrichment.py)
# ═══════════════════════════════════════════════════════════════
ENRICHMENT_COLS = [
    "stock_return",
    "market_return",
    "delivery_divergence_score",
    "volatility_compression_score",
    "relative_volume_score",
    "nifty_outperformance_score",
]

# ═══════════════════════════════════════════════════════════════
# SMC / FUSION (computed by smc_calculator.py)
# ═══════════════════════════════════════════════════════════════
SMC_COLS = [
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

# ═══════════════════════════════════════════════════════════════
# MOVING AVERAGES
# ═══════════════════════════════════════════════════════════════
MA_COLS = ["sma20", "sma50", "sma150", "sma200"]

# ═══════════════════════════════════════════════════════════════
# COMPLETE TECHNICAL DATA SCHEMA (all columns in technical_data)
# ═══════════════════════════════════════════════════════════════
TECHNICAL_DATA_ALL = (
    CORE_COLS_DB
    + DELIVERY_COLS
    + ENRICHMENT_COLS
    + SMC_COLS
    + MA_COLS
    + [
        "trades",
        "vwap",
        "atr20",
        "atr5",
        "atr14",
        "atr_pct",
        "std20",
        "drawdown",
        "money_flow_cr",
    ]
)


def validate_columns(df, required: List[str], context: str = "DataFrame") -> None:
    """Raise ValueError if any required column is missing (case-insensitive)."""
    available = {c.lower() for c in df.columns}
    missing = [c for c in required if c.lower() not in available]
    if missing:
        raise ValueError(f"[{context}] Missing columns: {missing}")
