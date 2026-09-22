"""Float Turnover backtest harness — smoke test.

For each monthly scan date, detect Float Turnover first-crossing signals
across the free-float universe, compute forward returns at horizons
[20,60,120] (calendar days), and compare against:

  (a) random entry within same stock/year (sanity check — stock-selection bias)
  (b) NIFTY 500 buy-and-hold

Universe: only symbols with usable free-float data:
  free_float_market_cap >0  OR  (market_cap>0 and free_float_pct>0)
  via fallback ff_mcap = ff_mcap_raw if >0 else mcap*ff_pct/100.
  Matches darvas_box_scanner / accumulation_base_scanner pattern.

Entry: first day Float Turnover(N) crosses above X% per symbol
(detect_float_turnover vectorized, no re-trigger while above).
"""

from __future__ import annotations

import argparse
import os
import random
import sqlite3
import sys
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd

from myra_app.constants import DB_DIR
from myra_app.librarian_core import LibrarianCore
from myra_app.db.bulk_loader import load_ohlcv_for_universe

TECH_DB = os.path.join(DB_DIR, LibrarianCore.DB_MAP["technical"])
META_DB = os.path.join(DB_DIR, LibrarianCore.DB_MAP["meta"])
VAL_DB = os.path.join(DB_DIR, LibrarianCore.DB_MAP["valuation"])

DEFAULT_HORIZONS = [20, 60, 120]
LOOKBACK_DAYS = 180
DEFAULT_WINDOW = 20
DEFAULT_THRESHOLD = 10.0
BROKERAGE_PCT = 0.5
STCG_RATE = 0.15
DEFAULT_START = "2021-01-01"
DEFAULT_END = "2026-06-01"
DEFAULT_N_SYMBOLS = 500

_tech_conn: sqlite3.Connection | None = None
_meta_conn: sqlite3.Connection | None = None
_close_cache: dict[tuple, float | None] = {}
_bench_cache: dict[str, float | None] = {}


def _get_tech_conn():
    global _tech_conn
    if _tech_conn is None:
        _tech_conn = sqlite3.connect(TECH_DB)
    return _tech_conn


def _get_meta_conn():
    global _meta_conn
    if _meta_conn is None:
        _meta_conn = sqlite3.connect(META_DB)
    return _meta_conn


def get_close(symbol: str, trade_date: str) -> float | None:
    key = (symbol, trade_date)
    if key in _close_cache:
        return _close_cache[key]
    conn = _get_tech_conn()
    row = conn.execute(
        "SELECT close FROM technical_data WHERE symbol=? AND date<=? ORDER BY date DESC LIMIT 1",
        (symbol, trade_date),
    ).fetchone()
    val = float(row[0]) if row and row[0] is not None else None
    _close_cache[key] = val
    return val


NIFTY500_CANDIDATES = ["^NIFTY500", "^NSE500", "NIFTY 500", "NIFTY500", "^CRSLDX", "^NSEI"]

def _find_nifty500_symbol() -> str | None:
    conn = _get_meta_conn()
    rows = conn.execute("SELECT DISTINCT symbol FROM benchmarks").fetchall()
    syms = [r[0] for r in rows]
    for cand in NIFTY500_CANDIDATES:
        if cand in syms:
            return cand
    # fallback to NSEI
    if "^NSEI" in syms:
        return "^NSEI"
    return syms[0] if syms else None

_NIFTY500_SYMBOL: str | None = None

def get_benchmark_close(trade_date: str) -> float | None:
    global _NIFTY500_SYMBOL
    if _NIFTY500_SYMBOL is None:
        _NIFTY500_SYMBOL = _find_nifty500_symbol()
    if trade_date in _bench_cache:
        return _bench_cache[trade_date]
    if _NIFTY500_SYMBOL is None:
        return None
    conn = _get_meta_conn()
    row = conn.execute(
        "SELECT close FROM benchmarks WHERE symbol=? AND date<=? ORDER BY date DESC LIMIT 1",
        (_NIFTY500_SYMBOL, trade_date),
    ).fetchone()
    val = float(row[0]) if row and row[0] is not None else None
    _bench_cache[trade_date] = val
    return val


