"""
Portfolio Database Layer for MYRA.
Manages holdings, snapshots, and transactions in myra_portfolio.db.
"""

import json
import os
import sqlite3
from datetime import datetime

from myra_app.constants import DB_DIR, PROJECT_ROOT

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
    conn.executescript(
        """
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
    """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS price_cache (
            symbol TEXT PRIMARY KEY,
            latest_close REAL,
            previous_close REAL,
            latest_date TEXT,
            updated_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS fundamental_cache (
            symbol TEXT PRIMARY KEY,
            pe REAL,
            sector TEXT,
            market_cap REAL,
            fetched_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS portfolio_meta (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """
    )
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
        conn.execute(
            """
            INSERT INTO holdings (symbol, category, net_qty, avg_price)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(symbol) DO UPDATE SET
                category=excluded.category,
                net_qty=excluded.net_qty,
                avg_price=excluded.avg_price,
                updated_at=datetime('now','localtime')
        """,
            (symbol, category, qty, price),
        )
        conn.execute(
            """
            INSERT INTO transactions (symbol, action, qty, price, notes)
            VALUES (?, 'IMPORT', ?, ?, ?)
        """,
            (symbol, qty, price, f"Imported {qty} @ {price}"),
        )
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
    conn.execute(
        """
        INSERT INTO holdings (symbol, category, net_qty, avg_price)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(symbol) DO UPDATE SET
            category=excluded.category,
            net_qty=excluded.net_qty,
            avg_price=excluded.avg_price,
            updated_at=datetime('now','localtime')
    """,
        (symbol, category, qty, avg_price),
    )
    conn.execute(
        """
        INSERT INTO transactions (symbol, action, qty, price, notes)
        VALUES (?, 'BUY', ?, ?, ?)
    """,
        (symbol, qty, avg_price, f"Added {qty} @ {avg_price}"),
    )
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
    conn.execute(
        f"UPDATE holdings SET {set_clause}, updated_at=datetime('now','localtime') WHERE symbol=?",
        values,
    )
    notes = ", ".join(f"{k}={v}" for k, v in updates.items())
    conn.execute(
        """
        INSERT INTO transactions (symbol, action, qty, price, notes)
        VALUES (?, 'UPDATE', ?, ?, ?)
    """,
        (symbol, updates.get("net_qty"), updates.get("avg_price"), notes),
    )
    conn.commit()
    conn.close()


def delete_holding(symbol):
    conn = get_connection()
    conn.execute("DELETE FROM holdings WHERE symbol=?", (symbol,))
    conn.execute(
        """
        INSERT INTO transactions (symbol, action, notes)
        VALUES (?, 'SELL', 'Full exit')
    """,
        (symbol,),
    )
    conn.commit()
    conn.close()


def record_snapshot(
    invested, current, overall_pnl, overall_pnl_pct, day_pnl, day_pnl_pct
):
    date = f"{datetime.now():%Y-%m-%d}"
    conn = get_connection()
    conn.execute(
        """
        INSERT INTO snapshots (date, total_invested, total_current, overall_pnl, overall_pnl_pct, day_pnl, day_pnl_pct)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """,
        (date, invested, current, overall_pnl, overall_pnl_pct, day_pnl, day_pnl_pct),
    )
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
            (symbol, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM transactions ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _tech_db():
    return os.path.join(DB_DIR, "myra_technical.db")


def _val_db():
    return os.path.join(DB_DIR, "myra_valuation.db")


def get_delivery_metrics(symbol):
    path = _tech_db()
    if not os.path.exists(path):
        return None
    try:
        conn = sqlite3.connect(path)
        cur = conn.execute(
            "SELECT date, delivery_qty, volume, close FROM technical_data "
            "WHERE symbol=? AND delivery_qty IS NOT NULL AND volume IS NOT NULL AND volume > 0 "
            "ORDER BY date DESC LIMIT 21",
            (symbol,),
        )
        rows = cur.fetchall()
        conn.close()
        if not rows:
            return None
        latest = rows[0]
        del_pct = (latest[1] / latest[2] * 100) if latest[2] else 0
        result = {
            "del_pct": round(del_pct, 1),
            "close": latest[3],
        }
        if len(rows) >= 6:
            avg_5d = sum((r[1] / r[2] * 100) for r in rows[:5] if r[2]) / 5
            result["avg_del_5d"] = round(avg_5d, 1)
        else:
            result["avg_del_5d"] = None
        if len(rows) >= 21:
            avg_20d = sum((r[1] / r[2] * 100) for r in rows[:20] if r[2]) / 20
            result["avg_del_20d"] = round(avg_20d, 1)
        else:
            total = sum((r[1] / r[2] * 100) for r in rows if r[2])
            n = sum(1 for r in rows if r[2])
            result["avg_del_20d"] = round(total / n, 1) if n else None
        avg_20d = result["avg_del_20d"]
        if avg_20d and avg_20d > 0:
            ratio = del_pct / avg_20d
            if ratio > 1.2:
                result["del_trend"] = "\u2191"
            elif ratio < 0.8:
                result["del_trend"] = "\u2193"
            else:
                result["del_trend"] = "\u2192"
        else:
            result["del_trend"] = "\u2014"
        return result
    except sqlite3.Error:
        return None


def get_technical_position(symbol):
    path = _tech_db()
    if not os.path.exists(path):
        return None
    try:
        conn = sqlite3.connect(path)
        cur = conn.execute(
            "SELECT close, sma_50, high_52w, low_52w FROM technical_data "
            "WHERE symbol=? AND close IS NOT NULL "
            "ORDER BY date DESC LIMIT 1",
            (symbol,),
        )
        row = cur.fetchone()
        conn.close()
        if not row:
            return None
        close, sma_50, high_52w, low_52w = row
        result = {"close": close}
        if sma_50 and sma_50 > 0:
            result["sma_50"] = sma_50
            result["vs_sma_pct"] = round((close - sma_50) / sma_50 * 100, 1)
        else:
            result["sma_50"] = None
            result["vs_sma_pct"] = None
        if high_52w and high_52w > 0:
            result["high_52w"] = high_52w
            result["vs_52w_high_pct"] = round((close - high_52w) / high_52w * 100, 1)
        else:
            result["high_52w"] = None
            result["vs_52w_high_pct"] = None
        if low_52w and low_52w > 0:
            result["low_52w"] = low_52w
            result["vs_52w_low_pct"] = round((close - low_52w) / low_52w * 100, 1)
        else:
            result["low_52w"] = None
            result["vs_52w_low_pct"] = None
        return result
    except sqlite3.Error:
        return None


def get_sector_allocation(holdings):
    sectors = {}
    total_current = sum(h.get("current", 0) for h in holdings) or 1
    for h in holdings:
        sec = h.get("sector") or "Unknown"
        if sec not in sectors:
            sectors[sec] = {"count": 0, "total_value": 0.0}
        sectors[sec]["count"] += 1
        sectors[sec]["total_value"] += h.get("current", 0)
    result = [
        {
            "sector": s,
            "count": v["count"],
            "total_value": v["total_value"],
            "weight_pct": round(v["total_value"] / total_current * 100, 1),
        }
        for s, v in sorted(sectors.items(), key=lambda x: -x[1]["total_value"])
    ]
    return result


def get_scanner_overlap(holdings):
    models_dir = os.path.join(PROJECT_ROOT, "models")
    scanner_files = {
        "Trigger": "trigger_cache.json",
        "InvisHand": "invisible_hand_cache.json",
        "FloatExh": "float_exhaustion_cache.json",
        "Wyckoff": "wyckoff_cache.json",
        "OpFinger": "operator_fingerprint_cache.json",
        "LiqFlip": "liquidity_flip_cache.json",
        "Darvas": "darvas_scan_cache.json",
        "Launchpad": "launchpad_scan_cache.json",
        "SeasDel": "seasonal_delivery_cache.json",
    }
    symbol_set = {h["symbol"] for h in holdings}
    result = {}
    for s in symbol_set:
        result[s] = {}
    for scanner_name, filename in scanner_files.items():
        filepath = os.path.join(models_dir, filename)
        if not os.path.exists(filepath):
            for s in symbol_set:
                result[s][scanner_name] = None
            continue
        try:
            with open(filepath, encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            for s in symbol_set:
                result[s][scanner_name] = None
            continue
        candidates = data.get("candidates", data.get("results", []))
        cand_map = {c.get("symbol"): c for c in candidates if c.get("symbol")}
        for s in symbol_set:
            if s in cand_map:
                result[s][scanner_name] = cand_map[s]
            else:
                result[s][scanner_name] = None
    return result


def get_delivery_alerts(holdings):
    alerts = []
    for h in holdings:
        del_metrics = get_delivery_metrics(h["symbol"])
        if not del_metrics:
            continue
        del_pct = del_metrics.get("del_pct", 0)
        avg_20d = del_metrics.get("avg_del_20d")
        if avg_20d and avg_20d > 0:
            ratio = del_pct / avg_20d
            if ratio >= 2.0:
                alerts.append(
                    {
                        "symbol": h["symbol"],
                        "alert_type": "DELIVERY SURGE",
                        "severity": "high",
                        "detail": f"Today's delivery {del_pct}% is {ratio:.1f}x the 20d avg ({avg_20d}%)",
                    }
                )
            elif ratio <= 0.5:
                alerts.append(
                    {
                        "symbol": h["symbol"],
                        "alert_type": "DELIVERY COLLAPSE",
                        "severity": "high",
                        "detail": f"Today's delivery {del_pct}% is only {ratio:.1f}x the 20d avg ({avg_20d}%)",
                    }
                )
        path = _tech_db()
        if os.path.exists(path):
            try:
                conn_tech = sqlite3.connect(path)
                rows = conn_tech.execute(
                    "SELECT date, delivery_qty, volume, close FROM technical_data "
                    "WHERE symbol=? AND delivery_qty IS NOT NULL AND volume > 0 "
                    "ORDER BY date DESC LIMIT 5",
                    (h["symbol"],),
                ).fetchall()
                conn_tech.close()
                if len(rows) >= 2:
                    recent_del = [(r[1] / r[2] * 100) if r[2] else 0 for r in rows]
                    recent_close = [r[3] for r in rows]
                    avg_recent_del = sum(recent_del) / len(recent_del)
                    price_changed = recent_close[0] - recent_close[-1]
                    if (
                        avg_recent_del > (avg_20d or 0) * 1.5
                        and price_changed < -0.01 * recent_close[-1]
                    ):
                        alerts.append(
                            {
                                "symbol": h["symbol"],
                                "alert_type": "ABSORPTION",
                                "severity": "info",
                                "detail": (
                                    f"Delivery surged ({avg_recent_del:.1f}%) while price dropped "
                                    f"{abs(price_changed)/recent_close[-1]*100:.1f}% over 5 days"
                                ),
                            }
                        )
                    elif (
                        avg_recent_del < (avg_20d or 0) * 0.5
                        and price_changed > 0.01 * recent_close[-1]
                    ):
                        alerts.append(
                            {
                                "symbol": h["symbol"],
                                "alert_type": "DISTRIBUTION",
                                "severity": "warning",
                                "detail": (
                                    f"Delivery collapsed ({avg_recent_del:.1f}%) while price rose "
                                    f"{price_changed/recent_close[-1]*100:.1f}% over 5 days"
                                ),
                            }
                        )
            except sqlite3.Error:
                pass
    return alerts


def get_concentration_risk():
    conn = get_connection()
    rows = conn.execute("SELECT symbol, net_qty, avg_price FROM holdings").fetchall()
    conn.close()
    if not rows:
        return None
    path = _tech_db()
    holdings_with_value = []
    total_value = 0
    for r in rows:
        symbol = r["symbol"]
        qty = r["net_qty"]
        if not os.path.exists(path):
            continue
        try:
            tech = sqlite3.connect(path)
            cur = tech.execute(
                "SELECT close FROM technical_data WHERE symbol=? ORDER BY date DESC LIMIT 1",
                (symbol,),
            )
            close_row = cur.fetchone()
            tech.close()
            if close_row:
                val = qty * close_row[0]
                holdings_with_value.append({"symbol": symbol, "value": val, "pct": 0})
                total_value += val
        except sqlite3.Error:
            pass
    if not holdings_with_value:
        return None
    for h in holdings_with_value:
        h["pct"] = round(h["value"] / total_value * 100, 1) if total_value else 0
    holdings_with_value.sort(key=lambda x: -x["value"])
    top3 = holdings_with_value[:3]
    top3_pct = sum(h["pct"] for h in top3)
    return {
        "top3_pct": round(top3_pct, 1),
        "top3_holdings": top3,
        "total_value": total_value,
    }


def get_drawdown_metrics():
    snapshots = get_snapshots(limit=365)
    if len(snapshots) < 2:
        return None
    values = [s["total_current"] for s in snapshots]
    dates = [s["date"] for s in snapshots]
    peak_idx = values.index(max(values))
    peak_value = values[peak_idx]
    peak_date = dates[peak_idx]
    current_value = values[0]
    drawdown = current_value - peak_value
    drawdown_pct = round(drawdown / peak_value * 100, 1) if peak_value else 0
    return {
        "peak_value": peak_value,
        "peak_date": peak_date,
        "current_value": current_value,
        "drawdown_pct": drawdown_pct,
        "drawdown_amount": drawdown,
        "days_from_peak": abs(
            (
                datetime.strptime(dates[0], "%Y-%m-%d")
                - datetime.strptime(peak_date, "%Y-%m-%d")
            ).days
        ),
    }


def get_allocation_by_mcap():
    conn = get_connection()
    rows = conn.execute("SELECT symbol, net_qty FROM holdings").fetchall()
    conn.close()
    path = _val_db()
    categories = {
        "large": {"count": 0, "value": 0.0, "pct": 0},
        "mid": {"count": 0, "value": 0.0, "pct": 0},
        "small": {"count": 0, "value": 0.0, "pct": 0},
        "unknown": {"count": 0, "value": 0.0, "pct": 0},
    }
    total_value = 0
    for r in rows:
        symbol = r["symbol"]
        qty = r["net_qty"]
        mc = None
        if os.path.exists(path):
            try:
                val_conn = sqlite3.connect(path)
                cur = val_conn.execute(
                    "SELECT market_cap FROM fundamentals WHERE symbol=?", (symbol,)
                )
                row = cur.fetchone()
                val_conn.close()
                if row:
                    mc = row[0]
            except sqlite3.Error:
                pass
        tech_path = _tech_db()
        price = 0
        if os.path.exists(tech_path):
            try:
                tech = sqlite3.connect(tech_path)
                cur = tech.execute(
                    "SELECT close FROM technical_data WHERE symbol=? ORDER BY date DESC LIMIT 1",
                    (symbol,),
                )
                p = cur.fetchone()
                tech.close()
                if p:
                    price = p[0]
            except sqlite3.Error:
                pass
        val = qty * price
        total_value += val
        if mc is not None and mc > 0:
            if mc >= 50000e7:
                categories["large"]["count"] += 1
                categories["large"]["value"] += val
            elif mc >= 5000e7:
                categories["mid"]["count"] += 1
                categories["mid"]["value"] += val
            else:
                categories["small"]["count"] += 1
                categories["small"]["value"] += val
        else:
            categories["unknown"]["count"] += 1
            categories["unknown"]["value"] += val
    for k in categories:
        categories[k]["pct"] = (
            round(categories[k]["value"] / total_value * 100, 1) if total_value else 0
        )
    return categories


def get_volatility_metrics():
    snapshots = get_snapshots(limit=30)
    if len(snapshots) < 2:
        return None
    day_pnl_values = [abs(s["day_pnl"]) for s in snapshots if s.get("day_pnl")]
    if not day_pnl_values:
        return None
    daily_vol = round(sum(day_pnl_values) / len(day_pnl_values), 1)
    max_gain = max(s.get("day_pnl", 0) for s in snapshots)
    max_loss = min(s.get("day_pnl", 0) for s in snapshots)
    returns = []
    for i in range(len(snapshots) - 1):
        curr = snapshots[i].get("total_current", 0)
        prev = snapshots[i + 1].get("total_current", 0)
        if prev:
            returns.append((curr - prev) / prev)
    if returns:
        import statistics

        daily_vol_pct = round(statistics.stdev(returns) * 100, 1)
    else:
        daily_vol_pct = 0
    gain_date = ""
    loss_date = ""
    for s in snapshots:
        if s.get("day_pnl") == max_gain:
            gain_date = s.get("date", "")
        if s.get("day_pnl") == max_loss:
            loss_date = s.get("date", "")
    return {
        "daily_vol": daily_vol,
        "daily_vol_pct": daily_vol_pct,
        "max_gain": max_gain,
        "gain_date": gain_date,
        "max_loss": max_loss,
        "loss_date": loss_date,
    }


def get_diversification_score():
    conn = get_connection()
    rows = conn.execute("SELECT symbol FROM holdings").fetchall()
    conn.close()
    total_holdings = len(rows)
    if not total_holdings:
        return {
            "score": 0,
            "rating": "Empty portfolio",
            "details": "No holdings",
            "top3_pct": 0,
        }
    path = _val_db()
    sectors = set()
    for r in rows:
        if os.path.exists(path):
            try:
                val_conn = sqlite3.connect(path)
                cur = val_conn.execute(
                    "SELECT sector FROM fundamentals WHERE symbol=?", (r["symbol"],)
                )
                row = cur.fetchone()
                val_conn.close()
                if row and row[0]:
                    sectors.add(row[0])
            except sqlite3.Error:
                pass
    num_sectors = len(sectors)
    conc = get_concentration_risk()
    top3_pct = conc["top3_pct"] if conc else 100
    score = 0
    score += min(total_holdings * 5, 30)
    score += min(num_sectors * 8, 30)
    if top3_pct < 30:
        score += 25
    elif top3_pct < 50:
        score += 15
    elif top3_pct < 70:
        score += 10
    else:
        score += 5
    if total_holdings >= 10:
        score += 10
    elif total_holdings >= 5:
        score += 5
    if num_sectors >= 5:
        score += 10
    elif num_sectors >= 3:
        score += 5
    score = min(score, 100)
    if score >= 80:
        rating = "Well diversified"
    elif score >= 60:
        rating = "Moderate"
    elif score >= 40:
        rating = "Low diversification"
    else:
        rating = "Concentrated"
    return {
        "score": score,
        "rating": rating,
        "details": f"{total_holdings} holdings across {num_sectors} sectors",
        "top3_pct": top3_pct,
    }


def _get_portfolio_meta(key: str) -> str | None:
    """Get a metadata value from portfolio_meta table."""
    db_path = get_db_path()
    if not os.path.exists(db_path):
        return None
    try:
        conn = sqlite3.connect(db_path)
        row = conn.execute(
            "SELECT value FROM portfolio_meta WHERE key=?", (key,)
        ).fetchone()
        conn.close()
        return row[0] if row else None
    except sqlite3.Error:
        return None


def auto_refresh_portfolio() -> dict:
    """
    Refresh portfolio prices and fundamentals from MYRA databases.
    Called by the background orchestrator after daily ingest completes.
    Returns {'prices_updated': N, 'fundamentals_updated': N, 'error': None}
    or {'error': str}.
    """
    db_path = get_db_path()
    if not os.path.exists(db_path):
        return {"error": "portfolio db not found"}

    holdings = get_all_holdings()
    if not holdings:
        return {"error": "no holdings in portfolio"}

    symbols = [h["symbol"] for h in holdings]
    tech_db = os.path.join(DB_DIR, "myra_technical.db")
    val_db = os.path.join(DB_DIR, "myra_valuation.db")

    prices_updated = 0
    fundamentals_updated = 0

    try:
        conn = get_connection()

        # Refresh prices from technical_data
        if os.path.exists(tech_db):
            try:
                tech_conn = sqlite3.connect(tech_db)
                for sym in symbols:
                    row = tech_conn.execute(
                        "SELECT close, date FROM technical_data WHERE symbol=? "
                        "AND close IS NOT NULL ORDER BY date DESC LIMIT 2",
                        (sym,),
                    ).fetchall()
                    if row:
                        latest_close = row[0][0]
                        latest_date = row[0][1]
                        prev_close = row[1][0] if len(row) > 1 else None
                        conn.execute(
                            """INSERT OR REPLACE INTO price_cache
                               (symbol, latest_close, previous_close, latest_date, updated_at)
                               VALUES (?, ?, ?, ?, datetime('now','localtime'))""",
                            (sym, latest_close, prev_close, latest_date),
                        )
                        prices_updated += 1
                tech_conn.close()
            except sqlite3.Error as e:
                return {"error": f"price refresh failed: {e}"}
        else:
            return {"error": "technical db not found"}

        # Refresh fundamentals from valuation db
        if os.path.exists(val_db):
            try:
                val_conn = sqlite3.connect(val_db)
                for sym in symbols:
                    row = val_conn.execute(
                        "SELECT pe, sector, market_cap FROM fundamentals WHERE symbol=?",
                        (sym,),
                    ).fetchone()
                    if row:
                        conn.execute(
                            """INSERT OR REPLACE INTO fundamental_cache
                               (symbol, pe, sector, market_cap, fetched_at)
                               VALUES (?, ?, ?, ?, datetime('now','localtime'))""",
                            (sym, row[0], row[1], row[2]),
                        )
                        fundamentals_updated += 1
                val_conn.close()
            except sqlite3.Error as e:
                conn.close()
                return {"error": f"fundamental refresh failed: {e}"}

        # Update last_refresh timestamp
        conn.execute(
            "INSERT OR REPLACE INTO portfolio_meta (key, value) VALUES (?, ?)",
            ("last_refresh", f"{datetime.now():%Y-%m-%d %H:%M}"),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        return {"error": f"portfolio refresh failed: {e}"}

    return {
        "prices_updated": prices_updated,
        "fundamentals_updated": fundamentals_updated,
        "error": None,
    }


init_db()
