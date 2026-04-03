import sqlite3
import pandas as pd
from datetime import datetime, timedelta

def generate_calendar(db_path="calendar.db", start_year=2021, end_year=2026):
    """
    Generates a market calendar for NSE trading days.
    Includes holiday logic and weekend handling.
    """
    print(f"[MYRA] Generating Market Calendar at {db_path}...")
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS market_calendar (
            date TEXT PRIMARY KEY,
            is_trading_day INTEGER NOT NULL,
            holiday_name TEXT
        )
    """)
    
    # NSE Holidays (Approximate list for major fixed-date holidays)
    # In a real system, this would be fetched or updated annually.
    fixed_holidays = {
        "01-26": "Republic Day",
        "04-14": "Ambedkar Jayanti",
        "05-01": "Maharashtra Day",
        "08-15": "Independence Day",
        "10-02": "Gandhi Jayanti",
        "12-25": "Christmas"
    }
    
    # Variable holidays (Lunar/Religious) - Placeholder list
    # Ideally these would be precise per year.
    variable_holidays = [
        "2024-03-25", # Holi
        "2024-03-29", # Good Friday
        "2024-04-11", # Eid
        "2024-04-17", # Ram Navami
        "2024-11-01", # Diwali
        "2025-03-14", # Holi
        "2025-04-18", # Good Friday
        "2026-03-03", # Holi (Example)
    ]
    
    start_date = datetime(start_year, 1, 1)
    end_date = datetime(end_year, 12, 31)
    current_date = start_date
    
    records = []
    while current_date <= end_date:
        d_str = current_date.strftime("%Y-%m-%d")
        mm_dd = current_date.strftime("%m-%d")
        
        is_trading = 1
        holiday = None
        
        # 1. Weekends
        if current_date.weekday() >= 5: # Saturday=5, Sunday=6
            is_trading = 0
            holiday = "Weekend"
        
        # 2. Fixed Holidays
        elif mm_dd in fixed_holidays:
            is_trading = 0
            holiday = fixed_holidays[mm_dd]
            
        # 3. Variable Holidays
        elif d_str in variable_holidays:
            is_trading = 0
            holiday = "Market Holiday"
            
        records.append((d_str, is_trading, holiday))
        current_date += timedelta(days=1)
        
    cursor.executemany("INSERT OR REPLACE INTO market_calendar VALUES (?, ?, ?)", records)
    conn.commit()
    conn.close()
    print(f"[+] Calendar generated with {len(records)} entries.")

if __name__ == "__main__":
    generate_calendar()
