"""Trace ALL myra_technical.db writes across any connection."""
import sqlite3, os, time

# Patch sqlite3 globally to trace every connection
original_connect = sqlite3.connect

def traced_connect(*args, **kwargs):
    conn = original_connect(*args, **kwargs)
    if 'myra_technical.db' in str(args[0]) if args else False:
        conn.set_trace_callback(lambda stmt: print(f'[WRITE @ {os.path.basename(args[0])}] {stmt.strip()[:150]}'))
    return conn

sqlite3.connect = traced_connect

print('Global trace active. Run the app and scan now...')
print('Press Ctrl+C to stop.')
try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    sqlite3.connect = original_connect
    print('Tracing stopped.')