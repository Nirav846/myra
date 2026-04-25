import os
import pandas as pd
import sqlite3
import glob
from datetime import datetime
from myra_core.utils.myra_log import myra_log
from myra_app.librarian_core import LibrarianCore

def resolve_delivery(df: pd.DataFrame) -> pd.DataFrame:
    """
    Resolves delivery quantity using a strict priority hierarchy.

    Tier 1: Use absolute delivery qty directly (most accurate).
    Tier 2: Calculate from delivery percentage × volume (derived, flagged).
    Tier 3: Neither available — mark rows so caller can reject them.

    Adds two columns to df:
      - delivery         : resolved absolute delivery quantity (float, may be NaN)
      - delivery_source  : "raw_qty" | "calculated_from_pct" | "unavailable"
    """
    # Normalise column names to lowercase for matching
    col_lower = {c.lower(): c for c in df.columns}

    # --- Tier 1: Absolute qty ---
    qty_col = next(
        (col_lower[k] for k in ["deliv_qty", "deliverable_qty"] if k in col_lower),
        None
    )

    # --- Tier 2: Percentage ---
    pct_col = next(
        (col_lower[k] for k in ["deliv_per", "delivery_pct"] if k in col_lower),
        None
    )

    # Initialise
    df["delivery"] = float("nan")
    df["delivery_source"] = "unavailable"

    if qty_col:
        qty = pd.to_numeric(df[qty_col], errors="coerce")
        df.loc[qty.notna() & (qty > 1), "delivery"] = qty
        df.loc[qty.notna() & (qty > 1), "delivery_source"] = "raw_qty"

    if pct_col and "volume" in df.columns:
        pct = pd.to_numeric(df[pct_col], errors="coerce")
        vol = pd.to_numeric(df["volume"], errors="coerce")

        derived_qty = (pct / 100) * vol

        # Cross-validate where both exist
        if qty_col:
            ratio = (derived_qty - qty).abs() / (qty.replace(0, float("nan")))
            suspicious = ratio > 0.05
            if suspicious.any():
                n = suspicious.sum()
                print(f"[!] WARNING: {n} rows have >5% mismatch between DELIV_QTY and DELIV_PER — possible NSE data corruption.")

        # Fill only rows still missing delivery (Tier 2 fallback)
        needs_fill = df["delivery_source"] == "unavailable"
        calculated = derived_qty.round()
        valid_calc = needs_fill & pct.notna() & vol.notna() & (calculated > 1)
        df.loc[valid_calc, "delivery"] = calculated
        df.loc[valid_calc, "delivery_source"] = "calculated_from_pct"

    return df


def ingest_bhavcopies(csv_folder, db_path=None):
    """
    STRICT DELIVERY INGESTION: Rejects any data lacking institutional footprint.
    """
    if db_path is None:
        _myra_app_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "myra_app")
        db_path = os.path.join(_myra_app_dir, "db", LibrarianCore.DB_MAP["technical"])

    if not os.path.exists(db_path):
        print(f"[!] Database {db_path} not found.")
        return

    print(f"[MYRA] Starting STRICT ingestion from {csv_folder}...")
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    try:
        cursor.execute("ALTER TABLE technical_data ADD COLUMN delivery_source TEXT")
        conn.commit()
    except sqlite3.OperationalError:
        # Column already exists
        pass

    csv_files = glob.glob(os.path.join(csv_folder, "nse_full_*.csv"))
    stats = {"processed": 0, "inserted": 0, "rejected": 0}

    for i, file_path in enumerate(csv_files, 1):
        myra_log(i, len(csv_files), desc="Ingesting files")
        try:
            df = pd.read_csv(file_path)
            df.columns = [c.strip().upper() for c in df.columns]

            # Filter for Equity Series only
            if "SERIES" in df.columns:
                df = df[df["SERIES"].str.strip().isin(["EQ", "BE", "SM"])]

            # Mapping
            mapping = {
                "SYMBOL": "symbol",
                "DATE1": "date",
                "TIMESTAMP": "date",
                "OPEN_PRICE": "open",
                "OPEN": "open",
                "HIGH_PRICE": "high",
                "HIGH": "high",
                "LOW_PRICE": "low",
                "LOW": "low",
                "CLOSE_PRICE": "close",
                "CLOSE": "close",
                "TTL_TRD_QNTY": "volume",
                "TOTTRDQTY": "volume",
                "NO_OF_TRADES": "trades",
            }
            df = df.rename(
                columns={k: v for k, v in mapping.items() if k in df.columns}
            )

            # 1. Cast numeric fields
            for col in ["open", "high", "low", "close", "volume"]:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors="coerce")

            # Resolve delivery
            df = resolve_delivery(df)

            # --- GATEKEEPER ---
            initial_count = len(df)

            # Reject rows where delivery could not be resolved at all
            unavailable_mask = df["delivery_source"] == "unavailable"
            if unavailable_mask.any():
                n = unavailable_mask.sum()
                print(f"[!] {os.path.basename(file_path)}: {n} rows have no delivery data from any source — excluded from DB. Consider fetching from an alternate bhavcopy source for this date.")

            df = df[~unavailable_mask]

            # Reject rows where delivery is still NaN or suspiciously small
            df = df.dropna(subset=["delivery"])
            df = df[df["delivery"] > 1.0]

            stats["rejected"] += initial_count - len(df)

            if df.empty:
                continue

            # Date Normalization
            df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.date.astype(str)
            df = df.dropna(subset=["date"])

            # Calculate Ratio
            df["delivery_ratio"] = (df["delivery"] / df["volume"]).fillna(0)

            # Insert
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
                    "delivery_ratio",
                    "delivery_source",
                ]
            ].values.tolist()
            cursor.executemany(
                "INSERT OR REPLACE INTO technical_data (symbol, date, open, high, low, close, volume, delivery, delivery_ratio, delivery_source) VALUES (?,?,?,?,?,?,?,?,?,?)",
                records,
            )
            stats["inserted"] += cursor.rowcount
            conn.commit()

        except Exception as e:
            print(f"[!] Error: {e}")

    conn.close()
    print(
        f"\n[+] Done. Inserted: {stats['inserted']}, Rejected (No Delivery): {stats['rejected']}"
    )
