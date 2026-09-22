"""Float Turnover grid search — 30 combos, optimized.

Windows: 10/20/30/45/60
Thresholds: 5/10/15/20/25/30%

Qualify: median >0 and win_rate >=50% at 60d (same as accumulation streak).
"""

from __future__ import annotations

import os
import random
import sqlite3
import sys
import time
from datetime import date, datetime, timedelta
from itertools import product

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np
import pandas as pd

from myra_app.constants import DB_DIR
from myra_app.librarian_core import LibrarianCore
from myra_app.db.bulk_loader import load_ohlcv_for_universe

TECH_DB = os.path.join(DB_DIR, LibrarianCore.DB_MAP["technical"])
META_DB = os.path.join(DB_DIR, LibrarianCore.DB_MAP["meta"])
VAL_DB = os.path.join(DB_DIR, LibrarianCore.DB_MAP["valuation"])

LOOKBACK_DAYS = 180
BROKERAGE_PCT = 0.5
STCG_RATE = 0.15
HORIZONS = [20, 40, 60, 90, 120]

WINDOWS = [10, 20, 30, 45, 60]
THRESHOLDS = [5, 10, 15, 20, 25, 30]


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


def detect_crossings(dates: np.ndarray, turnover: np.ndarray, threshold: float, close: np.ndarray) -> list[str]:
    n = len(turnover)
    if n == 0:
        return []
    prev = np.roll(turnover, 1)
    prev[0] = np.nan
    prev_below = np.isnan(prev) | (prev < threshold)
    curr_above = turnover >= threshold
    valid = ~np.isnan(turnover) & (close > 0)
    mask = valid & curr_above & prev_below
    return [str(dates[i]) for i in np.where(mask)[0]]


