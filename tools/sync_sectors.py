#!/usr/bin/env python
import os
import sys

# Add project root to path
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.append(PROJECT_ROOT)

from myra_app.sector_manager import SectorManager


def main():
    print("--- MYRA Sector Sync Utility ---")
    mgr = SectorManager()

    if "--incremental" in sys.argv:
        mgr.incremental_sync()
    else:
        mgr.sync_all()

    print("--- Sync Finished ---")


if __name__ == "__main__":
    main()
