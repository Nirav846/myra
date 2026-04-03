import sqlite3
import os
import sys

def check_recent_insider():
    db_path = os.path.join("db", "institutional.db")
    conn = sqlite3.connect(db_path)
    res = conn.execute("SELECT symbol, acq_name, date, value_cr FROM insider_trades ORDER BY date DESC LIMIT 10").fetchall()
    print("Most Recent Insider Trades:")
    for r in res:
        print(f"  - {r[2]} | {r[0]} | {r[1]} | ₹{r[3]}Cr")
    conn.close()

if __name__ == "__main__":
    check_recent_insider()
