import sqlite3
import sys
import argparse
import pandas as pd
import io
import os
import json
from datetime import datetime, timezone, timedelta

# IMPORT THE HARDENED FETCHER
from myra_app.fetcher import DataFetcher
from myra_app.librarian_core import LibrarianCore

# Path to your 945MB Atomic Vault
DB_PATH = os.path.join("db", LibrarianCore.DB_MAP["technical"])


def run_daily_update():
    """
    Guard-Compliant Daily Fetcher connected to the v3.2 Ghost Engine.
    """
    print("[MYRA] Initiating daily data ingestion via v3.2 Ghost Engine...")

    parser = argparse.ArgumentParser(description="MYRA Daily Ingestor")
    parser.add_argument("--date", type=str, help="Date in DD-MM-YYYY format to force a specific ingestion date")
    args, _ = parser.parse_known_args()

    # Force IST Time (Performance Guard compliant)
    ist_now = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
    if args.date:
        current_date = datetime.strptime(args.date, "%d-%m-%Y")
    else:
        current_date = ist_now

    # Pre-flight check for market holidays using myra_calendar.db
    calendar_db_path = os.path.join("db", LibrarianCore.DB_MAP["calendar"])
    if os.path.exists(calendar_db_path):
        try:
            with sqlite3.connect(calendar_db_path) as cal_conn:
                date_str = current_date.date().isoformat()
                cal_res = cal_conn.execute(
                    "SELECT is_trading_day, holiday_name FROM market_calendar WHERE date = ?",
                    (date_str,)
                ).fetchone()

                is_trading = True
                holiday_reason = ""
                if cal_res:
                    is_trading = bool(cal_res[0])
                    holiday_reason = cal_res[1]
                elif current_date.weekday() >= 5:
                    is_trading = False
                    holiday_reason = "Weekend"

                if not is_trading:
                    print(f"[INFO] Market is closed on {date_str} ({holiday_reason or 'Holiday'}). Skipping fetch.")
                    sys.exit(0)
        except Exception as e:
            print(f"⚠️ Warning: Could not query calendar DB: {e}")

    # Instantiate the hardened fetcher
    fetcher = DataFetcher()

    print(
        f"[MYRA] Requesting data for {current_date.day:02d}-{current_date.month:02d}-{current_date.year}..."
    )

    # Route the request through the stealth session
    data_csv, source = fetcher.fetch_ohlcv_delivery(current_date)

    # Handle the Fetcher's responses
    if data_csv == "too_early":
        print(
            "⚠️ Data not yet released. (IST Shield active: It is strictly before 6 PM IST)."
        )
        return
    elif data_csv == "holiday_skip":
        print("🛑 Market Holiday or Weekend. Skipping fetch.")
        return
    elif not data_csv:
        print("❌ Fetch failed. NSE WAF block or 404 Not Found.")
        return

    # If we got data, ingest it into the Atomic Vault
    try:
        df = pd.read_csv(io.StringIO(data_csv))
        df.columns = [c.strip().lower() for c in df.columns]

        # 1. Filter for standard Equities only (ignore bonds/options)
        if "series" in df.columns:
            df = df[df["series"].isin(["EQ", "BE", "SM"])]

        # 2. Map fetcher output to standard OHLCV
        rename_map = {
            "open_price": "open",
            "high_price": "high",
            "low_price": "low",
            "close_price": "close",
            "ttl_trd_qnty": "volume",
            "deliv_qty": "delivery",
            "deliv_per": "delivery_pct",
        }
        df = df.rename(columns=rename_map)

        # 3. Use ISO format to avoid .strftime() banned method
        df["date"] = current_date.date().isoformat()

        # 4. Dynamic Schema Enforcement (Bulletproof insertion)
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        conn = sqlite3.connect(DB_PATH)

        # Ask SQLite what columns actually exist in the table
        cursor = conn.execute("PRAGMA table_info(technical_data)")
        valid_cols = [info[1] for info in cursor.fetchall()]

        # Only keep the columns that the database recognizes
        df_to_insert = df[[c for c in df.columns if c in valid_cols]]

        # Append to DB using INSERT OR REPLACE for robust ingestion
        cols = df_to_insert.columns.tolist()
        placeholders = ', '.join(['?'] * len(cols))
        col_names = ', '.join(cols)
        sql = f"INSERT OR REPLACE INTO technical_data ({col_names}) VALUES ({placeholders})"

        conn.executemany(sql, df_to_insert.values.tolist())
        conn.commit()
        conn.close()

        print(
            f"✅ Successfully added {len(df_to_insert)} rows to Atomic Vault from {source}."
        )

        # Generate Data Confidence Sync Manifest
        try:
            missing_delivery_mask = df_to_insert["delivery"].isna() | (
                df_to_insert["delivery"] == 0
            )
            missing_symbols = (
                df_to_insert.loc[missing_delivery_mask, "symbol"].tolist()
                if "symbol" in df_to_insert.columns
                else []
            )

            manifest_payload = {
                "last_sync_date": current_date.date().isoformat(),
                "total_symbols_processed": len(df_to_insert),
                "missing_delivery_list": missing_symbols,
            }

            with open("data_sync_manifest.json", "w") as f:
                json.dump(manifest_payload, f, indent=4)

            # Automatic Metadata Hook
            lib = LibrarianCore(read_only=False)
            lib.set_metadata("cache_meta", json.dumps(manifest_payload))
            print("✅ Successfully updated cache_meta metadata.")
        except Exception as e:
            print(f"⚠️ Warning: Could not save data_sync_manifest.json or update metadata. Error: {e}")

    except Exception as e:
        print(f"❌ Critical Database Error: {e}")


if __name__ == "__main__":
    run_daily_update()
