import sqlite3
import os


def create_technical_db(db_path="technical.db"):
    """
    Creates the modular Technical Database (SQLite) for MYRA.
    Schema designed for high-performance time-series ingestion.
    """
    print(f"[MYRA] Initializing Technical DB at {db_path}...")

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # 1. Technical Data Table
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS technical_data (
            symbol TEXT NOT NULL,
            date TEXT NOT NULL,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            volume INTEGER,
            delivery INTEGER,
            trades INTEGER,
            vwap REAL,
            delivery_pct REAL,
            delivery_ratio REAL,
            PRIMARY KEY (symbol, date)
        )
    """
    )

    # 2. Indexes for fast retrieval
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_technical_symbol ON technical_data (symbol)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_technical_date ON technical_data (date)"
    )

    conn.commit()
    conn.close()
    print("[+] Technical DB created successfully.")


if __name__ == "__main__":
    create_technical_db()
