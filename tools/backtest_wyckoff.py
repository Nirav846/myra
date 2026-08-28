"""
Wyckoff Automaton Backtest Harness
==================================
Measures forward returns of EVERY event type emitted by the real
`WyckoffAutomaton` (SC / AR / ST / Spring / SOS) across a set of evenly
spaced historical scan dates, and reports per-type summary statistics.

Why this exists
---------------
`WyckoffAutomaton.scan()` returns only the best candidate per symbol, so it
cannot be used for per-event-type profitability analysis. This harness calls
the lower-level `WyckoffAutomaton._detect_events(...)` directly, which returns
ALL events, and measures forward returns for each one.

NOTE ON REPRODUCIBILITY
-----------------------
An earlier throwaway backtest script (built before this file existed) was
**deleted and NEVER committed** - `git rev-list` confirms no such file is in
the history. This file is a fresh, reproducible rebuild of that logic, per
`.agent/rules/02-strategy-backtest.md`. All randomness is seeded, so identical
runs produce identical output.

How to run
----------
    python tools/backtest_wyckoff.py                 # full default run
    python tools/backtest_wyckoff.py --n-symbols 100 # quicker smoke test
    python tools/backtest_wyckoff.py --dump-sc /tmp/sc.csv   # dump SC events
    python tools/backtest_wyckoff.py --no-costs      # skip cost adjustment

Methodology
-----------
1. Symbol universe: `WyckoffAutomaton._get_universe()` (default mcap
   510-530,000 Cr, ~1704 symbols), then `random.seed(42)` +
   `random.sample(universe, 400)` for a deterministic 400-symbol sample.
2. Scan dates: 12 evenly spaced dates over `[--start, --end]`, where
   `--start` defaults to 2025-07-01 and `--end` defaults to
   `min(2026-04-30, max_tech_date - 180d)` so that the longest (180d) forward
   window always has measurable data.
3. Per scan date: replicate `scan()`'s data prep - one bulk OHLCV load for
   the whole sample window (`load_ohlcv_for_universe(min_date, D, symbols)`),
   per-symbol 13-column DataFrame with the `max(55, int(90*0.6)+5) = 59` row
   gate, then `_detect_events(df, symbol, D)`.
4. Forward returns: CALENDAR-day horizons [20, 40, 60, 90, 120, 180]. Entry
   = `close` at `event_date`, exit = `close` at `event_date + N`. Benchmark
   excess uses `^NSEI` from the `benchmarks` table in `myra_metadata.db` over
   the same window.
5. Cost adjustment (default ON): 0.5% brokerage each side (1.0% round trip)
   + 15% STCG tax on positive gains. Toggle with `--no-costs`.

Output
------
- Human-readable summary table to stdout with, per event type and horizon:
  signal count, mean return, win rate, benchmark, excess, and the quality
  quintile (Q5 - Q1) spread.
- `wyckoff_backtest_results.csv` (repo root, gitignored) with one row per
  event x horizon: the full per-event detail.

`--dump-sc <csv>` emits a narrow dump of SC events whose window row-count is
in [59, 99] (these only exist post-look-ahead-fix; the old code required a
>=100-row window due to the calendar-vs-trading-day bug). Columns:
symbol, event_date, quality, close, range_low_90, vol_ratio, del_pct,
rows_in_window, scan_date (scan_date anchors the exact scan window so an
independent re-check can reproduce it).
"""

import argparse
import random
import sqlite3
import sys
import os
from datetime import date, datetime, timedelta

# Ensure the repo root is importable when run directly (Python adds the
# script's own directory to sys.path, not the cwd).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd  # noqa: E402  (after sys.path bootstrap)

from myra_app.constants import DB_DIR
from myra_app.librarian_core import LibrarianCore
from myra_app.db.bulk_loader import (
    COLUMNS_13,
    load_ohlcv_for_universe,
)
from myra_app.strategies.wyckoff_automaton import WyckoffAutomaton

TECH_DB = os.path.join(DB_DIR, LibrarianCore.DB_MAP["technical"])
META_DB = os.path.join(DB_DIR, LibrarianCore.DB_MAP["meta"])
DEFAULT_HORIZONS = [20, 40, 60, 90, 120, 180]
LOOKBACK_DAYS = 90
MIN_ROWS = max(55, int(LOOKBACK_DAYS * 0.6) + 5)  # = 59
BROKERAGE_PCT = 0.5  # per side
STCG_RATE = 0.15  # short-term capital gains tax on positive gains
EVENT_TYPES = ["SC", "AR", "ST", "Spring", "SOS"]

DEFAULT_START = "2025-07-01"
DEFAULT_END_HARD = "2026-04-30"
DEFAULT_N = 12
DEFAULT_N_SYMBOLS = 400


# -- Data access -------------------------------------------------------------
# Persistent connections + per-(symbol, date) close caching, so N+1 lookups
# (inherent to per-event forward-return measurement) touch the DB once.
_close_cache: dict[tuple, float | None] = {}
_bench_cache: dict[str, float | None] = {}

