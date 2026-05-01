import sqlite3

conn = sqlite3.connect("db/myra_technical.db")
cursor = conn.cursor()

# Tables
cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
tables = [t[0] for t in cursor.fetchall()]

print("\n=== TABLES ===")
for t in tables:
    print(t)

# Schema
for table in tables:
    print(f"\n=== SCHEMA: {table} ===")
    cursor.execute(f"PRAGMA table_info({table});")
    for col in cursor.fetchall():
        print(f"{col[1]} ({col[2]})")

# Indexes
for table in tables:
    print(f"\n=== INDEXES: {table} ===")
    cursor.execute(f"PRAGMA index_list({table});")
    indexes = cursor.fetchall()
    
    if not indexes:
        print("No indexes")
    else:
        for idx in indexes:
            print(idx)

conn.close()