def max_tech_date() -> str:
    conn = _get_tech_conn()
    row = conn.execute("SELECT MAX(date) FROM technical_data").fetchone()
    return row[0] if row and row[0] else date.today().isoformat()


def compute_return(entry_price: float | None, exit_price: float | None) -> float | None:
    if entry_price is None or exit_price is None or entry_price <= 0:
        return None
    return (exit_price - entry_price) / entry_price * 100


def cost_adjusted_return(gross: float | None) -> float | None:
    if gross is None:
        return None
    net = gross - BROKERAGE_PCT * 2
    if gross > 0:
        net -= STCG_RATE * gross
    return net


def get_universe_with_ff() -> tuple[dict[str, float], dict]:
    """Return {symbol: ff_mcap} for usable symbols and stats about exclusions."""
    # All technical symbols
    tconn = _get_tech_conn()
    tech_syms = set(r[0].strip() for r in tconn.execute("SELECT DISTINCT symbol FROM technical_data").fetchall())
    # Valuation latest rows
    vconn = sqlite3.connect(VAL_DB)
    rows = vconn.execute(
        """
        SELECT f.symbol, f.market_cap, f.free_float_pct, f.free_float_market_cap
        FROM fundamentals f
        INNER JOIN (
            SELECT symbol, MAX(COALESCE(date,'')) as max_date
            FROM fundamentals
            WHERE COALESCE(market_cap,0) > 0
            GROUP BY symbol
        ) latest ON f.symbol = latest.symbol AND COALESCE(f.date,'') = latest.max_date
        """,
    ).fetchall()
    vconn.close()
    ff_map: dict[str, float] = {}
    stats = {
        "tech_total": len(tech_syms),
        "valuation_rows": len(rows),
        "no_ff_pct": 0,
        "no_mcap": 0,
        "no_ff_mcap_computed": 0,
        "not_in_tech": 0,
        "usable": 0,
    }
    for sym, mcap, ff_pct, ff_mcap_raw in rows:
        sym = sym.strip()
        if sym not in tech_syms:
            stats["not_in_tech"] += 1
            continue
        # fallback pattern
        ff_mcap = None
        if ff_mcap_raw is not None and ff_mcap_raw > 0:
            ff_mcap = float(ff_mcap_raw)
        elif mcap is not None and mcap > 0 and ff_pct is not None and ff_pct > 0:
            ff_mcap = float(mcap) * float(ff_pct) / 100.0
        if ff_mcap is None or ff_mcap <= 0:
            if mcap is None or mcap <= 0:
                stats["no_mcap"] += 1
            elif ff_pct is None or ff_pct <= 0:
                stats["no_ff_pct"] += 1
            else:
                stats["no_ff_mcap_computed"] += 1
            continue
        ff_map[sym] = ff_mcap
        stats["usable"] += 1
    # Also count tech symbols with no valuation entry at all
    val_syms = set(r[0].strip() for _, r in [(None, (r[0],)) for r in rows])  # dummy
    # Instead compute directly
    vconn2 = sqlite3.connect(VAL_DB)
    all_val_syms = set(r[0].strip() for r in vconn2.execute("SELECT DISTINCT symbol FROM fundamentals").fetchall())
    vconn2.close()
    stats["tech_no_val_row"] = len(tech_syms - all_val_syms)
    return ff_map, stats


