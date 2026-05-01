import logging
import sqlite3

from myra_app.schema_registry import SchemaRegistry

logger = logging.getLogger(__name__)


class LibrarianSchemaMixin:
    """Handles all database initialization and migrations."""

    def _migrate_meta_schema(self):
        """Auto-migrates new columns into symbols_master if they don't exist."""
        columns = {
            "source": "TEXT",
            "confidence": "REAL",
            "last_updated_sector": "TEXT",
            "sector_locked": "INTEGER DEFAULT 0",
            "is_active": "INTEGER DEFAULT 1",
            "instrument_type": "TEXT DEFAULT 'EQUITY'",
        }
        for col, col_type in columns.items():
            try:
                self.safe_execute(
                    f"ALTER TABLE symbols_master ADD COLUMN {col} {col_type}",
                    conn=self._meta_conn,
                )
            except Exception:
                pass

    def _create_tables(self):
        """Initializes all tables in their respective sidecars."""

        # --- 1. META.DB (System Brain) ---
        if self._meta_conn:
            self.safe_execute(
                """
                CREATE TABLE IF NOT EXISTS symbols_master (
                    symbol TEXT PRIMARY KEY,
                    first_seen TEXT,
                    last_seen TEXT,
                    in_active_universe INTEGER DEFAULT 0,
                    in_nifty500 INTEGER DEFAULT 0,
                    sector TEXT,
                    industry TEXT,
                    raw_sector TEXT,
                    raw_industry TEXT,
                    source TEXT,
                    confidence REAL,
                    last_updated_sector TEXT,
                    sector_locked INTEGER DEFAULT 0,
                    is_active INTEGER DEFAULT 1,
                    instrument_type TEXT DEFAULT 'EQUITY',
                    last_fundamental_update TEXT
                )
            """,
                conn=self._meta_conn,
            )
            self._migrate_meta_schema()
            self.safe_execute(
                "CREATE TABLE IF NOT EXISTS index_constituents (index_name TEXT, symbol TEXT, PRIMARY KEY (index_name, symbol))",
                conn=self._meta_conn,
            )
            self.safe_execute(
                "CREATE TABLE IF NOT EXISTS benchmarks (symbol TEXT, date TEXT, close REAL, PRIMARY KEY (symbol, date))",
                conn=self._meta_conn,
            )
            self.safe_execute(
                "CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT)",
                conn=self._meta_conn,
            )
            # PRIORITY 8: DATA LINEAGE TRACKING
            self.safe_execute(
                """
                CREATE TABLE IF NOT EXISTS lineage_tracking (
                    dataset_name TEXT,
                    fetch_time TEXT,
                    source_url TEXT,
                    rows_processed INTEGER,
                    status TEXT,
                    transformations_applied TEXT,
                    PRIMARY KEY (dataset_name, fetch_time)
                )
                """,
                conn=self._meta_conn,
            )

        # --- 2. TECHNICAL.DB (Price History) ---
        if self._tech_conn:
            self.safe_execute(
                """
                CREATE TABLE IF NOT EXISTS technical_data (
                    symbol TEXT NOT NULL,
                    date TEXT NOT NULL,
                    open REAL,
                    high REAL,
                    low REAL,
                    close REAL,
                    volume INTEGER,
                    delivery INTEGER,
                    trades INTEGER,
                    vwap REAL,
                    delivery_pct REAL,
                    delivery_ratio REAL,
                    delivery_source TEXT,
                    PRIMARY KEY (symbol, date)
                )
            """,
                conn=self._tech_conn,
            )

        # --- 3. INSTITUTIONAL.DB (Smart Money) ---
        if self._inst_conn:
            self.safe_execute(
                """
                CREATE TABLE IF NOT EXISTS fii_dii_history (
                    symbol TEXT,
                    date TEXT,
                    fii_pct REAL,
                    dii_pct REAL,
                    promoter_pct REAL,
                    pledged_pct REAL,
                    fii_change REAL,
                    dii_change REAL,
                    car_ratio REAL,
                    is_hidden_accumulation INTEGER DEFAULT 0,
                    PRIMARY KEY (symbol, date)
                )
            """,
                conn=self._inst_conn,
            )
            self.safe_execute(
                """
                CREATE TABLE IF NOT EXISTS institutional_owners (
                    symbol TEXT,
                    owner_name TEXT,
                    owner_type TEXT,
                    shares_held INTEGER,
                    pct_held REAL,
                    date TEXT,
                    PRIMARY KEY (symbol, owner_name, date)
                )
            """,
                conn=self._inst_conn,
            )
            self.safe_execute(
                """
                CREATE TABLE IF NOT EXISTS large_deals (
                    symbol TEXT,
                    type TEXT,
                    client TEXT,
                    buy_sell TEXT,
                    qty INTEGER,
                    price REAL,
                    value_cr REAL,
                    date TEXT,
                    PRIMARY KEY (symbol, client, date, qty, price)
                )
            """,
                conn=self._inst_conn,
            )

        # --- 4. VALUATION.DB (Fundamentals) ---
        if self._val_conn:
            self.safe_execute(
                """
                CREATE TABLE IF NOT EXISTS fundamentals (
                    symbol TEXT PRIMARY KEY,
                    pe REAL,
                    roe REAL,
                    eps REAL,
                    book_value REAL,
                    market_cap REAL,
                    sector TEXT,
                    last_updated TEXT
                )
            """,
                conn=self._val_conn,
            )
            self.safe_execute(
                """
                CREATE TABLE IF NOT EXISTS quarterly_results (
                    symbol TEXT,
                    report_date TEXT,
                    period_end TEXT,
                    revenue REAL,
                    net_profit REAL,
                    eps REAL,
                    opm_pct REAL,
                    PRIMARY KEY (symbol, report_date)
                )
            """,
                conn=self._val_conn,
            )

        # --- 5. GOVERNANCE.DB (Pledge & SAST) ---
        if self._gov_conn:
            self.safe_execute(
                """
                CREATE TABLE IF NOT EXISTS sast_disclosures (
                    disclosure_id TEXT PRIMARY KEY,
                    symbol TEXT,
                    date TEXT,
                    acq_name TEXT,
                    qty_pct REAL,
                    type TEXT
                )
            """,
                conn=self._gov_conn,
            )
            self.safe_execute(
                """
                CREATE TABLE IF NOT EXISTS pledged_history (
                    symbol TEXT,
                    date TEXT,
                    promoter_holding REAL,
                    pledged_pct REAL,
                    change_qoq REAL,
                    PRIMARY KEY (symbol, date)
                )
            """,
                conn=self._gov_conn,
            )
            self.safe_execute(
                """
                CREATE TABLE IF NOT EXISTS shareholding_history (
                    symbol TEXT,
                    date TEXT,
                    fii_pct REAL,
                    dii_pct REAL,
                    promoter_pct REAL,
                    PRIMARY KEY (symbol, date)
                )
            """,
                conn=self._gov_conn,
            )
            self.safe_execute(
                """
                CREATE TABLE IF NOT EXISTS ias_history (
                    symbol TEXT,
                    date TEXT,
                    ias_score REAL,
                    ias_rank REAL,
                    tags TEXT,
                    PRIMARY KEY (symbol, date)
                )
            """,
                conn=self._gov_conn,
            )

        self._create_indices()

        # PRIORITY 2.3: Runtime Schema Validation on Startup
        if self._tech_conn:
            SchemaRegistry.validate_schema(self._tech_conn, "technical_data")

    def _create_indices(self):
        """Optimizes all sidecars with covering indices."""
        try:
            if self._tech_conn:
                self.safe_execute(
                    "CREATE INDEX IF NOT EXISTS idx_tech_date ON technical_data (date)",
                    conn=self._tech_conn,
                )
                self.safe_execute(
                    "CREATE INDEX IF NOT EXISTS idx_tech_symbol ON technical_data (symbol)",
                    conn=self._tech_conn,
                )

            if self._inst_conn:
                self.safe_execute(
                    "CREATE INDEX IF NOT EXISTS idx_deals_sym ON large_deals (symbol)",
                    conn=self._inst_conn,
                )

            if self._meta_conn:
                self.safe_execute(
                    "CREATE INDEX IF NOT EXISTS idx_master_active ON symbols_master (in_active_universe)",
                    conn=self._meta_conn,
                )
                self.safe_execute(
                    "CREATE INDEX IF NOT EXISTS idx_master_sector ON symbols_master (sector)",
                    conn=self._meta_conn,
                )
                self.safe_execute(
                    "CREATE INDEX IF NOT EXISTS idx_master_industry ON symbols_master (industry)",
                    conn=self._meta_conn,
                )
        except Exception:
            pass
