#!/usr/bin/env python
"""
MYRA Librarian Core - Connectivity & Persistence Layer (TRILOGY ERA)
EXCLUSIVE GATEKEEPER for Modular SQLite Sidecars.
Transitioning from DuckDB to Multi-DB Architecture.
"""
import os
import sys
import threading
import time
import sqlite3
from datetime import datetime
from rich.console import Console


class SyncStatus:
    """Tracks background synchronization progress."""

    def __init__(self):
        self.task_name = ""
        self.completed_count = 0
        self.total_count = 0

    @property
    def percentage(self):
        if self.total_count == 0:
            return 0
        return round((self.completed_count / self.total_count) * 100, 1)

    def update(self, task=None, completed=None, total=None):
        if task is not None:
            self.task_name = task
        if completed is not None:
            self.completed_count = completed
        if total is not None:
            self.total_count = total


class LibrarianCore:
    """
    Base persistence layer for MYRA.
    Handles connections to the Atomic Trilogy DB stack.
    """

    _db_lock = threading.Lock()

    # Standardized DB Map (TRILOGY ERA v3.2)
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

    def __init__(self, read_only=False, console=None, db_path=None):
        # Legacy DuckDB path (for fallback/migration validation)
        self.db_path = (
            db_path
            if db_path
            else os.path.join(os.getcwd(), "db", "myra_market_data.db")
        )
        self.read_only = read_only
        self.console = console if console else Console()

        # Connection Handles
        self._tech_conn = None
        self._inst_conn = None
        self._meta_conn = None
        self._val_conn = None
        self._gov_conn = None

        self.sync_status = SyncStatus()
        self._is_syncing = False
        self._sync_thread = None
        self.last_stats_update = 0
        self.cached_stats = {"status": "Initialized", "size": "0MB"}

        self.connect()

    def connect(self):
        """Establishes connections to all modular databases."""
        try:
            # 2. Connect Atomic SQLite Sidecars
            self._connect_sqlite()

        except Exception as e:
            print(f"[!] LibrarianCore: Connection failed: {e}")

    def _connect_sqlite(self):
        """Initializes all SQLite sidecar handles."""
        db_dir = os.path.join(os.getcwd(), "db")
        os.makedirs(db_dir, exist_ok=True)

        def _get_conn(name):
            path = os.path.join(db_dir, name)
            if not os.path.exists(path) and self.read_only:
                return None
            try:
                conn = sqlite3.connect(path, check_same_thread=False)
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA synchronous=NORMAL")
                return conn
            except Exception as e:
                print(f"[!] LibrarianCore: Failed to connect to {name}: {e}")
                return None

        # Standardized Connections via DB_MAP
        self._tech_conn = _get_conn(self.DB_MAP["technical"])
        self._inst_conn = _get_conn(self.DB_MAP["institutional"])
        self._meta_conn = _get_conn(self.DB_MAP["meta"])
        self._val_conn = _get_conn(self.DB_MAP["valuation"])
        self._gov_conn = _get_conn(self.DB_MAP["governance"])

    def safe_execute(self, sql, params=None, conn=None):
        """Thread-safe SQL execution for SQLite."""
        # if it's SQLite, use standard execute
        c = conn
        if not c:
            return None

        # SQLite Path
        try:
            with self._db_lock:
                if params:
                    return c.execute(sql, params)
                else:
                    return c.execute(sql)
        except Exception as e:
            raise e

    def get_metadata(self, key):
        if not self._meta_conn:
            return None
        try:
            res = self._meta_conn.execute(
                "SELECT value FROM metadata WHERE key = ?", (key,)
            ).fetchone()
            return res[0] if res else None
        except Exception:
            return None

    def set_metadata(self, key, value):
        if not self._meta_conn or self.read_only:
            return
        self._meta_conn.execute(
            "INSERT OR REPLACE INTO metadata VALUES (?, ?)", (key, value)
        )
        self._meta_conn.commit()

    def get_db_stats(self):
        now = time.time()
        if now - self.last_stats_update < 300:
            return self.cached_stats

        size_bytes = 0
        db_dir = os.path.join(os.getcwd(), "db")
        if os.path.exists(db_dir):
            for f in os.listdir(db_dir):
                if f.endswith(".db") or f.endswith(".sqlite"):
                    size_bytes += os.path.getsize(os.path.join(db_dir, f))

        size_mb = size_bytes / (1024 * 1024)
        status = "Healthy" if self._tech_conn and self._meta_conn else "Degraded"

        self.cached_stats = {"status": status, "size": f"{round(size_mb, 1)}MB"}
        self.last_stats_update = now
        return self.cached_stats

    def close(self):
        """Graceful shutdown of all database handles."""
        conns = [
            self._tech_conn,
            self._inst_conn,
            self._meta_conn,
            self._val_conn,
            self._gov_conn,
        ]
        for c in conns:
            if c:
                try:
                    c.close()
                except Exception:
                    pass
        self._tech_conn = self._inst_conn = self._meta_conn = self._val_conn = self._gov_conn = None

    def record_lineage(self, dataset: str, source: str, rows: int, status: str, transforms: str):
        """PRIORITY 8: Centralized Data Lineage Tracking."""
        if not self._meta_conn or self.read_only:
            return
        fetch_time = datetime.now().isoformat()
        try:
            self._meta_conn.execute(
                """
                INSERT OR REPLACE INTO lineage_tracking
                (dataset_name, fetch_time, source_url, rows_processed, status, transformations_applied)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (dataset, fetch_time, source, rows, status, transforms)
            )
            self._meta_conn.commit()
        except Exception as e:
            print(f"[MYRA] Failed to record lineage for {dataset}: {e}")
