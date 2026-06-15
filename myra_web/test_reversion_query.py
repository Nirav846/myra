import sqlite3
import os

DB_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "repo", "db"))
db_path = os.path.join(DB_DIR, "myra_technical.db")

conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

query = """
        WITH RecentDates AS (
           SELECT DISTINCT date FROM technical_data ORDER BY date DESC LIMIT 30
        ),
        RecentData AS (
           SELECT td.* FROM technical_data td
           INNER JOIN RecentDates rd ON td.date = rd.date
        ),
        RankedData AS (
          SELECT 
            symbol as ticker,
            date as "Date",
            open as "Open",
            high as "High",
            low as "Low",
            close as "Close",
            volume as "Volume",
            delivery as "Deliverable_Volume",
            (delivery * 100.0 / NULLIF(volume, 0)) as del_perc,
            
            AVG(volume) OVER w20 as avg_vol_20,
            
            AVG((delivery * 100.0 / NULLIF(volume, 0))) OVER w20 as avg_del_20,
            AVG((delivery * 100.0 / NULLIF(volume, 0)) * (delivery * 100.0 / NULLIF(volume, 0))) OVER w20 as avg_del_sq_20,

            MAX(high) OVER w20 as high_20,
            MIN(low) OVER w20 as low_20,
            
            AVG(close) OVER w20 as avg_close_20,
            AVG(close * close) OVER w20 as avg_close_sq_20,
            
            AVG((high - low) / NULLIF(close, 0)) OVER w20 as vol_long,
            AVG((high - low) / NULLIF(close, 0)) OVER w5 as vol_short,

            ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY date DESC) as rn
          FROM RecentData
          WINDOW 
            w20 AS (PARTITION BY symbol ORDER BY date ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING),
            w5 AS (PARTITION BY symbol ORDER BY date ROWS BETWEEN 5 PRECEDING AND 1 PRECEDING)
        )
        SELECT * FROM RankedData 
        WHERE rn = 1 
        ORDER BY "Volume" DESC 
        LIMIT 2500
"""
try:
    cursor.execute(query)
    rows = cursor.fetchall()
    print("Fetched", len(rows), "rows")
except Exception as e:
    print("Error:", e)
