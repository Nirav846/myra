"""
DCB + Fund Traction Combined Backtest
=====================================
Identifies stocks with DCB discount > min_discount AND traction score > threshold,
then measures forward returns over a configurable horizon.

Supports single-run and grid-search modes.

Usage:
    # Single run
    python tools/backtest_dcb_traction.py --min-score 50 --horizon 1

    # Grid search (default thresholds and horizons)
    python tools/backtest_dcb_traction.py --grid

    # Custom grid
    python tools/backtest_dcb_traction.py --grid --thresholds 30,50,70 --horizons 1,3,6
"""

import argparse
import json
import os
import sqlite3
import sys
from dataclasses import dataclass, asdict

import numpy as np

# -- Constants ---------------------------------------------------------------
DB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "myra_app", "db")
TECH_DB = os.path.join(DB_DIR, "myra_technical.db")
VAL_DB = os.path.join(DB_DIR, "myra_valuation.db")
META_DB = os.path.join(DB_DIR, "myra_metadata.db")
DCB_WINDOW = 120

# -- Cache for expensive lookups ---------------------------------------------
_close_cache: dict[tuple, float | None] = {}
_bench_cache: dict[str, float | None] = {}
_dcb_cache: dict[tuple, float | None] = {}


def _reset_cache():
    global _close_cache, _bench_cache, _dcb_cache
    _close_cache = {}
    _bench_cache = {}
    _dcb_cache = {}


# -- Data access -------------------------------------------------------------

def get_traction_months() -> list[str]:
    conn = sqlite3.connect(VAL_DB)
    rows = conn.execute("SELECT DISTINCT month FROM fund_traction ORDER BY month").fetchall()
    conn.close()
    return [r[0] for r in rows]


def get_traction_stocks(month: str, min_score: float) -> dict[str, float]:
    conn = sqlite3.connect(VAL_DB)
    rows = conn.execute(
        "SELECT symbol, traction_score FROM fund_traction WHERE month = ? AND traction_score >= ?",
        (month, min_score),
    ).fetchall()
    conn.close()
    return {r[0]: r[1] for r in rows}


def get_month_end_date(month: str) -> str:
    year, m = (int(x) for x in month.split("-"))
    if m == 12:
        end = f"{year + 1}-01-05"
    else:
        end = f"{year}-{m + 1:02d}-05"
    start = f"{year}-{m:02d}-01"
    conn = sqlite3.connect(TECH_DB)
    row = conn.execute(
        "SELECT MAX(date) FROM technical_data WHERE date >= ? AND date <= ?",
        (start, end),
    ).fetchone()
    conn.close()
    return row[0] if row and row[0] else f"{year}-{m:02d}-28"


def get_forward_date(month: str, months_ahead: int) -> str | None:
    year, m = (int(x) for x in month.split("-"))
    m += months_ahead
    while m > 12:
        m -= 12
        year += 1
    target = f"{year}-{m:02d}-01"
    end = f"{year}-{m:02d}-28" if m < 12 else f"{year + 1}-01-10"
    conn = sqlite3.connect(TECH_DB)
    row = conn.execute(
        "SELECT MIN(date) FROM technical_data WHERE date >= ? AND date <= ?",
        (target, end),
    ).fetchone()
    conn.close()
    return row[0] if row else None


def get_close(symbol: str, trade_date: str) -> float | None:
    key = (symbol, trade_date)
    if key in _close_cache:
        return _close_cache[key]
    conn = sqlite3.connect(TECH_DB)
    row = conn.execute(
        "SELECT close FROM technical_data WHERE symbol = ? AND date <= ? ORDER BY date DESC LIMIT 1",
        (symbol, trade_date),
    ).fetchone()
    conn.close()
    val = float(row[0]) if row else None
    _close_cache[key] = val
    return val


def get_benchmark_close(trade_date: str) -> float | None:
    if trade_date in _bench_cache:
        return _bench_cache[trade_date]
    conn = sqlite3.connect(META_DB)
    row = conn.execute(
        "SELECT close FROM benchmarks WHERE symbol = '^NSEI' AND date <= ? ORDER BY date DESC LIMIT 1",
        (trade_date,),
    ).fetchone()
    conn.close()
    val = float(row[0]) if row else None
    _bench_cache[trade_date] = val
    return val


