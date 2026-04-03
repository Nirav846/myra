#!/usr/bin/env python
import sys
import os

# Add project root to path
sys.path.append(os.getcwd())

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
