import sys
import os
import pandas as pd
import sqlite3
import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from myra_app.ingest_bhavcopy import ingest_bhavcopies

def test_ingestion():
    # create dummy data
    if not os.path.exists('data'):
        os.makedirs('data')
    df = pd.DataFrame({
        'SYMBOL': ['TEST1', 'TEST2', 'TEST3'],
        'SERIES': ['EQ', 'EQ', 'EQ'],
        'TIMESTAMP': ['2023-01-01', '2023-01-01', np.nan],
        'OPEN': [100, 0, 100],
        'HIGH': [110, 110, 110],
        'LOW': [90, 90, 90],
        'CLOSE': [105, 105, 105],
        'TOTTRDQTY': [1000, 1000, 1000]
    })
    df.to_csv('data/nse_full_test.csv', index=False)

    # test ingest
    if os.path.exists('test_db.db'):
        os.remove('test_db.db')

    conn = sqlite3.connect('test_db.db')
    cursor = conn.cursor()
    cursor.execute("""
    CREATE TABLE technical_data (
        symbol TEXT,
        date TEXT,
        open REAL,
        high REAL,
        low REAL,
        close REAL,
        volume REAL,
        delivery REAL,
        trades REAL,
        vwap REAL,
        delivery_pct REAL,
        delivery_ratio REAL,
        UNIQUE(symbol, date)
    )
    """)
    conn.commit()
    conn.close()

    ingest_bhavcopies('data', db_path='test_db.db')

    conn = sqlite3.connect('test_db.db')
    res = conn.execute("SELECT symbol FROM technical_data").fetchall()

    print("Ingested symbols:", res)
    assert len(res) == 1
    assert res[0][0] == 'TEST1'
    print("Test passed")

if __name__ == '__main__':
    test_ingestion()
