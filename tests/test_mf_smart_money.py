"""Tests for the MF Smart Money scanner (whole held universe + MoM behaviour)."""

import os
import sqlite3

import pandas as pd
import pytest

from myra_app.librarian_core import LibrarianCore
from myra_app.strategies import mf_smart_money as msm
from myra_app.strategies.mf_smart_money import MFSmartMoneyScanner

MONTH, PRIOR = "2026-08", "2026-07"
AS_OF = "2026-10-09"


# ── fixtures ────────────────────────────────────────────────────────────────


def _val_conn():
    """In-memory valuation DB with the tables the scanner reads."""
    c = sqlite3.connect(":memory:")
    c.executescript(
        """
        CREATE TABLE mf_holding (
            fund_slug TEXT, month TEXT, fincode TEXT, company TEXT,
            weight_pct REAL, shares REAL
        );
        CREATE TABLE mf_fund_aum (fund_slug TEXT, month TEXT, aum_cr REAL);
        CREATE TABLE fundamentals (
            symbol TEXT, market_cap REAL, sector TEXT, free_float_pct REAL,
            date TEXT
        );
        CREATE TABLE fund_traction (
            symbol TEXT, month TEXT, traction_score REAL, number_of_funds INTEGER,
            adds_new INTEGER, reduces_closes INTEGER, pct_vs_sma REAL,
            nse TEXT, name TEXT, direction TEXT
        );
        """
    )
    holdings = [
        # (fund, month, fincode, company, weight, shares)
        # Alpha: two funds, everything flat -> steady
        ("fund_a", PRIOR, "S1", "Alpha Industries Ltd", 2.0, 1000),
        ("fund_b", PRIOR, "S1", "Alpha Industries Ltd", 1.0, 500),
        ("fund_a", MONTH, "S1", "Alpha Industries Ltd", 2.0, 1000),
        ("fund_b", MONTH, "S1", "Alpha Industries Ltd", 1.0, 500),
        # Beta: fund_a adds, fund_b enters -> increase
        ("fund_a", PRIOR, "S2", "Beta Finance Ltd", 1.0, 1500),
        ("fund_a", MONTH, "S2", "Beta Finance Ltd", 1.0, 2000),
        ("fund_b", MONTH, "S2", "Beta Finance Ltd", 0.5, 300),
        # Gamma: sole fund trims -> decrease
        ("fund_a", PRIOR, "S3", "Gamma Ltd", 2.0, 900),
        ("fund_a", MONTH, "S3", "Gamma Ltd", 1.5, 800),
        # Zzzz: unlisted name, new this month
        ("fund_a", MONTH, "S4", "Zzzz Unlisted Pvt", 0.5, 100),
    ]
    c.executemany("INSERT INTO mf_holding VALUES (?,?,?,?,?,?)", holdings)
    c.executemany(
        "INSERT INTO mf_fund_aum VALUES (?,?,?)",
        [
            ("fund_a", MONTH, 1000.0),
            ("fund_b", MONTH, 500.0),
            ("fund_a", PRIOR, 900.0),
        ],
    )
    c.executemany(
        "INSERT INTO fundamentals VALUES (?,?,?,?,?)",
        [
            ("ALPHA", 5.0e11, "Capital Goods", 60.0, "2026-10-01"),
            ("BETA", 2.0e11, "Financials", 70.0, "2026-10-01"),
        ],
    )
    c.execute(
        "INSERT INTO fund_traction VALUES "
        "('ALPHA', ?, 42.0, 12, 3, 1, -4.0, 'ALPHA', 'Alpha Industries', 'increase')",
        (MONTH,),
    )
    c.commit()
    return c


