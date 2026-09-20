"""Accumulation streak grid search — 96 combos, fully optimized.

Optimization v3: detect streaks ONCE per symbol on full history, then
filter by scan-date window.  24 (dp,pl) pairs × 500 symbols = 12K
detection calls instead of 24 × 66 × 500 = 792K.
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

LOOKBACK_DAYS = 180
MIN_ROWS = max(55, int(LOOKBACK_DAYS * 0.6) + 5)
BROKERAGE_PCT = 0.5
STCG_RATE = 0.15
HORIZONS = [20, 40, 60, 90, 120]

DP_VALS = [50, 55, 60, 65, 70, 75]
MS_VALS = [2, 3, 4, 5]
PL_VALS = [3, 5, 7, 10]


def _detect_raw(df, dp_thresh, pl):
    """Vectorized detection. Returns list of (sl, avg_dp, avg_dq, sc, start_date, end_date, pch)."""
    n = len(df)
    if n < pl + 2:
        return []
    close = df["close"].to_numpy(dtype=float)
    dp_raw = df["delivery_pct"].to_numpy(dtype=float)
    dq_raw = df["delivery"].to_numpy(dtype=float)
    dates = df["date"].astype(str).str[:10].to_numpy()
    dp = np.nan_to_num(dp_raw, nan=0.0)
    dq = np.nan_to_num(dq_raw, nan=0.0)
    high_delivery = dp >= dp_thresh
    lookback_close = np.empty(n, dtype=float)
    lookback_close[:pl] = close[:pl]
    lookback_close[pl:] = close[:n - pl]
    safe_lb = np.where(lookback_close > 0, lookback_close, 1.0)
    price_flat = (close / safe_lb - 1.0) <= 0.05
    is_acc = high_delivery & price_flat & (close > 0)
    streaks = []
    i = 0
    while i < n:
        if not is_acc[i]:
            i += 1
            continue
        sl = 0
        gap = 0
        dp_s = 0.0
        dq_s = 0.0
        ei = i
        j = i
        while j < n:
            if is_acc[j]:
                sl += 1
                dp_s += dp[j]
                dq_s += dq[j]
                ei = j
                gap = 0
            else:
                gap += 1
                if gap > 2:
                    break
                ei = j
            j += 1
        avg_dp = dp_s / sl if sl > 0 else 0.0
        avg_dq = dq_s / sl if sl > 0 else 0.0
        sc = avg_dp * sl
        streaks.append((sl, avg_dp, avg_dq, sc, dates[i], dates[ei],
                        (close[ei] / close[i] - 1) * 100 if close[i] > 0 else 0.0))
        i = ei + 1
    return streaks


def main():
    t0 = time.time()

    tech_conn = sqlite3.connect(TECH_DB)
    meta_conn = sqlite3.connect(META_DB)

    mtd = tech_conn.execute("SELECT MAX(date) FROM technical_data").fetchone()[0]
    end_guard = (datetime.strptime(mtd, "%Y-%m-%d") - timedelta(days=max(HORIZONS))).date()
    start = datetime.strptime("2021-01-01", "%Y-%m-%d").date()
    end = min(datetime.strptime("2026-06-01", "%Y-%m-%d").date(), end_guard)

    universe = [r[0] for r in tech_conn.execute(
        "SELECT DISTINCT symbol FROM technical_data").fetchall()]
    random.seed(42)
    sampled = random.sample(universe, min(500, len(universe)))
    print(f"Universe: {len(universe)}, Sampled: {len(sampled)}", flush=True)

    scan_dates = []
    cur = start
    while cur <= end:
        scan_dates.append(cur)
        cur += timedelta(days=30)
    print(f"Scan dates: {len(scan_dates)} ({scan_dates[0]}..{scan_dates[-1]})", flush=True)

    # ── Pre-cache close prices (all dates) ──
    print("\nCaching close prices...", flush=True)
    cache_start = (scan_dates[0] - timedelta(days=LOOKBACK_DAYS + 30)).isoformat()
    cache_end = (scan_dates[-1] + timedelta(days=max(HORIZONS) + 30)).isoformat()
    rows = tech_conn.execute(
        "SELECT symbol, date, close FROM technical_data WHERE date BETWEEN ? AND ?",
        (cache_start, cache_end)).fetchall()
    close_cache = {(sym, dt): float(cl) for sym, dt, cl in rows if cl is not None}
    print(f"  {len(close_cache):,} close prices", flush=True)

    bench_rows = meta_conn.execute(
        "SELECT date, close FROM benchmarks WHERE symbol='^NSEI' AND date BETWEEN ? AND ?",
        (cache_start, cache_end)).fetchall()
    bench_cache = {dt: float(cl) for dt, cl in bench_rows if cl is not None}
    print(f"  {len(bench_cache):,} benchmark prices", flush=True)

    def gc(sym, td): return close_cache.get((sym, td))
    def gb(td): return bench_cache.get(td)
    def cret(e, x):
        if e is None or x is None or e <= 0: return None
        return (x - e) / e * 100
    def nret(g):
        n = g - BROKERAGE_PCT * 2
        if g > 0: n -= STCG_RATE * g
        return n

    # ── Phase 1: Load FULL history per symbol (one big query) ──
    print("\nPhase 1: Loading full history per symbol...", flush=True)
    t1 = time.time()
    sym_data: dict[str, pd.DataFrame] = {}
    full_start = (start - timedelta(days=LOOKBACK_DAYS + 30)).isoformat()
    full_end = end.isoformat()
    sql = "SELECT symbol, date, open, high, low, close, volume, delivery, delivery_pct FROM technical_data WHERE date BETWEEN ? AND ? AND symbol IN ({})".format(
        ",".join("?" for _ in sampled))
    all_rows = tech_conn.execute(sql, [full_start, full_end] + sampled).fetchall()
    cols = ["symbol", "date", "open", "high", "low", "close", "volume", "delivery", "delivery_pct"]
    big_df = pd.DataFrame(all_rows, columns=cols)
    for sym, grp in big_df.groupby("symbol"):
        sym_data[sym] = grp.sort_values("date").reset_index(drop=True)
    print(f"  {len(sym_data)} symbols loaded ({len(big_df):,} rows) in {time.time()-t1:.1f}s", flush=True)

    # ── Phase 2: Detect streaks for each (dp, pl) pair on FULL history ──
    print("\nPhase 2: Detecting streaks (24 dp×pl pairs)...", flush=True)
    t1 = time.time()
    dp_pl_pairs = list(product(DP_VALS, PL_VALS))
    # all_streaks[(dp, pl)] = dict of {dedup_key: event_dict}
    all_streaks: dict[tuple, dict] = {}

    for pi, (dp, pl) in enumerate(dp_pl_pairs):
        streaks_dict = {}
        for sym, sdf in sym_data.items():
            raw = _detect_raw(sdf, dp, pl)
            for (sl, avg_dp, avg_dq, sc, sd, ed, pch) in raw:
                dk = f"{sym}|{ed}"
                if dk in streaks_dict:
                    continue
                streaks_dict[dk] = {
                    "sym": sym, "ed": ed, "sd": sd,
                    "sl": sl, "adp": avg_dp, "adq": avg_dq,
                    "ss": sc, "pch": pch,
                }
        all_streaks[(dp, pl)] = streaks_dict
        print(f"  dp={dp} pl={pl}: {len(streaks_dict)} streaks", flush=True)
    print(f"  Detection done in {time.time()-t1:.1f}s", flush=True)

    # ── Phase 3: For each combo, filter by scan-date window + min_streak_length ──
    print(f"\nPhase 3: 96 combos (filter step)...", flush=True)
    t1 = time.time()
    all_results = []

    for dp in DP_VALS:
        for pl in PL_VALS:
            streaks_dict = all_streaks[(dp, pl)]
            for ms in MS_VALS:
                # Filter: min_streak_length + scan-date window + dedup by (sym, end_date) per window.
                seen = set()
                events = []
                for D in scan_dates:
                    ds = D.isoformat()
                    lb = (D - timedelta(days=LOOKBACK_DAYS)).isoformat()
                    for dk, v in streaks_dict.items():
                        if v["sl"] < ms:
                            continue
                        if v["ed"] < lb or v["ed"] > ds:
                            continue
                        # Dedup: same streak end_date seen in earlier window.
                        sym_ed = f"{v['sym']}|{v['ed']}"
                        if sym_ed in seen:
                            continue
                        seen.add(sym_ed)
                        ep = gc(v["sym"], v["ed"])
                        if ep is None or ep <= 0:
                            continue
                        events.append(v)

                n_streaks = len(events)

                # Forward returns.
                fwd = {h: [] for h in HORIZONS}
                for e in events:
                    for h in HORIZONS:
                        ed = e["ed"]
                        xd = (datetime.strptime(ed, "%Y-%m-%d") + timedelta(days=h)).date().isoformat()
                        en = gc(e["sym"], ed)
                        xn = gc(e["sym"], xd)
                        g = cret(en, xn)
                        be = gb(ed)
                        bx = gb(xd)
                        br = cret(be, bx)
                        n = nret(g) if g is not None else None
                        if n is not None:
                            fwd[h].append(n)

                # Stats.
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
                    stats[h] = {"n": len(arr), "mean": round(mean, 4), "med": round(med, 4),
                                 "wr": round(wr, 2), "snr": round(snr, 4) if snr is not None else None}

                # Selection metric.
                s60 = stats[60]
                if (s60["snr"] is not None and s60["n"] >= 20
                        and s60["med"] is not None and s60["med"] > 0
                        and s60["wr"] is not None and s60["wr"] >= 50):
                    sel = s60["snr"] * np.log(max(s60["n"], 1))
                else:
                    sel = -999

                row = {"dp": dp, "ms": ms, "pl": pl, "n": n_streaks, "sel": round(sel, 4)}
                for h in HORIZONS:
                    for k, v in stats[h].items():
                        row[f"h{h}_{k}"] = v
                all_results.append(row)

    print(f"  Done in {time.time()-t1:.1f}s", flush=True)

    # ── Phase 4: Baselines ──
    print("\nPhase 4: Baselines...", flush=True)
    rng = random.Random(42)
    rnd_data = {h: [] for h in HORIZONS}
    for D in scan_dates:
        ds = D.isoformat()
        chosen = rng.sample(sampled, min(20, len(sampled)))
        for sym in chosen:
            en = gc(sym, ds)
            if en is None or en <= 0: continue
            for h in HORIZONS:
                xd = (D + timedelta(days=h)).isoformat()
                xn = gc(sym, xd)
                g = cret(en, xn)
                be = gb(ds)
                bx = gb(xd)
                br = cret(be, bx)
                n = nret(g) if g is not None else None
                if n is not None: rnd_data[h].append(n)

    n50_data = {h: [] for h in HORIZONS}
    for D in scan_dates:
        ds = D.isoformat()
        en = gb(ds)
        if en is None or en <= 0: continue
        for h in HORIZONS:
            xd = (D + timedelta(days=h)).isoformat()
            xn = gb(xd)
            g = cret(en, xn)
            n = nret(g) if g is not None else None
            if n is not None: n50_data[h].append(n)

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

    # ── Output ──
    all_results.sort(key=lambda r: r["sel"], reverse=True)
    pd.DataFrame(all_results).to_csv("accum_streak_grid_results.csv", index=False)
    print(f"\nResults: accum_streak_grid_results.csv", flush=True)

    qualified = [r for r in all_results if r["sel"] > -999]
    print(f"Qualified (med>0, wr>=50%): {len(qualified)}/{len(all_results)}", flush=True)

    print("\n--- Baselines ---", flush=True)
    for h in [60, 120]:
        b = baselines[h]
        print(f"  {h}d: Random mean={b['rnd_m']:+.2f}% med={b['rnd_med']:+.2f}% wr={b['rnd_wr']:.1f}% | "
              f"N50 mean={b['n50_m']:+.2f}% med={b['n50_med']:+.2f}% wr={b['n50_wr']:.1f}%", flush=True)

    print("\n" + "=" * 120, flush=True)
    print("TOP 5 BY SELECTION METRIC", flush=True)
    print("=" * 120, flush=True)
    for rank, row in enumerate(all_results[:5], 1):
        print(f"\n#{rank}  dp={row['dp']} ms={row['ms']} pl={row['pl']}  "
              f"n={row['n']}  sel={row['sel']}", flush=True)
        for h in [60, 120]:
            b = baselines[h]
            m = row.get(f"h{h}_mean")
            d = row.get(f"h{h}_med")
            w = row.get(f"h{h}_wr")
            sn = row.get(f"h{h}_snr")
            n = row.get(f"h{h}_n")
            print(f"  {h}d: n={n}  mean={m:+.2f}%  med={d:+.2f}%  wr={w:.1f}%  snr={sn}", flush=True)
            print(f"       Random: mean={b['rnd_m']:+.2f}%  med={b['rnd_med']:+.2f}%  wr={b['rnd_wr']:.1f}%", flush=True)
            print(f"       N50:    mean={b['n50_m']:+.2f}%  med={b['n50_med']:+.2f}%  wr={b['n50_wr']:.1f}%", flush=True)

    if not qualified:
        print("\n*** NO COMBO QUALIFIED (all have median <= 0 or win_rate < 50% at 60d). ***", flush=True)
        by_mean = sorted([r for r in all_results if r.get("h60_mean") is not None],
                         key=lambda r: r["h60_mean"], reverse=True)
        print("Top 5 by raw mean at 60d (unqualified):", flush=True)
        for rank, row in enumerate(by_mean[:5], 1):
            print(f"  #{rank} dp={row['dp']} ms={row['ms']} pl={row['pl']}: "
                  f"mean={row['h60_mean']:+.2f}%  med={row['h60_med']:+.2f}%  "
                  f"wr={row['h60_wr']:.1f}%  n={row['h60_n']}", flush=True)

    print(f"\nTotal time: {time.time()-t0:.1f}s", flush=True)


if __name__ == "__main__":
    sys.exit(main())
