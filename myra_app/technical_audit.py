import sqlite3
import pandas as pd
import os

class TechnicalAudit:
    """
    PKScreener Superpower: Data Integrity Audit.
    Verifies completeness and consistency across modular databases.
    """
    def __init__(self, tech_db=None, cal_db=None):
        self.tech_db = tech_db if tech_db else os.path.join(os.getcwd(), "db", "technical.db")
        self.cal_db = cal_db if cal_db else os.path.join(os.getcwd(), "db", "calendar.db")

    def run_audit(self):
        print("[MYRA] Initializing Data Integrity Audit...")
        
        if not os.path.exists(self.tech_db):
            print("[!] technical.db missing.")
            return
        if not os.path.exists(self.cal_db):
            print("[!] calendar.db missing.")
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
        rows_t = pd.read_sql("SELECT COUNT(*) FROM technical_data", conn_t).iloc[0,0]
        symbols = pd.read_sql("SELECT COUNT(DISTINCT symbol) FROM technical_data", conn_t).iloc[0,0]
        days_c = pd.read_sql("SELECT COUNT(*) FROM market_calendar WHERE is_trading_day=1", conn_c).iloc[0,0]
        
        print(f"[*] Total Records: {rows_t}")
        print(f"[*] Total Symbols: {symbols}")
        print(f"[*] Trading Days:  {days_c}")

        # 3. Data Consistency (No Zero Prices)
        zeros = pd.read_sql("SELECT COUNT(*) FROM technical_data WHERE close = 0 OR close IS NULL", conn_t).iloc[0,0]
        if zeros > 0:
            print(f"[warning][!] Found {zeros} rows with zero/null close prices.")
        else:
            print("[+] Price consistency: OK (No zeros)")

        # 4. Symbol Sample Deep Dive
        sample_sym = "RELIANCE"
        sample_data = pd.read_sql(f"SELECT COUNT(*) FROM technical_data WHERE symbol='{sample_sym}'", conn_t).iloc[0,0]
        print(f"[*] Sample Integrity ({sample_sym}): {sample_data}/{days_c} days (approx {round(sample_data/days_c*100, 1)}%)")

        conn_t.close()
        conn_c.close()
        print("[+] Audit Complete.")

if __name__ == "__main__":
    TechnicalAudit().run_audit()