def _tech_conn():
    c = sqlite3.connect(":memory:")
    c.execute(
        "CREATE TABLE technical_data (symbol TEXT, date TEXT, open REAL, high REAL,"
        " low REAL, close REAL, volume REAL, delivery REAL, delivery_pct REAL)"
    )

    def day(sym, date, hi, lo, close, delivery):
        return (sym, date, close, hi, lo, close, 1e5, delivery, 50.0)

    rows = [
        day("ALPHA", f"{MONTH}-05", 110, 90, 100, 1000),
        day("ALPHA", AS_OF, 101, 99, 100, 800),
        day("BETA", f"{MONTH}-05", 100, 80, 90, 500),
        day("BETA", AS_OF, 71, 69, 70, 400),
        day("GAMMA", f"{MONTH}-05", 55, 45, 50, 300),
        day("GAMMA", AS_OF, 61, 59, 60, 200),
    ]
    c.executemany("INSERT INTO technical_data VALUES (?,?,?,?,?,?,?,?,?)", rows)
    c.commit()
    return c


@pytest.fixture
def scanner_env(tmp_path, monkeypatch):
    """Point the scanner at isolated DBs; return (scan_kwargs, val_conn)."""
    monkeypatch.setattr(msm, "DB_DIR", str(tmp_path))
    meta = os.path.join(str(tmp_path), LibrarianCore.DB_MAP["meta"])
    with sqlite3.connect(meta) as mc:
        mc.execute("CREATE TABLE symbols_master (symbol TEXT, name TEXT)")
        mc.executemany(
            "INSERT INTO symbols_master VALUES (?,?)",
            [
                ("ALPHA", "Alpha Industries Ltd"),
                ("BETA", "Beta Finance Ltd"),
                ("GAMMA", "Gamma Ltd"),
            ],
        )
        mc.execute(
            "CREATE TABLE symbol_alias (alias TEXT PRIMARY KEY, canonical TEXT,"
            " source TEXT, confidence REAL, updated_at TEXT)"
        )
    return {"val_conn": _val_conn(), "tech_conn": _tech_conn()}


def _by_company(df):
    return {r["company"]: r for _, r in df.iterrows()}


# ── tests ───────────────────────────────────────────────────────────────────


def test_universe_is_every_held_stock(scanner_env):
    df = MFSmartMoneyScanner(**scanner_env).scan(as_on_date=AS_OF)
    assert len(df) == 4  # every fincode, nothing dropped
    assert set(df["company"]) == {
        "Alpha Industries Ltd",
        "Beta Finance Ltd",
        "Gamma Ltd",
        "Zzzz Unlisted Pvt",
    }


def test_steady_hold_detected(scanner_env):
    df = MFSmartMoneyScanner(**scanner_env).scan(as_on_date=AS_OF)
    alpha = _by_company(df)["Alpha Industries Ltd"]
    assert alpha["direction"] == "steady"
    assert alpha["fund_count"] == 2
    assert alpha["funds_steady"] == 2
    assert alpha["net_funds"] == 0
    assert alpha["funds_entered"] == 0 and alpha["funds_exited"] == 0
    assert alpha["stake_change_pp"] == 0.0


def test_accumulation_counts_add_and_entry(scanner_env):
    df = MFSmartMoneyScanner(**scanner_env).scan(as_on_date=AS_OF)
    beta = _by_company(df)["Beta Finance Ltd"]
    assert beta["direction"] == "increase"
    assert beta["funds_added"] == 1
    assert beta["funds_entered"] == 1  # fund_b appears
    assert beta["net_funds"] == 2


def test_trimming_and_exit_detected(scanner_env):
    df = MFSmartMoneyScanner(**scanner_env).scan(as_on_date=AS_OF)
    gamma = _by_company(df)["Gamma Ltd"]
    assert gamma["direction"] == "decrease"
    assert gamma["funds_trimmed"] == 1


def test_price_context_and_aum_footprint(scanner_env):
    df = MFSmartMoneyScanner(**scanner_env).scan(as_on_date=AS_OF)
    rows = _by_company(df)
    alpha, beta = rows["Alpha Industries Ltd"], rows["Beta Finance Ltd"]
    # DWAP = delivery-weighted typical price of the holdings month.
    assert alpha["dwap"] == pytest.approx(100.0, abs=0.01)
    assert alpha["vs_dwap_pct"] == pytest.approx(0.0, abs=0.01)
    assert beta["dwap"] == pytest.approx(90.0, abs=0.01)
    assert beta["vs_dwap_pct"] == pytest.approx(-22.22, abs=0.05)
    # aum_held_cr = sum(weight% x holder AUM / 100): 1000*2/100 + 500*1/100.
    assert alpha["aum_held_cr"] == pytest.approx(25.0, abs=0.1)
    assert alpha["market_cap_cr"] == pytest.approx(50000.0, abs=1.0)
    assert alpha["sector"] == "Capital Goods"
    assert alpha["traction_score"] == 42.0


