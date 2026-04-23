import sqlite3
import os

db_path = os.path.join("db", "myra_technical.db")
if not os.path.exists(db_path):
    print(f"Database {db_path} does not exist. Skipping cleanup.")
else:
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # 1. Clean technical_data duplicates
    print("Cleaning duplicates from technical_data...")
    cursor.execute("""
        DELETE FROM technical_data
        WHERE rowid NOT IN (
            SELECT max(rowid)
            FROM technical_data
            GROUP BY symbol, date
        )
    """)
    deleted = cursor.rowcount
    print(f"Deleted {deleted} duplicate rows from technical_data.")

    conn.commit()
    conn.close()
    print("Database cleanup completed.")

# Also check for myra_metadata.db and clear bhavcopy_status if it exists
meta_path = os.path.join("db", "myra_metadata.db")
if os.path.exists(meta_path):
    conn = sqlite3.connect(meta_path)
    cursor = conn.cursor()
    try:
        # Exploring metadata to see if bhavcopy_status is present
        cursor.execute("SELECT key FROM metadata WHERE key LIKE '%bhavcopy%'")
        keys = cursor.fetchall()
        if keys:
            print(f"Found related metadata keys: {keys}")
            cursor.execute("DELETE FROM metadata WHERE key IN ('bhavcopy_status')")
            print(f"Deleted {cursor.rowcount} metadata rows.")
            conn.commit()
    except Exception as e:
        print(f"Metadata cleanup failed or not applicable: {e}")
    conn.close()
