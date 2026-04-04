import sqlite3
import os

# Fix path to allow running from anywhere in the project
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
meta_db_path = os.path.join(PROJECT_ROOT, "db", "meta.db")
val_db_path = os.path.join(PROJECT_ROOT, "db", "valuation.db")

if os.path.exists(meta_db_path) and os.path.exists(val_db_path):
    # 1. Reset Sync Markers in meta.db
    m_conn = sqlite3.connect(meta_db_path)
    print("Forcing fundamental refresh in meta.db...")
    try:
        # Ensure the marker column exists (Librarian normally handles this, but we'll be safe)
        cursor = m_conn.execute("PRAGMA table_info(symbols_master)")
        cols = [info[1] for info in cursor.fetchall()]
        if "last_fundamental_update" not in cols:
            print("Adding last_fundamental_update column to symbols_master...")
            m_conn.execute("ALTER TABLE symbols_master ADD COLUMN last_fundamental_update TEXT")
        
        m_conn.execute("UPDATE symbols_master SET last_fundamental_update = '1900-01-01'")
        m_conn.commit()
    except Exception as e:
        print(f"Warning during meta.db update: {e}")
    finally:
        m_conn.close()

    # 2. Clear Fundamentals Summary in valuation.db
    v_conn = sqlite3.connect(val_db_path)
    print("Clearing fundamentals summary in valuation.db...")
    try:
        v_conn.execute("DELETE FROM fundamentals")
        # Also clear quarterly_results to force full re-fetch
        v_conn.execute("DELETE FROM quarterly_results")
        v_conn.commit()
    except Exception as e:
        print(f"Warning during valuation.db update: {e}")
    finally:
        v_conn.close()

    print("Done. Please restart MYRA. On startup, background sync will repopulate fundamentals correctly.")
else:
    print(f"Error: DB not found at {meta_db_path} or {val_db_path}.")
    print("Ensure you are running this from the MYRA project root.")