def collect_float_turnover_signals(
    ff_map: dict[str, float],
    sampled_symbols: list[str],
    scan_date: date,
    window: int,
    threshold: float,
    seen: set[str],
) -> list[dict]:
    tools_dir = os.path.dirname(os.path.abspath(__file__))
    if tools_dir not in sys.path:
        sys.path.insert(0, tools_dir)
    from detect_float_turnover import detect_float_turnover

    scan_s = scan_date.isoformat()
    lookback_start = (scan_date - timedelta(days=LOOKBACK_DAYS)).isoformat()
    bulk = load_ohlcv_for_universe(lookback_start, scan_s, symbols=sampled_symbols)
    out: list[dict] = []
    for sym in sampled_symbols:
        ff_mcap = ff_map.get(sym)
        if ff_mcap is None or ff_mcap <= 0:
            continue
        df = bulk.get(sym)
        if df is None or len(df) < window + 5:
            continue
        df = df.sort_values("date").reset_index(drop=True)
        signals = detect_float_turnover(df, ff_mcap, window, threshold)
        for sig in signals:
            if sig.date < lookback_start or sig.date > scan_s:
                continue
            dk = f"{sym}|{sig.date}"
            if dk in seen:
                continue
            seen.add(dk)
            entry_price = get_close(sym, sig.date)
            if entry_price is None or entry_price <= 0:
                continue
            out.append(
                {
                    "symbol": sym,
                    "event": "FLOAT_TURNOVER",
                    "event_date": sig.date,
                    "close": entry_price,
                    "turnover_pct": sig.turnover_pct,
                    "window": window,
                    "threshold": threshold,
                    "ff_mcap": ff_mcap,
                    "_scan_date": scan_s,
                }
            )
    return out


def build_forward_row(e: dict, horizon: int, use_costs: bool) -> dict:
    sym = e["symbol"]
    entry_date = e["event_date"]
    exit_date = (datetime.strptime(entry_date, "%Y-%m-%d") + timedelta(days=horizon)).date().isoformat()
    raw = {
        "symbol": sym,
        "event": e["event"],
        "event_date": entry_date,
        "scan_date": e["_scan_date"],
        "turnover_pct": e["turnover_pct"],
        "window": e["window"],
        "threshold": e["threshold"],
        "close": e["close"],
        "horizon": horizon,
        "entry_date": entry_date,
        "exit_date": exit_date,
    }
    entry = get_close(sym, entry_date)
    exitp = get_close(sym, exit_date)
    raw["entry_price"] = entry
    raw["exit_price"] = exitp
    raw["gross_return"] = compute_return(entry, exitp)
    bench_e = get_benchmark_close(entry_date)
    bench_x = get_benchmark_close(exit_date)
    raw["bench_return"] = compute_return(bench_e, bench_x)
    if raw["gross_return"] is not None:
        raw["net_return"] = cost_adjusted_return(raw["gross_return"]) if use_costs else raw["gross_return"]
        raw["excess"] = raw["net_return"] - raw["bench_return"] if raw["bench_return"] is not None else None
        raw["win"] = bool(raw["net_return"] > 0)
    else:
        raw["net_return"] = None
        raw["excess"] = None
        raw["win"] = None
    return raw


