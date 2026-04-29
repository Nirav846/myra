import os
import sqlite3

from myra_app.librarian_core import LibrarianCore


def create_technical_db(db_path=None):
    """
    Creates the modular Technical Database (SQLite) for MYRA.
    Schema designed for high-performance time-series ingestion.
    """
    if db_path is None:
        db_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "..",
            "db",
            LibrarianCore.DB_MAP["technical"],
        )
    print(f"[MYRA] Initializing Technical DB at {db_path}...")

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # 1. Technical Data Table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS technical_data (
            symbol TEXT NOT NULL,
            date TEXT NOT NULL,
            close REAL,
            high REAL,
            low REAL,
            volume INTEGER,
            delivery INTEGER,
            delivery_pct REAL,
            PRIMARY KEY (symbol, date)
        )
    """)

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
