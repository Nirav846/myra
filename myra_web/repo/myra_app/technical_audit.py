import sqlite3
import pandas as pd
import os
from myra_app.librarian_core import LibrarianCore


class TechnicalAudit:
    """
    MYRA Data Integrity Audit (v3.2)
    Verifies completeness and consistency across modular Trilogy sidecars.
    """

    def __init__(self, tech_db=None, cal_db=None):
        # Standardized path resolution using LibrarianCore DB_MAP
        db_dir = os.path.join(os.getcwd(), "db")

        self.tech_db = (
            tech_db
            if tech_db
            else os.path.join(db_dir, LibrarianCore.DB_MAP["technical"])
        )
        self.cal_db = (
            cal_db if cal_db else os.path.join(db_dir, LibrarianCore.DB_MAP["calendar"])
        )

    def run_audit(self):
        print("[MYRA] Initializing Data Integrity Audit...")

        if not os.path.exists(self.tech_db):
            print(f"[!] {os.path.basename(self.tech_db)} missing.")
            return
        if not os.path.exists(self.cal_db):
            print(f"[!] {os.path.basename(self.cal_db)} missing.")
            return

        # 1. Connectivity Check
        try:
            conn_t = sqlite3.connect(self.tech_db)
            conn_c = sqlite3.connect(self.cal_db)
            print("[+] Database connectivity: OK")
        except Exception as e:
            print(f"[!] Connectivity failed: {e}")
            return

        # 2. Row Count Stats
        try:
            rows_t = pd.read_sql("SELECT COUNT(*) FROM technical_data", conn_t).iloc[
                0, 0
            ]
            symbols = pd.read_sql(
                "SELECT COUNT(DISTINCT symbol) FROM technical_data", conn_t
            ).iloc[0, 0]
            days_c = pd.read_sql(
                "SELECT COUNT(*) FROM market_calendar WHERE is_trading_day=1", conn_c
            ).iloc[0, 0]

            print(f"[*] Total Records: {rows_t}")
            print(f"[*] Total Symbols: {symbols}")
            print(f"[*] Trading Days:  {days_c}")

            # 3. Data Consistency (No Zero Prices)
            zeros = pd.read_sql(
                "SELECT COUNT(*) FROM technical_data WHERE close = 0 OR close IS NULL",
                conn_t,
            ).iloc[0, 0]
            if zeros > 0:
                print(f"[warning][!] Found {zeros} rows with zero/null close prices.")
            else:
                print("[+] Price consistency: OK (No zeros)")

            # 4. Operational Health Score (Delivery Check)
            # This implements the health score logic discussed for Phase 2
            health_query = """
                SELECT 
                    (COUNT(CASE WHEN delivery > 0 THEN 1 END) * 100.0 / COUNT(*)) as health_score
                FROM technical_data 
                WHERE date = (SELECT MAX(date) FROM technical_data)
            """
            health_score = pd.read_sql(health_query, conn_t).iloc[0, 0]
            print(
                f"[*] System Health Score (Delivery Readiness): {round(health_score, 1)}%"
            )

            # 5. Symbol Sample Deep Dive
            sample_sym = "RELIANCE"
            sample_data = pd.read_sql(
                f"SELECT COUNT(*) FROM technical_data WHERE symbol='{sample_sym}'",
                conn_t,
            ).iloc[0, 0]
            print(
                f"[*] Sample Integrity ({sample_sym}): {sample_data}/{days_c} days "
                f"(approx {round(sample_data/days_c*100, 1)}%)"
            )

        except Exception as e:
            print(f"[!] Audit failed during data analysis: {e}")
        finally:
            conn_t.close()
            conn_c.close()

        print("[+] Audit Complete.")


if __name__ == "__main__":
    TechnicalAudit().run_audit()