_tech_conn = None
_meta_conn = None


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
    """Last close at or before trade_date for a symbol (technical_data)."""
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
    """Last ^NSEI close at or before trade_date (myra_metadata.benchmarks)."""
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
    """0.5% brokerage each side + 15% STCG on positive gains."""
    net = gross - BROKERAGE_PCT * 2
    if gross > 0:
        net -= STCG_RATE * gross
    return net


# -- Event collection --------------------------------------------------------


def collect_events(
    automaton: WyckoffAutomaton,
    sampled_symbols: list[str],
    scan_date: date,
) -> list[dict]:
    """Replicate scan()'s data prep and return ALL events for the sample."""
    scan_date_s = scan_date.isoformat()
    min_date = (scan_date - timedelta(days=LOOKBACK_DAYS)).isoformat()
    symbols = [s.strip() for s in sampled_symbols]
    bulk = load_ohlcv_for_universe(min_date, scan_date_s, symbols=symbols)
    automaton._bulk_data = bulk

    events_out: list[dict] = []
    for symbol in symbols:
        tech = automaton._get_tech_data(symbol, min_date, max_date=scan_date_s)
        if len(tech) < MIN_ROWS:
            continue
        df = pd.DataFrame(tech, columns=list(COLUMNS_13))
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date").reset_index(drop=True)
        if len(df) < MIN_ROWS:
            continue
        events = automaton._detect_events(df, symbol=symbol, as_on_date=scan_date_s)
        for e in events:
            # Normalize date and tag provenance for downstream fidelity.
            e["event_date"] = str(e["event_date"])[:10]
            e["_rows_in_window"] = int(len(df))
            e["_scan_date"] = scan_date_s
        events_out.extend(events)
    return events_out


def build_forward_row(e: dict, horizon: int, use_costs: bool) -> dict:
    """Compute entry/exit returns + benchmark excess for one event, one horizon."""
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
            cost_adjusted_return(raw["gross_return"])
            if use_costs
            else raw["gross_return"]
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


# -- Printing helpers --------------------------------------------------------


def fmt(v, suffix="%"):
    return "N/A" if v is None else f"{v:+.2f}{suffix}"


def print_summary(rows: list[dict], horizons: list[int], use_costs: bool):
    """Per event type x horizon: count, mean ret, win rate, bench, excess, Q5-Q1."""
    df = pd.DataFrame(rows)
    df = df[df["net_return"].notna()]
    if df.empty:
        print("\nNo measurable forward returns. Nothing to summarise.")
        return

    label = "Net (cost-adj)" if use_costs else "Gross"
    print()
    print("=" * 108)
    print(f"WYCKOFF BACKTEST SUMMARY - return metric: {label}")
    print("=" * 108)

    for ev in EVENT_TYPES:
        sub = df[df["event"] == ev]
        if sub.empty:
            continue
        print(f"\n### EVENT TYPE: {ev}  (n = {len(sub)} events)")
        header = (
            f"| {'Horizon':>7} | {'N':>5} | {'MeanRet':>8} | {'Win%':>6} | "
            f"{'Bench':>8} | {'Excess':>8} | {'Q5-Q1':>8} |"
        )
        print(header)
        print("|" + "-" * (len(header) - 2) + "|")
        for h in horizons:
            hsub = sub[sub["horizon"] == h]
            if hsub.empty:
                continue
            n = int(len(hsub))
            mean_ret = float(hsub["net_return"].mean())
            win_rate = float((hsub["net_return"] > 0).mean() * 100)
            exc = hsub["excess"].dropna()
            excess = float(exc.mean()) if len(exc) else None
            bench = float(
                hsub["bench_return"].mean()
                if hsub["bench_return"].notna().any()
                else 0.0
            )

            # Quintile spread (Q5-Q1) on quality, within this type+horizon.
            qs = "N/A"
            if n >= 5:
                qual = hsub["quality"]
                try:
                    q = pd.qcut(qual, q=5, duplicates="drop")
                    g = hsub.groupby(q, observed=False)["net_return"].mean()
                    if len(g) >= 5:
                        qs = f"{g.iloc[-1] - g.iloc[0]:+.2f}%"
                except ValueError:
                    qs = "N/A"
            print(
                f"| {h:>7d} | {n:>5d} | {fmt(mean_ret):>8} | {win_rate:>5.1f}% | "
                f"{fmt(bench):>8} | {fmt(excess):>8} | {qs:>8} |"
            )


# -- SC dump -----------------------------------------------------------------


