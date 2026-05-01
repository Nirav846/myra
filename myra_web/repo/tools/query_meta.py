import sqlite3
import os
import sys


def check_meta(symbol):
    db_path = os.path.join("db", "meta.db")
    if not os.path.exists(db_path):
        print(f"[!] {db_path} missing.")
        return

    conn = sqlite3.connect(db_path)
    res = conn.execute(
        f"SELECT first_seen, in_active_universe FROM symbols_master WHERE symbol = '{symbol}'"
    ).fetchone()
    if res:
        print(f"Metadata for {symbol}:")
        print(f"  - First Seen: {res[0]}")
        print(f"  - Active Uni: {res[1]}")
    else:
        print(f"[!] {symbol} not found in meta.db")
    conn.close()


if __name__ == "__main__":
    sym = sys.argv[1] if len(sys.argv) > 1 else "RELIANCE"
    check_meta(sym)
