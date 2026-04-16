import sqlite3
import argparse
import os

def inspect_cache(db_paths):
    for db_path in db_paths:
        if not os.path.exists(db_path):
            print(f"[Error] Database file not found at {db_path}")
            continue

        print(f"Inspecting database: {db_path}")
        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()

            # Get all table names
            tables = cursor.execute("SELECT name FROM sqlite_master WHERE type='table';").fetchall()
            valid_tables = [t[0] for t in tables]
            
            print(f"{'Table Name':<25} | {'Row Count':<10}")
            print("-" * 40)
            
            for table_name in valid_tables:
                # Secure execution: table_name is validated against sqlite_master list.
                # Using double-quotes for identifier escaping.
                count = cursor.execute(f'SELECT COUNT(*) FROM "{table_name}"').fetchone()[0]
                print(f"{table_name:<25} | {count:<10}")

                # Use parameterized table-valued function for PRAGMA to avoid SQL injection
                columns = cursor.execute("SELECT name FROM pragma_table_info(?)", (table_name,)).fetchall()
                col_names = [col[0] for col in columns]
                print(f"  └─ Columns: {', '.join(col_names)}\n")

            conn.close()
            print("=" * 60)
        except Exception as e:
            print(f"[!] Error inspecting {db_path}: {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Inspect SQLite cache databases.")
    parser.add_argument(
        'db_paths',
        nargs='*',
        default=['db/technical.db', 'db/valuation.db'],
        help="Paths to SQLite databases to inspect (defaults to db/technical.db and db/valuation.db)"
    )
    args = parser.parse_args()

    inspect_cache(args.db_paths)
