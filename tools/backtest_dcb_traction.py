"""
DCB + Fund Traction Combined Backtest
======================================
For each month with traction data, identifies stocks with:
  - DCB discount > 15% (delivery-weighted cost basis above current price)
  - Traction score > 50 (institutional buying pressure)

Measures forward 1-month, 3-month returns vs benchmark (Nifty 50) and random control.

Usage:
    python tools/backtest_dcb_traction.py [--min-score 50] [--min-discount 15] [--months 4]
"""

import argparse
import os
import sqlite3
import sys
from datetime import date, timedelta

import numpy as np

# ── Constants ────────────────────────────────────────────────────────────────
DB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "myra_app", "db")
TECH_DB = os.path.join(DB_DIR, "myra_technical.db")
VAL_DB = os.path.join(DB_DIR, "myra_valuation.db")
META_DB = os.path.join(DB_DIR, "myra_metadata.db")
DCB_WINDOW = 120  # trading days for delivery cost basis


def get_traction_months() -> list[str]:
    """Return sorted list of available traction months."""
    conn = sqlite3.connect(VAL_DB)
    rows = conn.execute("SELECT DISTINCT month FROM fund_traction ORDER BY month").fetchall()
    conn.close()
    return [r[0] for r in rows]


def get_traction_stocks(month: str, min_score: float) -> dict[str, float]:
    """Return {symbol: traction_score} for stocks above threshold."""
    conn = sqlite3.connect(VAL_DB)
    rows = conn.execute(
        "SELECT symbol, traction_score FROM fund_traction WHERE month = ? AND traction_score >= ?",
        (month, min_score),
    ).fetchall()
    conn.close()
    return {r[0]: r[1] for r in rows}


def get_month_end_date(month: str) -> str:
    """Convert YYYY-MM to the last trading day available in technical_data."""
    year, m = (int(x) for x in month.split("-"))
    # Use the 28th as minimum, then find actual last trading day
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
    """Get the approximate date N months ahead for return calculation."""
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
    """Get close price for a symbol on a specific date (or nearest before)."""
    conn = sqlite3.connect(TECH_DB)
    row = conn.execute(
        "SELECT close FROM technical_data WHERE symbol = ? AND date <= ? ORDER BY date DESC LIMIT 1",
        (symbol, trade_date),
    ).fetchone()
    conn.close()
    return float(row[0]) if row else None


def get_benchmark_close(trade_date: str) -> float | None:
    """Get Nifty 50 close on or before trade_date."""
    conn = sqlite3.connect(META_DB)
    row = conn.execute(
        "SELECT close FROM benchmarks WHERE symbol = '^NSEI' AND date <= ? ORDER BY date DESC LIMIT 1",
        (trade_date,),
    ).fetchone()
    conn.close()
    return float(row[0]) if row else None


def compute_dcb(symbol: str, as_of_date: str, window: int = DCB_WINDOW) -> float | None:
    """Compute delivery-weighted cost basis over trailing window."""
    conn = sqlite3.connect(TECH_DB)
    rows = conn.execute(
        """
        SELECT close, delivery_pct FROM technical_data
        WHERE symbol = ? AND date <= ?
        ORDER BY date DESC LIMIT ?
        """,
        (symbol, as_of_date, window),
    ).fetchall()
    conn.close()

    if len(rows) < window * 0.6:
        return None

    closes = np.array([r[0] for r in reversed(rows)], dtype=float)
    del_pcts = np.array([r[1] if r[1] is not None else 0.0 for r in reversed(rows)], dtype=float)

    avg_del = np.nanmean(del_pcts)
    mask = del_pcts > avg_del
    if mask.sum() == 0:
        return None

    return float(np.average(closes[mask], weights=del_pcts[mask]))


def compute_return(entry_price: float, exit_price: float) -> float:
    """Percentage return."""
    if entry_price <= 0:
        return 0.0
    return (exit_price - entry_price) / entry_price * 100


