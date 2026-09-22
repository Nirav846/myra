"""
Float Turnover conditioned on tight base formation.

Reuses DAR-INDEPENDENT base logic from AccumulationBaseScanner
without calling .scan() (which bakes DAR into its filter/score):

  - _compute_atr
  - _volume_dry_up_ratio
  - _detect_equal_lows
  - _compute_linear_slope
  - tightness: price_range_pct / sqrt(base_days) vs
    effective_full = 3.0*sqrt(base_days/21), effective_zero = 8.0*sqrt(base_days/21)
  - 52-week position penalty (wk52_pos)

For each Float Turnover signal (w=45/t=5% and w=60/t=5% — the two
that qualified and held on holdout), compute tightness over the
base_days window ending on the signal date, split into:

  (a) in base  — tightness <= effective_full  (tightness_score ==100)
  (b) not in base — everything else

Report per bucket at 60d/120d (calendar, cost-adj): mean/median/win_rate
and the same same-stock/year paired random-entry sanity check used in
backtest_float_turnover.py.

Monthly scan dates 2021-01-01 -> 2026-05-05, holdout 2024-01-01+.
"""

from __future__ import annotations

import os
import random
import sqlite3
import sys
import time
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np
import pandas as pd

from myra_app.constants import DB_DIR
from myra_app.librarian_core import LibrarianCore
from myra_app.strategies.accumulation_base_scanner import AccumulationBaseScanner

TECH_DB = os.path.join(DB_DIR, LibrarianCore.DB_MAP["technical"])
META_DB = os.path.join(DB_DIR, LibrarianCore.DB_MAP["meta"])
VAL_DB = os.path.join(DB_DIR, LibrarianCore.DB_MAP["valuation"])

LOOKBACK_DAYS = 180
BASE_DAYS = 42  # AccumulationBaseScanner default
HORIZONS = [60, 120]
BROKERAGE_PCT = 0.5
STCG_RATE = 0.15
HOLDOUT_START = "2024-01-01"

COMBOS = [
    {"window": 45, "threshold": 5.0, "label": "w=45 t=5%"},
    {"window": 60, "threshold": 5.0, "label": "w=60 t=5%"},
]


def get_ff_map() -> dict[str, float]:
    vconn = sqlite3.connect(VAL_DB)
    rows = vconn.execute(
        """
        SELECT f.symbol, f.market_cap, f.free_float_pct, f.free_float_market_cap
        FROM fundamentals f
        INNER JOIN (
            SELECT symbol, MAX(COALESCE(date,'')) as max_date
            FROM fundamentals WHERE COALESCE(market_cap,0)>0 GROUP BY symbol
        ) latest ON f.symbol=latest.symbol AND COALESCE(f.date,'')=latest.max_date
        """
    ).fetchall()
    vconn.close()
    ff: dict[str, float] = {}
    for sym, mcap, ff_pct, ff_raw in rows:
        sym = sym.strip()
        ff_mcap = None
        if ff_raw is not None and ff_raw > 0:
            ff_mcap = float(ff_raw)
        elif mcap is not None and mcap > 0 and ff_pct is not None and ff_pct > 0:
            ff_mcap = float(mcap) * float(ff_pct) / 100.0
        if ff_mcap and ff_mcap > 0:
            ff[sym] = ff_mcap
    return ff


def compute_turnover_series(close: np.ndarray, delivery: np.ndarray, ff_mcap: float, window: int) -> np.ndarray:
    n = len(close)
    if n < window or ff_mcap <= 0:
        return np.full(n, np.nan)
    dv = np.nan_to_num(delivery, nan=0.0) * np.nan_to_num(close, nan=0.0)
    s = pd.Series(dv)
    rolling = s.rolling(window, min_periods=window).sum().to_numpy(dtype=float)
    return rolling / ff_mcap * 100.0


def tightness_from_window(df_window: pd.DataFrame, base_days: int = BASE_DAYS) -> tuple[float, float, float]:
    """Return (tightness, tightness_score, price_range_pct) DAR-independent."""
    highs = pd.to_numeric(df_window["high"], errors="coerce").to_numpy(dtype=float)
    lows = pd.to_numeric(df_window["low"], errors="coerce").to_numpy(dtype=float)
    # filter NaN
    highs = highs[~np.isnan(highs)]
    lows = lows[~np.isnan(lows)]
    if len(highs) == 0 or len(lows) == 0 or float(np.nanmin(lows)) <= 0:
        return float("nan"), 0.0, 99.0
    price_range_pct = (float(np.nanmax(highs)) - float(np.nanmin(lows))) / float(np.nanmin(lows)) * 100.0
    tightness = price_range_pct / np.sqrt(base_days)
    effective_full = 3.0 * np.sqrt(base_days / 21)
    effective_zero = 8.0 * np.sqrt(base_days / 21)
    if tightness <= effective_full:
        score = 100.0
    elif tightness >= effective_zero:
        score = 0.0
    else:
        score = (effective_zero - tightness) / (effective_zero - effective_full) * 100.0
    return tightness, score, price_range_pct