def compute_dcb(symbol: str, as_of_date: str, window: int = DCB_WINDOW) -> float | None:
    key = (symbol, as_of_date)
    if key in _dcb_cache:
        return _dcb_cache[key]
    conn = sqlite3.connect(TECH_DB)
    rows = conn.execute(
        """SELECT close, delivery_pct FROM technical_data
        WHERE symbol = ? AND date <= ?
        ORDER BY date DESC LIMIT ?""",
        (symbol, as_of_date, window),
    ).fetchall()
    conn.close()

    if len(rows) < window * 0.6:
        _dcb_cache[key] = None
        return None

    closes = np.array([r[0] for r in reversed(rows)], dtype=float)
    del_pcts = np.array([r[1] if r[1] is not None else 0.0 for r in reversed(rows)], dtype=float)

    avg_del = np.nanmean(del_pcts)
    mask = del_pcts > avg_del
    if mask.sum() == 0:
        _dcb_cache[key] = None
        return None

    val = float(np.average(closes[mask], weights=del_pcts[mask]))
    _dcb_cache[key] = val
    return val


# -- Analytics ---------------------------------------------------------------

def compute_return(entry_price: float, exit_price: float) -> float:
    if entry_price <= 0:
        return 0.0
    return (exit_price - entry_price) / entry_price * 100


def sharpe_ratio(returns: list[float], annualize: bool = False) -> float:
    if len(returns) < 2:
        return 0.0
    arr = np.array(returns)
    mean = np.mean(arr)
    std = np.std(arr, ddof=1)
    if std == 0:
        return 0.0
    factor = np.sqrt(12) if annualize else 1.0
    return float(mean / std * factor)


@dataclass
class BacktestResult:
    threshold: float
    horizon: int
    n_signals: int
    avg_return: float
    win_rate: float
    sharpe: float
    benchmark_avg: float
    excess_vs_benchmark: float
    monthly_detail: list[dict]


def run_single_backtest(
    min_score: float,
    min_discount: float,
    horizon: int,
    max_stocks: int,
    verbose: bool = False,
) -> BacktestResult:
    """Run backtest for a single threshold + horizon combo."""
    _reset_cache()
    months = get_traction_months()

    all_combined_rets = []
    all_bench_rets = []
    monthly_detail = []

    for month in months:
        entry_date = get_month_end_date(month)
        fwd_date = get_forward_date(month, horizon)
        if not fwd_date:
            if verbose:
                print(f"  {month}: no forward date for {horizon}M horizon, skipping")
            continue

        # Traction stocks
        traction_stocks = get_traction_stocks(month, min_score)

        # DCB signals (filter to traction stocks, compute DCB for each)
        dcb_signals = []
        for symbol in list(traction_stocks.keys())[:max_stocks]:
            dcb = compute_dcb(symbol, entry_date)
            if dcb is not None:
                close = get_close(symbol, entry_date)
                if close and close > 0:
                    discount = (dcb - close) / dcb * 100
                    if discount >= min_discount:
                        dcb_signals.append((symbol, discount, traction_stocks[symbol]))

        combined = list(dcb_signals)

        # Benchmark return
        bench_entry = get_benchmark_close(entry_date)
        bench_exit = get_benchmark_close(fwd_date)
        bench_ret = compute_return(bench_entry, bench_exit) if bench_entry and bench_exit else None
        if bench_ret is not None:
            all_bench_rets.append(bench_ret)

        # Combined returns
        month_rets = []
        for symbol, *_ in combined[:50]:
            entry_price = get_close(symbol, entry_date)
            exit_price = get_close(symbol, fwd_date)
            if entry_price and exit_price:
                ret = compute_return(entry_price, exit_price)
                month_rets.append(ret)

        all_combined_rets.extend(month_rets)

        n_sig = len(combined)
        avg_ret = float(np.mean(month_rets)) if month_rets else 0.0
        wins = sum(1 for r in month_rets if r > 0)
        wr = wins / len(month_rets) * 100 if month_rets else 0.0

        monthly_detail.append({
            "month": month,
            "entry_date": entry_date,
            "fwd_date": fwd_date,
            "n_signals": n_sig,
            "avg_return": round(avg_ret, 2),
            "win_rate": round(wr, 1),
            "benchmark_return": round(bench_ret, 2) if bench_ret is not None else None,
        })

        if verbose:
            bstr = f"bench={bench_ret:+.2f}%" if bench_ret is not None else "bench=N/A"
            print(f"  {month}: n={n_sig} avg={avg_ret:+.2f}% wr={wr:.0f}% {bstr}")

    # Aggregate
    n = len(all_combined_rets)
    avg = float(np.mean(all_combined_rets)) if n > 0 else 0.0
    wr = sum(1 for r in all_combined_rets if r > 0) / n * 100 if n > 0 else 0.0
    sr = sharpe_ratio(all_combined_rets, annualize=True)
    bench_avg = float(np.mean(all_bench_rets)) if all_bench_rets else 0.0
    excess = avg - bench_avg

    return BacktestResult(
        threshold=min_score,
        horizon=horizon,
        n_signals=n,
        avg_return=round(avg, 2),
        win_rate=round(wr, 1),
        sharpe=round(sr, 2),
        benchmark_avg=round(bench_avg, 2),
        excess_vs_benchmark=round(excess, 2),
        monthly_detail=monthly_detail,
    )