def compute_same_stock_random_baseline(
    events: list[dict],
    ff_map: dict[str, float],
    horizons: list[int],
    symbol_data: dict[str, pd.DataFrame] | None = None,
    seed: int = 42,
) -> pd.DataFrame:
    """For each event, pick random date within same stock/year and compute return."""
    rng = random.Random(seed)
    rows: list[dict] = []
    # Need per-symbol data for year sampling
    if symbol_data is None:
        # lazy load minimal: we will sample random date via close cache scan?
        # Instead fallback: random date near event
        for e in events:
            sym = e["symbol"]
            ed = e["event_date"]
            try:
                y = int(ed[:4])
            except:
                continue
            # pick random day in same year within ±180 days window
            # construct random offset
            for h in horizons:
                # random entry: pick a random date in same year from technical_data
                # Use approach: random year date
                # To avoid DB scan per event, pick event date +/- random 30-180 days within same year
                for _ in range(5):
                    delta = rng.randint(-120, 120)
                    rd = (datetime.strptime(ed, "%Y-%m-%d") + timedelta(days=delta)).date()
                    if rd.year != y:
                        continue
                    rds = rd.isoformat()
                    # ensure not same as event
                    if rds == ed:
                        continue
                    re = get_close(sym, rds)
                    if re is None or re <= 0:
                        continue
                    xd = (rd + timedelta(days=h)).isoformat()
                    rx = get_close(sym, xd)
                    gross = compute_return(re, rx)
                    if gross is None:
                        continue
                    net = cost_adjusted_return(gross)
                    rows.append(
                        {
                            "horizon": h,
                            "gross_return": gross,
                            "net_return": net,
                            "pair_type": "same_stock_random",
                        }
                    )
                    break
        return pd.DataFrame(rows)
    # if symbol_data provided, use it for year sampling (more accurate)
    for e in events:
        sym = e["symbol"]
        ed = e["event_date"]
        y = int(ed[:4])
        sdf = symbol_data.get(sym) if symbol_data else None
        if sdf is None:
            continue
        year_dates = sdf["date"].astype(str).str[:10]
        year_dates = [d for d in year_dates if d.startswith(str(y))]
        if len(year_dates) < 20:
            continue
        for h in horizons:
            # streak return
            en = get_close(sym, ed)
            xd = (datetime.strptime(ed, "%Y-%m-%d") + timedelta(days=h)).date().isoformat()
            xn = get_close(sym, xd)
            g_streak = compute_return(en, xn)
            # random
            rds = rng.choice(year_dates)
            if rds == ed:
                for _ in range(5):
                    rds = rng.choice(year_dates)
                    if rds != ed:
                        break
            re = get_close(sym, rds)
            rx = get_close(sym, (datetime.strptime(rds, "%Y-%m-%d") + timedelta(days=h)).date().isoformat())
            g_rand = compute_return(re, rx)
            if g_streak is None or g_rand is None:
                continue
            rows.append(
                {
                    "horizon": h,
                    "streak_net": cost_adjusted_return(g_streak),
                    "random_net": cost_adjusted_return(g_rand),
                }
            )
    return pd.DataFrame(rows)


def compute_random_baseline(
    sampled_symbols: list[str],
    scan_dates: list[date],
    horizons: list[int],
    n_random_per_scan: int = 20,
    seed: int = 42,
) -> pd.DataFrame:
    rng = random.Random(seed)
    rows: list[dict] = []
    for scan_d in scan_dates:
        scan_s = scan_d.isoformat()
        chosen = rng.sample(sampled_symbols, min(n_random_per_scan, len(sampled_symbols)))
        for sym in chosen:
            entry = get_close(sym, scan_s)
            if entry is None or entry <= 0:
                continue
            for h in horizons:
                exit_d = (scan_d + timedelta(days=h)).isoformat()
                exitp = get_close(sym, exit_d)
                gross = compute_return(entry, exitp)
                if gross is None:
                    continue
                net = cost_adjusted_return(gross)
                bench_e = get_benchmark_close(scan_s)
                bench_x = get_benchmark_close(exit_d)
                bench_ret = compute_return(bench_e, bench_x)
                rows.append(
                    {
                        "horizon": h,
                        "gross_return": gross,
                        "net_return": net,
                        "bench_return": bench_ret,
                        "excess": (net - bench_ret if bench_ret is not None else None),
                    }
                )
    return pd.DataFrame(rows)


def compute_nifty500_baseline(
    horizons: list[int],
    scan_dates: list[date],
) -> pd.DataFrame:
    rows: list[dict] = []
    for scan_d in scan_dates:
        scan_s = scan_d.isoformat()
        entry = get_benchmark_close(scan_s)
        if entry is None or entry <= 0:
            continue
        for h in horizons:
            exit_d = (scan_d + timedelta(days=h)).isoformat()
            exitp = get_benchmark_close(exit_d)
            gross = compute_return(entry, exitp)
            if gross is None:
                continue
            net = cost_adjusted_return(gross)
            rows.append(
                {
                    "horizon": h,
                    "gross_return": gross,
                    "net_return": net,
                    "bench_return": gross,
                    "excess": 0.0,
                }
            )
    return pd.DataFrame(rows)


