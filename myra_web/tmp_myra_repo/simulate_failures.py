import pandas as pd
import io
import sqlite3
import os
from myra_app.utils.bhavcopy_parser import BhavcopyParser
from myra_app.schema_registry import SchemaRegistry
from myra_app.librarian_core import LibrarianCore

def test_bhavcopy_parser():
    print("--- 1. BHAVCOPY PARSER EDGE CASES ---")

    # 1.1 Different filename formats
    f1 = BhavcopyParser.detect_file_format("nse_full_2023-10-25.csv")
    f2 = BhavcopyParser.detect_file_format("nse_full_25102023.csv")
    f3 = BhavcopyParser.detect_file_format("bhavcopy25102023.csv")
    print(f"Formats detected: {f1}, {f2}, {f3}")

    # 1.2 Empty File
    df, rep = BhavcopyParser.parse_csv("")
    print(f"Empty file parsing: Rows: {rep['rows_processed']}, Errors: {rep['errors']}")

    # 1.3 Headers Only
    headers_only = "SYMBOL,SERIES,DATE1,CLOSE_PRICE,TTL_TRD_QNTY,DELIV_QTY\n"
    df, rep = BhavcopyParser.parse_csv(headers_only)
    print(f"Headers only parsing: Rows: {rep['rows_processed']}, Errors: {rep['errors']}")

    # 1.4 Missing Required Column
    missing_col = "SYMBOL,SERIES,DATE1,TTL_TRD_QNTY\nRELIANCE,EQ,2023-10-25,100"
    df, rep = BhavcopyParser.parse_csv(missing_col)
    print(f"Missing column parsing (missing CLOSE): Rows: {rep['rows_processed']}, Errors: {rep['errors']}")

    # 1.5 Header variation & Recovery
    # No date in CSV, but in filename
    recover_date = "TICKER,SERIES,CLOSE,Volume,DELIV_QTY\nRELIANCE,EQ,2500,1000,500"
    df, rep = BhavcopyParser.parse_csv(recover_date, source_filename="nse_full_2023-10-25.csv")
    print(f"Date recovery parsing: Date in DF: {df['date'].iloc[0] if not df.empty else 'Failed'}, Errors: {rep['errors']}")

def test_schema_registry():
    print("\n--- 2. SCHEMA REGISTRY VALIDATION ---")
    db_path = "test_schema.db"
    if os.path.exists(db_path): os.remove(db_path)

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    # Create with missing columns
    cursor.execute("CREATE TABLE technical_data (symbol TEXT, date TEXT)")
    conn.commit()

    print("Validating incomplete table...")
    SchemaRegistry.validate_schema(conn, "technical_data")

    cursor.execute("PRAGMA table_info(technical_data)")
    cols = [c[1] for c in cursor.fetchall()]
    print(f"Columns after auto-fix: {len(cols)}")
    print("Is 'close' present?", "close" in cols)
    conn.close()

def test_lineage():
     print("\n--- 3. LINEAGE TRACKING ---")
     db_path = os.path.join("db", LibrarianCore.DB_MAP["meta"])
     os.makedirs("db", exist_ok=True)

     lib = LibrarianCore()
     lib.record_lineage("technical_data", "test_source", 100, "SUCCESS", "none")

     if lib._meta_conn:
         res = lib._meta_conn.execute("SELECT dataset_name, source_url, rows_processed FROM lineage_tracking").fetchall()
         print(f"Lineage entry: {res}")
     lib.close()

if __name__ == "__main__":
    test_bhavcopy_parser()
    test_schema_registry()
    test_lineage()
