#!/usr/bin/env python
"""
MYRA Fundamentals Sync
Resumable monthly fundamentals data synchronization with yfinance.
"""

import sqlite3
import os
import time
import logging
import json
from datetime import datetime, timezone, timedelta

IST = timezone(timedelta(hours=5, minutes=30))

# Import constants for DB paths
try:
    from myra_app.constants import DB_DIR
except ImportError:
    # Fallback if constants not available
    DB_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "db")

logger = logging.getLogger(__name__)

def get_metadata_db_path():
    """Get path to metadata database."""
    return os.path.join(DB_DIR, "myra_metadata.db")

def get_valuation_db_path():
    """Get path to valuation database."""
    return os.path.join(DB_DIR, "myra_valuation.db")

def get_sync_metadata():
    """Get current sync status from metadata."""
    metadata_db = get_metadata_db_path()
    if not os.path.exists(metadata_db):
        return {}
    
    try:
        with sqlite3.connect(metadata_db, timeout=10) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
            """)
            
            cursor = conn.execute(
                "SELECT value FROM metadata WHERE key = ?",
                ("fundamentals_sync_status",)
            )
            row = cursor.fetchone()
            if row:
                return json.loads(row[0])
    except Exception as e:
        logger.error(f"Failed to read sync metadata: {e}")
    
    return {}

def set_sync_metadata(status_data):
    """Update sync status metadata."""
    metadata_db = get_metadata_db_path()
    os.makedirs(os.path.dirname(metadata_db), exist_ok=True)
    
    try:
        with sqlite3.connect(metadata_db, timeout=10) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
            """)
            
            conn.execute(
                "INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)",
                ("fundamentals_sync_status", json.dumps(status_data))
            )
            conn.commit()
    except Exception as e:
        logger.error(f"Failed to write sync metadata: {e}")

def sync_fundamentals(force=False):
    """
    Resumable monthly fundamentals sync.
    
    Args:
        force: Force sync regardless of last sync date
    """
    print("[Fundamentals Sync] Checking fundamentals sync status...")
    
    # Get current status
    status = get_sync_metadata()
    ist_now = datetime.now(IST)
    today = ist_now.date().isoformat()
    
    # Check if already done this month
    if not force and status.get("status") == "complete":
        last_sync = status.get("last_sync_date")
        if last_sync:
            try:
                last_date = datetime.strptime(last_sync, "%Y-%m-%d").date()
                days_since = (ist_now.date() - last_date).days
                if days_since < 30:
                    print(f"[Fundamentals Sync] Already synced {days_since} days ago. Skipping.")
                    return True
            except ValueError:
                pass
    
    # Initialize or resume sync
    if force or status.get("status") != "in_progress":
        # Start new sync
        valuation_db = get_valuation_db_path()
        if not os.path.exists(valuation_db):
            print("[Fundamentals Sync] Valuation database not found. Skipping.")
            return False
        
        try:
            with sqlite3.connect(valuation_db, timeout=10) as conn:
                cursor = conn.execute(
                    "SELECT COUNT(*) FROM fundamentals WHERE sector IS NULL OR sector = ''"
                )
                total_symbols = cursor.fetchone()[0]
                
                if total_symbols == 0:
                    print("[Fundamentals Sync] All symbols already have sector data. Skipping.")
                    return True
                
                status = {
                    "status": "in_progress",
                    "last_processed_symbol": None,
                    "total_symbols": total_symbols,
                    "failed_count": 0,
                    "updated_count": 0,
                    "start_time": ist_now.isoformat(),
                    "last_sync_date": None
                }
                set_sync_metadata(status)
                print(f"[Fundamentals Sync] Starting sync for {total_symbols} symbols...")
                
        except Exception as e:
            logger.error(f"Failed to initialize fundamentals sync: {e}")
            return False
    else:
        # Resume existing sync
        last_symbol = status.get("last_processed_symbol")
        print(f"[Fundamentals Sync] Resuming from symbol: {last_symbol}")
    
    # Process symbols
    valuation_db = get_valuation_db_path()
    processed_count = 0
    batch_updates = []
    
    try:
        with sqlite3.connect(valuation_db, timeout=30) as conn:
            # Get symbols to process
            last_symbol = status.get("last_processed_symbol")
            if last_symbol:
                cursor = conn.execute(
                    "SELECT symbol FROM fundamentals WHERE sector IS NULL OR sector = '' "
                    "AND symbol > ? ORDER BY symbol",
                    (last_symbol,)
                )
            else:
                cursor = conn.execute(
                    "SELECT symbol FROM fundamentals WHERE sector IS NULL OR sector = '' "
                    "ORDER BY symbol"
                )
            
            symbols = [row[0] for row in cursor.fetchall()]
            
            if not symbols:
                print("[Fundamentals Sync] No symbols need processing. Marking complete.")
                status["status"] = "complete"
                status["last_sync_date"] = today
                set_sync_metadata(status)
                return True
            
            # Process each symbol
            for i, symbol in enumerate(symbols):
                if processed_count >= 25:  # Update metadata every 25 symbols
                    status["last_processed_symbol"] = symbol
                    status["updated_count"] += len(batch_updates)
                    set_sync_metadata(status)
                    batch_updates = []
                    processed_count = 0
                
                try:
                    # Import yfinance here to avoid circular imports
                    import yfinance as yf
                    ticker = yf.Ticker(symbol + ".NS")
                    info = ticker.info
                    
                    sector = info.get("sector")
                    if sector and sector.strip():
                        batch_updates.append((sector, symbol))
                        # Update immediately for this symbol
                        conn.execute(
                            "UPDATE fundamentals SET sector = ? WHERE symbol = ?",
                            (sector, symbol)
                        )
                    else:
                        status["failed_count"] += 1
                    
                    # Rate limiting
                    time.sleep(0.3)
                    
                except Exception as e:
                    logger.debug(f"Failed to fetch sector for {symbol}: {e}")
                    status["failed_count"] += 1
                    continue
                
                processed_count += 1
                
                # Progress indicator
                if (i + 1) % 50 == 0:
                    progress = (i + 1) / len(symbols) * 100
                    print(f"[Fundamentals Sync] Progress: {i+1}/{len(symbols)} ({progress:.1f}%)")
            
            # Final update
            if batch_updates:
                status["updated_count"] += len(batch_updates)
            
            # Mark complete
            status["status"] = "complete"
            status["last_sync_date"] = today
            status["last_processed_symbol"] = None
            set_sync_metadata(status)
            
            print(f"[Fundamentals Sync] Complete! Updated: {status['updated_count']}, Failed: {status['failed_count']}")
            return True
            
    except Exception as e:
        logger.error(f"Fundamentals sync failed: {e}")
        # Save current progress
        status["last_processed_symbol"] = symbols[-1] if symbols else None
        set_sync_metadata(status)
        return False

def get_sync_status():
    """Get current sync status for display."""
    return get_sync_metadata()
