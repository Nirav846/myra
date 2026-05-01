import sqlite3
import os


def create_scoring_db(db_path="scoring.db"):
    """
    Creates the modular Scoring Database (SQLite) for MYRA.
    Stores fundamental scores and ranking history.
    """
    print(f"[MYRA] Initializing Scoring DB at {db_path}...")

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # 1. Fundamental Scores Table
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS fundamental_scores (
            symbol TEXT NOT NULL,
            date TEXT NOT NULL,
            growth_score REAL,
            quality_score REAL,
            stability_score REAL,
            risk_score REAL,
            total_funda_score REAL,
            grade TEXT,
            PRIMARY KEY (symbol, date)
        )
    """
    )

    # 2. Ranking History
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS ranking_history (
            symbol TEXT NOT NULL,
            date TEXT NOT NULL,
            rank_nifty500 INTEGER,
            rank_sector INTEGER,
            PRIMARY KEY (symbol, date)
        )
    """
    )

    conn.commit()
    conn.close()
    print("[+] Scoring DB created successfully.")


if __name__ == "__main__":
    create_scoring_db()
