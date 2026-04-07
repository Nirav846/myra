import sqlite3
import os


def merge_caches():
    c1_path = "db/network_cache.sqlite"
    c2_path = "db/request_cache.sqlite"

    if not os.path.exists(c2_path):
        print("[*] request_cache.sqlite not found, skipping merge.")
        return

    print(f"[*] Merging {c2_path} into {c1_path}...")
    try:
        conn = sqlite3.connect(c1_path)
        # Ensure target table exists
        conn.execute(
            "CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, value BLOB, expiry TIMESTAMP)"
        )

        conn.execute(f"ATTACH '{c2_path}' AS req")
        conn.execute("INSERT OR IGNORE INTO cache SELECT * FROM req.cache")
        conn.commit()
        conn.close()
        print("[+] Merge successful.")

        # Cleanup
        os.remove(c2_path)
        print(f"[+] Deleted legacy {c2_path}")
    except Exception as e:
        print(f"[!] Merge failed: {e}")


if __name__ == "__main__":
    merge_caches()