def print_summary(signal_df, same_stock_df, random_df, nifty_df, horizons, use_costs, nifty_label="NIFTY500"):
    label = "Net (cost-adj)" if use_costs else "Gross"
    print()
    print("=" * 110)
    print(f"FLOAT TURNOVER SMOKE TEST — return metric: {label} | NIFTY={_NIFTY500_SYMBOL or 'N/A'} ({nifty_label})")
    print("=" * 110)
    sig = signal_df[signal_df["net_return"].notna()] if "net_return" in signal_df.columns else signal_df
    header = f"| {'Hor':>5} | {'N':>6} | {'Mean':>8} | {'Med':>8} | {'Win%':>6} | {'Excess':>8} | {'RndMean':>8} | {'N500Mean':>8} | {'SNR':>6} |"
    print(header)
    print("|" + "-" * (len(header) - 2) + "|")
    for h in horizons:
        s_h = sig[sig["horizon"] == h] if len(sig) else sig
        r_h = random_df[random_df["horizon"] == h] if len(random_df) else random_df
        n_h = nifty_df[nifty_df["horizon"] == h] if len(nifty_df) else nifty_df
        n = len(s_h)
        if n == 0:
            print(f"| {h:>5d} | {'0':>6} | {'N/A':>8} | {'N/A':>8} | {'N/A':>6} | {'N/A':>8} | {'N/A':>8} | {'N/A':>8} | {'N/A':>6} |")
            continue
        mean_ret = float(s_h["net_return"].mean())
        med_ret = float(s_h["net_return"].median())
        win_rate = float((s_h["net_return"] > 0).mean() * 100)
        exc = s_h["excess"].dropna()
        excess = float(exc.mean()) if len(exc) else None
        rnd_mean = float(r_h["net_return"].mean()) if len(r_h) and "net_return" in r_h.columns and r_h["net_return"].notna().any() else None
        n500_mean = float(n_h["net_return"].mean()) if len(n_h) and "net_return" in n_h.columns and n_h["net_return"].notna().any() else None
        snr = float(mean_ret / s_h["net_return"].std()) if s_h["net_return"].std() > 0 else None

        def _fmt(v):
            return "N/A" if v is None else f"{v:+.2f}%"

        print(
            f"| {h:>5d} | {n:>6d} | {_fmt(mean_ret):>8} | {_fmt(med_ret):>8} | "
            f"{win_rate:>5.1f}% | {_fmt(excess):>8} | {_fmt(rnd_mean):>8} | {_fmt(n500_mean):>8} | {snr:.3f}" if snr is not None else f"| {h:>5d} | {n:>6d} | {_fmt(mean_ret):>8} | {_fmt(med_ret):>8} | {win_rate:>5.1f}% | {_fmt(excess):>8} | {_fmt(rnd_mean):>8} | {_fmt(n500_mean):>8} | N/A |" + " |"
        )
    # same-stock sanity
    if same_stock_df is not None and len(same_stock_df):
        print("\n--- Same-stock random sanity (paired) ---")
        for h in horizons:
            sh = same_stock_df[same_stock_df["horizon"] == h] if "horizon" in same_stock_df.columns else same_stock_df
            if len(sh) == 0:
                continue
            # handle two formats: columns streak_net/random_net vs net_return
            if "streak_net" in sh.columns:
                s = sh["streak_net"].dropna()
                r = sh["random_net"].dropna()
                if len(s) == 0 or len(r) == 0:
                    continue
                diff = s.values - r.values[: len(s)]
                print(
                    f"  {h:>3}d: streak mean={float(s.mean()):+.2f}% med={float(s.median()):+.2f}% wr={float((s>0).mean()*100):.1f}% | "
                    f"random mean={float(r.mean()):+.2f}% med={float(r.median()):+.2f}% wr={float((r>0).mean()*100):.1f}% | "
                    f"streak wins {float((diff>0).mean()*100):.1f}% of pairs (Δ mean {float(diff.mean()):+.2f}%)"
                )


