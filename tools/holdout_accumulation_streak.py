"""Out-of-sample holdout + sanity check for top-5 accumulation streak combos.

1. Holdout: re-run top-5 combos on 2024-01-01 onward only (data the grid
   search didn't use to pick winners).
2. Sanity check: same-stock random entry baseline — for each streak event,
   what's the 120d return if you'd entered on a RANDOM day within the
   same stock during the same year?
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

TECH_DB = os.path.join(DB_DIR, LibrarianCore.DB_MAP["technical"])
META_DB = os.path.join(DB_DIR, LibrarianCore.DB_MAP["meta"])

LOOKBACK_DAYS = 180
BROKERAGE_PCT = 0.5
STCG_RATE = 0.15
HORIZONS = [20, 40, 60, 90, 120]
HOLDOUT_START = "2024-01-01"

TOP5 = [
    {"dp": 75, "ms": 2, "pl": 3, "label": "dp=75 ms=2 pl=3"},
    {"dp": 60, "ms": 4, "pl": 3, "label": "dp=60 ms=4 pl=3"},
    {"dp": 65, "ms": 5, "pl": 3, "label": "dp=65 ms=5 pl=3"},
    {"dp": 60, "ms": 5, "pl": 3, "label": "dp=60 ms=5 pl=3"},
    {"dp": 60, "ms": 3, "pl": 3, "label": "dp=60 ms=3 pl=3"},
]


def _detect_raw(df, dp_thresh, pl):
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
    max_scan_end = (datetime.strptime(mtd, "%Y-%m-%d") - timedelta(days=max(HORIZONS))).date()

    universe = [r[0] for r in tech_conn.execute(
        "SELECT DISTINCT symbol FROM technical_data").fetchall()]
    random.seed(42)
    sampled = random.sample(universe, min(500, len(universe)))
    print(f"Universe: {len(universe)}, Sampled: {len(sampled)}", flush=True)

    # ── Cache close prices ──
    print("Caching close prices...", flush=True)
    cache_start = (date(2020, 6, 1)).isoformat()  # well before earliest scan
    cache_end = (max_scan_end + timedelta(days=max(HORIZONS) + 30)).isoformat()
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

    # ── Load full history per symbol ──
    print("Loading full history...", flush=True)
    t1 = time.time()
    sym_data: dict[str, pd.DataFrame] = {}
    sql = "SELECT symbol, date, open, high, low, close, volume, delivery, delivery_pct FROM technical_data WHERE date BETWEEN ? AND ? AND symbol IN ({})".format(
        ",".join("?" for _ in sampled))
    all_rows = tech_conn.execute(sql, ["2020-06-01", max_scan_end.isoformat()] + sampled).fetchall()
    cols = ["symbol", "date", "open", "high", "low", "close", "volume", "delivery", "delivery_pct"]
    big_df = pd.DataFrame(all_rows, columns=cols)
    for sym, grp in big_df.groupby("symbol"):
        sym_data[sym] = grp.sort_values("date").reset_index(drop=True)
    print(f"  {len(sym_data)} symbols, {len(big_df):,} rows in {time.time()-t1:.1f}s", flush=True)

    # ── Detect streaks for each top-5 combo ──
    print("\nDetecting streaks for top-5 combos...", flush=True)
    t1 = time.time()
    combo_streaks = {}
    for c in TOP5:
        dp, ms, pl = c["dp"], c["ms"], c["pl"]
        streaks_dict = {}
        for sym, sdf in sym_data.items():
            raw = _detect_raw(sdf, dp, pl)
            for (sl, avg_dp, avg_dq, sc, sd, ed, pch) in raw:
                if sl < ms:
                    continue
                dk = f"{sym}|{ed}"
                if dk in streaks_dict:
                    continue
                streaks_dict[dk] = {
                    "sym": sym, "ed": ed, "sd": sd,
                    "sl": sl, "adp": avg_dp, "ss": sc,
                }
        combo_streaks[c["label"]] = streaks_dict
        print(f"  {c['label']}: {len(streaks_dict)} total streaks", flush=True)
    print(f"  Detection: {time.time()-t1:.1f}s", flush=True)

    # ── Scan dates for holdout period ──
    holdout_start = datetime.strptime(HOLDOUT_START, "%Y-%m-%d").date()
    scan_dates_holdout = []
    cur = holdout_start
    while cur <= max_scan_end:
        scan_dates_holdout.append(cur)
        cur += timedelta(days=30)
    print(f"\nHoldout scan dates: {len(scan_dates_holdout)} ({scan_dates_holdout[0]}..{scan_dates_holdout[-1]})", flush=True)

    # ── Scan dates for full period (for comparison) ──
    scan_dates_full = []
    cur = date(2021, 1, 1)
    while cur <= max_scan_end:
        scan_dates_full.append(cur)
        cur += timedelta(days=30)

    # ══════════════════════════════════════════════════════════════════
    # TASK 1: Out-of-sample holdout
    # ══════════════════════════════════════════════════════════════════
    print("\n" + "=" * 100)
    print("TASK 1: OUT-OF-SAMPLE HOLDOUT (2024-01-01 onward)")
    print("=" * 100)

    for c in TOP5:
        label = c["label"]
        streaks_dict = combo_streaks[label]

        # Filter to holdout period only.
        seen = set()
        events_holdout = []
        for D in scan_dates_holdout:
            ds = D.isoformat()
            lb = (D - timedelta(days=LOOKBACK_DAYS)).isoformat()
            for dk, v in streaks_dict.items():
                if v["ed"] < lb or v["ed"] > ds:
                    continue
                sym_ed = f"{v['sym']}|{v['ed']}"
                if sym_ed in seen:
                    continue
                seen.add(sym_ed)
                ep = gc(v["sym"], v["ed"])
                if ep is None or ep <= 0:
                    continue
                events_holdout.append(v)

        n = len(events_holdout)
        fwd = {h: [] for h in HORIZONS}
        for e in events_holdout:
            for h in HORIZONS:
                ed = e["ed"]
                xd = (datetime.strptime(ed, "%Y-%m-%d") + timedelta(days=h)).date().isoformat()
                en = gc(e["sym"], ed)
                xn = gc(e["sym"], xd)
                g = cret(en, xn)
                be = gb(ed)
                bx = gb(xd)
                br = cret(be, bx)
                net = nret(g) if g is not None else None
                if net is not None:
                    fwd[h].append(net)

        print(f"\n--- {label} (holdout: n={n}) ---")
        for h in [60, 120]:
            arr = np.array(fwd[h])
            if len(arr) == 0:
                print(f"  {h}d: no data")
                continue
            mean = float(arr.mean())
            med = float(np.median(arr))
            wr = float((arr > 0).mean() * 100)
            std = float(arr.std())
            snr = mean / std if std > 0 else None
            print(f"  {h}d: n={len(arr)}  mean={mean:+.2f}%  med={med:+.2f}%  wr={wr:.1f}%  snr={snr:.4f}")

    # ══════════════════════════════════════════════════════════════════
    # Baselines for holdout period
    # ══════════════════════════════════════════════════════════════════
    print("\n--- Holdout baselines ---")
    rng = random.Random(42)
    rnd_holdout = {h: [] for h in HORIZONS}
    for D in scan_dates_holdout:
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
                net = nret(g) if g is not None else None
                if net is not None:
                    rnd_holdout[h].append(net)

    n50_holdout = {h: [] for h in HORIZONS}
    for D in scan_dates_holdout:
        ds = D.isoformat()
        en = gb(ds)
        if en is None or en <= 0: continue
        for h in HORIZONS:
            xd = (D + timedelta(days=h)).isoformat()
            xn = gb(xd)
            g = cret(en, xn)
            net = nret(g) if g is not None else None
            if net is not None:
                n50_holdout[h].append(net)

    for h in [60, 120]:
        ra = np.array(rnd_holdout[h]) if rnd_holdout[h] else np.array([0.0])
        na = np.array(n50_holdout[h]) if n50_holdout[h] else np.array([0.0])
        print(f"  {h}d: Random mean={float(ra.mean()):+.2f}% med={float(np.median(ra)):+.2f}% wr={float((ra>0).mean()*100):.1f}% | "
              f"N50 mean={float(na.mean()):+.2f}% med={float(np.median(na)):+.2f}% wr={float((na>0).mean()*100):.1f}%")

    # ══════════════════════════════════════════════════════════════════
    # TASK 2: Sanity check — same-stock random entry
    # ══════════════════════════════════════════════════════════════════
    print("\n" + "=" * 100)
    print("TASK 2: SANITY CHECK — Same-stock random entry at 120d")
    print("=" * 100)
    print("For each streak event, compare streak-end entry vs random-day entry")
    print("within the same stock during the same year.\n")

    rng_sanity = random.Random(12345)

    for c in TOP5:
        label = c["label"]
        streaks_dict = combo_streaks[label]

        # Collect all streak events (full period, for max sample size).
        seen = set()
        all_events = []
        for D in scan_dates_full:
            ds = D.isoformat()
            lb = (D - timedelta(days=LOOKBACK_DAYS)).isoformat()
            for dk, v in streaks_dict.items():
                if v["ed"] < lb or v["ed"] > ds:
                    continue
                sym_ed = f"{v['sym']}|{v['ed']}"
                if sym_ed in seen:
                    continue
                seen.add(sym_ed)
                ep = gc(v["sym"], v["ed"])
                if ep is None or ep <= 0:
                    continue
                all_events.append(v)

        n_events = len(all_events)
        # For each streak event, pick a random day in same stock/year and compute 120d return.
        streak_rets_120 = []
        random_rets_120 = []
        both_valid = 0

        for e in all_events:
            sym = e["sym"]
            ed = e["ed"]
            ed_date = datetime.strptime(ed, "%Y-%m-%d").date()
            year = ed_date.year

            # Streak entry return at 120d.
            xd = (ed_date + timedelta(days=120)).isoformat()
            en = gc(sym, ed)
            xn = gc(sym, xd)
            g_streak = cret(en, xn)
            n_streak = nret(g_streak) if g_streak is not None else None

            # Random entry: pick a random trading day in same stock, same year.
            sdf = sym_data.get(sym)
            if sdf is None or n_streak is None:
                continue

            # Get all dates in this stock's data for the same year.
            sym_dates = sdf["date"].astype(str).str[:10]
            year_dates = [d for d in sym_dates if d.startswith(str(year))]
            if len(year_dates) < 20:
                continue

            # Pick a random date (not the streak end date itself).
            for _ in range(5):  # retry a few times
                rand_date_str = rng_sanity.choice(year_dates)
                if rand_date_str != ed:
                    break

            rand_entry = gc(sym, rand_date_str)
            if rand_entry is None or rand_entry <= 0:
                continue
            rand_exit_date = (datetime.strptime(rand_date_str, "%Y-%m-%d") + timedelta(days=120)).date().isoformat()
            rand_exit = gc(sym, rand_exit_date)
            g_rand = cret(rand_entry, rand_exit)
            n_rand = nret(g_rand) if g_rand is not None else None

            if n_rand is not None:
                streak_rets_120.append(n_streak)
                random_rets_120.append(n_rand)
                both_valid += 1

        s_arr = np.array(streak_rets_120)
        r_arr = np.array(random_rets_120)

        print(f"--- {label} (n_events={n_events}, paired={both_valid}) ---")
        print(f"  Streak entry  120d: mean={float(s_arr.mean()):+.2f}%  med={float(np.median(s_arr)):+.2f}%  wr={float((s_arr>0).mean()*100):.1f}%")
        print(f"  Random entry 120d: mean={float(r_arr.mean()):+.2f}%  med={float(np.median(r_arr)):+.2f}%  wr={float((r_arr>0).mean()*100):.1f}%")
        diff = s_arr - r_arr
        print(f"  Streak minus Random: mean={float(diff.mean()):+.2f}%  med={float(np.median(diff)):+.2f}%  "
              f"streak wins {float((diff > 0).mean()*100):.1f}% of pairs")

    # ══════════════════════════════════════════════════════════════════
    # Summary
    # ══════════════════════════════════════════════════════════════════
    print(f"\nTotal time: {time.time()-t0:.1f}s")


if __name__ == "__main__":
    sys.exit(main())
