import os
import shutil
import sqlite3

# Define the DB_MAP from LibrarianCore
DB_MAP = {
    "technical": "myra_technical.db",
    "institutional": "myra_institutional.db",
    "meta": "myra_metadata.db",
    "valuation": "myra_valuation.db",
    "governance": "myra_governance.db",
    "network_cache": "myra_cache_network.db",
    "scoring": "myra_scoring.db",
    "calendar": "myra_calendar.db",
}


def migrate():
    db_dir = os.path.join(os.getcwd(), "db")
    if not os.path.exists(db_dir):
        print(f"[!] DB directory {db_dir} not found.")
        return

    print("🚀 Starting DB name migration...")

    # Current/Old filenames
    old_names = {
        "technical": "technical.db",
        "institutional": "institutional.db",
        "meta": "meta.db",
        "valuation": "valuation.db",
        "governance": "governance.db",
        "network_cache": "network_cache.sqlite",
        "scoring": "scoring.db",
        "calendar": "calendar.db",
    }

    for key, old_name in old_names.items():
        new_name = DB_MAP[key]
        old_path = os.path.join(db_dir, old_name)
        new_path = os.path.join(db_dir, new_name)

        if os.path.exists(old_path):
            if os.path.exists(new_path):
                print(f"[!] Both {old_name} and {new_name} exist. Skipping...")
                continue

            print(f"[+] Renaming {old_name} -> {new_name}")
            shutil.move(old_path, new_path)

            # Verify integrity
            try:
                conn = sqlite3.connect(new_path)
                conn.execute("SELECT 1").fetchone()  # noqa: N+1
                conn.close()
                print(f"[✓] Verified integrity for {new_name}")
            except Exception as e:
                print(f"[!] Integrity check failed for {new_name}: {e}")
        else:
            print(f"[-] {old_name} not found (Already migrated or new).")

    print("\n✅ Migration complete.")


if __name__ == "__main__":
    migrate()
