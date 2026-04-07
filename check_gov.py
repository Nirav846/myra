import sqlite3

conn = sqlite3.connect("db/governance.db")
cursor = conn.execute("SELECT COUNT(*) FROM sast_disclosures")
print(f"SAST Count: {cursor.fetchone()[0]}")
conn.close()
