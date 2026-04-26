"""
MYRA Technical Data Ingestion Pipeline
Processes end-of-day NSE Bhavcopy archives for swing and long-term trading analysis.
Filters non-equity ETFs and strictly mandates institutional delivery footprints.
"""

import sys
import os
import argparse
import pandas as pd
import sqlite3
import glob

# Dynamically add the project root to sys.path so it works seamlessly 
# when executed directly from any directory.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from myra_app.utils.bhavcopy_parser import BhavcopyParser
from myra_core.utils.myra_log import myra_log
from myra_app.librarian_core import LibrarianCore

ETF_SYMBOLS = {
    "LIQUIDBEES", "NIFTYBEES", "BANKBEES", "GOLDBEES", "JUNIORBEES",
    "SETFNIF50", "SETFNN50", "MOM100", "CONSUMBEES", "DIVOPPBEES",
    "INFRABBEES", "ITBEES", "PHARMABEES", "PSUBNKBEES", "SHARIABEES",
    "CPSEETF", "BHARAT22ETF", "MON100", "EBBETF0423", "EBBETF0431",
    "ICICIB22", "NV20BEES", "SETF10GILT", "LIQUIDCASE", "LIQUIDIETF",
    "HDFCNIFTY", "ICICIFIXBL", "ABSLNN50ET"
}

def resolve_delivery(df: pd.DataFrame) -> pd.DataFrame:
    """
    Resolves delivery quantity using MYRA canonical column names mapped by SchemaRegistry.
    
    Tier 1: Use absolute delivery qty directly (most accurate).
    Tier 2: Calculate from delivery_pct × volume (derived, flagged).
    Tier 3: Neither available — mark rows so Gatekeeper can reject them.
    """
    if "delivery" not in df.columns:
        df["delivery"] = float("nan")
    
    df["delivery_source"] = "unavailable"

    # --- Tier 1: Absolute qty ---
    if "delivery" in df.columns:
        qty = pd.to_numeric(df["delivery"], errors="coerce")
        valid_qty = qty.notna() & (qty > 1)
        df.loc[valid_qty, "delivery"] = qty
        df.loc[valid_qty, "delivery_source"] = "raw_qty"

    # --- Tier 2: Percentage Fallback ---
    if "delivery_pct" in df.columns and "volume" in df.columns:
        pct = pd.to_numeric(df["delivery_pct"], errors="coerce")
        vol = pd.to_numeric(df["volume"], errors="coerce")
        derived_qty = (pct / 100) * vol

        # Fill only rows still missing delivery (Tier 2 fallback)
        needs_fill = df["delivery_source"] == "unavailable"
        calculated = derived_qty.round()
        valid_calc = needs_fill & pct.notna() & vol.notna() & (calculated > 1)
        
        df.loc[valid_calc, "delivery"] = calculated
        df.loc[valid_calc, "delivery_source"] = "calculated_from_pct"

    return df

def ingest_bhavcopies(csv_folder: str, db_path: str = None) -> None:
    """
    STRICT DELIVERY INGESTION: Rejects any data lacking institutional footprint.
    """
    if not os.path.exists(csv_folder):
        print(f"[!] Error: The data directory '{csv_folder}' does not exist.")
        return

    # Dynamically locate the DB if not provided
    if db_path is None:
        _current_dir = os.path.dirname(os.path.abspath(__file__))
        db_path = os.path.join(_current_dir, "db", LibrarianCore.DB_MAP.get("technical", "myra_technical.db"))

    # Ensure DB directory exists
    os.makedirs(os.path.dirname(db_path), exist_ok=True)

    print(f"[MYRA] Starting STRICT ingestion from: {csv_folder}")
    print(f"[MYRA] Target Database: {db_path}")
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    try:
        cursor.execute("ALTER TABLE technical_data ADD COLUMN delivery_source TEXT")
        conn.commit()
    except sqlite3.OperationalError:
        pass # Column already exists

    csv_files = glob.glob(os.path.join(csv_folder, "nse_full_*.csv"))
    if not csv_files:
        print("[!] No matching 'nse_full_*.csv' files found in the specified directory.")
        return

    stats = {"processed": 0, "inserted": 0, "rejected": 0}

    for i, file_path in enumerate(csv_files, 1):
        myra_log(i, len(csv_files), desc="Ingesting files")
        try:
            # Route through the MYRA v9.0 BhavcopyParser for schema standardization
            df, report = BhavcopyParser.parse_csv(file_path, source_filename=file_path)
            
            if df.empty:
                if report["errors"]:
                    print(f"\n[!] {os.path.basename(file_path)}: {report['errors']}")
                continue

            # Rename canonical lowercase to CamelCase for processing (Rule 39 compliance)
            df = df.rename(columns={
                "open": "Open", "high": "High", "low": "Low", 
                "close": "Close", "volume": "Volume"
            })

            # Block known ETFs that NSE lists under EQ series
            if "symbol" in df.columns:
                df = df[~df["symbol"].isin(ETF_SYMBOLS)]

            # Resolve canonical delivery
            df = resolve_delivery(df)

            # --- GATEKEEPER ---
            initial_count = len(df)

            # Reject rows where delivery could not be resolved at all
            unavailable_mask = df["delivery_source"] == "unavailable"
            if unavailable_mask.any():
                n = unavailable_mask.sum()
                print(f"\n[!] {os.path.basename(file_path)}: {n} rows lack delivery data. Excluded from DB.")

            df = df[~unavailable_mask]

            # Reject rows where delivery is suspiciously small
            df = df.dropna(subset=["delivery"])
            df = df[df["delivery"] > 1.0]

            stats["rejected"] += initial_count - len(df)

            if df.empty:
                continue

            # Date Normalization & Calculations
            df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.date.astype(str)
            df = df.dropna(subset=["date"])
            df["delivery_ratio"] = (df["delivery"] / df["Volume"]).fillna(0)

            # Rename back to lowercase for DB insert
            df = df.rename(columns={
                "Open": "open", "High": "high", "Low": "low", 
                "Close": "close", "Volume": "volume"
            })

            # Prepare records for insertion
            records = df[[
                "symbol", "date", "open", "high", "low", "close", 
                "volume", "delivery", "delivery_ratio", "delivery_source"
            ]].values.tolist()

            # Batched database insertions for performance
            cursor.executemany(
                "INSERT OR REPLACE INTO technical_data (symbol, date, open, high, low, close, volume, delivery, delivery_ratio, delivery_source) VALUES (?,?,?,?,?,?,?,?,?,?)",
                records,
            )
            stats["inserted"] += cursor.rowcount
            conn.commit()

        except Exception as e:
            print(f"\n[!] Critical Error processing {os.path.basename(file_path)}: {e}")

    conn.close()
    print(f"\n[+] Pipeline Complete. Inserted: {stats['inserted']} | Rejected (No Delivery): {stats['rejected']}")

if __name__ == "__main__":
    # Setup CLI Argument Parsing for portability
    parser = argparse.ArgumentParser(description="MYRA End-of-Day Data Ingestion Pipeline")
    
    # Calculate default paths relative to the script's location
    default_csv_dir = os.path.join(_ROOT, "data", "Market_Archives")
    
    parser.add_argument(
        "--csv-dir", 
        type=str, 
        default=default_csv_dir,
        help="Path to the directory containing nse_full_*.csv files"
    )
    
    parser.add_argument(
        "--db-path", 
        type=str, 
        default=None,
        help="Override path for the MYRA technical SQLite database"
    )

    args = parser.parse_args()
    ingest_bhavcopies(csv_folder=args.csv_dir, db_path=args.db_path)