"""Out-of-sample holdout + sanity check for top Float Turnover combos.

1. Holdout: re-run combos on 2024-01-01 onward only
2. Sanity: same-stock random entry (streak wins X% of pairs)
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
VAL_DB = os.path.join(DB_DIR, LibrarianCore.DB_MAP["valuation"])

LOOKBACK_DAYS = 180
BROKERAGE_PCT = 0.5
STCG_RATE = 0.15
HORIZONS = [20, 40, 60, 90, 120]
HOLDOUT_START = "2024-01-01"

# Will be overwritten by grid winner if csv exists
TOP_COMBOS = [
    {"window": 20, "threshold": 10, "label": "w=20 t=10%"},
    {"window": 30, "threshold": 10, "label": "w=30 t=10%"},
    {"window": 20, "threshold": 15, "label": "w=20 t=15%"},
    {"window": 30, "threshold": 15, "label": "w=30 t=15%"},
    {"window": 45, "threshold": 10, "label": "w=45 t=10%"},
]


def load_top_from_grid():
    path = "float_turnover_grid_results.csv"
    if not os.path.exists(path):
        return None
    try:
        df = pd.read_csv(path)
        # sort by sel desc
        df = df.sort_values("sel", ascending=False)
        top = df.head(5)
        out = []
        for _, r in top.iterrows():
            out.append({"window": int(r["window"]), "threshold": float(r["threshold"]), "label": f"w={int(r['window'])} t={r['threshold']:.0f}%"})
        return out
    except Exception as e:
        print(f"Could not load grid results: {e}")
        return None


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


def main():
    t0 = time.time()
    maybe = load_top_from_grid()
    top_combos = maybe if maybe else TOP_COMBOS
    print(f"Testing {len(top_combos)} combos: {[c['label'] for c in top_combos]}")

    tech_conn = sqlite3.connect(TECH_DB)
    meta_conn = sqlite3.connect(META_DB)

    mtd = tech_conn.execute("SELECT MAX(date) FROM technical_data").fetchone()[0]
    max_scan_end = (datetime.strptime(mtd, "%Y-%m-%d") - timedelta(days=max(HORIZONS))).date()

    ff_map = get_ff_map()
    tech_syms = [r[0] for r in tech_conn.execute("SELECT DISTINCT symbol FROM technical_data").fetchall()]
    usable = [s for s in tech_syms if s in ff_map]
    print(f"Universe: tech {len(tech_syms)}, with ff {len(usable)}, excluded {len(tech_syms)-len(usable)}")

    random.seed(42)
    sampled = random.sample(usable, min(500, len(usable)))
    print(f"Sampled: {len(sampled)}")

    # cache close — filter to sampled symbols only (avoid 8M scan)
    print("Caching close prices...")
    cache_start = date(2020, 6, 1).isoformat()
    cache_end = (max_scan_end + timedelta(days=max(HORIZONS) + 30)).isoformat()
    ph = ",".join("?" for _ in sampled)
    rows = tech_conn.execute(
        f"SELECT symbol, date, close FROM technical_data WHERE date BETWEEN ? AND ? AND symbol IN ({ph})",
        [cache_start, cache_end] + sampled,
    ).fetchall()
    close_cache = {(sym, dt): float(cl) for sym, dt, cl in rows if cl is not None}
    print(f"  {len(close_cache):,} close")
    bench_rows = meta_conn.execute("SELECT date, close FROM benchmarks WHERE symbol='^NSEI' AND date BETWEEN ? AND ?", (cache_start, cache_end)).fetchall()
    bench_cache = {dt: float(cl) for dt, cl in bench_rows if cl is not None}
    print(f"  {len(bench_cache):,} bench")

    def gc(sym, td): return close_cache.get((sym, td))
    def gb(td): return bench_cache.get(td)
    def cret(e, x):
        if e is None or x is None or e <= 0: return None
        return (x - e) / e * 100
    def nret(g):
        if g is None: return None
        n = g - BROKERAGE_PCT * 2
        if g > 0: n -= STCG_RATE * g
        return n

    # Load full history
    print("Loading full history...")
    t1 = time.time()
    sym_data: dict[str, pd.DataFrame] = {}
    sql = "SELECT symbol, date, open, high, low, close, volume, delivery, delivery_pct FROM technical_data WHERE date BETWEEN ? AND ? AND symbol IN ({})".format(",".join("?" for _ in sampled))
    all_rows = tech_conn.execute(sql, ["2020-06-01", max_scan_end.isoformat()] + sampled).fetchall()
    cols = ["symbol", "date", "open", "high", "low", "close", "volume", "delivery", "delivery_pct"]
    big_df = pd.DataFrame(all_rows, columns=cols)
    for sym, grp in big_df.groupby("symbol"):
        sym_data[sym] = grp.sort_values("date").reset_index(drop=True)
    print(f"  {len(sym_data)} symbols, {len(big_df):,} rows in {time.time()-t1:.1f}s")

    # detect per combo
    print("\nDetecting...")
    combo_signals: dict[str, dict] = {}
    for c in top_combos:
        w, t = c["window"], c["threshold"]
        d = {}
        for sym, sdf in sym_data.items():
            ff_mcap = ff_map.get(sym)
            if not ff_mcap: continue
            close = pd.to_numeric(sdf["close"], errors="coerce").to_numpy(dtype=float)
            delivery = pd.to_numeric(sdf["delivery"], errors="coerce").to_numpy(dtype=float)
            dates = sdf["date"].astype(str).str[:10].to_numpy()
            turnover = compute_turnover_series(close, delivery, ff_mcap, w)
            prev = np.roll(turnover, 1)
            prev[0] = np.nan
            mask = (~np.isnan(turnover)) & (turnover >= t) & (np.isnan(prev) | (prev < t)) & (close > 0)
            for idx in np.where(mask)[0]:
                ed = str(dates[idx])
                dk = f"{sym}|{ed}"
                if dk in d: continue
                d[dk] = {"sym": sym, "ed": ed, "turnover": float(turnover[idx])}
        combo_signals[c["label"]] = d
        print(f"  {c['label']}: {len(d)} signals")

    # Holdout scan dates
    holdout_start = datetime.strptime(HOLDOUT_START, "%Y-%m-%d").date()
    scan_dates_holdout = []
    cur = holdout_start
    while cur <= max_scan_end:
        scan_dates_holdout.append(cur)
        cur += timedelta(days=30)
    print(f"\nHoldout scan dates: {len(scan_dates_holdout)} ({scan_dates_holdout[0]}..{scan_dates_holdout[-1]})")
    scan_dates_full = []
    cur = date(2021, 1, 1)
    while cur <= max_scan_end:
        scan_dates_full.append(cur)
        cur += timedelta(days=30)

    print("\n" + "=" * 100)
    print("TASK 1: OUT-OF-SAMPLE HOLDOUT (2024-01-01 onward)")
    print("=" * 100)
    for c in top_combos:
        label = c["label"]
        d = combo_signals[label]
        seen = set()
        events = []
        for D in scan_dates_holdout:
            ds = D.isoformat()
            lb = (D - timedelta(days=LOOKBACK_DAYS)).isoformat()
            for dk, v in d.items():
                if v["ed"] < lb or v["ed"] > ds: continue
                sym_ed = f"{v['sym']}|{v['ed']}"
                if sym_ed in seen: continue
                seen.add(sym_ed)
                ep = gc(v["sym"], v["ed"])
                if ep is None or ep <= 0: continue
                events.append(v)
        n = len(events)
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
        print(f"\n--- {label} (holdout n={n}) ---")
        for h in [60, 120]:
            arr = np.array(fwd[h])
            if len(arr) == 0:
                print(f"  {h}d: no data")
                continue
            print(f"  {h}d: n={len(arr)} mean={float(arr.mean()):+.2f}% med={float(np.median(arr)):+.2f}% wr={float((arr>0).mean()*100):.1f}% snr={float(arr.mean()/arr.std()) if arr.std()>0 else 0:.4f}")

    # Baselines holdout
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
                net = nret(g)
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
            net = nret(g)
            if net is not None:
                n50_holdout[h].append(net)
    for h in [60, 120]:
        ra = np.array(rnd_holdout[h]) if rnd_holdout[h] else np.array([0.0])
        na = np.array(n50_holdout[h]) if n50_holdout[h] else np.array([0.0])
        print(f"  {h}d: Random mean={float(ra.mean()):+.2f}% med={float(np.median(ra)):+.2f}% wr={float((ra>0).mean()*100):.1f}% | N50 mean={float(na.mean()):+.2f}% med={float(np.median(na)):+.2f}% wr={float((na>0).mean()*100):.1f}%")

    print("\n" + "=" * 100)
    print("TASK 2: SANITY — Same-stock random entry at 120d")
    print("=" * 100)
    rng_sanity = random.Random(12345)
    for c in top_combos:
        label = c["label"]
        d = combo_signals[label]
        seen = set()
        all_events = []
        for D in scan_dates_full:
            ds = D.isoformat()
            lb = (D - timedelta(days=LOOKBACK_DAYS)).isoformat()
            for dk, v in d.items():
                if v["ed"] < lb or v["ed"] > ds: continue
                sym_ed = f"{v['sym']}|{v['ed']}"
                if sym_ed in seen: continue
                seen.add(sym_ed)
                ep = gc(v["sym"], v["ed"])
                if ep is None or ep <= 0: continue
                all_events.append(v)
        n_events = len(all_events)
        streak_rets = []
        random_rets = []
        for e in all_events:
            sym = e["sym"]; ed = e["ed"]
            xd = (datetime.strptime(ed, "%Y-%m-%d") + timedelta(days=120)).date().isoformat()
            en = gc(sym, ed); xn = gc(sym, xd)
            g_streak = cret(en, xn)
            n_streak = nret(g_streak)
            if n_streak is None: continue
            sdf = sym_data.get(sym)
            if sdf is None: continue
            year = int(ed[:4])
            year_dates = [d for d in sdf["date"].astype(str).str[:10] if d.startswith(str(year))]
            if len(year_dates) < 20: continue
            for _ in range(5):
                rds = rng_sanity.choice(year_dates)
                if rds != ed: break
            re = gc(sym, rds)
            if re is None or re <= 0: continue
            rx = gc(sym, (datetime.strptime(rds, "%Y-%m-%d") + timedelta(days=120)).date().isoformat())
            g_rand = cret(re, rx)
            n_rand = nret(g_rand)
            if n_rand is None: continue
            streak_rets.append(n_streak)
            random_rets.append(n_rand)
        s_arr = np.array(streak_rets)
        r_arr = np.array(random_rets)
        if len(s_arr) == 0:
            print(f"--- {label} no pairs ---")
            continue
        diff = s_arr - r_arr
        print(f"--- {label} (n_events={n_events}, paired={len(s_arr)}) ---")
        print(f"  Streak 120d: mean={float(s_arr.mean()):+.2f}% med={float(np.median(s_arr)):+.2f}% wr={float((s_arr>0).mean()*100):.1f}%")
        print(f"  Random 120d: mean={float(r_arr.mean()):+.2f}% med={float(np.median(r_arr)):+.2f}% wr={float((r_arr>0).mean()*100):.1f}%")
        print(f"  Diff mean={float(diff.mean()):+.2f}% med={float(np.median(diff)):+.2f}% streak wins {float((diff>0).mean()*100):.1f}% of pairs")
    print(f"\nTotal time: {time.time()-t0:.1f}s")


if __name__ == "__main__":
    import sys
    sys.exit(main())
