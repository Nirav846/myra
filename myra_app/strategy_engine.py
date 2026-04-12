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
    exprs = []
    for category, category_filters in filters_config.items():
        for key, value in category_filters.items():
            if key.startswith("min_"):
                col_name = key[4:]
            elif key.startswith("max_"):
                col_name = key[4:]
            else:
                col_name = key

            # Try to resolve to the exact column name
            resolved_col = col_name
            if resolved_col not in df.columns:
                # Try prefixing with category_
                if f"{category}_{col_name}" in df.columns:
                    resolved_col = f"{category}_{col_name}"
                else:
                    # Look for substring match
                    matching_cols = [c for c in df.columns if col_name in c]
                    if matching_cols:
                        resolved_col = matching_cols[0]

            if key.startswith("min_"):
                exprs.append(pl.col(resolved_col) >= value)
            elif key.startswith("max_"):
                exprs.append(pl.col(resolved_col) <= value)
            else:
                exprs.append(pl.col(resolved_col) == value)
    return df.filter(exprs)


def run_strategy():
    yaml_path = os.path.join(PROJECT_ROOT, "strategies", "weekly_swing.yaml")
    config = load_config(yaml_path)
    filters_config = config.get("filters", {})

    db_path = os.path.join(PROJECT_ROOT, "db", "myra_technical.db")

    # 1. Connect and get the latest date
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT MAX(date) FROM technical_data")
    latest_date_row = cursor.fetchone()

    if not latest_date_row or not latest_date_row[0]:
        print("No data found in technical_data table.")
        conn.close()
        return

    latest_date = latest_date_row[0]

    # 2. Fetch data for the latest date using Polars
    # Using read_database which is robust.
    # Or simple query with fetchall and create dataframe.
    query = "SELECT * FROM technical_data WHERE date = ?"
    cursor.execute(query, (latest_date,))
    columns = [description[0] for description in cursor.description]
    data = cursor.fetchall()
    conn.close()

    if not data:
        print(f"No data found for the latest date: {latest_date}")
        return

    df = pl.DataFrame(data, schema=columns, orient="row")

    # 3. Apply dynamic filters
    filtered_df = apply_dynamic_filters(df, filters_config)

    # 4. Select required columns
    required_cols = [
        "symbol",
        "close",
        "delivery_divergence_score",
        "volatility_compression_score",
        "relative_volume_score",
        "nifty_outperformance_score",
    ]
    # Check if all required cols exist
    available_cols = [col for col in required_cols if col in filtered_df.columns]

    result_df = filtered_df.select(available_cols)

    # 5. Print a clean Polars table
    print(f"Results for strategy: {config.get('metadata', {}).get('name', 'Unknown')}")
    print(f"Latest Date: {latest_date}")
    print(result_df)


if __name__ == "__main__":
    run_strategy()
