import glob
import os
import shutil
import sqlite3
import time
from datetime import datetime

import pandas as pd
from rich.console import Console


class Gatekeeper:
    TEMP_DIR = "temp/"
    ARCHIVE_DIR = "db/archives/"
    LOCK_FILE = ".gatekeeper_last_run"
    DRY_RUN_LOG = "logs/gatekeeper_dry_run.csv"

    @staticmethod
    def smart_gatekeeper(db_map, console=None):
        if console is None:
            console = Console()

        # 1. Loop Prevention (24-hour check)
        if os.path.exists(Gatekeeper.LOCK_FILE):
            try:
                with open(Gatekeeper.LOCK_FILE, "r") as f:
                    last_run_str = f.read().strip()
                    last_run = datetime.fromisoformat(last_run_str)
                    if (datetime.now() - last_run).total_seconds() < 86400:
                        return  # Exit silently
            except Exception:
                pass

        # 2. Date-Agnostic Detection
        files = glob.glob(os.path.join(Gatekeeper.TEMP_DIR, "MW-ETF-*.csv"))
        if not files:
            return

        latest_file = max(files, key=os.path.getmtime)
        console.print(
            f"[bold cyan][Gatekeeper] Found new ETF list: {os.path.basename(latest_file)}[/]"
        )

        try:
            # 3. Load ETF List
            df_etf = pd.read_csv(latest_file)
            # Clean headers (NSE CSVs often have newlines/spaces)
            df_etf.columns = [
                c.strip().split("\n")[0].strip().upper() for c in df_etf.columns
            ]

            if "SYMBOL" not in df_etf.columns:
                console.print(
                    "[red][!] Gatekeeper: SYMBOL column not found in CSV. Skipping...[/]"
                )
                return

            etf_list = df_etf["SYMBOL"].unique().tolist()
            etf_list = [str(s).strip().upper() for s in etf_list if s]

            # 4. Dry Run Log (First run or if requested)
            os.makedirs("logs", exist_ok=True)
            pd.DataFrame({"symbol": etf_list}).to_csv(
                Gatekeeper.DRY_RUN_LOG, index=False
            )

            # 5. Execution (SQLite for RAM efficiency)
            tech_db = os.path.join("db", db_map["technical"])
            meta_db = os.path.join("db", db_map["meta"])
            gov_db = os.path.join("db", db_map["governance"])

            if not etf_list:
                return

            placeholders = ", ".join(["?"] * len(etf_list))

            if os.path.exists(tech_db):
                con_t = sqlite3.connect(tech_db, check_same_thread=False)
                # Check how many rows will be deleted
                count_res = con_t.execute(
                    f"SELECT COUNT(*) FROM technical_data WHERE symbol IN ({placeholders})",
                    etf_list,
                ).fetchone()
                row_count = count_res[0] if count_res else 0

                if row_count > 0:
                    console.print(
                        f"[yellow][Gatekeeper] Purging {row_count} ETF rows from {db_map['technical']}...[/]"
                    )
                    con_t.execute(
                        f"DELETE FROM technical_data WHERE symbol IN ({placeholders})",
                        etf_list,
                    )
                    con_t.commit()
                    con_t.execute("VACUUM")
                con_t.close()

            if os.path.exists(gov_db):
                try:
                    con_g = sqlite3.connect(gov_db, check_same_thread=False)
                    con_g.execute(
                        f"DELETE FROM ias_history WHERE symbol IN ({placeholders})",
                        etf_list,
                    )
                    con_g.commit()
                    con_g.execute("VACUUM")
                    con_g.close()
                except Exception:
                    pass

            if os.path.exists(meta_db):
                con_m = sqlite3.connect(meta_db, check_same_thread=False)
                # Mark as ETF and inactive
                con_m.execute(
                    f"UPDATE symbols_master SET instrument_type = 'ETF', is_active = 0, in_active_universe = 0 WHERE symbol IN ({placeholders})",
                    etf_list,
                )
                con_m.commit()
                con_m.close()

            # 6. Archive and Lock
            if not os.path.exists(Gatekeeper.ARCHIVE_DIR):
                os.makedirs(Gatekeeper.ARCHIVE_DIR)

            shutil.move(
                latest_file,
                os.path.join(Gatekeeper.ARCHIVE_DIR, os.path.basename(latest_file)),
            )

            with open(Gatekeeper.LOCK_FILE, "w") as f:
                f.write(datetime.now().isoformat())

            console.print(
                f"[bold green][✓] Gatekeeper: Sanitization complete. Processed {len(etf_list)} symbols.[/]"
            )

        except Exception as e:
            console.print(f"[bold red][!] Gatekeeper Error: {e}[/]")


if __name__ == "__main__":
    from myra_app.librarian_core import LibrarianCore

    Gatekeeper.smart_gatekeeper(LibrarianCore.DB_MAP)