def dump_sc(rows: list[dict], out_path: str):
    """Write SC events whose window row-count is in [59,99] to a CSV.

    `scan_date` is included (in addition to the core SC fields) purely to make
    independent re-verification exact: the scanner's expanding-mean baselines
    are anchored to the scan window `[scan_date - 90d, scan_date]`, so a
    re-check needs the same window.
    """
    sc = [
        r for r in rows if r["event"] == "SC" and 59 <= int(r["_rows_in_window"]) <= 99
    ]
    cols = [
        "symbol",
        "event_date",
        "quality",
        "close",
        "range_low_90",
        "vol_ratio",
        "del_pct",
        "rows_in_window",
        "scan_date",
    ]
    out = []
    for e in sc:
        out.append(  # noqa: PG-APPEND  (accumulate SC dump rows; dicts, no vectorised alt)
            {
                "symbol": e["symbol"],
                "event_date": e["event_date"],
                "quality": e["quality"],
                "close": e["close"],
                "range_low_90": e["range_low_90"],
                "vol_ratio": e["vol_ratio"],
                "del_pct": e["del_pct"],
                "rows_in_window": e["_rows_in_window"],
                "scan_date": e["_scan_date"],
            }
        )
    pd.DataFrame(out, columns=cols).to_csv(out_path, index=False)
    print(f"\nDumped {len(out)} SC events (row-count in [59,99]) to {out_path}")


# -- Main --------------------------------------------------------------------


def parse_args(argv):
    p = argparse.ArgumentParser(description="Wyckoff Automaton backtest harness")
    p.add_argument(
        "--start", default=DEFAULT_START, help="First scan date (YYYY-MM-DD)"
    )
    p.add_argument(
        "--end",
        default=None,
        help="Last scan date (YYYY-MM-DD); defaults to min(hard cap, max_tech_date-180d)",
    )
    p.add_argument(
        "--horizons",
        default=",".join(map(str, DEFAULT_HORIZONS)),
        help="Comma-separated forward horizons in calendar days",
    )
    p.add_argument(
        "--n-symbols",
        type=int,
        default=DEFAULT_N_SYMBOLS,
        help="Number of symbols to sample from the universe (default 400)",
    )
    p.add_argument(
        "--seed", type=int, default=42, help="Random seed for symbol sampling"
    )
    p.add_argument(
        "--no-costs",
        action="store_true",
        help="Disable cost adjustment (0.5%% x2 + 15%% STCG)",
    )
    p.add_argument(
        "--out",
        default="wyckoff_backtest_results.csv",
        help="CSV output path for per-event detail (default wyckoff_backtest_results.csv)",
    )
    p.add_argument(
        "--dump-sc",
        default=None,
        metavar="CSV",
        help="Dump SC events (row-count in [59,99]) to this CSV and exit",
    )
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    horizons = [int(h.strip()) for h in args.horizons.split(",") if h.strip()]
    use_costs = not args.no_costs

    mtd = max_tech_date()
    # Guard: longest horizon must always be measurable.
    end_guard = (
        datetime.strptime(mtd, "%Y-%m-%d") - timedelta(days=max(horizons))
    ).date()
    end_default = min(datetime.strptime(DEFAULT_END_HARD, "%Y-%m-%d").date(), end_guard)
    start = datetime.strptime(args.start, "%Y-%m-%d").date()
    end = datetime.strptime(args.end, "%Y-%m-%d").date() if args.end else end_default
    if start >= end:
        print(f"ERROR: start ({start}) must be before end ({end}).", file=sys.stderr)
        return 1

    automaton = WyckoffAutomaton()
    universe = automaton._get_universe()
    print(f"Universe size (mcap 510-530,000 Cr): {len(universe)}")

    # Deterministic symbol sample.
    random.seed(args.seed)
    sampled = random.sample([r[0].strip() for r in universe], args.n_symbols)
    print(f"Sampled symbols: {len(sampled)} (seed={args.seed})")

    # Scan dates: evenly spaced over [start, end].
    span = (end - start).days
    step = max(1, span // (DEFAULT_N - 1))
    scan_dates = [start + timedelta(days=i * step) for i in range(DEFAULT_N)]
    scan_dates = [d for d in scan_dates if d <= end]
    print(f"Scan dates ({len(scan_dates)}): {[d.isoformat() for d in scan_dates]}")

    # Collect events across all scan dates.
    all_events: list[dict] = []
    for D in scan_dates:
        evs = collect_events(automaton, sampled, D)
        print(f"  {D.isoformat()}: {len(evs):4d} events")
        all_events.extend(evs)

    print(f"\nTotal events collected: {len(all_events)}")
    from collections import Counter

    per_type = Counter(e["event"] for e in all_events)
    print("Signal counts by event type:", dict(per_type))

    # SC dump mode (exit before forward-return computation).
    if args.dump_sc:
        dump_sc(all_events, args.dump_sc)
        return 0

    # Forward returns for every event x horizon.
    rows: list[dict] = []
    for e in all_events:
        for h in horizons:
            rows.append(  # noqa: PG-APPEND  (accumulate forward-return row dicts)
                build_forward_row(e, h, use_costs)
            )

    summary = pd.DataFrame(rows)
    summary.to_csv(args.out, index=False)
    print(f"\nPer-event detail written to {args.out}")

    # Horizon measurability counts.
    print("\nMeasured (non-null) forward-return counts per horizon:")
    meas = summary[summary["net_return"].notna()].groupby("horizon")["symbol"].count()
    for h in horizons:
        print(f"  {h:>3}d: {int(meas.get(h, 0))}")

    print_summary(rows, horizons, use_costs)
    return 0


if __name__ == "__main__":
    sys.exit(main())
