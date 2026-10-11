"""Integration test: FundamentalSync._merge_and_insert respects Source ownership.

While Screener.in's claim on a symbol is fresh, the Morningstar/NSE bulk merge
must not overwrite the fields Screener is authoritative for -- but must still
write the fields Screener does not own.

Uses a *per-test copy* of the valuation fixture (the ``fixture_db_dir`` session
fixture is shared and other tests snapshot it, so it must stay pristine).
"""

import os
import shutil
import sqlite3

import pytest

import myra_app.fundamental_sync as fs_module
from myra_app import source_priority as sp
from myra_app.fundamental_sync import FundamentalSync


@pytest.fixture
def writable_valuation_db(fixture_db_dir, tmp_path, monkeypatch):
    """Function-scoped copy of the valuation fixture DB."""
    src = os.path.join(fixture_db_dir, "myra_valuation.db")
    dst = tmp_path / "myra_valuation.db"
    shutil.copy2(src, dst)
    # fundamental_sync binds DB_DIR at import time; patch the module attribute.
    monkeypatch.setattr(fs_module, "DB_DIR", str(tmp_path))
    yield str(dst)


_COLS = "pe, roe, book_value, market_cap, sector, face_value, daily_volatility"

_MS = {
    "ZZGUARD": {
        "peRatio": 99.0,
        "returnOnEquity": 5.0,
        "bookValuePerShare": 1.0,
        "marketCap": 5.0,
        "sector": "MS Sector",
    }
}
_NSE = {"ZZGUARD": {"pe": 88.0, "face_value": 7.0, "daily_volatility": 1.2}}


def _row(db_path, symbol):
    with sqlite3.connect(db_path) as conn:
        return conn.execute(
            f"SELECT {_COLS} FROM fundamentals WHERE symbol = ?", (symbol,)
        ).fetchone()


def test_merge_preserves_screener_owned_fields_when_claim_is_fresh(
    writable_valuation_db,
):
    with sqlite3.connect(writable_valuation_db) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO fundamentals "
            "(symbol, pe, roe, book_value, market_cap, sector) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("ZZGUARD", 10.0, 20.0, 30.0, 100.0, "Screener Sector"),
        )
        sp.record_source_success("ZZGUARD", "screener_in", conn=conn)
        conn.commit()

    FundamentalSync()._merge_and_insert(_MS, _NSE, "2026-01-01")

    pe, roe, book_value, market_cap, sector, face_value, daily_vol = _row(
        writable_valuation_db, "ZZGUARD"
    )
    # Screener-owned -> untouched by Morningstar/NSE.
    assert pe == 10.0
    assert roe == 20.0
    assert book_value == 30.0
    assert market_cap == 100.0
    assert sector == "Screener Sector"
    # Not Screener-owned -> the bulk sources fill them.
    assert face_value == 7.0
    assert daily_vol == 1.2


def test_merge_overwrites_when_no_screener_claim(writable_valuation_db):
    with sqlite3.connect(writable_valuation_db) as conn:
        # Distinct symbol with no freshness claim: the guard must lift entirely.
        conn.execute(
            "INSERT OR REPLACE INTO fundamentals "
            "(symbol, public_holding_pct) VALUES (?, ?)",
            ("ZZGUARD_NOCLAIM", 55.0),
        )
        conn.commit()

    ms = {"ZZGUARD_NOCLAIM": {"peRatio": 77.0, "sector": "MS Sector"}}
    FundamentalSync()._merge_and_insert(ms, {}, "2026-01-01")

    with sqlite3.connect(writable_valuation_db) as conn:
        row = conn.execute(
            "SELECT pe, sector FROM fundamentals WHERE symbol = ?",
            ("ZZGUARD_NOCLAIM",),
        ).fetchone()
    # With no fresh Screener claim, the weaker source is allowed through.
    assert row[0] == 77.0
    assert row[1] == "MS Sector"
