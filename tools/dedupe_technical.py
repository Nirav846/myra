import os
import sqlite3


def deduplicate_database():
    db_path = r"D:\01screener\Myra\myra_app\db\myra_technical.db"

    if not os.path.exists(db_path):
        print(f"[!] DB not found at {db_path}")
        return

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    print("[MYRA] Starting deduplication of technical_data...")
    print("       This may take a minute for 2.4M+ rows. Please wait...")

    # The magic SQL query: Keep only the highest rowid (most recently inserted)
    # for each symbol/date pair. Delete the rest.
    dedupe_query = """
    DELETE FROM technical_data 
    WHERE rowid NOT IN (
        SELECT MAX(rowid) 
        FROM technical_data 
        GROUP BY symbol, date
    );
    """

    try:
        cursor.execute(dedupe_query)
        deleted_count = cursor.rowcount
        conn.commit()
        print(f"[+] Deduplication complete. Purged {deleted_count} duplicate rows.")

        # Reclaim the physical disk space left behind by the deleted rows
        print("[MYRA] Vacuuming database to reclaim space...")
        cursor.execute("VACUUM;")
        conn.commit()
        print("[+] DB optimized.")

    except Exception as e:
        print(f"[!] Error during deduplication: {e}")
    finally:
        conn.close()


if __name__ == "__main__":
    deduplicate_database()
