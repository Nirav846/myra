"""
Compute z>6 discontinuity events list for the backtest engine (Phase 0 Q2).

This is the canonical source for `.agent/cache/discontinuity_events.pkl`,
which `myra_app/backtest_engine.py` loads to apply ±5 trading-day blackout
windows for symbols with unexplained price discontinuities.

Methodology
-----------
1. For each (symbol, date), compute the rolling 60-day mean/std of close.
2. Compute z-score = |close - rolling_mean| / rolling_std.
3. Flag rows with z > 6.
4. Cross-check against `corporate_actions` ±7 days for the same symbol.
5. Keep ONLY rows that have NO corporate-action match (i.e. unexplained).

Re-run this script whenever you change the methodology, or to refresh the
cache after a new corporate-action sync. The cache is keyed only on this
file's behavior — no DB version stamp — so deletion + re-run is the safe
operation.
"""
from __future__ import annotations

import os
import pickle
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from myra_app.constants import DB_DIR  # noqa: E402
from myra_app.librarian_core import LibrarianCore  # noqa: E402


TECH_DB = os.path.join(DB_DIR, LibrarianCore.DB_MAP["technical"])
INST_DB = os.path.join(DB_DIR, LibrarianCore.DB_MAP["institutional"])

CACHE_DIR = Path(__file__).resolve().parents[1] / ".agent" / "cache"
CACHE_FILE = CACHE_DIR / "discontinuity_events.pkl"

ROLLING_WINDOW = 60
Z_THRESHOLD = 6.0
CA_WINDOW_DAYS = 7  # corporate action ±7 days
DATE_MIN = "2015-01-01"
DATE_MAX = "2026-09-04"


def _fetch_tech() -> pd.DataFrame:
    conn = sqlite3.connect(TECH_DB)
    df = pd.read_sql(
        "SELECT symbol, date, close FROM technical_data "
        "WHERE date BETWEEN ? AND ? ORDER BY symbol, date",
        conn,
        params=(DATE_MIN, DATE_MAX),
    )
    conn.close()
    return df


def _fetch_corp_actions() -> pd.DataFrame:
    if not os.path.exists(INST_DB):
        return pd.DataFrame(columns=["symbol", "date"])
    conn = sqlite3.connect(INST_DB)
    df = pd.read_sql(
        "SELECT symbol, date FROM corporate_actions WHERE date IS NOT NULL",
        conn,
    )
    conn.close()
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date"])
    return df


def compute_z_scores(tech: pd.DataFrame) -> pd.DataFrame:
    """Per-symbol rolling z-score of close vs rolling 60d mean/std."""
    tech = tech.copy()
    tech["date"] = pd.to_datetime(tech["date"])
    tech = tech.sort_values(["symbol", "date"]).reset_index(drop=True)

    def _per_symbol(g: pd.DataFrame) -> pd.DataFrame:
        g = g.sort_values("date")
        roll_mean = g["close"].rolling(ROLLING_WINDOW, min_periods=20).mean()
        roll_std = g["close"].rolling(ROLLING_WINDOW, min_periods=20).std()
        roll_std = roll_std.replace(0, np.nan)
        g["z"] = (g["close"] - roll_mean) / roll_std
        return g

    out: list[pd.DataFrame] = []
    for _, g in tech.groupby("symbol", sort=False):
        out.append(_per_symbol(g))  # noqa: PG-APPEND
    return pd.concat(out, ignore_index=True)


def flag_unmatched_events(zdf: pd.DataFrame, ca: pd.DataFrame) -> pd.DataFrame:
    if zdf.empty:
        return zdf
    flagged = zdf[zdf["z"].abs() > Z_THRESHOLD].copy()
    if flagged.empty:
        return flagged
    if ca.empty:
        flagged["ca_match"] = False
        return flagged[["symbol", "date", "close", "z"]]

    ca["ca_date"] = pd.to_datetime(ca["date"])
    ca_dates_by_sym: dict[str, pd.DatetimeIndex] = {}
    for sym, g in ca.groupby("symbol"):
        ca_dates_by_sym[sym] = pd.DatetimeIndex(g["ca_date"].tolist())

    def _near(sym: str, d: pd.Timestamp) -> bool:
        idx = ca_dates_by_sym.get(sym)
        if idx is None or len(idx) == 0:
            return False
        deltas = np.abs((idx - d).days)
        return bool((deltas <= CA_WINDOW_DAYS).any())

    flagged["date"] = pd.to_datetime(flagged["date"])
    flagged["ca_match"] = [
        _near(s, d) for s, d in zip(flagged["symbol"], flagged["date"])
    ]
    return flagged[~flagged["ca_match"]][["symbol", "date", "close", "z"]]


def main():
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    print(f"[discontinuity] loading technical data from {TECH_DB} ...")
    tech = _fetch_tech()
    print(f"[discontinuity] {len(tech):,} technical rows")

    print("[discontinuity] computing rolling z-scores ...")
    zdf = compute_z_scores(tech)

    print("[discontinuity] loading corporate actions ...")
    ca = _fetch_corp_actions()
    print(f"[discontinuity] {len(ca):,} corporate-action rows")

    print("[discontinuity] flagging unmatched events ...")
    events = flag_unmatched_events(zdf, ca)
    print(f"[discontinuity] {len(events):,} z>6 events without CA match")

    with open(CACHE_FILE, "wb") as f:
        pickle.dump(events, f)
    print(f"[discontinuity] cached to {CACHE_FILE}")


if __name__ == "__main__":
    main()
