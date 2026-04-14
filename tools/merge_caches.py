import sqlite3
import os


def merge_caches():
    c1_path = "db/network_cache.sqlite"
    c2_path = "db/request_cache.sqlite"

    if not os.path.exists(c2_path):
        print("[*] request_cache.sqlite not found, skipping merge.")
        return

    print(f"[*] Resetting legacy cache {c2_path} and migrating to JSON cache schema in {c1_path}...")
    try:
        conn = sqlite3.connect(c1_path)

        # Drop legacy tables to clear out pickle blobs safely
        conn.execute("DROP TABLE IF EXISTS cache")

        # Ensure target table exists (now designed for JSON blobs)
        conn.execute(
            "CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, value BLOB, expiry TIMESTAMP)"
        )
        conn.commit()
        conn.close()

        # We don't migrate the insecure pickle blobs. MYRA will rebuild JSON cache on next run.

        # Cleanup legacy db completely
        os.remove(c2_path)
        print(f"[+] Deleted legacy {c2_path}")
    except Exception as e:
        print(f"[!] Merge failed: {e}")


if __name__ == "__main__":
    merge_caches()