def run_grid_search(
    thresholds: list[float],
    horizons: list[int],
    min_discount: float,
    max_stocks: int,
) -> list[BacktestResult]:
    """Run backtest for every (threshold, horizon) combination."""
    results = []
    total = len(thresholds) * len(horizons)
    done = 0
    for h in horizons:
        for t in thresholds:
            done += 1
            print(f"[{done}/{total}] threshold={t} horizon={h}M ...", end=" ", flush=True)
            r = run_single_backtest(t, min_discount, h, max_stocks, verbose=False)
            print(f"n={r.n_signals} avg={r.avg_return:+.2f}% sr={r.sharpe:.2f}")
            results.append(r)
    return results


def print_markdown_table(results: list[BacktestResult]):
    """Print a Markdown summary table."""
    print()
    print("| Threshold | Horizon | N Trades | Avg Return | Win% | Sharpe | Benchmark | Excess |")
    print("|-----------|---------|----------|------------|------|--------|-----------|--------|")
    for r in sorted(results, key=lambda x: (-x.excess_vs_benchmark, -x.sharpe)):
        if r.n_signals == 0:
            continue
        print(
            f"| {r.threshold:>9.0f} | {r.horizon:>7d}M | {r.n_signals:>8d} "
            f"| {r.avg_return:>+9.2f}% | {r.win_rate:>5.1f}% "
            f"| {r.sharpe:>6.2f} | {r.benchmark_avg:>+8.2f}% "
            f"| {r.excess_vs_benchmark:>+7.2f}% |"
        )


def main():
    parser = argparse.ArgumentParser(description="DCB + Fund Traction Backtest")
    parser.add_argument("--min-score", type=float, default=50,
                        help="Min traction score threshold (default: 50)")
    parser.add_argument("--min-discount", type=float, default=15,
                        help="Min DCB discount %% (default: 15)")
    parser.add_argument("--horizon", type=int, default=1,
                        help="Forward return horizon in months (default: 1)")
    parser.add_argument("--max-stocks", type=int, default=200,
                        help="Max stocks to compute DCB for per month (default: 200)")
    parser.add_argument("--grid", action="store_true",
                        help="Run grid search over thresholds and horizons")
    parser.add_argument("--thresholds", type=str, default="10,20,30,40,50,60,70,80",
                        help="Comma-separated traction score thresholds for grid search")
    parser.add_argument("--horizons", type=str, default="1,3,6",
                        help="Comma-separated horizons (months) for grid search")
    parser.add_argument("--json", type=str, default="",
                        help="Save grid results to JSON file")
    args = parser.parse_args()

    if args.grid:
        thresholds = [float(x.strip()) for x in args.thresholds.split(",")]
        horizons = [int(x.strip()) for x in args.horizons.split(",")]
        print(f"Grid search: thresholds={thresholds} horizons={horizons}")
        print(f"DCB discount >= {args.min_discount}%, max_stocks={args.max_stocks}")
        results = run_grid_search(thresholds, horizons, args.min_discount, args.max_stocks)
        print_markdown_table(results)
        if args.json:
            out = [asdict(r) for r in results]
            with open(args.json, "w") as f:
                json.dump(out, f, indent=2)
            print(f"\nResults saved to {args.json}")
    else:
        r = run_single_backtest(args.min_score, args.min_discount, args.horizon,
                                args.max_stocks, verbose=True)
        print(f"\n{'='*60}")
        print(f"RESULT: threshold={r.threshold} horizon={r.horizon}M")
        print(f"  Signals: {r.n_signals}")
        print(f"  Avg Return: {r.avg_return:+.2f}%")
        print(f"  Win Rate: {r.win_rate:.1f}%")
        print(f"  Sharpe (annualized): {r.sharpe:.2f}")
        print(f"  Benchmark: {r.benchmark_avg:+.2f}%")
        print(f"  Excess vs Benchmark: {r.excess_vs_benchmark:+.2f}%")
        print(f"{'='*60}")


if __name__ == "__main__":
    main()