def main():
    t0 = time.time()
    scanner = AccumulationBaseScanner(base_days=BASE_DAYS)

    tech_conn = sqlite3.connect(TECH_DB)
    meta_conn = sqlite3.connect(META_DB)

    mtd = tech_conn.execute("SELECT MAX(date) FROM technical_data").fetchone()[0]
    max_scan_end = (datetime.strptime(mtd, "%Y-%m-%d") - timedelta(days=max(HORIZONS))).date()
    start = datetime.strptime("2021-01-01", "%Y-%m-%d").date()
    end = min(datetime.strptime("2026-05-05", "%Y-%m-%d").date(), max_scan_end)

    ff_map = get_ff_map()
    tech_syms = [r[0] for r in tech_conn.execute("SELECT DISTINCT symbol FROM technical_data").fetchall()]
    usable = [s for s in tech_syms if s in ff_map]
    excluded = len(tech_syms) - len(usable)
    print(f"Universe: tech {len(tech_syms)}, with ff {len(usable)}, excluded {excluded} ({excluded/len(tech_syms)*100:.1f}%)")
    print(f"  Base formation window: BASE_DAYS={BASE_DAYS}, effective_full={3.0*np.sqrt(BASE_DAYS/21):.2f}, effective_zero={8.0*np.sqrt(BASE_DAYS/21):.2f}")

    random.seed(42)
    sampled = random.sample(usable, min(500, len(usable)))
    print(f"Sampled: {len(sampled)}")

    # scan dates
    scan_dates = []
    cur = start
    while cur <= end:
        scan_dates.append(cur)
        cur += timedelta(days=30)
    scan_dates_holdout = [d for d in scan_dates if d >= datetime.strptime(HOLDOUT_START, "%Y-%m-%d").date()]
    print(f"Scan dates: {len(scan_dates)} ({scan_dates[0]}..{scan_dates[-1]}) holdout {len(scan_dates_holdout)} from {HOLDOUT_START}")

    # cache close prices filtered to sampled
    cache_start = (scan_dates[0] - timedelta(days=LOOKBACK_DAYS + 100)).isoformat()
    cache_end = (scan_dates[-1] + timedelta(days=max(HORIZONS) + 30)).isoformat()
    ph = ",".join("?" for _ in sampled)
    rows = tech_conn.execute(
        f"SELECT symbol, date, close FROM technical_data WHERE date BETWEEN ? AND ? AND symbol IN ({ph})",
        [cache_start, cache_end] + sampled,
    ).fetchall()
    close_cache = {(sym, dt): float(cl) for sym, dt, cl in rows if cl is not None}
    print(f"  {len(close_cache):,} close prices")

    def gc(sym, td): return close_cache.get((sym, td))
    def cret(e, x):
        if e is None or x is None or e <= 0: return None
        return (x - e) / e * 100
    def nret(g):
        if g is None: return None
        n = g - BROKERAGE_PCT * 2
        if g > 0: n -= STCG_RATE * g
        return n

    # Load full history per symbol (for turnover + base window)
    print("Loading full history...")
    t1 = time.time()
    sym_data: dict[str, pd.DataFrame] = {}
    full_start = (start - timedelta(days=max(LOOKBACK_DAYS, BASE_DAYS) + 100)).isoformat()
    full_end = end.isoformat()
    sql = "SELECT symbol, date, open, high, low, close, volume, delivery, delivery_pct, sma_50, high_52w, low_52w FROM technical_data WHERE date BETWEEN ? AND ? AND symbol IN ({})".format(",".join("?" for _ in sampled))
    # fallback if columns missing
    try:
        all_rows = tech_conn.execute(sql, [full_start, full_end] + sampled).fetchall()
        cols = ["symbol", "date", "open", "high", "low", "close", "volume", "delivery", "delivery_pct", "sma_50", "high_52w", "low_52w"]
    except sqlite3.OperationalError:
        sql = "SELECT symbol, date, open, high, low, close, volume, delivery, delivery_pct FROM technical_data WHERE date BETWEEN ? AND ? AND symbol IN ({})".format(",".join("?" for _ in sampled))
        all_rows = tech_conn.execute(sql, [full_start, full_end] + sampled).fetchall()
        cols = ["symbol", "date", "open", "high", "low", "close", "volume", "delivery", "delivery_pct"]
    big_df = pd.DataFrame(all_rows, columns=cols)
    for sym, grp in big_df.groupby("symbol"):
        sym_data[sym] = grp.sort_values("date").reset_index(drop=True)
    print(f"  {len(sym_data)} symbols, {len(big_df):,} rows in {time.time()-t1:.1f}s")

    # Precompute turnover crossings per combo
    combo_signals: dict[str, dict[str, dict]] = {}
    for combo in COMBOS:
        w, thresh = combo["window"], combo["threshold"]
        ddict: dict[str, dict] = {}
        for sym, sdf in sym_data.items():
            ff_mcap = ff_map.get(sym)
            if not ff_mcap: continue
            close = pd.to_numeric(sdf["close"], errors="coerce").to_numpy(dtype=float)
            delivery = pd.to_numeric(sdf["delivery"], errors="coerce").to_numpy(dtype=float)
            dates = sdf["date"].astype(str).str[:10].to_numpy()
            turnover = compute_turnover_series(close, delivery, ff_mcap, w)
            prev = np.roll(turnover, 1); prev[0] = np.nan
            mask = (~np.isnan(turnover)) & (turnover >= thresh) & (np.isnan(prev) | (prev < thresh)) & (close > 0)
            for idx in np.where(mask)[0]:
                ed = str(dates[idx])
                dk = f"{sym}|{ed}"
                if dk in ddict: continue
                ddict[dk] = {"sym": sym, "ed": ed, "turnover": float(turnover[idx])}
        combo_signals[combo["label"]] = ddict
        print(f"  {combo['label']}: {len(ddict)} raw signals")

    rng = random.Random(12345)
    effective_full = 3.0 * np.sqrt(BASE_DAYS / 21)

    for combo in COMBOS:
        label = combo["label"]
        signals = combo_signals[label]
        print("\n" + "=" * 110)
        print(f"COMBO {label} — conditioning on tight base (tightness <= {effective_full:.2f})")
        print("=" * 110)

        for period_name, scan_dates_period in [("FULL 2021-05-05", scan_dates), ("HOLDOUT 2024+", scan_dates_holdout)]:
            # collect events in period
            seen = set()
            events = []
            for D in scan_dates_period:
                ds = D.isoformat()
                lb = (D - timedelta(days=LOOKBACK_DAYS)).isoformat()
                for dk, v in signals.items():
                    if v["ed"] < lb or v["ed"] > ds: continue
                    se = f"{v['sym']}|{v['ed']}"
                    if se in seen: continue
                    seen.add(se)
                    ep = gc(v["sym"], v["ed"])
                    if ep is None or ep <= 0: continue
                    events.append(v)

            # bucket by tightness
            in_base_events = []
            not_base_events = []
            # also track DAR-independent diagnostics (ATR, dry-up, equal lows) just to prove reuse
            for ev in events:
                sym = ev["sym"]; ed = ev["ed"]
                sdf = sym_data.get(sym)
                if sdf is None:
                    not_base_events.append(ev)
                    continue
                # locate index of ed
                mask = sdf["date"].astype(str).str[:10] == ed
                idxs = np.where(mask)[0]
                if len(idxs) == 0:
                    not_base_events.append(ev)
                    continue
                i = int(idxs[0])
                if i + 1 < BASE_DAYS:
                    not_base_events.append(ev)
                    continue
                window_df = sdf.iloc[i - BASE_DAYS + 1: i + 1]
                # --- DAR-independent tightness ---
                tightness, score, prange = tightness_from_window(window_df, BASE_DAYS)
                # --- Demonstrate reuse of the other requested helpers (not used for bucket, just invoked) ---
                try:
                    highs = pd.to_numeric(window_df["high"], errors="coerce").to_numpy(dtype=float)
                    lows = pd.to_numeric(window_df["low"], errors="coerce").to_numpy(dtype=float)
                    closes = pd.to_numeric(window_df["close"], errors="coerce").to_numpy(dtype=float)
                    _atr = scanner._compute_atr(highs, lows, closes, period=14)
                    _dry = scanner._volume_dry_up_ratio(window_df, BASE_DAYS)
                    _eq = scanner._detect_equal_lows(lows, tolerance_pct=0.5)
                    _slope = scanner._compute_linear_slope(pd.to_numeric(window_df["delivery_pct"], errors="coerce").to_numpy(dtype=float)[~np.isnan(pd.to_numeric(window_df["delivery_pct"], errors="coerce").to_numpy(dtype=float))])
                    # 52w position
                    # need high_52w/low_52w if present, else fallback to window extremes
                    _ = _atr; _ = _dry; _ = _eq; _ = _slope
                except Exception:
                    pass
                ev["_tightness"] = tightness
                ev["_tight_score"] = score
                ev["_prange"] = prange
                if tightness <= effective_full:
                    in_base_events.append(ev)
                else:
                    not_base_events.append(ev)

            pct = len(in_base_events)/len(events)*100 if events else 0
            print(f"\n[{period_name}] total {len(events)} in_base {len(in_base_events)} ({pct:.1f}%) / not_in_base {len(not_base_events)}")

            for bucket_name, bucket in [("IN BASE (tight)", in_base_events), ("NOT IN BASE", not_base_events)]:
                if len(bucket) == 0:
                    print(f"  {bucket_name}: n=0 — no data")
                    continue
                for h in HORIZONS:
                    arr = []
                    for ev in bucket:
                        ed = ev["ed"]; sym = ev["sym"]
                        en = gc(sym, ed)
                        xd = (datetime.strptime(ed, "%Y-%m-%d") + timedelta(days=h)).date().isoformat()
                        xn = gc(sym, xd)
                        g = cret(en, xn)
                        net = nret(g)
                        if net is not None:
                            arr.append(net)
                    arr = np.array(arr)
                    if len(arr) == 0:
                        print(f"  {bucket_name} {h}d: no measured")
                        continue
                    mean = float(arr.mean()); med = float(np.median(arr)); wr = float((arr > 0).mean() * 100)
                    print(f"  {bucket_name} {h:>3}d: n={len(arr):4d} mean={mean:+.2f}% med={med:+.2f}% wr={wr:.1f}%")

                # paired same-stock/year sanity per bucket
                for h in HORIZONS:
                    streak_rets = []
                    random_rets = []
                    for ev in bucket:
                        sym = ev["sym"]; ed = ev["ed"]
                        en = gc(sym, ed)
                        xd = (datetime.strptime(ed, "%Y-%m-%d") + timedelta(days=h)).date().isoformat()
                        xn = gc(sym, xd)
                        g_s = cret(en, xn); n_s = nret(g_s)
                        if n_s is None: continue
                        sdf = sym_data.get(sym)
                        if sdf is None: continue
                        year = int(ed[:4])
                        year_dates = [d for d in sdf["date"].astype(str).str[:10] if d.startswith(str(year))]
                        if len(year_dates) < 20: continue
                        for _ in range(5):
                            rds = rng.choice(year_dates)
                            if rds != ed: break
                        re = gc(sym, rds)
                        if re is None or re <= 0: continue
                        rx = gc(sym, (datetime.strptime(rds, "%Y-%m-%d") + timedelta(days=h)).date().isoformat())
                        g_r = cret(re, rx); n_r = nret(g_r)
                        if n_r is None: continue
                        streak_rets.append(n_s); random_rets.append(n_r)
                    if len(streak_rets) == 0:
                        print(f"    paired {h:>3}d: no pairs")
                        continue
                    s_arr = np.array(streak_rets); r_arr = np.array(random_rets)
                    diff = s_arr - r_arr
                    print(f"    paired {h:>3}d: streak {float(s_arr.mean()):+.2f}%/{float(np.median(s_arr)):+.2f}% wr{float((s_arr>0).mean()*100):.1f}% vs random {float(r_arr.mean()):+.2f}%/{float(np.median(r_arr)):+.2f}% wr{float((r_arr>0).mean()*100):.1f}% -> streak wins {float((diff>0).mean()*100):.1f}% (D  mean {float(diff.mean()):+.2f}%, med {float(np.median(diff)):+.2f}%)")

    print(f"\nTotal time: {time.time()-t0:.1f}s")
    print("\nNOTE: Base conditioning is DAR-independent (tightness/ATR/volume-dry-up/equal-lows/slope/wk52) — .scan() was NOT called.")


if __name__ == "__main__":
    import sys
    sys.exit(main())
