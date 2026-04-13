import sqlite3
import yaml
import polars as pl
import os
import sys

# Ensure PROJECT_ROOT is in path
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, PROJECT_ROOT)


def load_config(yaml_path):
    with open(yaml_path, "r") as file:
        return yaml.safe_load(file)


def apply_dynamic_filters(df, filters_config):
    """Optimized filter application using list comprehension."""
    exprs = [
        (
            (pl.col(key.replace("min_", "").replace("max_", "")) >= value)
            if key.startswith("min_")
            else (
                (pl.col(key.replace("min_", "").replace("max_", "")) <= value)
                if key.startswith("max_")
                else (pl.col(key.replace("min_", "").replace("max_", "")) == value)
            )
        )
        for category in filters_config.values()
        for key, value in category.items()
        if key.replace("min_", "").replace("max_", "") in df.columns
    ]
    return df.filter(exprs) if exprs else df


def run_strategy():
    yaml_path = os.path.join(PROJECT_ROOT, "strategies", "weekly_swing.yaml")
    config = load_config(yaml_path)
    filters_config = config.get("filters", {})
    db_path = os.path.join(PROJECT_ROOT, "db", "myra_technical.db")

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT MAX(date) FROM technical_data")
    latest_date_row = cursor.fetchone()

    if not latest_date_row or not latest_date_row[0]:
        print("[!] No data found in technical_data table.")
        conn.close()
        return

    latest_date = latest_date_row[0]

    # --- SQL CALCULATION LAYER (SMC & DWAP OFF-LOADING) ---
    query = """
    SELECT symbol, date, close, high, low, volume, delivery, delivery_pct,
           AVG(delivery) OVER w_10 AS avg_10d_delivery,
           delivery_pct - LAG(delivery_pct) OVER w_order AS sql_delivery_divergence,
           
           -- 10-Day Anchored DWAP (Delivery Weighted Average Price)
           SUM(((high + low + close) / 3.0) * delivery) OVER w_10 / SUM(delivery) OVER w_10 AS dwap_10d,
           
           -- Fair Value Gaps (3-Candle Imbalance)
           low - LAG(high, 2) OVER w_order AS fvg_bullish_gap,
           LAG(low, 2) OVER w_order - high AS fvg_bearish_gap

    FROM technical_data 
    WHERE date IN (SELECT DISTINCT date FROM technical_data ORDER BY date DESC LIMIT 10)
      
      -- UNIVERSE FILTER: Wipe out Penny Stocks, Illiquidity, and ETFs
      AND close >= 50
      AND volume >= 100000
      AND symbol NOT LIKE '%ETF%'
      AND symbol NOT LIKE '%BEES%'
      AND symbol NOT LIKE '%NIFTY%'
      
    WINDOW w_10 AS (PARTITION BY symbol ORDER BY date ROWS BETWEEN 9 PRECEDING AND CURRENT ROW),
           w_order AS (PARTITION BY symbol ORDER BY date)
    """
    df_raw = pl.read_database(query, conn)
    conn.close()

    if df_raw.is_empty():
        print(f"[!] No data found for window ending: {latest_date}")
        return

    # --- POLARS LOGIC LAYER ---
    processed_df = (
        df_raw.sort(["symbol", "date"])
        .with_columns(
            [
                # Volatility & Volume
                ((pl.col("high") - pl.col("low")) / pl.col("close")).alias(
                    "volatility_compression_score"
                ),
                (pl.col("delivery") / pl.col("avg_10d_delivery")).alias(
                    "relative_volume_score"
                ),
                pl.col("sql_delivery_divergence").alias("delivery_divergence_score"),
                # SMC Triggers
                (pl.col("fvg_bullish_gap") > 0).alias("has_bullish_fvg"),
                (pl.col("fvg_bearish_gap") > 0).alias("has_bearish_fvg"),
                # Distance from DWAP (Positive = Above DWAP)
                ((pl.col("close") - pl.col("dwap_10d")) / pl.col("dwap_10d")).alias(
                    "distance_from_dwap"
                ),
            ]
        )
        .filter(pl.col("date") == latest_date)
    )

    filtered_df = apply_dynamic_filters(processed_df, filters_config)

    target_cols = [
        "symbol",
        "close",
        "volatility_compression_score",
        "distance_from_dwap",
        "has_bullish_fvg",
        "relative_volume_score",
    ]
    final_cols = [c for c in target_cols if c in filtered_df.columns]

    result_df = filtered_df.select(final_cols).sort(
        "volatility_compression_score", descending=False
    )

    print(
        f"\n[MYRA v3.2 SMC] Strategy: {config.get('metadata', {}).get('name', 'Weekly Swing')}"
    )
    print(
        f"Analysis Date: {latest_date} | High-Conviction Universe: {len(processed_df)}"
    )

    if result_df.is_empty():
        print("[!] No symbols passed filters. Broad market volatility high.")
    else:
        print(result_df.head(20))


if __name__ == "__main__":
    run_strategy()