def sharpe_ratio(returns: list[float], annualize: bool = False) -> float:
    """Annualized Sharpe ratio (assuming monthly returns, rf=0)."""
    if len(returns) < 2:
        return 0.0
    arr = np.array(returns)
    mean = np.mean(arr)
    std = np.std(arr, ddof=1)
    if std == 0:
        return 0.0
    factor = np.sqrt(12) if annualize else 1.0
    return float(mean / std * factor)


def run_backtest(min_score: float, min_discount: float, max_stocks: int):
    """Main backtest loop."""
    months = get_traction_months()
    print(f"\n{'='*80}")
    print(f"DCB + Fund Traction Combined Backtest")
    print(f"{'='*80}")
    print(f"Parameters: min_score={min_score}, min_discount={min_discount}%, DCB window={DCB_WINDOW} days")
    print(f"Available months: {', '.join(months)}")
    print()

    all_results = {
        "combined": [],     # DCB + Traction
        "dcb_only": [],     # DCB only (no traction filter)
        "traction_only": [], # Traction only (no DCB filter)
        "benchmark": [],    # Nifty 50 buy-and-hold
        "random": [],       # Random control
    }

    for month in months:
        entry_date = get_month_end_date(month)
        print(f"--- Month: {month} (entry date: {entry_date}) ---")

        # Get traction stocks
        traction_stocks = get_traction_stocks(month, min_score)
        print(f"  Traction stocks (score >= {min_score}): {len(traction_stocks)}")

        # Compute DCB for ALL traction stocks (expensive but needed)
        dcb_signals = []
        for symbol in list(traction_stocks.keys())[:max_stocks]:
            dcb = compute_dcb(symbol, entry_date)
            if dcb is not None:
                close = get_close(symbol, entry_date)
                if close and close > 0:
                    discount = (dcb - close) / dcb * 100
                    if discount >= min_discount:
                        dcb_signals.append((symbol, discount, traction_stocks[symbol]))

        print(f"  DCB signals (discount >= {min_discount}%): {len(dcb_signals)}")

        # Combined signal: DCB + Traction
        combined = [(s, d, t) for s, d, t in dcb_signals]
        print(f"  Combined signal (both): {len(combined)}")

        # Get forward dates
        fwd_1m = get_forward_date(month, 1)
        fwd_3m = get_forward_date(month, 3)
        if not fwd_1m:
            print(f"  Skipping: no 1-month forward date available")
            continue

        # Benchmark return
        bench_entry = get_benchmark_close(entry_date)
        bench_exit_1m = get_benchmark_close(fwd_1m) if fwd_1m else None
        bench_exit_3m = get_benchmark_close(fwd_3m) if fwd_3m else None

        bench_ret_1m = compute_return(bench_entry, bench_exit_1m) if bench_exit_1m else None
        bench_ret_3m = compute_return(bench_entry, bench_exit_3m) if bench_exit_3m else None
        if bench_ret_1m is not None:
            all_results["benchmark"].append(bench_ret_1m)
        print(f"  Benchmark (Nifty 50): 1M={bench_ret_1m:+.2f}%, 3M={bench_ret_3m:+.2f}%" if bench_ret_3m else f"  Benchmark: 1M={bench_ret_1m:+.2f}%")

        # Measure returns for each strategy
        for strategy_name, stock_list in [
            ("combined", combined),
            ("dcb_only", [(s, d, 0) for s, d, _ in dcb_signals]),  # ignore traction score
            ("traction_only", [(s, 0, t) for s, t in traction_stocks.items()]),
        ]:
            returns_1m = []
            returns_3m = []
            wins_1m = 0
            wins_3m = 0

            for symbol, *_ in stock_list[:50]:  # cap at 50 per month
                entry_price = get_close(symbol, entry_date)
                if not entry_price:
                    continue

                exit_1m = get_close(symbol, fwd_1m) if fwd_1m else None
                exit_3m = get_close(symbol, fwd_3m) if fwd_3m else None

                if exit_1m:
                    ret = compute_return(entry_price, exit_1m)
                    returns_1m.append(ret)
                    if ret > 0:
                        wins_1m += 1

                if exit_3m:
                    ret = compute_return(entry_price, exit_3m)
                    returns_3m.append(ret)
                    if ret > 0:
                        wins_3m += 1

            if returns_1m:
                avg_1m = np.mean(returns_1m)
                wr_1m = wins_1m / len(returns_1m) * 100
                sr_1m = sharpe_ratio(returns_1m)
                all_results[strategy_name].extend(returns_1m)
                print(f"  {strategy_name:18s}: 1M avg={avg_1m:+.2f}% wr={wr_1m:.0f}% n={len(returns_1m)} sr={sr_1m:.2f}", end="")
                if returns_3m:
                    avg_3m = np.mean(returns_3m)
                    wr_3m = wins_3m / len(returns_3m) * 100
                    sr_3m = sharpe_ratio(returns_3m)
                    print(f" | 3M avg={avg_3m:+.2f}% wr={wr_3m:.0f}% n={len(returns_3m)} sr={sr_3m:.2f}")
                else:
                    print()

        # Random control: pick random stocks from traction pool
        all_traction_syms = list(traction_stocks.keys())
        if len(all_traction_syms) > 10:
            rng = np.random.RandomState(42)  # reproducible
            random_pick = rng.choice(all_traction_syms, min(50, len(all_traction_syms)), replace=False)
            rand_returns_1m = []
            for symbol in random_pick:
                entry_price = get_close(symbol, entry_date)
                exit_1m = get_close(symbol, fwd_1m) if fwd_1m else None
                if entry_price and exit_1m:
                    rand_returns_1m.append(compute_return(entry_price, exit_1m))
            if rand_returns_1m:
                avg_rm = np.mean(rand_returns_1m)
                wr_rm = sum(1 for r in rand_returns_1m if r > 0) / len(rand_returns_1m) * 100
                all_results["random"].extend(rand_returns_1m)
                print(f"  {'random':18s}: 1M avg={avg_rm:+.2f}% wr={wr_rm:.0f}% n={len(rand_returns_1m)}")

        print()

    # ── Summary ──────────────────────────────────────────────────────────────
    print(f"\n{'='*80}")
    print("SUMMARY (all months pooled)")
    print(f"{'='*80}")
    print(f"{'Strategy':<22} {'N trades':>10} {'Avg 1M%':>10} {'Win%':>8} {'Sharpe':>8}")
    print("-" * 60)

    for name in ["combined", "dcb_only", "traction_only", "benchmark", "random"]:
        rets = all_results[name]
        if not rets:
            print(f"{name:<22} {'0':>10} {'N/A':>10} {'N/A':>8} {'N/A':>8}")
            continue
        n = len(rets)
        avg = np.mean(rets)
        wr = sum(1 for r in rets if r > 0) / n * 100
        sr = sharpe_ratio(rets, annualize=True)
        print(f"{name:<22} {n:>10} {avg:>+9.2f}% {wr:>7.1f}% {sr:>8.2f}")

    # Excess return over benchmark
    bench_rets = all_results["benchmark"]
    if bench_rets:
        bench_avg = np.mean(bench_rets)
        for name in ["combined", "dcb_only", "traction_only"]:
            rets = all_results[name]
            if rets:
                excess = np.mean(rets) - bench_avg
                print(f"\n  {name} excess vs benchmark: {excess:>+.2f}% per month")

    print(f"\n{'='*80}")
    print("Done.")


def main():
    parser = argparse.ArgumentParser(description="DCB + Fund Traction Backtest")
    parser.add_argument("--min-score", type=float, default=50, help="Min traction score (default: 50)")
    parser.add_argument("--min-discount", type=float, default=15, help="Min DCB discount %% (default: 15)")
    parser.add_argument("--max-stocks", type=int, default=200, help="Max stocks to compute DCB for per month (default: 200)")
    args = parser.parse_args()
    run_backtest(args.min_score, args.min_discount, args.max_stocks)


if __name__ == "__main__":
    main()
