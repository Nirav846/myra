import os
import sys

# Fix path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from myra_app.librarian import Librarian


def force_sync():
    print("[MYRA] Initiating FORCED Institutional & Market Sync...")
    lib = Librarian()

    # Reset metadata to force sync
    print("[*] Resetting sync timestamps...")
    lib.set_metadata("last_insider_sync", "1900-01-01 00:00:00")
    lib.set_metadata("last_large_deals_sync", "1900-01-01 00:00:00")

    print("[*] Running Sync (this might take a minute)...")
    # history_years=0 to only get recent updates
    lib.sync_market_data(history_years=0, skip_maintenance=False)

    print("\n[+] Force Sync Complete.")
    print(f"[*] New Max Insider Date: {lib.get_max_insider_date()}")
    lib.close()


if __name__ == "__main__":
    force_sync()
