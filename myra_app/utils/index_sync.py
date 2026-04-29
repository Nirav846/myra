#!/usr/bin/env python
"""
MYRA Index Constituents Sync
Auto-updates NIFTY 500 and other NSE indices every 15 days.
"""

import os
import sqlite3
from datetime import datetime, timedelta, timezone

import requests

IST = timezone(timedelta(hours=5, minutes=30))

NSE_INDICES = {
    "NIFTY 50": "https://www.nseindia.com/api/equity-stockIndices?index=NIFTY%2050",
    "NIFTY 500": "https://www.nseindia.com/api/equity-stockIndices?index=NIFTY%20500",
}


def get_librarian_core():
    """Get LibrarianCore instance for DB operations."""
    try:
        from myra_app.librarian_core import LibrarianCore

        return LibrarianCore(read_only=False)
    except ImportError:
        return None


def sync_index_constituents(index_name, force=False):
    """
    Sync index constituents from NSE API.

    Args:
        index_name: Name of the index (e.g., "NIFTY 500")
        force: Force sync regardless of last sync date
    """
    if index_name not in NSE_INDICES:
        print(f"[Index Sync] Unknown index: {index_name}")
        return False

    lib = get_librarian_core()
    if not lib:
        print("[Index Sync] Could not initialize LibrarianCore")
        return False

    try:
        # Check last sync date
        last_sync_key = f"last_sync_{index_name.replace(' ', '_')}"
        if not force:
            last_sync = lib.get_metadata(last_sync_key)
            if last_sync:
                try:
                    last_sync_date = datetime.strptime(last_sync, "%Y-%m-%d").date()
                    days_since_sync = (datetime.now(IST).date() - last_sync_date).days
                    if days_since_sync < 15:
                        print(
                            f"[Index Sync] {index_name} synced {days_since_sync} days ago. Skipping."
                        )
                        return True
                except ValueError:
                    pass

        print(f"[Index Sync] Syncing {index_name} constituents...")

        # Fetch from NSE API
        url = NSE_INDICES[index_name]
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
        }

        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()

        data = response.json()
        if "data" not in data or not data["data"]:
            print(f"[Index Sync] No data found for {index_name}")
            return False

        # Extract symbols
        symbols = []
        for item in data["data"]:
            symbol = item.get("symbol")
            if symbol:
                symbols.append(symbol)

        # Filter out dummy / test symbols that NSE sometimes includes
        EXCLUDE_SYMBOLS = {
            "DUMMY",
            "TEST",
            "DEMO",
            "NSE",
            "INDIA",
            "EQ",
            "TEMP",
            "123456",
            "789012",
            "MII",
            "MSEI",
            "BSE",
            "NIFTY",
            "SENSEX",
            "BANKNIFTY",
            "FINNIFTY",
        }
        # Also remove any symbol that contains any of these words
        EXCLUDE_PATTERNS = ["DUMMY", "TEST", "DEMO"]

        filtered = []
        for sym in symbols:
            sym_upper = sym.upper()
            if sym_upper in EXCLUDE_SYMBOLS:
                continue
            if any(p in sym_upper for p in EXCLUDE_PATTERNS):
                continue
            filtered.append(sym)
        symbols = filtered

        if not symbols:
            print(f"[Index Sync] No symbols found for {index_name}")
            return False

        print(f"[Index Sync] Found {len(symbols)} symbols for {index_name}")

        # Update database
        metadata_db_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "db", "myra_metadata.db"
        )

        os.makedirs(os.path.dirname(metadata_db_path), exist_ok=True)

        with sqlite3.connect(metadata_db_path, timeout=30) as conn:
            # Create table if not exists
            conn.execute("""
                CREATE TABLE IF NOT EXISTS index_constituents (
                    index_name TEXT,
                    symbol TEXT,
                    last_updated TEXT,
                    PRIMARY KEY (index_name, symbol)
                )
            """)

            # Clear old entries
            conn.execute(
                "DELETE FROM index_constituents WHERE index_name = ?", (index_name,)
            )

            # Insert new entries
            today = datetime.now(IST).date().isoformat()
            rows = [(index_name, symbol, today) for symbol in symbols]
            conn.executemany(
                "INSERT INTO index_constituents (index_name, symbol, last_updated) VALUES (?, ?, ?)",
                rows,
            )

            conn.commit()

        # Update metadata
        lib.set_metadata(last_sync_key, datetime.now(IST).date().isoformat())

        print(
            f"[Index Sync] Successfully synced {len(symbols)} symbols for {index_name}"
        )
        return True

    except requests.RequestException as e:
        print(f"[Index Sync] Network error for {index_name}: {e}")
        return False
    except Exception as e:
        print(f"[Index Sync] Error syncing {index_name}: {e}")
        return False
    finally:
        try:
            lib.close()
        except:
            pass


def heal_index_if_stale(index_name, expected_count=None):
    """Check if stored index count differs significantly from live and force re-sync if so."""
    import os
    import sqlite3

    from myra_app.constants import DB_DIR

    meta_db = os.path.join(DB_DIR, "myra_metadata.db")
    with sqlite3.connect(meta_db, timeout=10) as conn:
        stored = conn.execute(
            "SELECT COUNT(*) FROM index_constituents WHERE index_name = ?",
            (index_name,),
        ).fetchone()[0]

    if expected_count is None:
        # Use historical average if no expected count provided
        expected_count = stored  # fallback: skip check

    if stored > 0 and expected_count > 0:
        diff_pct = abs(stored - expected_count) / expected_count * 100
        if diff_pct > 5:
            print(
                f"[INDEX HEAL] {index_name} count mismatch: stored={stored}, expected={expected_count} ({diff_pct:.1f}%)"
            )
            print(f"[INDEX HEAL] Forcing re-sync of {index_name}...")
            sync_index_constituents(index_name, force=True)


def get_index_symbols(index_name):
    """Get symbols for a given index from local database."""
    if index_name not in NSE_INDICES:
        return []

    metadata_db_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)), "db", "myra_metadata.db"
    )

    if not os.path.exists(metadata_db_path):
        return []

    try:
        with sqlite3.connect(metadata_db_path, timeout=10) as conn:
            cursor = conn.execute(
                "SELECT symbol FROM index_constituents WHERE index_name = ? ORDER BY symbol",
                (index_name,),
            )
            return [row[0] for row in cursor.fetchall()]
    except Exception:
        return []
