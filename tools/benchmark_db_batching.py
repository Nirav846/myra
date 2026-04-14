import sqlite3
import time
import os

def setup_db(db_path):
    if os.path.exists(db_path):
        os.remove(db_path)
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS technical_data (
            symbol TEXT NOT NULL,
            date TEXT NOT NULL,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            volume INTEGER,
            delivery INTEGER,
            trades INTEGER,
            vwap REAL,
            delivery_pct REAL,
            delivery_ratio REAL,
            PRIMARY KEY (symbol, date)
        )
    """)
    conn.commit()
    return conn

def simulate_current(conn, num_symbols=50, rows_per_symbol=10):
    cursor = conn.cursor()
    start_time = time.time()
    for i in range(num_symbols):
        symbol = f"SYM{i}"
        records = []
        for j in range(rows_per_symbol):
            records.append((symbol, f"2023-01-{j:02d}", 100.0, 105.0, 95.0, 102.0, 1000, None, None, 101.0, None, None))

        cursor.executemany("""
            INSERT OR IGNORE INTO technical_data
            (symbol, date, open, high, low, close, volume, delivery, trades, vwap, delivery_pct, delivery_ratio)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, records)
        conn.commit()
    end_time = time.time()
    return end_time - start_time

def simulate_optimized(conn, num_symbols=50, rows_per_symbol=10):
    cursor = conn.cursor()
    start_time = time.time()
    all_batch_records = []
    for i in range(num_symbols):
        symbol = f"SYM{i}_OPT"
        for j in range(rows_per_symbol):
            all_batch_records.append((symbol, f"2023-01-{j:02d}", 100.0, 105.0, 95.0, 102.0, 1000, None, None, 101.0, None, None))

    cursor.executemany("""
        INSERT OR IGNORE INTO technical_data
        (symbol, date, open, high, low, close, volume, delivery, trades, vwap, delivery_pct, delivery_ratio)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, all_batch_records)
    conn.commit()
    end_time = time.time()
    return end_time - start_time

def main():
    db_path = "benchmark_test.db"
    conn = setup_db(db_path)

    print("Running Baseline (Commit per symbol)...")
    baseline_time = simulate_current(conn, num_symbols=100, rows_per_symbol=20)
    print(f"Baseline Time: {baseline_time:.4f}s")

    print("\nRunning Optimized (Commit per batch)...")
    optimized_time = simulate_optimized(conn, num_symbols=100, rows_per_symbol=20)
    print(f"Optimized Time: {optimized_time:.4f}s")

    improvement = (baseline_time - optimized_time) / baseline_time * 100
    print(f"\nImprovement: {improvement:.2f}%")

    conn.close()
    if os.path.exists(db_path):
        os.remove(db_path)

if __name__ == "__main__":
    main()
