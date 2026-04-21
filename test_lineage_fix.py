import sqlite3
import os
from myra_app.librarian_core import LibrarianCore
from myra_app.librarian_schema import LibrarianSchemaMixin

class TestLib(LibrarianCore, LibrarianSchemaMixin):
     pass

db_path = os.path.join("db", LibrarianCore.DB_MAP["meta"])
os.makedirs("db", exist_ok=True)
if os.path.exists(db_path): os.remove(db_path)

lib = TestLib(read_only=False)
lib._create_tables()

lib.record_lineage("technical_data", "test_source", 100, "SUCCESS", "none")
if lib._meta_conn:
    res = lib._meta_conn.execute("SELECT dataset_name, source_url, rows_processed FROM lineage_tracking").fetchall()
    print(f"Lineage entry: {res}")
lib.close()
