"""
Portfolio Database Layer for MYRA.
Manages holdings, snapshots, and transactions in myra_portfolio.db.
"""

import os
import sqlite3
from datetime import datetime

from myra_app.constants import DB_DIR

DB_NAME = "myra_portfolio.db"


def get_db_path():
    return os.path.join(DB_DIR, DB_NAME)


def get_connection():
    conn = sqlite3.connect(get_db_path())
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_connection()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS holdings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL UNIQUE,
            category TEXT DEFAULT 'NSE EQ',
            net_qty INTEGER NOT NULL,
            avg_price REAL NOT NULL,
            created_at TEXT DEFAULT (datetime('now','localtime')),
            updated_at TEXT DEFAULT (datetime('now','localtime'))
        );

        CREATE TABLE IF NOT EXISTS snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            total_invested REAL NOT NULL,
            total_current REAL NOT NULL,
            overall_pnl REAL NOT NULL,
            overall_pnl_pct REAL NOT NULL,
            day_pnl REAL NOT NULL,
            day_pnl_pct REAL NOT NULL,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        );

        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            action TEXT NOT NULL,
            qty INTEGER,
            price REAL,
            notes TEXT,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        );
    """)
    conn.commit()
    conn.close()


def import_holdings(rows):
    conn = get_connection()
    count = 0
    for row in rows:
        symbol = row["symbol"].strip().upper()
        qty = int(row["net_qty"])
        price = float(row["avg_price"])
        category = row.get("category", "NSE EQ")
        conn.execute("""
            INSERT INTO holdings (symbol, category, net_qty, avg_price)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(symbol) DO UPDATE SET
                category=excluded.category,
                net_qty=excluded.net_qty,
                avg_price=excluded.avg_price,
                updated_at=datetime('now','localtime')
        """, (symbol, category, qty, price))
        conn.execute("""
            INSERT INTO transactions (symbol, action, qty, price, notes)
            VALUES (?, 'IMPORT', ?, ?, ?)
        """, (symbol, qty, price, f"Imported {qty} @ {price}"))
        count += 1
    conn.commit()
    conn.close()
    return count


def get_all_holdings():
    conn = get_connection()
    rows = conn.execute("SELECT * FROM holdings ORDER BY symbol").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_holding(symbol):
    conn = get_connection()
    row = conn.execute("SELECT * FROM holdings WHERE symbol=?", (symbol,)).fetchone()
    conn.close()
    return dict(row) if row else None


def add_holding(symbol, qty, avg_price, category="NSE EQ"):
    conn = get_connection()
    conn.execute("""
        INSERT INTO holdings (symbol, category, net_qty, avg_price)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(symbol) DO UPDATE SET
            category=excluded.category,
            net_qty=excluded.net_qty,
            avg_price=excluded.avg_price,
            updated_at=datetime('now','localtime')
    """, (symbol, category, qty, avg_price))
    conn.execute("""
        INSERT INTO transactions (symbol, action, qty, price, notes)
        VALUES (?, 'BUY', ?, ?, ?)
    """, (symbol, qty, avg_price, f"Added {qty} @ {avg_price}"))
    conn.commit()
    conn.close()


def update_holding(symbol, **kwargs):
    fields = {k: v for k, v in kwargs.items() if v is not None}
    if not fields:
        return
    allowed = {"net_qty", "avg_price", "category"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return
    set_clause = ", ".join(f"{k}=?" for k in updates)
    values = list(updates.values()) + [symbol]
    conn = get_connection()
    conn.execute(f"UPDATE holdings SET {set_clause}, updated_at=datetime('now','localtime') WHERE symbol=?", values)
    notes = ", ".join(f"{k}={v}" for k, v in updates.items())
    conn.execute("""
        INSERT INTO transactions (symbol, action, qty, price, notes)
        VALUES (?, 'UPDATE', ?, ?, ?)
    """, (symbol, updates.get("net_qty"), updates.get("avg_price"), notes))
    conn.commit()
    conn.close()


def delete_holding(symbol):
    conn = get_connection()
    conn.execute("DELETE FROM holdings WHERE symbol=?", (symbol,))
    conn.execute("""
        INSERT INTO transactions (symbol, action, notes)
        VALUES (?, 'SELL', 'Full exit')
    """, (symbol,))
    conn.commit()
    conn.close()


def record_snapshot(invested, current, overall_pnl, overall_pnl_pct, day_pnl, day_pnl_pct):
    date = datetime.now().strftime("%Y-%m-%d")
    conn = get_connection()
    conn.execute("""
        INSERT INTO snapshots (date, total_invested, total_current, overall_pnl, overall_pnl_pct, day_pnl, day_pnl_pct)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (date, invested, current, overall_pnl, overall_pnl_pct, day_pnl, day_pnl_pct))
    conn.commit()
    conn.close()
    return date


def get_snapshots(limit=30):
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM snapshots ORDER BY date DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_transactions(symbol=None, limit=50):
    conn = get_connection()
    if symbol:
        rows = conn.execute(
            "SELECT * FROM transactions WHERE symbol=? ORDER BY created_at DESC LIMIT ?",
            (symbol, limit)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM transactions ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


init_db()
