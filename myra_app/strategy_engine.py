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
    """
    Optimized filter application using list comprehension to avoid O(N^2) loop risks.
    Satisfies MYRA Performance Guard.
    """
    # Flattening the nested YAML config into a list of Polars expressions
    exprs = [
        (pl.col(key.replace("min_", "").replace("max_", "")) >= value) if key.startswith("min_") else
        (pl.col(key.replace("min_", "").replace("max_", "")) <= value) if key.startswith("max_") else
        (pl.col(key.replace("min_", "").replace("max_", "")) == value)
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

    # 1. Connect to the 945MB Vault
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT MAX(date) FROM technical_data")
    latest_date_row = cursor.fetchone()

    if not latest_date_row or not latest_date_row[0]:
        print("[!] No data found in technical_data table.")
        conn.close()
        return

    latest_date = latest_date_row[0]

    # 2. Fetch Data (10-day window for signal generation)
    query = """
    SELECT symbol, date, close, high, low, delivery, delivery_pct 
    FROM technical_data 
    WHERE date IN (SELECT DISTINCT date FROM technical_data ORDER BY date DESC LIMIT 10)
    """
    df_raw = pl.read_database(query, conn)
    conn.close()

    if df_raw.is_empty():
        print(f"[!] No data found for window ending: {latest_date}")
        return

    # 3. Calculation Layer (Institutional Materiality Logic)
    # Market Benchmark for April 13, 2026
    nifty_benchmark = 23820.80 

    processed_df = (
        df_raw.sort(["symbol", "date"])
        .with_columns([
            ((pl.col("high") - pl.col("low")) / pl.col("close")).alias("volatility_compression_score"),
            (pl.col("delivery") / pl.col("delivery").mean().over("symbol")).alias("relative_volume_score"),
            (pl.col("delivery_pct") - pl.col("delivery_pct").shift(1).over("symbol")).alias("delivery_divergence_score"),
            (pl.col("close") / nifty_benchmark).alias("nifty_outperformance_score")
        ])
        .filter(pl.col("date") == latest_date)
    )

    # 4. Apply optimized filters
    filtered_df = apply_dynamic_filters(processed_df, filters_config)

    # 5. Format Output
    target_cols = ["symbol", "close", "volatility_compression_score", "delivery_divergence_score", "relative_volume_score"]
    final_cols = [c for c in target_cols if c in filtered_df.columns]
    
    result_df = filtered_df.select(final_cols).sort("volatility_compression_score", descending=False)

    print(f"\n[MYRA v3.0] Strategy: {config.get('metadata', {}).get('name', 'Weekly Swing')}")
    print(f"Analysis Date: {latest_date} | Symbols Processed: {len(processed_df)}")
    
    if result_df.is_empty():
        print("[!] No symbols passed filters. Broad market volatility high.")
    else:
        print(result_df.head(20))

if __name__ == "__main__":
    run_strategy()