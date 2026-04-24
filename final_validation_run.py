import os
import io
import sqlite3
import pandas as pd
from myra_app.utils.bhavcopy_parser import BhavcopyParser
from myra_app.schema_registry import SchemaRegistry
from myra_app.librarian_core import LibrarianCore
from myra_app.librarian_schema import LibrarianSchemaMixin
from myra_app.librarian_intelligence import LibrarianIntelligenceMixin
import logging

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

def test_bhavcopy():
    print("\n" + "="*50)
    print("1. BHAVCOPY STRESS TEST & 2. FAILURE SIMULATION")
    print("="*50)

    # 1. Filename Formats
    print("\n--- Filename Format Detection ---")
    f1 = BhavcopyParser.detect_file_format("nse_full_2023-10-25.csv")
    f2 = BhavcopyParser.detect_file_format("nse_full_25102023.csv")
    f3 = BhavcopyParser.detect_file_format("bhavcopy25102023.csv")
    print(f"Format 1 (YYYY-MM-DD): {f1}")
    print(f"Format 2 (DDMMYYYY): {f2}")
    print(f"Format 3 (bhavcopyDDMMYYYY): {f3}")

    # 2. Header Variations
    print("\n--- Header Variations ---")
    h1 = "SYMBOL,SERIES,DATE1,OPEN_PRICE,HIGH_PRICE,LOW_PRICE,CLOSE_PRICE,TTL_TRD_QNTY,DELIV_QTY,DELIV_PER\nRELIANCE,EQ,2023-10-25,100,110,90,105,1000,500,50"
    h2 = "Symbol,Series,Date,Open,High,Low,Close,Volume,Delivery Quantity,Delivery Percentage\nTCS,EQ,2023-10-25,200,210,190,205,2000,1000,50"
    h3 = "TICKER,EQ,TIMESTAMP,OPEN,HIGH,LOW,CLOSE,TOTTRDQTY,Deliverable Volume,% Dly Qt to Traded Qty\nINFY,EQ,2023-10-25,300,310,290,305,3000,1500,50"

    df1, r1 = BhavcopyParser.parse_csv(h1)
    df2, r2 = BhavcopyParser.parse_csv(h2)
    df3, r3 = BhavcopyParser.parse_csv(h3)

    print(f"H1 Parsed shape: {df1.shape}, Canonical cols: {list(df1.columns)[:3]}")
    print(f"H2 Parsed shape: {df2.shape}, Canonical cols: {list(df2.columns)[:3]}")
    print(f"H3 Parsed shape: {df3.shape}, Canonical cols: {list(df3.columns)[:3]}")

    # 3. Missing Columns
    print("\n--- Missing Columns ---")
    bad_csv = "SYMBOL,SERIES,DATE1\nRELIANCE,EQ,2023-10-25"
    df_bad, r_bad = BhavcopyParser.parse_csv(bad_csv)
    print(f"Parsed shape: {df_bad.shape}")
    print(f"Report: {r_bad}")

    # 4. Corrupted Rows & Uneven Columns (Ragged CSV)
    print("\n--- Corrupted / Ragged Rows ---")
    ragged_csv = "SYMBOL,SERIES,DATE1,CLOSE_PRICE,TTL_TRD_QNTY,DELIV_QTY\nRELIANCE,EQ,2023-10-25,100,1000,500\nBADROW,EQ,100\nTCS,EQ,2023-10-25,200,2000,1000"
    df_ragged, r_ragged = BhavcopyParser.parse_csv(ragged_csv)
    print(f"Ragged Parsed rows: {len(df_ragged)}, Skipped: {r_ragged['rows_skipped']}")

    # 5. Empty File
    print("\n--- Empty File ---")
    df_empty, r_empty = BhavcopyParser.parse_csv("")
    print(f"Empty File Report: {r_empty}")

def test_schema():
    print("\n" + "="*50)
    print("3. SCHEMA VALIDATION TEST")
    print("="*50)

    db_path = "test_schema.db"
    if os.path.exists(db_path): os.remove(db_path)
    conn = sqlite3.connect(db_path)

    # Create with missing columns and wrong type
    conn.execute("CREATE TABLE technical_data (symbol TEXT, date TEXT, volume TEXT)")
    conn.commit()

    print("Running validation...")
    SchemaRegistry.validate_schema(conn, "technical_data")

    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(technical_data)")
    cols = [(c[1], c[2]) for c in cursor.fetchall()]
    print("\nFinal Table Schema:")
    for c in cols:
        print(f" - {c[0]}: {c[1]}")
    conn.close()

def test_indicator_sync():
    print("\n" + "="*50)
    print("4. INDICATOR SYNC TEST")
    print("="*50)
    print("Simulating older Parquet lake vs newer DB...")

    # We mock this via the logic inside LibrarianIntelligence
    class DummyLoader:
        class DummyInds:
            def load_indicators(self, path, sym):
                # Return old data
                df = pd.DataFrame({"rsi": [50]}, index=pd.DatetimeIndex(["2023-01-01"], name="date"))
                return df
        indicators = DummyInds()

    class TestIntelligence(LibrarianIntelligenceMixin):
        def __init__(self):
            self.loader = DummyLoader()
            # Mock the DB connection to return a newer date
            self._tech_conn = sqlite3.connect(":memory:")
            self._tech_conn.execute("CREATE TABLE technical_data (date TEXT)")
            self._tech_conn.execute("INSERT INTO technical_data VALUES ('2023-12-01')")

        def get_active_universe(self):
            return ["RELIANCE"]

        def safe_execute(self, sql, conn):
            return conn.execute(sql)

    test_intel = TestIntelligence()
    df = test_intel.precompute_indicators()
    print("\nReturned DataFrame from precompute:")
    print(df)


def test_lineage():
    print("\n" + "="*50)
    print("5. LINEAGE TABLE OUTPUT")
    print("="*50)

    class TestLib(LibrarianCore, LibrarianSchemaMixin):
        pass

    db_path = os.path.join("db", LibrarianCore.DB_MAP["meta"])
    os.makedirs("db", exist_ok=True)
    if os.path.exists(db_path): os.remove(db_path)

    lib = TestLib(read_only=False)
    lib._create_tables()

    lib.record_lineage("technical_data", "https://nseindia.com/api/bhavcopy", 1850, "SUCCESS", "BhavcopyParser, dedupe")

    res = lib._meta_conn.execute("SELECT * FROM lineage_tracking").fetchall()
    cols = [d[0] for d in lib._meta_conn.execute("PRAGMA table_info(lineage_tracking)").fetchall()]

    print("\nLineage Entry:")
    for row in res:
        for i, val in enumerate(row):
            print(f"  {cols[i]}: {val}")
    lib.close()

if __name__ == "__main__":
    test_bhavcopy()
    test_schema()
    test_indicator_sync()
    test_lineage()
