import sqlite3
import json
import os

try:
    conn = sqlite3.connect('myra_app/db/myra_technical.db')
    conn.row_factory = sqlite3.Row
    cursor = conn.execute("SELECT * FROM technical_data LIMIT 1")
    rows = [dict(row) for row in cursor.fetchall()]
    print(json.dumps(rows))
    conn.close()
except Exception as e:
    print(str(e))