def parse_args(argv):
    p = argparse.ArgumentParser(description="Float Turnover backtest (smoke test)")
    p.add_argument("--start", default=DEFAULT_START)
    p.add_argument("--end", default=DEFAULT_END)
    p.add_argument("--horizons", default=",".join(map(str, DEFAULT_HORIZONS)))
    p.add_argument("--n-symbols", type=int, default=DEFAULT_N_SYMBOLS)
    p.add_argument("--window", type=int, default=DEFAULT_WINDOW, help="Float Turnover window N")
    p.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD, help="Threshold X%")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--no-costs", action="store_true")
    p.add_argument("--out", default="float_turnover_backtest_results.csv")
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    horizons = [int(h.strip()) for h in args.horizons.split(",") if h.strip()]
    use_costs = not args.no_costs
    mtd = max_tech_date()
    end_guard = (datetime.strptime(mtd, "%Y-%m-%d") - timedelta(days=max(horizons))).date()
    start = datetime.strptime(args.start, "%Y-%m-%d").date()
    end = min(datetime.strptime(args.end, "%Y-%m-%d").date(), end_guard)
    if start >= end:
        print(f"ERROR: start {start} >= end {end}", file=sys.stderr)
        return 1
    ff_map, stats = get_universe_with_ff()
    print(f"Universe stats: {stats}")
    print(f"  Usable free-float universe: {len(ff_map)} symbols")
    print(f"  Excluded: valuation_no_ff={stats['no_ff_pct']} mcap_missing={stats['no_mcap']} not_in_tech={stats['not_in_tech']} tech_no_val_row={stats['tech_no_val_row']}")
    universe = list(ff_map.keys())
    print(f"Universe size (for sampling): {len(universe)}")
    random.seed(args.seed)
    sampled = random.sample(universe, min(args.n_symbols, len(universe)))
    print(f"Sampled: {len(sampled)} (seed={args.seed}) window={args.window} threshold={args.threshold}%")
    scan_dates: list[date] = []
    cur = start
    while cur <= end:
        scan_dates.append(cur)
        cur += timedelta(days=30)
    print(f"Scan dates {len(scan_dates)}: {scan_dates[0]}..{scan_dates[-1]}")
    seen: set[str] = set()
    all_events: list[dict] = []
    for D in scan_dates:
        evs = collect_float_turnover_signals(ff_map, sampled, D, args.window, args.threshold, seen)
        print(f"  {D.isoformat()}: {len(evs):4d} new signals (cum {len(all_events)})")
        all_events.extend(evs)
    print(f"\nTotal unique Float Turnover signals: {len(all_events)}")
    if not all_events:
        print("No signals — nothing to summarise.")
        return 0
    # forward returns
    rows: list[dict] = []
    for e in all_events:
        for h in horizons:
            rows.append(build_forward_row(e, h, use_costs))
    signal_df = pd.DataFrame(rows)
    signal_df.to_csv(args.out, index=False)
    print(f"\nDetail written to {args.out}")
    measured = signal_df[signal_df["net_return"].notna()].groupby("horizon")["symbol"].count()
    print("\nMeasured per horizon:")
    for h in horizons:
        print(f"  {h:>3}d: {int(measured.get(h,0))}")
    print("\nComputing baselines...")
    random_baseline = compute_random_baseline(sampled, scan_dates, horizons, seed=args.seed)
    nifty_baseline = compute_nifty500_baseline(horizons, scan_dates)
    # same-stock random baseline (paired) — sample events for comparison
    same_stock_df = compute_same_stock_random_baseline(all_events, ff_map, horizons, seed=args.seed)
    print_summary(signal_df, same_stock_df, random_baseline, nifty_baseline, horizons, use_costs)
    return 0


if __name__ == "__main__":
    sys.exit(main())
