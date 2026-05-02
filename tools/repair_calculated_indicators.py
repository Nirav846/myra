"""
Standalone script to recalculate all SMC indicators 
using smc_calculator.py and update technical_data.
Run from project root: python tools/recalc_all_indicators.py
"""

import os
import sys
import sqlite3
import pandas as pd

# Add project root to path so we can import from myra_app.utils
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

# Now import from the correct location
from myra_app.utils.smc_calculator import calculate_smc_indicators

# Database path
DB_PATH = os.path.join(PROJECT_ROOT, "myra_app", "db", "myra_technical.db")


def recalc_and_update():
    print("[MYRA] Recalculating SMC indicators...")

    # 1. Load data from database
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query(
        "SELECT symbol, date, open, high, low, close, volume, delivery FROM technical_data ORDER BY symbol, date",
        conn,
        parse_dates=["date"],
    )
    conn.close()
    print(f"Loaded {len(df)} rows.")

    # Ensure delivery_qty column exists (smc_calculator expects it)
    if "delivery_qty" not in df.columns:
        df["delivery_qty"] = df["delivery"]

    # 2. Run SMC calculations
    df_calc = calculate_smc_indicators(df, swing_length=5)

    # Columns that will be updated
    update_cols = [
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

    # 3. Write back to database
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Create a temporary table for updates
    df_calc[["symbol", "date"] + update_cols].to_sql(
        "temp_updates", conn, if_exists="replace", index=False
    )

    # Update each column
    for col in update_cols:
        if col in df_calc.columns:
            sql = f"""
                UPDATE technical_data 
                SET {col} = (
                    SELECT {col} FROM temp_updates 
                    WHERE temp_updates.symbol = technical_data.symbol 
                    AND temp_updates.date = technical_data.date
                )
                WHERE EXISTS (
                    SELECT 1 FROM temp_updates 
                    WHERE temp_updates.symbol = technical_data.symbol 
                    AND temp_updates.date = technical_data.date
                )
            """
            cursor.execute(sql)  # noqa: PG-NPLUS1
            print(f"Updated {col}")

    conn.commit()

    # Drop temporary table
    cursor.execute("DROP TABLE temp_updates")
    conn.close()

    print("[MYRA] SMC indicator recalculation complete.")


if __name__ == "__main__":
    recalc_and_update()
