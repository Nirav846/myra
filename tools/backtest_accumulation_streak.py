"""Accumulation streak backtest harness — smoke test.

For each monthly scan date from 2021-01-01 to 2026-06-01, detect silent
accumulation streaks ending in the lookback window across the NIFTY 500
universe, compute forward returns at horizons [20, 40, 60, 90, 120] days,
and compare against two baselines: (a) random entry on the same
date/universe, and (b) NIFTY 50 buy-and-hold.

Default thresholds (smoke test):
  deliveryPctThreshold=60, minStreakLength=3, priceFlatLookback=5

Dedup rule: each streak is counted once by its end_date — if a streak's
end_date falls inside multiple monthly lookback windows, only the first
window that sees it records it.
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

DEFAULT_HORIZONS = [20, 40, 60, 90, 120]
LOOKBACK_DAYS = 180  # detect streaks ending in last 180 days
MIN_ROWS = max(55, int(LOOKBACK_DAYS * 0.6) + 5)
BROKERAGE_PCT = 0.5
STCG_RATE = 0.15

DEFAULT_START = "2021-01-01"
DEFAULT_END = "2026-06-01"
DEFAULT_N_SYMBOLS = 500
SCAN_MONTHS = 1  # monthly scan dates

# -- Persistent connections --------------------------------------------------

_tech_conn: sqlite3.Connection | None = None
_meta_conn: sqlite3.Connection | None = None
_close_cache: dict[tuple, float | None] = {}
_bench_cache: dict[str, float | None] = {}


def _get_tech_conn() -> sqlite3.Connection:
    global _tech_conn
    if _tech_conn is None:
        _tech_conn = sqlite3.connect(TECH_DB)
    return _tech_conn


def _get_meta_conn() -> sqlite3.Connection:
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
        "SELECT close FROM technical_data WHERE symbol = ? AND date <= ? "
        "ORDER BY date DESC LIMIT 1",
        (symbol, trade_date),
    ).fetchone()
    val = float(row[0]) if row else None
    _close_cache[key] = val
    return val


def get_benchmark_close(trade_date: str) -> float | None:
    if trade_date in _bench_cache:
        return _bench_cache[trade_date]
    conn = _get_meta_conn()
    row = conn.execute(
        "SELECT close FROM benchmarks WHERE symbol = '^NSEI' AND date <= ? "
        "ORDER BY date DESC LIMIT 1",
        (trade_date,),
    ).fetchone()
    val = float(row[0]) if row else None
    _bench_cache[trade_date] = val
    return val


def max_tech_date() -> str:
    conn = _get_tech_conn()
    row = conn.execute("SELECT MAX(date) FROM technical_data").fetchone()
    return row[0] if row and row[0] else date.today().isoformat()


def compute_return(entry_price: float, exit_price: float) -> float:
    if entry_price is None or exit_price is None or entry_price <= 0:
        return 0.0
    return (exit_price - entry_price) / entry_price * 100


def cost_adjusted_return(gross: float) -> float:
    net = gross - BROKERAGE_PCT * 2
    if gross > 0:
        net -= STCG_RATE * gross
    return net


# -- Universe ----------------------------------------------------------------

def get_universe() -> list[str]:
    """Return all symbols present in technical_data."""
    conn = _get_tech_conn()
    rows = conn.execute(
        "SELECT DISTINCT symbol FROM technical_data"
    ).fetchall()
    return [r[0].strip() for r in rows]


# -- Scan harness ------------------------------------------------------------

def collect_streaks(
    streak_end_dates_seen: set[str],
    sampled_symbols: list[str],
    scan_date: date,
    bulk_cache: dict[str, pd.DataFrame] | None,
    delivery_pct_threshold: float,
    min_streak_length: int,
    price_flat_lookback: int,
) -> list[dict]:
    """Detect accumulation streaks ending in [scan_date - lookback, scan_date].

    Dedup: only streaks whose end_date has not been seen before are returned.
    Updates streak_end_dates_seen in place.
    """
    # Import detection function (tools/ is already on sys.path via the
    # sys.path.insert at module level, but the parent dir is needed too).
    tools_dir = os.path.dirname(os.path.abspath(__file__))
    if tools_dir not in sys.path:
        sys.path.insert(0, tools_dir)
    from detect_accumulation_streak import detect_accumulation_streaks

    scan_date_s = scan_date.isoformat()
    lookback_start = (scan_date - timedelta(days=LOOKBACK_DAYS)).isoformat()

    # Bulk load OHLCV for the window.
    bulk = load_ohlcv_for_universe(
        lookback_start, scan_date_s, symbols=sampled_symbols
    )

    streaks_out: list[dict] = []
    for symbol in sampled_symbols:
        df = bulk.get(symbol)
        if df is None or len(df) < MIN_ROWS:
            continue

        df = df.sort_values("date").reset_index(drop=True)

        # Detect streaks.
        streaks = detect_accumulation_streaks(
            df,
            delivery_pct_threshold=delivery_pct_threshold,
            min_streak_length=min_streak_length,
            price_flat_lookback=price_flat_lookback,
        )

        for s in streaks:
            # Only streaks ending in the lookback window.
            if s.end_date < lookback_start or s.end_date > scan_date_s:
                continue
            # Dedup by end_date.
            dedup_key = f"{symbol}|{s.end_date}"
            if dedup_key in streak_end_dates_seen:
                continue
            streak_end_dates_seen.add(dedup_key)

            # Entry price = close on streak end date.
            entry_price = get_close(symbol, s.end_date)
            if entry_price is None or entry_price <= 0:
                continue

            streaks_out.append({
                "symbol": symbol,
                "event": "ACC_STREAK",
                "event_date": s.end_date,
                "close": entry_price,
                "quality": s.strength_score,
                "streak_length": s.streak_length,
                "avg_delivery_pct": s.avg_delivery_pct,
                "_scan_date": scan_date_s,
                "_rows_in_window": len(df),
            })

    return streaks_out


def build_forward_row(e: dict, horizon: int, use_costs: bool) -> dict:
    """Compute entry/exit returns + benchmark excess for one streak, one horizon."""
    sym = e["symbol"]
    entry_date = e["event_date"]
    exit_date = (
        (datetime.strptime(entry_date, "%Y-%m-%d") + timedelta(days=horizon))
        .date()
        .isoformat()
    )

    raw = {
        "symbol": sym,
        "event": e["event"],
        "event_date": entry_date,
        "scan_date": e["_scan_date"],
        "quality": e["quality"],
        "streak_length": e["streak_length"],
        "avg_delivery_pct": e["avg_delivery_pct"],
        "close": e["close"],
        "rows_in_window": e["_rows_in_window"],
        "horizon": horizon,
        "entry_date": entry_date,
        "exit_date": exit_date,
    }

    entry = get_close(sym, entry_date)
    exitp = get_close(sym, exit_date)
    raw["entry_price"] = entry
    raw["exit_price"] = exitp
    raw["gross_return"] = compute_return(entry, exitp) if entry and exitp else None

    bench_entry = get_benchmark_close(entry_date)
    bench_exit = get_benchmark_close(exit_date)
    raw["bench_return"] = (
        compute_return(bench_entry, bench_exit) if bench_entry and bench_exit else None
    )

    if raw["gross_return"] is not None:
        raw["net_return"] = (
            cost_adjusted_return(raw["gross_return"]) if use_costs else raw["gross_return"]
        )
        raw["excess"] = (
            raw["net_return"] - raw["bench_return"]
            if raw["bench_return"] is not None
            else None
        )
        raw["win"] = bool(raw["net_return"] > 0)
    else:
        raw["net_return"] = None
        raw["excess"] = None
        raw["win"] = None
    return raw


# -- Baselines ----------------------------------------------------------------

def compute_random_baseline(
    sampled_symbols: list[str],
    scan_dates: list[date],
    horizons: list[int],
    n_random_per_scan: int = 20,
    seed: int = 42,
) -> pd.DataFrame:
    """Random entry baseline: pick random symbols at each scan date."""
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
                gross = compute_return(entry, exitp) if exitp else None
                bench_e = get_benchmark_close(scan_s)
                bench_x = get_benchmark_close(exit_d)
                bench_ret = compute_return(bench_e, bench_x) if bench_e and bench_x else None
                net = cost_adjusted_return(gross) if gross is not None else None
                rows.append({
                    "horizon": h,
                    "gross_return": gross,
                    "net_return": net,
                    "bench_return": bench_ret,
                    "excess": (net - bench_ret if net is not None and bench_ret is not None else None),
                })
    return pd.DataFrame(rows)


def compute_nifty50_baseline(
    horizons: list[int],
    scan_dates: list[date],
) -> pd.DataFrame:
    """NIFTY 50 buy-and-hold baseline at each scan date."""
    # Use the earliest scan date as the entry point for a single B&H baseline,
    # but also measure at each scan date entry for fair comparison.
    rows: list[dict] = []
    for scan_d in scan_dates:
        scan_s = scan_d.isoformat()
        entry = get_benchmark_close(scan_s)
        if entry is None or entry <= 0:
            continue
        for h in horizons:
            exit_d = (scan_d + timedelta(days=h)).isoformat()
            exitp = get_benchmark_close(exit_d)
            gross = compute_return(entry, exitp) if exitp else None
            net = cost_adjusted_return(gross) if gross is not None else None
            rows.append({
                "horizon": h,
                "gross_return": gross,
                "net_return": net,
                "bench_return": gross,  # for B&H, benchmark = itself
                "excess": 0.0,
            })
    return pd.DataFrame(rows)


# -- Summary ------------------------------------------------------------------

def print_summary(
    signal_df: pd.DataFrame,
    random_df: pd.DataFrame,
    nifty50_df: pd.DataFrame,
    horizons: list[int],
    use_costs: bool,
):
    label = "Net (cost-adj)" if use_costs else "Gross"
    print()
    print("=" * 100)
    print(f"ACCUMULATION STREAK SMOKE TEST — return metric: {label}")
    print("=" * 100)

    sig = signal_df[signal_df["net_return"].notna()]
    rnd = random_df[random_df["net_return"].notna()]
    n50 = nifty50_df[nifty50_df["net_return"].notna()]

    header = (
        f"| {'Horizon':>7} | {'N':>5} | {'MeanRet':>8} | {'MedRet':>8} | "
        f"{'Win%':>6} | {'Excess':>8} | {'RndMean':>8} | {'RndExcs':>8} | "
        f"{'N50Mean':>8} | {'SNR':>6} |"
    )
    print(header)
    print("|" + "-" * (len(header) - 2) + "|")

    for h in horizons:
        s_h = sig[sig["horizon"] == h]
        r_h = rnd[rnd["horizon"] == h]
        n_h = n50[n50["horizon"] == h]

        n = len(s_h)
        if n == 0:
            print(f"| {h:>7d} | {'0':>5} | {'N/A':>8} | {'N/A':>8} | {'N/A':>6} | {'N/A':>8} | {'N/A':>8} | {'N/A':>8} | {'N/A':>8} | {'N/A':>6} |")
            continue

        mean_ret = float(s_h["net_return"].mean())
        med_ret = float(s_h["net_return"].median())
        win_rate = float((s_h["net_return"] > 0).mean() * 100)
        exc = s_h["excess"].dropna()
        excess = float(exc.mean()) if len(exc) else None

        rnd_mean = float(r_h["net_return"].mean()) if len(r_h) else None
        rnd_exc = float(r_h["excess"].dropna().mean()) if len(r_h) and r_h["excess"].dropna().any() else None

        n50_mean = float(n_h["net_return"].mean()) if len(n_h) else None

        # SNR: signal mean / signal std (higher = cleaner signal)
        snr = float(mean_ret / s_h["net_return"].std()) if s_h["net_return"].std() > 0 else None

        def _fmt(v):
            return "N/A" if v is None else f"{v:+.2f}%"

        print(
            f"| {h:>7d} | {n:>5d} | {_fmt(mean_ret):>8} | {_fmt(med_ret):>8} | "
            f"{win_rate:>5.1f}% | {_fmt(excess):>8} | {_fmt(rnd_mean):>8} | "
            f"{_fmt(rnd_exc):>8} | {_fmt(n50_mean):>8} | {snr if snr is not None else 'N/A':>6} |"
        )


# -- Main ---------------------------------------------------------------------

def parse_args(argv):
    p = argparse.ArgumentParser(description="Accumulation streak backtest (smoke test)")
    p.add_argument("--start", default=DEFAULT_START, help="First scan date (YYYY-MM-DD)")
    p.add_argument("--end", default=DEFAULT_END, help="Last scan date (YYYY-MM-DD)")
    p.add_argument(
        "--horizons",
        default=",".join(map(str, DEFAULT_HORIZONS)),
        help="Comma-separated forward horizons in calendar days",
    )
    p.add_argument("--n-symbols", type=int, default=DEFAULT_N_SYMBOLS)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--no-costs", action="store_true")
    p.add_argument("--out", default="accum_streak_backtest_results.csv")
    p.add_argument("--delivery-pct-threshold", type=float, default=60.0)
    p.add_argument("--min-streak-length", type=int, default=3)
    p.add_argument("--price-flat-lookback", type=int, default=5)
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    horizons = [int(h.strip()) for h in args.horizons.split(",") if h.strip()]
    use_costs = not args.no_costs

    mtd = max_tech_date()
    end_guard = (
        datetime.strptime(mtd, "%Y-%m-%d") - timedelta(days=max(horizons))
    ).date()
    start = datetime.strptime(args.start, "%Y-%m-%d").date()
    end = min(datetime.strptime(args.end, "%Y-%m-%d").date(), end_guard)

    if start >= end:
        print(f"ERROR: start ({start}) must be before end ({end}).", file=sys.stderr)
        return 1

    # Universe.
    universe = get_universe()
    print(f"Universe size: {len(universe)}")

    random.seed(args.seed)
    sampled = random.sample(universe, min(args.n_symbols, len(universe)))
    print(f"Sampled symbols: {len(sampled)} (seed={args.seed})")

    # Monthly scan dates.
    scan_dates: list[date] = []
    current = start
    while current <= end:
        scan_dates.append(current)
        # Advance by ~1 month (30 days).
        current += timedelta(days=30)
    print(f"Scan dates ({len(scan_dates)}): {scan_dates[0].isoformat()} to {scan_dates[-1].isoformat()}")

    # Collect streaks with dedup.
    streak_end_dates_seen: set[str] = set()
    all_events: list[dict] = []
    for D in scan_dates:
        evs = collect_streaks(
            streak_end_dates_seen,
            sampled,
            D,
            None,
            args.delivery_pct_threshold,
            args.min_streak_length,
            args.price_flat_lookback,
        )
        print(f"  {D.isoformat()}: {len(evs):4d} new streaks (cumul: {len(all_events)})")
        all_events.extend(evs)

    print(f"\nTotal unique streaks collected: {len(all_events)}")
    if not all_events:
        print("No streaks found — nothing to summarise.")
        return 0

    # Streak stats.
    streak_lengths = [e["streak_length"] for e in all_events]
    strengths = [e["quality"] for e in all_events]
    print(f"Streak length: min={min(streak_lengths)}, max={max(streak_lengths)}, "
          f"mean={np.mean(streak_lengths):.1f}, median={np.median(streak_lengths):.1f}")
    print(f"Strength score: min={min(strengths):.1f}, max={max(strengths):.1f}, "
          f"mean={np.mean(strengths):.1f}")

    # Forward returns.
    rows: list[dict] = []
    for e in all_events:
        for h in horizons:
            rows.append(build_forward_row(e, h, use_costs))

    signal_df = pd.DataFrame(rows)
    signal_df.to_csv(args.out, index=False)
    print(f"\nPer-event detail written to {args.out}")

    # Measurability counts.
    measured = signal_df[signal_df["net_return"].notna()].groupby("horizon")["symbol"].count()
    print("\nMeasured forward returns per horizon:")
    for h in horizons:
        print(f"  {h:>3}d: {int(measured.get(h, 0))}")

    # Baselines.
    print("\nComputing baselines...")
    random_baseline = compute_random_baseline(sampled, scan_dates, horizons, seed=args.seed)
    nifty50_baseline = compute_nifty50_baseline(horizons, scan_dates)

    print_summary(signal_df, random_baseline, nifty50_baseline, horizons, use_costs)

    return 0


if __name__ == "__main__":
    sys.exit(main())
