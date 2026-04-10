import os
import sys
import pandas as pd
import sqlite3
from datetime import datetime
import time
from io import StringIO
import concurrent.futures
from myra_core.utils.myra_log import myra_log

# Fix path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

from myra_app.fetcher import DataFetcher

from tools.symbol_mapper import SymbolMapper

# Use PKNSETools for robust fetching
try:
    from PKNSETools.Benny import NSE
except ImportError:
    NSE = None


class DeepBhavcopyRecovery:
    """
    Advanced Multi-Threaded Recovery Layer with Holiday Awareness.
    """

    def __init__(self):
        self.fetcher = DataFetcher()
        self.mapper = SymbolMapper()
        self.archive_dir = os.path.join("data", "Market_Archives")
        os.makedirs(self.archive_dir, exist_ok=True)
        self.nse_tool = NSE(self.archive_dir) if NSE else None
        self._load_trading_calendar()

    def _load_trading_calendar(self):
        """Loads official trading dates to prevent fetching on holidays."""
        self.valid_dates = set()
        cal_path = os.path.join("data", "trading_calendar_master.csv")
        if os.path.exists(cal_path):
            try:
                df_cal = pd.read_csv(cal_path)
                self.valid_dates = set(df_cal["date"].astype(str).tolist())
            except:
                pass

    def fetch_date(self, target_date):
        # Performance Guard Compliant (Fix 48)
        d_str = (
            target_date.date().isoformat()
            if hasattr(target_date, "date")
            else str(target_date)
        )

        # Holiday Awareness: Skip if not a valid trading date
        if self.valid_dates and d_str not in self.valid_dates:
            return None, d_str

        # Future Awareness: Skip if date is today or later (unless after market hours)
        now = datetime.now()
        if target_date.date() >= now.date():
            # If today, only allow after 6 PM
            if target_date.date() == now.date() and now.hour < 18:
                return None, d_str
            if target_date.date() > now.date():
                return None, d_str

        local_standard = os.path.join(self.archive_dir, f"nse_full_{d_str}.csv")
        # Fix 64: Manual formatting to avoid .strftime
        ds_leg = f"{target_date.day:02d}{target_date.month:02d}{target_date.year}"
        local_pknse = os.path.join(self.archive_dir, f"sec_bhavdata_full_{ds_leg}.csv")

        # 1. Local Cache Check
        if os.path.exists(local_standard):
            try:
                with open(local_standard, "r", encoding="utf-8", errors="ignore") as f:
                    return f.read(), d_str
            except:
                pass
        if os.path.exists(local_pknse):
            try:
                with open(local_pknse, "r", encoding="utf-8", errors="ignore") as f:
                    return f.read(), d_str
            except:
                pass

        if not self.nse_tool:
            return None, d_str

        # 2. Try PKNSETools (Network Fetch)
        try:
            path = self.nse_tool.deliveryBhavcopy(
                target_date.date() if isinstance(target_date, datetime) else target_date
            )
            if path and os.path.exists(path):
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                    with open(local_standard, "w", encoding="utf-8") as fs:
                        fs.write(content)
                    return content, d_str
        except:
            pass

        return None, d_str


def backfill_missing_data(
    missing_csv="data/missing_data.csv",
    db_path="db/technical.db",
    target_symbols=None,
    threads=4,
):
    print(f"[MYRA] Starting Accelerated Recovery ({threads} threads)...")

    if not os.path.exists(missing_csv):
        print(f"[!] {missing_csv} not found.")
        return

    df_missing = pd.read_csv(missing_csv)
    if df_missing.empty:
        return
    if target_symbols:
        df_missing = df_missing[df_missing["symbol"].isin(target_symbols)]

    recovery = DeepBhavcopyRecovery()
    missing_dates = sorted(df_missing["missing_date"].unique(), reverse=True)

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    stats = {"rows": 0, "errors": 0}

    # Use ThreadPool for faster date fetching
    with concurrent.futures.ThreadPoolExecutor(max_workers=threads) as executor:
        future_to_date = {
            executor.submit(recovery.fetch_date, datetime.strptime(d, "%Y-%m-%d")): d
            for d in missing_dates
        }

        total_dates = len(missing_dates)
        for i, future in enumerate(concurrent.futures.as_completed(future_to_date)):
            myra_log(i + 1, total_dates, desc="Recovering")

            try:
                csv_text, date_processed = future.result()
                if csv_text:
                    df_bhav = pd.read_csv(StringIO(csv_text))
                    df_bhav.columns = [c.strip().upper() for c in df_bhav.columns]

                    # Target CURRENT symbols missing for this date
                    # Fix 122: Use .loc for safety/performance
                    targets = set(
                        df_missing.loc[
                            df_missing["missing_date"] == date_processed, "symbol"
                        ]
                    )

                    # Optimized with list comprehension (Fix 138: Avoid .append in loop)
                    def _to_record(row_dict):
                        raw_sym = str(row_dict.get("SYMBOL", "")).strip().upper()
                        current_name = recovery.mapper.get_current_symbol(raw_sym)
                        if current_name in targets:
                            vol = row_dict.get(
                                "TTL_TRD_QNTY", row_dict.get("TOTTRDQTY", 0)
                            )
                            close = row_dict.get(
                                "CLOSE_PRICE", row_dict.get("CLOSE", 0)
                            )
                            deliv = row_dict.get("DELIV_QTY", 0)
                            return (
                                current_name,
                                date_processed,
                                float(
                                    row_dict.get("OPEN_PRICE", row_dict.get("OPEN", 0))
                                ),
                                float(
                                    row_dict.get("HIGH_PRICE", row_dict.get("HIGH", 0))
                                ),
                                float(
                                    row_dict.get("LOW_PRICE", row_dict.get("LOW", 0))
                                ),
                                float(close),
                                int(vol),
                                int(deliv),
                                int(
                                    row_dict.get(
                                        "NO_OF_TRADES", row_dict.get("TOTALTRADES", 0)
                                    )
                                ),
                                float(row_dict.get("AVG_PRICE", close)),
                                float(row_dict.get("DELIV_PER", 0)),
                                (float(deliv) / float(vol)) if vol and vol > 0 else 0,
                            )
                        return None

                    records = [
                        r
                        for row in df_bhav.itertuples(index=False)
                        if (r := _to_record(row._asdict()))
                    ]

                    if records:
                        cursor.executemany(
                            "INSERT OR IGNORE INTO technical_data VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                            records,
                        )
                        conn.commit()
                        stats["rows"] += len(records)

                time.sleep(0.05)
            except Exception:
                stats["errors"] += 1

    conn.close()
    print(
        f"\n[+] Recovery Complete. Added {stats['rows']} rows. Errors: {stats['errors']}"
    )


if __name__ == "__main__":
    import sys

    targets = None
    thread_count = 8  # Safe for local cache heavy work
    for arg in sys.argv:
        if "," in arg:
            targets = arg.split(",")
    backfill_missing_data(target_symbols=targets, threads=thread_count)