def test_unresolved_company_is_listed_by_name(scanner_env):
    df = MFSmartMoneyScanner(**scanner_env).scan(as_on_date=AS_OF)
    zzzz = _by_company(df)["Zzzz Unlisted Pvt"]
    assert zzzz["symbol"] == ""
    assert zzzz["has_price"] is False
    # Mixed None/float columns become NaN in the DataFrame; the API layer
    # sanitises NaN -> None before serialising.
    assert pd.isna(zzzz["cmp"]) and pd.isna(zzzz["dwap"])


def test_mode_filters(scanner_env):
    steady = MFSmartMoneyScanner(mode="steady", **scanner_env).scan(as_on_date=AS_OF)
    assert list(steady["company"]) == ["Alpha Industries Ltd"]

    acc = MFSmartMoneyScanner(mode="accumulating", **scanner_env).scan(as_on_date=AS_OF)
    assert set(acc["company"]) == {"Beta Finance Ltd", "Zzzz Unlisted Pvt"}

    trimming = MFSmartMoneyScanner(mode="trimming", **scanner_env).scan(
        as_on_date=AS_OF
    )
    assert list(trimming["company"]) == ["Gamma Ltd"]


def test_bargain_mode_requires_below_cost_and_buying(scanner_env):
    df = MFSmartMoneyScanner(mode="bargain", **scanner_env).scan(as_on_date=AS_OF)
    # Only Beta is below DWAP *and* accumulating (Alpha is at cost, Zzzz has no price).
    assert list(df["company"]) == ["Beta Finance Ltd"]


def test_require_price_and_limit_and_sorting(scanner_env):
    priced = MFSmartMoneyScanner(require_price=True, **scanner_env).scan(
        as_on_date=AS_OF
    )
    assert "Zzzz Unlisted Pvt" not in set(priced["company"])

    top = MFSmartMoneyScanner(sort_by="aum_held_cr", limit=1, **scanner_env).scan(
        as_on_date=AS_OF
    )
    assert list(top["company"]) == ["Alpha Industries Ltd"]

    deep = MFSmartMoneyScanner(sort_by="vs_dwap_pct", **scanner_env).scan(
        as_on_date=AS_OF
    )
    # Ascending: the deepest discount (Beta) comes first.
    assert deep.iloc[0]["company"] == "Beta Finance Ltd"


def test_invalid_mode_and_sort_key_rejected(scanner_env):
    with pytest.raises(ValueError):
        MFSmartMoneyScanner(mode="nonsense")
    with pytest.raises(ValueError):
        MFSmartMoneyScanner(sort_by="nonsense")


def test_empty_holdings_returns_empty_frame(tmp_path, monkeypatch):
    monkeypatch.setattr(msm, "DB_DIR", str(tmp_path))
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        "CREATE TABLE mf_holding (fund_slug TEXT, month TEXT, fincode TEXT,"
        " company TEXT, weight_pct REAL, shares REAL);"
        "CREATE TABLE mf_fund_aum (fund_slug TEXT, month TEXT, aum_cr REAL);"
        "CREATE TABLE fundamentals (symbol TEXT, market_cap REAL, sector TEXT,"
        " free_float_pct REAL, date TEXT);"
        "CREATE TABLE fund_traction (symbol TEXT, month TEXT, traction_score REAL,"
        " number_of_funds INTEGER, adds_new INTEGER, reduces_closes INTEGER,"
        " pct_vs_sma REAL, nse TEXT, name TEXT, direction TEXT);"
    )
    df = MFSmartMoneyScanner(val_conn=conn, tech_conn=conn).scan(as_on_date=AS_OF)
    assert df.empty
