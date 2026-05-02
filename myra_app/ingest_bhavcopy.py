"""
MYRA Technical Data Ingestion Pipeline
Processes end-of-day NSE Bhavcopy archives for swing and long-term trading analysis.
Filters non-equity ETFs and strictly mandates institutional delivery footprints.
"""

import argparse
import glob
import os
import sqlite3
import sys

import pandas as pd

# Dynamically add the project root to sys.path so it works seamlessly
# when executed directly from any directory.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from myra_app.constants import DB_DIR
from myra_app.librarian_core import LibrarianCore
from myra_app.utils.bhavcopy_parser import BhavcopyParser
from myra_app.utils.date_parser import parse_bhavcopy_date
from myra_app.utils.etf_sync import get_etf_symbols
from myra_core.utils.myra_log import myra_log


def validate_calendar_date(canonical_date: str, lib: LibrarianCore) -> tuple[bool, str]:
    """
    Validate date against market calendar for trading day and muhurat session.

    Returns:
        (should_ingest, delivery_source_tag)
        - should_ingest: False if holiday with no special session, True otherwise
        - delivery_source_tag: 'muhurat' if muhurat session, None otherwise
    """
    if not canonical_date:
        return True, None

    try:
        cal_path = os.path.join(DB_DIR, lib.DB_MAP["calendar"])
        if not os.path.exists(cal_path):
            print(
                f"[DEBUG] {canonical_date} not in calendar (file not found), ingesting anyway"
            )
            return True, None

        conn = sqlite3.connect(cal_path, timeout=5)
        cursor = conn.execute(
            "SELECT is_trading_day, session_type FROM market_calendar WHERE date = ?",
            (canonical_date,),
        )
        row = cursor.fetchone()
        conn.close()

        if row:
            is_trading_day, session_type = row
            if is_trading_day == 0 and session_type is None:
                print(f"[WARN] {canonical_date} is a full holiday. Skipping ingestion.")
                return False, None
            elif session_type == "muhurat":
                print(
                    f"[INFO] Muhurat session detected for {canonical_date}, ingesting with tag"
                )
                return True, "muhurat"

        print(f"[DEBUG] {canonical_date} not in calendar, ingesting anyway")
        return True, None

    except Exception as e:
        print(
            f"[DEBUG] Calendar validation failed for {canonical_date}: {e}. Ingesting anyway."
        )
        return True, None


