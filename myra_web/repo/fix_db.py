import os

import duckdb
import polars as pl
from myra_app.feature_enrichment import enrich_features
from myra_app.librarian_core import LibrarianCore

# 1. Connect
con = duckdb.connect(os.path.join("db", LibrarianCore.DB_MAP["technical"]))

# 2. Load Data
print("Reading data from technical_data...")
df = con.execute("SELECT * FROM technical_data").pl()

# 3. Get Nifty Data (Using a flexible search for the name)
nifty_df = con.execute(
    "SELECT date, close FROM technical_data WHERE symbol LIKE '%NIFTY 50%'"
).pl()

if nifty_df.is_empty():
    print("⚠️ Warning: Nifty 50 data not found. RS score might be empty.")

# 4. Enrich
print("Running Institutional Enrichment (Vectorized Polars)...")
enriched_df = enrich_features(df, nifty_df)

# 5. Write back to DuckDB
con.execute("CREATE OR REPLACE TABLE technical_data AS SELECT * FROM enriched_df")

print("🚀 Success! Institutional scores are now in the database.")
print(con.execute("PRAGMA table_info('technical_data')").pl())
