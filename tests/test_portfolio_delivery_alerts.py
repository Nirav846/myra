"""Regression tests for delivery alerts over a production-faithful schema.

``technical_data`` has two delivery columns: ``delivery`` (the delivery
quantity, populated for every row) and ``delivery_qty`` (a legacy duplicate that
is **never** populated — 0 of 8,009,058 rows in the live database). The generic
fixture seeder fills every column, so a fixture can never reproduce a
"column is never populated in production" defect; these tests build a small
database that mirrors production instead.
"""

import sqlite3

import myra_app.portfolio_db as portfolio_db


def _write_technical_db(path):
    """`technical_data` with production's shape: delivery set, delivery_qty NULL."""
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            "CREATE TABLE technical_data ("
            "symbol TEXT, date TEXT, close REAL, volume INTEGER, "
            "delivery REAL, delivery_pct REAL, delivery_qty REAL)"
        )
        rows = []
        for i in range(21):
            # i == 0 is the most recent session.
            del_pct = 80.0 if i < 5 else 20.0
            volume = 1000
            rows.append(
                (
                    "TESTSYM01",
                    f"2025-01-{21 - i:02d}",
                    100.0 + i,  # price falls over the recent 5 sessions
                    volume,
                    del_pct / 100.0 * volume,  # `delivery` is the quantity
                    del_pct,
                    None,  # `delivery_qty` is never populated
                )
            )
        conn.executemany("INSERT INTO technical_data VALUES (?,?,?,?,?,?,?)", rows)
        conn.commit()
    finally:
        conn.close()


def test_delivery_alerts_read_the_populated_delivery_column(tmp_path, monkeypatch):
    """A NULL `delivery_qty` must not crash alert generation.

    The alert query used to divide ``delivery_qty`` (always NULL) by volume,
    raising ``TypeError`` — which ``except sqlite3.Error`` does not catch, so the
    portfolio CLI crashed and the API route silently dropped every alert.
    """
    db = tmp_path / "myra_technical.db"
    _write_technical_db(db)
    monkeypatch.setattr(portfolio_db, "_tech_db", lambda: str(db))

    alerts = portfolio_db.get_delivery_alerts([{"symbol": "TESTSYM01"}])

    kinds = {a["alert_type"] for a in alerts}
    assert "ABSORPTION" in kinds, alerts
    assert "DELIVERY SURGE" in kinds, alerts
    # The detail strings are derived from `delivery`, so they must show real
    # percentages rather than 0.0 (which is what a None/0 fallback yields).
    absorption = next(a for a in alerts if a["alert_type"] == "ABSORPTION")
    assert "80.0%" in absorption["detail"], absorption["detail"]
