#!/usr/bin/env python
"""
MYRA Librarian Schema Layer (TRILOGY ERA)
Handles multi-DB table creation and schema migrations.
Routes tables to their designated Atomic SQLite sidecars.
"""

class LibrarianSchemaMixin:
    def _migrate_schema(self, conn=None):
        """
        PKScreener Superpower: Automatic Schema Migrations
        Handles migrations across modular databases.
        """
        if self.read_only: return
        # Migration logic will be updated to target specific sidecars in Phase 3
        pass

    def _migrate_meta_schema(self):
        """Helper to add new columns to symbols_master for existing DBs."""
        columns = {
            "industry": "TEXT",
            "raw_sector": "TEXT",
            "raw_industry": "TEXT",
            "source": "TEXT",
            "confidence": "REAL",
            "last_updated_sector": "TEXT",
            "sector_locked": "INTEGER DEFAULT 0"
        }
        for col, col_type in columns.items():
            try:
                self.safe_execute(f"ALTER TABLE symbols_master ADD COLUMN {col} {col_type}", conn=self._meta_conn)
            except Exception: pass # Column already exists

    def _create_tables(self):
        """Initializes all tables in their respective sidecars."""
        
        # --- 1. META.DB (System Brain) ---
        if self._meta_conn:
            self.safe_execute("""
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
                    last_fundamental_update TEXT
                )
            """, conn=self._meta_conn)
            # Schema Migration: Add missing columns if they don't exist
            self._migrate_meta_schema()
            self.safe_execute("CREATE TABLE IF NOT EXISTS index_constituents (index_name TEXT, symbol TEXT, PRIMARY KEY (index_name, symbol))", conn=self._meta_conn)
            self.safe_execute("CREATE TABLE IF NOT EXISTS benchmarks (symbol TEXT, date TEXT, close REAL, PRIMARY KEY (symbol, date))", conn=self._meta_conn)
            self.safe_execute("CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT)", conn=self._meta_conn)

        # --- 2. TECHNICAL.DB (Price History) ---
        if self._tech_conn:
            self.safe_execute("""
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
                    PRIMARY KEY (symbol, date)
                )
            """, conn=self._tech_conn)

        # --- 3. INSTITUTIONAL.DB (Smart Money) ---
        if self._inst_conn:
            self.safe_execute("""
                CREATE TABLE IF NOT EXISTS insider_trades (
                    symbol TEXT,
                    acq_name TEXT,
                    category TEXT,
                    type TEXT,
                    mode TEXT,
                    value_cr REAL,
                    avg_price REAL,
                    date TEXT,
                    PRIMARY KEY (symbol, acq_name, date, value_cr)
                )
            """, conn=self._inst_conn)
            self.safe_execute("""
                CREATE TABLE IF NOT EXISTS large_deals (
                    symbol TEXT,
                    type TEXT,
                    client TEXT,
                    buy_sell TEXT,
                    qty INTEGER,
                    price REAL,
                    date TEXT,
                    PRIMARY KEY (symbol, client, date, qty, price)
                )
            """, conn=self._inst_conn)

        # --- 4. VALUATION.DB (Fundamentals) ---
        if self._val_conn:
            self.safe_execute("""
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
            """, conn=self._val_conn)
            self.safe_execute("""
                CREATE TABLE IF NOT EXISTS quarterly_results (
                    symbol TEXT,
                    report_date TEXT,
                    revenue REAL,
                    net_profit REAL,
                    eps REAL,
                    opm_pct REAL,
                    PRIMARY KEY (symbol, report_date)
                )
            """, conn=self._val_conn)

        # --- 5. GOVERNANCE.DB (Pledge & SAST) ---
        if self._gov_conn:
            self.safe_execute("""
                CREATE TABLE IF NOT EXISTS sast_disclosures (
                    disclosure_id TEXT PRIMARY KEY,
                    symbol TEXT,
                    date TEXT,
                    acq_name TEXT,
                    qty_pct REAL,
                    type TEXT
                )
            """, conn=self._gov_conn)
            self.safe_execute("""
                CREATE TABLE IF NOT EXISTS pledged_history (
                    symbol TEXT,
                    date TEXT,
                    promoter_holding REAL,
                    pledged_pct REAL,
                    change_qoq REAL,
                    PRIMARY KEY (symbol, date)
                )
            """, conn=self._gov_conn)
            self.safe_execute("""
                CREATE TABLE IF NOT EXISTS shareholding_history (
                    symbol TEXT,
                    date TEXT,
                    fii_pct REAL,
                    dii_pct REAL,
                    promoter_pct REAL,
                    PRIMARY KEY (symbol, date)
                )
            """, conn=self._gov_conn)
            self.safe_execute("""
                CREATE TABLE IF NOT EXISTS ias_history (
                    symbol TEXT,
                    date TEXT,
                    ias_score REAL,
                    ias_rank REAL,
                    tags TEXT,
                    PRIMARY KEY (symbol, date)
                )
            """, conn=self._gov_conn)

        # --- 6. DUCKDB (Legacy/High-Speed Cache) ---
        if self.conn:
            # We keep 'prices' in DuckDB for vectorized math if needed, 
            # but it will be a mirror of technical.db
            self.safe_execute("""
                CREATE TABLE IF NOT EXISTS prices (
                    symbol VARCHAR, date DATE, open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE, 
                    volume BIGINT, delivery_qty BIGINT, delivery_percent DOUBLE, exchange VARCHAR,
                    PRIMARY KEY (symbol, date, exchange)
                )
            """, conn=self.conn)
            
        self._create_indices()

    def _create_indices(self):
        """Optimizes all sidecars with covering indices."""
        try:
            if self._tech_conn:
                self.safe_execute("CREATE INDEX IF NOT EXISTS idx_tech_date ON technical_data (date)", conn=self._tech_conn)
                self.safe_execute("CREATE INDEX IF NOT EXISTS idx_tech_symbol ON technical_data (symbol)", conn=self._tech_conn)
            
            if self._inst_conn:
                self.safe_execute("CREATE INDEX IF NOT EXISTS idx_insider_sym ON insider_trades (symbol)", conn=self._inst_conn)
                self.safe_execute("CREATE INDEX IF NOT EXISTS idx_deals_sym ON large_deals (symbol)", conn=self._inst_conn)
                
            if self._meta_conn:
                self.safe_execute("CREATE INDEX IF NOT EXISTS idx_master_active ON symbols_master (in_active_universe)", conn=self._meta_conn)
                self.safe_execute("CREATE INDEX IF NOT EXISTS idx_master_sector ON symbols_master (sector)", conn=self._meta_conn)
                self.safe_execute("CREATE INDEX IF NOT EXISTS idx_master_industry ON symbols_master (industry)", conn=self._meta_conn)
        except Exception: pass