def process_date_rows(
    df: pd.DataFrame,
    etf_symbols: list,
    cursor,
    conn,
    stats: dict,
    delivery_tag: str,
    file_path: str,
) -> bool:
    """
    Process DataFrame rows for a specific date through the delivery validation pipeline.

    Returns:
        True if processing succeeded, False if no rows to insert
    """
    # Rename canonical lowercase to CamelCase for processing (Rule 39 compliance)
    df = df.rename(
        columns={
            "open": "Open",
            "high": "High",
            "low": "Low",
            "close": "Close",
            "volume": "Volume",
        }
    )

    # Block known ETFs that NSE lists under EQ series
    if "symbol" in df.columns:
        df = df[~df["symbol"].isin(etf_symbols)]

    # Resolve canonical delivery
    df = resolve_delivery(df)

    # Apply muhurat tag if specified
    if delivery_tag:
        df["delivery_source"] = delivery_tag

    # --- GATEKEEPER ---
    initial_count = len(df)

    # Reject rows where delivery could not be resolved at all
    unavailable_mask = df["delivery_source"] == "unavailable"
    if unavailable_mask.any():
        n = unavailable_mask.sum()
        print(
            f"\n[!] {os.path.basename(file_path)}: {n} rows lack delivery data. Excluded from DB."
        )

    df = df[~unavailable_mask]

    # Reject rows where delivery is suspiciously small
    df = df.dropna(subset=["delivery"])
    df = df[df["delivery"] > 1.0]

    stats["rejected"] += initial_count - len(df)

    if df.empty:
        return False

    # Date Normalization & Calculations
    df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.date.astype(str)
    df = df.dropna(subset=["date"])
    df["delivery_ratio"] = (df["delivery"] / df["Volume"]).fillna(0)
    df["delivery_pct"] = (df["delivery_ratio"] * 100).round(2)

    # Rename back to lowercase for DB insert
    df = df.rename(
        columns={
            "Open": "open",
            "High": "high",
            "Low": "low",
            "Close": "close",
            "Volume": "volume",
        }
    )

    # Prepare records for insertion
    records = df[
        [
            "symbol",
            "date",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "delivery",
            "delivery_pct",
            "delivery_ratio",
            "delivery_source",
        ]
    ].values.tolist()

    # Batched database insertions for performance
    cursor.executemany(
        "INSERT OR REPLACE INTO technical_data (symbol, date, open, high, low, close, volume, delivery, delivery_pct, delivery_ratio, delivery_source) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        records,
    )
    stats["inserted"] += cursor.rowcount
    conn.commit()

    return True


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
        db_path = os.path.join(
            _current_dir,
            "db",
            LibrarianCore.DB_MAP.get("technical", "myra_technical.db"),
        )

    # Ensure DB directory exists
    os.makedirs(os.path.dirname(db_path), exist_ok=True)

    print(f"[MYRA] Starting STRICT ingestion from: {csv_folder}")
    print(f"[MYRA] Target Database: {db_path}")

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Initialize LibrarianCore for calendar validation
    lib = LibrarianCore(read_only=False)

    try:
        cursor.execute("ALTER TABLE technical_data ADD COLUMN delivery_source TEXT")
        conn.commit()
    except sqlite3.OperationalError:
        pass  # Column already exists

    try:
        cursor.execute("ALTER TABLE technical_data ADD COLUMN delivery_pct REAL")
        conn.commit()
    except sqlite3.OperationalError:
        pass  # Column already exists

    csv_files = glob.glob(os.path.join(csv_folder, "nse_full_*.csv"))
    if not csv_files:
        print(
            "[!] No matching 'nse_full_*.csv' files found in the specified directory."
        )
        return

    stats = {"processed": 0, "inserted": 0, "rejected": 0}

    ETF_SYMBOLS = get_etf_symbols()
    print(f"[MYRA] ETF blocklist loaded: {len(ETF_SYMBOLS)} symbols")

    for i, file_path in enumerate(csv_files, 1):
        myra_log(i, len(csv_files), desc="Ingesting files")
        try:
            # Route through the MYRA v9.0 BhavcopyParser for schema standardization
            df, report = BhavcopyParser.parse_csv(file_path, source_filename=file_path)

            if df.empty:
                if report["errors"]:
                    print(f"\n[!] {os.path.basename(file_path)}: {report['errors']}")
                continue

            # --- PAYLOAD TRUST LOGIC ---
            # Read internal date from date1/TRADEDATE column (whichever exists)
            internal_date_col = None
            for col in ["date1", "TRADEDATE"]:
                if col in df.columns:
                    internal_date_col = col
                    break

            filename_date = BhavcopyParser.extract_date_from_filename(file_path)

            if internal_date_col and not df[internal_date_col].empty:
                # Parse all unique internal dates
                unique_internal_dates = set()
                for raw_date in df[internal_date_col].dropna().unique():
                    parsed = parse_bhavcopy_date(str(raw_date))
                    if parsed:
                        unique_internal_dates.add(parsed)

                if len(unique_internal_dates) == 1:
                    # Single internal date - compare with filename
                    internal_date = unique_internal_dates.pop()
                    if filename_date and internal_date != filename_date:
                        print(
                            f"[WARN] Filename date {filename_date} != internal date {internal_date}. Trusting internal date."
                        )
                    canonical_date = internal_date
                    # Set the canonical date for all rows
                    df["date"] = canonical_date

                elif len(unique_internal_dates) > 1:
                    # Multi-date file - process each date separately
                    print(
                        f"[INFO] Multi-date file: {os.path.basename(file_path)}, dates={sorted(unique_internal_dates)}"
                    )

                    for date_val in unique_internal_dates:
                        date_rows = df.copy()
                        if internal_date_col in date_rows.columns:
                            # Filter rows for this specific date
                            date_mask = date_rows[internal_date_col].notna()
                            parsed = date_rows[internal_date_col].map(
                                lambda x: parse_bhavcopy_date(str(x))
                                if pd.notna(x)
                                else None
                            )
                            date_mask = date_mask & (parsed == date_val)
                            date_rows = date_rows[date_mask]

                        if date_rows.empty:
                            continue

                        date_rows["date"] = date_val

                        # Calendar validation for this date
                        should_ingest, delivery_tag = validate_calendar_date(
                            date_val, lib
                        )
                        if not should_ingest:
                            continue

                        # Process this date's rows through the rest of the pipeline
                        if not process_date_rows(
                            date_rows,
                            ETF_SYMBOLS,
                            cursor,
                            conn,
                            stats,
                            delivery_tag,
                            file_path,
                        ):
                            continue

                    stats["processed"] += 1
                    continue  # Skip the rest of the loop for multi-date files
                else:
                    # No valid internal dates found
                    if filename_date:
                        df["date"] = filename_date
                        canonical_date = filename_date
                    else:
                        print(
                            f"[!] {os.path.basename(file_path)}: No valid dates found in file or filename"
                        )
                        continue
            else:
                # No internal date column, fall back to filename
                if filename_date:
                    df["date"] = filename_date
                    canonical_date = filename_date
                else:
                    print(
                        f"[!] {os.path.basename(file_path)}: No date column found and filename doesn't contain date"
                    )
                    continue

            # Calendar validation for single-date files
            should_ingest, delivery_tag = validate_calendar_date(canonical_date, lib)
            if not should_ingest:
                stats["processed"] += 1
                continue

            # Process single-date file
            if not process_date_rows(
                df, ETF_SYMBOLS, cursor, conn, stats, delivery_tag, file_path
            ):
                continue

            stats["processed"] += 1

        except Exception as e:
            print(f"\n[!] Critical Error processing {os.path.basename(file_path)}: {e}")

    conn.close()
    print(
        f"\n[+] Pipeline Complete. Inserted: {stats['inserted']} | Rejected (No Delivery): {stats['rejected']}"
    )


if __name__ == "__main__":
    # Setup CLI Argument Parsing for portability
    parser = argparse.ArgumentParser(
        description="MYRA End-of-Day Data Ingestion Pipeline"
    )

    # Calculate default paths relative to the script's location
    default_csv_dir = os.path.join(_ROOT, "data", "Market_Archives")

    parser.add_argument(
        "--csv-dir",
        type=str,
        default=default_csv_dir,
        help="Path to the directory containing nse_full_*.csv files",
    )

    parser.add_argument(
        "--db-path",
        type=str,
        default=None,
        help="Override path for the MYRA technical SQLite database",
    )

    args = parser.parse_args()
    ingest_bhavcopies(csv_folder=args.csv_dir, db_path=args.db_path)
