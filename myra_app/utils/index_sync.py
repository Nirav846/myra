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

INDEX_SOURCES = {
    "NIFTY 50": {
        "type": "api",
        "url": "https://www.nseindia.com/api/equity-stockIndices?index=NIFTY%2050"
    },
    "NIFTY 500": {
        "type": "csv",
        "url": "https://www.niftyindices.com/IndexConstituent/ind_nifty500list.csv"
    },
    "NIFTY SMALLCAP 250": {
        "type": "csv",
        "url": "https://www.niftyindices.com/IndexConstituent/ind_niftysmallcap250list.csv"
    }
}


def get_librarian_core():
    """Get LibrarianCore instance for DB operations."""
    try:
        from myra_app.librarian_core import LibrarianCore

        return LibrarianCore(read_only=False)
    except ImportError:
        return None


def sync_index_constituents(index_name, force=False, task_id: int = None):
    """
    Sync index constituents from NSE API.

    Args:
        index_name: Name of the index (e.g., "NIFTY 500")
        force: Force sync regardless of last sync date
        task_id: Optional task ID for progress tracking
    """
    from myra_app.task_tracker import update

    if task_id is not None:
        update(task_id, f"Syncing {index_name} constituents…")

    if index_name not in INDEX_SOURCES:
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

        source = INDEX_SOURCES[index_name]
        symbols = []

        if source["type"] == "api":
            # Use a session to get cookies first
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "*/*",
                "Accept-Language": "en-US,en;q=0.9",
            }
            session = requests.Session()
            session.get("https://www.nseindia.com", headers=headers)  # get cookies
            response = session.get(source["url"], headers=headers, timeout=30)
            response.raise_for_status()
            data = response.json()
            if "data" not in data or not data["data"]:
                print(f"[Index Sync] No data found for {index_name}")
                return False
            for item in data["data"]:
                symbol = item.get("symbol")
                if symbol:
                    symbols.append(symbol)

        elif source["type"] == "csv":
            try:
                import io
                import pandas as pd
                import time

                headers = {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.5",
                    "Referer": "https://www.niftyindices.com/",
                    "Connection": "keep-alive",
                    "Upgrade-Insecure-Requests": "1",
                }

                # Retry up to 3 times
                for attempt in range(3):
                    try:
                        session = requests.Session()
                        # First visit the main page to get cookies
                        session.get("https://www.niftyindices.com/", headers=headers, timeout=10)
                        response = session.get(source["url"], headers=headers, timeout=30)
                        response.raise_for_status()
                        break
                    except Exception as e:
                        if attempt == 2:
                            raise
                        print(f"[Index Sync] Retry {attempt+1}/3 for {index_name}...")
                        time.sleep(2)

                df = pd.read_csv(io.StringIO(response.text))
                # The CSV usually has a column named "Symbol"
                if "Symbol" in df.columns:
                    symbols = df["Symbol"].dropna().astype(str).str.strip().tolist()
                elif "SYMBOL" in df.columns:
                    symbols = df["SYMBOL"].dropna().astype(str).str.strip().tolist()
                else:
                    # Fallback: take first column
                    symbols = df.iloc[:, 0].dropna().astype(str).str.strip().tolist()
                print(f"[Index Sync] Downloaded {len(symbols)} symbols from CSV for {index_name}")
            except Exception as e:
                print(f"[Index Sync] CSV download failed for {index_name}: {e}")
                return False

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
            filtered.append(sym)  # noqa: PG-APPEND
        symbols = filtered

        if not symbols:
            print(f"[Index Sync] No symbols found for {index_name}")
            return False

        print(f"[Index Sync] Found {len(symbols)} symbols for {index_name}")

        if task_id is not None:
            update(task_id, f"Writing {len(symbols)} symbols to DB…")

        # Update database
        metadata_db_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "db", "myra_metadata.db"
        )

        os.makedirs(os.path.dirname(metadata_db_path), exist_ok=True)

        with sqlite3.connect(metadata_db_path, timeout=30) as conn:
            # Create table if not exists
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS index_constituents (
                    index_name TEXT,
                    symbol TEXT,
                    added_date TEXT,
                    PRIMARY KEY (index_name, symbol)
                )
                """
            )

            # Clear old entries
            conn.execute(
                "DELETE FROM index_constituents WHERE index_name = ?", (index_name,)
            )

            # Insert new entries
            today = datetime.now(IST).date().isoformat()
            rows = [(index_name, symbol, today) for symbol in symbols]
            conn.executemany(
                "INSERT INTO index_constituents (index_name, symbol, added_date) VALUES (?, ?, ?)",
                rows,
            )

            conn.commit()

        # Update metadata
        lib.set_metadata(last_sync_key, datetime.now(IST).date().isoformat())

        print(
            f"[Index Sync] Successfully synced {len(symbols)} symbols for {index_name}"
        )

        if task_id is not None:
            update(task_id, f"{index_name} sync complete")

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
    if index_name not in INDEX_SOURCES:
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
