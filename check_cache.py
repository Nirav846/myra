import sqlite3

def inspect_cache():
    try:
        conn = sqlite3.connect('myra_cache_network.db')
        cursor = conn.cursor()
        
        # Get all table names
        tables = cursor.execute("SELECT name FROM sqlite_master WHERE type='table';").fetchall()
        
        print(f"{'Table Name':<25} | {'Row Count':<10}")
        print("-" * 40)
        
        for (table_name,) in tables:
            # Count rows in each table
            count = cursor.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]
            print(f"{table_name:<25} | {count:<10}")
            
            # Optional: Print column names for each table
            columns = cursor.execute(f"PRAGMA table_info({table_name})").fetchall()
            col_names = [col[1] for col in columns]
            print(f"  └─ Columns: {', '.join(col_names)}\n")
            
        conn.close()
    except Exception as e:
        print(f"[!] Error: {e}")

if __name__ == "__main__":
    inspect_cache()