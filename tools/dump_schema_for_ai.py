# tools/dump_schema_for_ai.py
"""
Run this and paste the output into AI Studio before any DB task.
Gives the AI the live schema, not stale docs.
"""
import sqlite3
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from myra_app.constants import DB_DIR
from myra_app.librarian_core import LibrarianCore

TABLES_OF_INTEREST = {
    "meta":        ["symbols_master", "index_constituents", "etf_blocklist", "metadata"],
    "technical":   ["technical_data"],
    "valuation":   ["fundamentals"],
    "institutional": ["insider_trades", "large_deals"],
}

def dump():
    print("# MYRA Live Schema Dump — paste into AI Studio\n")
    for db_key, tables in TABLES_OF_INTEREST.items():
        db_file = os.path.join(DB_DIR, LibrarianCore.DB_MAP[db_key])
        print(f"## {db_key} ({os.path.basename(db_file)})")
        conn = sqlite3.connect(db_file)
        for table in tables:
            try:
                cols = conn.execute(f"PRAGMA table_info('{table}')").fetchall()
                row_count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                print(f"\n### {table} ({row_count:,} rows)")
                for col in cols:
                    pk = " PK" if col[5] else ""
                    default = f" DEFAULT {col[4]}" if col[4] is not None else ""
                    print(f"  {col[1]} {col[2]}{default}{pk}")
            except Exception as e:
                print(f"  [skip] {table}: {e}")
        conn.close()
        print()

if __name__ == "__main__":
    dump()