def main():
    t0 = time.time()
    tech_conn = sqlite3.connect(TECH_DB)
    meta_conn = sqlite3.connect(META_DB)

    mtd = tech_conn.execute("SELECT MAX(date) FROM technical_data").fetchone()[0]
    end_guard = (datetime.strptime(mtd, "%Y-%m-%d") - timedelta(days=max(HORIZONS))).date()
    start = datetime.strptime("2021-01-01", "%Y-%m-%d").date()
    end = min(datetime.strptime("2026-06-01", "%Y-%m-%d").date(), end_guard)

    ff_map = get_ff_map()
    # restrict to technical symbols with ff
    tech_syms = [r[0] for r in tech_conn.execute("SELECT DISTINCT symbol FROM technical_data").fetchall()]
    usable = [s for s in tech_syms if s in ff_map]
    excluded = len(tech_syms) - len(usable)
    print(f"Universe: tech {len(tech_syms)}, with ff {len(usable)}, excluded {excluded} ({excluded/len(tech_syms)*100:.1f}%)")

    random.seed(42)
    sampled = random.sample(usable, min(500, len(usable)))
    print(f"Sampled: {len(sampled)}", flush=True)

    scan_dates = []
    cur = start
    while cur <= end:
        scan_dates.append(cur)
        cur += timedelta(days=30)
    print(f"Scan dates: {len(scan_dates)} ({scan_dates[0]}..{scan_dates[-1]})", flush=True)

    # cache close prices — filter to sampled symbols to avoid 8M-row scan
    print("\nCaching close prices...", flush=True)
    cache_start = (scan_dates[0] - timedelta(days=LOOKBACK_DAYS + 30)).isoformat()
    cache_end = (scan_dates[-1] + timedelta(days=max(HORIZONS) + 30)).isoformat()
    ph = ",".join("?" for _ in sampled)
    rows = tech_conn.execute(
        f"SELECT symbol, date, close FROM technical_data WHERE date BETWEEN ? AND ? AND symbol IN ({ph})",
        [cache_start, cache_end] + sampled,
    ).fetchall()
    close_cache = {(sym, dt): float(cl) for sym, dt, cl in rows if cl is not None}
    print(f"  {len(close_cache):,} close prices", flush=True)
    bench_rows = meta_conn.execute(
        "SELECT date, close FROM benchmarks WHERE symbol='^NSEI' AND date BETWEEN ? AND ?", (cache_start, cache_end)
    ).fetchall()
    bench_cache = {dt: float(cl) for dt, cl in bench_rows if cl is not None}
    print(f"  {len(bench_cache):,} benchmark prices", flush=True)

    def gc(sym, td):
        return close_cache.get((sym, td))

    def gb(td):
        return bench_cache.get(td)

    def cret(e, x):
        if e is None or x is None or e <= 0:
            return None
        return (x - e) / e * 100

    def nret(g):
        if g is None:
            return None
        n = g - BROKERAGE_PCT * 2
        if g > 0:
            n -= STCG_RATE * g
        return n

    # Load full history per symbol
    print("\nLoading full history...", flush=True)
    t1 = time.time()
    sym_data: dict[str, pd.DataFrame] = {}
    full_start = (start - timedelta(days=LOOKBACK_DAYS + 30)).isoformat()
    full_end = end.isoformat()
    sql = "SELECT symbol, date, open, high, low, close, volume, delivery, delivery_pct FROM technical_data WHERE date BETWEEN ? AND ? AND symbol IN ({})".format(
        ",".join("?" for _ in sampled)
    )
    all_rows = tech_conn.execute(sql, [full_start, full_end] + sampled).fetchall()
    cols = ["symbol", "date", "open", "high", "low", "close", "volume", "delivery", "delivery_pct"]
    big_df = pd.DataFrame(all_rows, columns=cols)
    for sym, grp in big_df.groupby("symbol"):
        sym_data[sym] = grp.sort_values("date").reset_index(drop=True)
    print(f"  {len(sym_data)} symbols, {len(big_df):,} rows in {time.time()-t1:.1f}s", flush=True)

    # Precompute turnover series per (symbol, window) — reused across thresholds
    print("\nPrecomputing turnover signals per window...", flush=True)
    t1 = time.time()
    turnover_cache: dict[tuple[str, int], tuple[np.ndarray, np.ndarray, np.ndarray]] = {}
    for w in WINDOWS:
        for sym, sdf in sym_data.items():
            ff_mcap = ff_map.get(sym)
            if not ff_mcap:
                continue
            close = pd.to_numeric(sdf["close"], errors="coerce").to_numpy(dtype=float)
            delivery = pd.to_numeric(sdf["delivery"], errors="coerce").to_numpy(dtype=float)
            dates = sdf["date"].astype(str).str[:10].to_numpy()
            turnover = compute_turnover_series(close, delivery, ff_mcap, w)
            turnover_cache[(sym, w)] = (dates, turnover, close)
    print(f"  Cached {len(turnover_cache)} turnover series in {time.time()-t1:.1f}s", flush=True)

    # Grid search
    print(f"\nGrid: {len(WINDOWS)*len(THRESHOLDS)} combos...", flush=True)
    t1 = time.time()
    all_results = []
    for window in WINDOWS:
        for thresh in THRESHOLDS:
            # build crossings per symbol for this thresh
            crossings_dict: dict[str, dict] = {}
            for sym in sampled:
                key = (sym, window)
                if key not in turnover_cache:
                    continue
                dates, turnover, close = turnover_cache[key]
                # detect for this thresh
                prev = np.roll(turnover, 1)
                prev[0] = np.nan
                mask = (~np.isnan(turnover)) & (turnover >= thresh) & (np.isnan(prev) | (prev < thresh)) & (close > 0)
                idxs = np.where(mask)[0]
                for idx in idxs:
                    ed = str(dates[idx])
                    dk = f"{sym}|{ed}"
                    if dk in crossings_dict:
                        continue
                    crossings_dict[dk] = {"sym": sym, "ed": ed, "turnover": float(turnover[idx])}
            # filter by scan dates
            seen = set()
            events = []
            for D in scan_dates:
                ds = D.isoformat()
                lb = (D - timedelta(days=LOOKBACK_DAYS)).isoformat()
                for dk, v in crossings_dict.items():
                    if v["ed"] < lb or v["ed"] > ds:
                        continue
                    sym_ed = f"{v['sym']}|{v['ed']}"
                    if sym_ed in seen:
                        continue
                    seen.add(sym_ed)
                    ep = gc(v["sym"], v["ed"])
                    if ep is None or ep <= 0:
                        continue
                    events.append(v)
            n_events = len(events)
            fwd = {h: [] for h in HORIZONS}
            for e in events:
                for h in HORIZONS:
                    ed = e["ed"]
                    xd = (datetime.strptime(ed, "%Y-%m-%d") + timedelta(days=h)).date().isoformat()
                    en = gc(e["sym"], ed)
                    xn = gc(e["sym"], xd)
                    g = cret(en, xn)
                    net = nret(g)
                    if net is not None:
                        fwd[h].append(net)
            stats = {}
            for h in HORIZONS:
                arr = np.array(fwd[h])
                if len(arr) == 0:
                    stats[h] = {"n": 0, "mean": None, "med": None, "wr": None, "snr": None}
                    continue
                mean = float(arr.mean())
                med = float(np.median(arr))
                wr = float((arr > 0).mean() * 100)
                std = float(arr.std())
                snr = mean / std if std > 0 else None
                stats[h] = {"n": len(arr), "mean": round(mean, 4), "med": round(med, 4), "wr": round(wr, 2), "snr": round(snr, 4) if snr is not None else None}
            s60 = stats[60]
            if s60["snr"] is not None and s60["n"] >= 20 and s60["med"] is not None and s60["med"] > 0 and s60["wr"] is not None and s60["wr"] >= 50:
                sel = s60["snr"] * np.log(max(s60["n"], 1))
            else:
                sel = -999
            row = {"window": window, "threshold": thresh, "n": n_events, "sel": round(sel, 4)}
            for h in HORIZONS:
                for k, v in stats[h].items():
                    row[f"h{h}_{k}"] = v
            all_results.append(row)
    print(f"  Done in {time.time()-t1:.1f}s", flush=True)

    # Baselines
    print("\nBaselines...", flush=True)
    rng = random.Random(42)
    rnd_data = {h: [] for h in HORIZONS}
    for D in scan_dates:
        ds = D.isoformat()
        chosen = rng.sample(sampled, min(20, len(sampled)))
        for sym in chosen:
            en = gc(sym, ds)
            if en is None or en <= 0:
                continue
            for h in HORIZONS:
                xd = (D + timedelta(days=h)).isoformat()
                xn = gc(sym, xd)
                g = cret(en, xn)
                net = nret(g)
                if net is not None:
                    rnd_data[h].append(net)
    n50_data = {h: [] for h in HORIZONS}
    for D in scan_dates:
        ds = D.isoformat()
        en = gb(ds)
        if en is None or en <= 0:
            continue
        for h in HORIZONS:
            xd = (D + timedelta(days=h)).isoformat()
            xn = gb(xd)
            g = cret(en, xn)
            net = nret(g)
            if net is not None:
                n50_data[h].append(net)
    baselines = {}
    for h in HORIZONS:
        ra = np.array(rnd_data[h]) if rnd_data[h] else np.array([0.0])
        na = np.array(n50_data[h]) if n50_data[h] else np.array([0.0])
        baselines[h] = {
            "rnd_m": round(float(ra.mean()), 4),
            "rnd_med": round(float(np.median(ra)), 4),
            "rnd_wr": round(float((ra > 0).mean() * 100), 2),
            "n50_m": round(float(na.mean()), 4),
            "n50_med": round(float(np.median(na)), 4),
            "n50_wr": round(float((na > 0).mean() * 100), 2),
        }

    all_results.sort(key=lambda r: r["sel"], reverse=True)
    pd.DataFrame(all_results).to_csv("float_turnover_grid_results.csv", index=False)
    print("\nResults: float_turnover_grid_results.csv", flush=True)
    qualified = [r for r in all_results if r["sel"] > -999]
    print(f"Qualified (med>0, wr>=50%): {len(qualified)}/{len(all_results)}", flush=True)
    print("\n--- Baselines ---", flush=True)
    for h in [60, 120]:
        b = baselines[h]
        print(f"  {h}d: Random mean={b['rnd_m']:+.2f}% med={b['rnd_med']:+.2f}% wr={b['rnd_wr']:.1f}% | N50 mean={b['n50_m']:+.2f}% med={b['n50_med']:+.2f}% wr={b['n50_wr']:.1f}%", flush=True)
    print("\n" + "=" * 120, flush=True)
    print("TOP 5 BY SELECTION METRIC", flush=True)
    print("=" * 120, flush=True)
    for rank, row in enumerate(all_results[:5], 1):
        print(f"\n#{rank}  window={row['window']} thresh={row['threshold']}%  n={row['n']} sel={row['sel']}", flush=True)
        for h in [60, 120]:
            b = baselines[h]
            m = row.get(f"h{h}_mean")
            d = row.get(f"h{h}_med")
            w = row.get(f"h{h}_wr")
            sn = row.get(f"h{h}_snr")
            n = row.get(f"h{h}_n")
            print(f"  {h}d: n={n} mean={m:+.2f}% med={d:+.2f}% wr={w:.1f}% snr={sn}", flush=True)
            print(f"       Random: mean={b['rnd_m']:+.2f}% med={b['rnd_med']:+.2f}% wr={b['rnd_wr']:.1f}%", flush=True)
            print(f"       N50:    mean={b['n50_m']:+.2f}% med={b['n50_med']:+.2f}% wr={b['n50_wr']:.1f}%", flush=True)
    if not qualified:
        print("\n*** NO COMBO QUALIFIED ***", flush=True)
        by_mean = sorted([r for r in all_results if r.get("h60_mean") is not None], key=lambda r: r["h60_mean"], reverse=True)
        print("Top 5 by raw mean at 60d:", flush=True)
        for rank, row in enumerate(by_mean[:5], 1):
            print(f"  #{rank} w={row['window']} t={row['threshold']}%: mean={row['h60_mean']:+.2f}% med={row['h60_med']:+.2f}% wr={row['h60_wr']:.1f}% n={row['h60_n']}", flush=True)
    print(f"\nTotal time: {time.time()-t0:.1f}s", flush=True)


if __name__ == "__main__":
    import sys
    sys.exit(main())
