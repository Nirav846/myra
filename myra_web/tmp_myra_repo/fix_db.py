import sqlite3

conn = sqlite3.connect("db/myra_technical.db")
cur = conn.cursor()

print("🚧 Creating new table with safety constraints...")

cur.execute("""
CREATE TABLE IF NOT EXISTS technical_data_new (
    symbol TEXT,
    date TEXT,
    open REAL,
    high REAL,
    low REAL,
    close REAL,
    volume INTEGER,
    delivery REAL,
    trades REAL,
    vwap REAL,
    delivery_pct REAL,
    delivery_ratio REAL,
    delivery_qty REAL,
    stock_return REAL,
    market_return REAL,
    delivery_divergence_score REAL,
    volatility_compression_score REAL,
    relative_volume_score REAL,
    nifty_outperformance_score REAL,
    UNIQUE(symbol, date)
);
""")

print("📦 Copying data (removing duplicates automatically)...")

cur.execute("""
INSERT OR IGNORE INTO technical_data_new
SELECT * FROM technical_data;
""")

print("🗑 Dropping old table...")
cur.execute("DROP TABLE technical_data;")

print("🔁 Renaming new table...")
cur.execute("ALTER TABLE technical_data_new RENAME TO technical_data;")

print("⚡ Adding index...")
cur.execute("""
CREATE INDEX idx_symbol_date 
ON technical_data(symbol, date);
""")

conn.commit()
conn.close()

print("✅ DONE: Database is now safe")