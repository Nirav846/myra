"""
Confluence Forward-Return Backtest Harness
==========================================
Measures the forward returns of symbols grouped by how many *selective*
scanners flagged them together on one date (the confluence headline count),
across the calibration snapshots in ``models/calibration/``.

Built to the same shape as ``tools/backtest_wyckoff.py`` (evenly spaced scan
dates, forward-return horizons, benchmark excess, quintile spread, gitignored
CSV) with ONE substitution: events are read from precomputed confluence
snapshots rather than re-derived from a live scanner run. The bucket
assignment itself comes from ``myra_web.utils.build_report_from_snapshot`` --
the identical code path the live UI uses -- so a backtest bucket can never
disagree with what the UI showed for that date.

    python tools/backtest_confluence.py
    python tools/backtest_confluence.py --no-costs       # skip cost adjustment
    python tools/backtest_confluence.py --horizons 20 60 # subset

Methodology
-----------
1. Population: FULL UNION of all scanner candidates on each snapshot date --
   what the UI actually displays. NOT point-in-time restricted (see the
   SURVIVORSHIP-BIAS warning printed above the results table).
2. Buckets: ``selective_scanner_count`` of 0, 1, 2, 3, 4, "5+". The 0 and 1
   buckets together are the CONTROL group.
3. Forward returns: TRADING-day horizons [20, 40, 60, 90, 120, 180], entry at
   the snapshot's close, exit at the Nth subsequent trading day's close.
4. Benchmark: ``^NSEI`` from ``myra_metadata.benchmarks`` over the same window.
5. Costs (default ON): 0.5% brokerage each side + 15% STCG on positive gains.

Control group rationale
-----------------------
The question is not "do these stocks go up" but "does scanner *agreement*
carry information". The control therefore holds "was flagged by at least one
scanner" constant by drawing from the same day's union, and varies only
agreement. A raw market sample is NOT used as the baseline: it would confound
the agreement effect with a universe/size effect.

Note on bucket 0
----------------
The bucket definitions name "0" as "not in the union", but a symbol outside the
union does not appear in the snapshot at all, so it is unreachable from this
data. The realised control is therefore bucket 1 alone (in the union, below
the inclusion gate); bucket 0 is reported as empty for transparency. This
preserves the intent -- baseline drawn from scanner-flagged names, not the
arbitrary market.

Output
------
``confluence_backtest_results.csv`` (repo root, gitignored) with one row per
symbol x date x horizon.
"""

import argparse
import csv
import importlib.util
import json
import os
import sqlite3
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from myra_app.constants import DB_DIR, MODELS_DIR  # noqa: E402
from myra_web.utils import (  # noqa: E402
    BROAD_SCANNERS,
    MIN_SELECTIVE_FOR_INCLUSION,
    build_report_from_snapshot,
)

TECH_DB = os.path.join(DB_DIR, "myra_technical.db")
META_DB = os.path.join(DB_DIR, "myra_metadata.db")
CAL_DIR = os.path.join(MODELS_DIR, "calibration")
CSV_OUT = "confluence_backtest_results.csv"

DEFAULT_HORIZONS = [20, 40, 60, 90, 120, 180]
BUCKET_LABELS = ["0", "1", "2", "3", "4", "5+"]
CONTROL_BUCKETS = ("0", "1")

# Reuse the reference tool's price/cost helpers verbatim rather than adding a
# 5th copy (these are currently duplicated across 5 files in tools/; hoisting
# them into a shared module is tracked as separate cleanup).
_wyckoff_spec = importlib.util.spec_from_file_location(
    "_bc_wyckoff_helpers",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "backtest_wyckoff.py"),
)
_wy = importlib.util.module_from_spec(_wyckoff_spec)
_wyckoff_spec.loader.exec_module(_wy)
get_close = _wy.get_close
get_benchmark_close = _wy.get_benchmark_close
cost_adjusted_return = _wy.cost_adjusted_return


# -- STEP 1: preflight --------------------------------------------------------
def discover_snapshots() -> list[str]:
    """Glob calibration snapshots; fail loudly if none exist."""
    if not os.path.isdir(CAL_DIR):
        print(
            "No confluence calibration snapshots found. Generate them first with:\n"
            '  python -c "from myra_web.confluence_batch import '
            "run_confluence_batch; run_confluence_batch(as_on_date='<YYYY-MM-DD>', "
            "out_path='models/calibration/confluence_<YYYY_MM_DD>.json')\"",
            file=sys.stderr,
        )
        raise SystemExit(1)
    paths = sorted(
        os.path.join(CAL_DIR, f)
        for f in os.listdir(CAL_DIR)
        if f.startswith("confluence_") and f.endswith(".json")
    )
    if not paths:
        print(
            "No confluence calibration snapshots found. Generate them first with:\n"
            '  python -c "from myra_web.confluence_batch import '
            "run_confluence_batch; run_confluence_batch(as_on_date='<YYYY-MM-DD>', "
            "out_path='models/calibration/confluence_<YYYY_MM_DD>.json')\"",
            file=sys.stderr,
        )
        raise SystemExit(1)
    return paths


# -- Trading calendar ---------------------------------------------------------
_trading_days: list[str] = []


def load_trading_days() -> list[str]:
    global _trading_days
    if _trading_days:
        return _trading_days
    conn = sqlite3.connect(TECH_DB)
    _trading_days = [
        r[0]
        for r in conn.execute("SELECT DISTINCT date FROM technical_data ORDER BY date")
    ]
    conn.close()
    if not _trading_days:
        raise SystemExit("technical_data has no rows; cannot build trading calendar")
    return _trading_days


def nth_trading_day(start_iso: str, n: int) -> str | None:
    """The n-th trading day strictly after start_iso, or None if unavailable."""
    days = load_trading_days()
    try:
        i = days.index(start_iso)
    except ValueError:
        later = [d for d in days if d > start_iso]
        if not later:
            return None
        i = days.index(later[0]) - 1
    j = i + n
    return days[j] if 0 <= j < len(days) else None


# -- STEP 2: collect_events ---------------------------------------------------
def bucket_label(count: int) -> str:
    return "5+" if count >= 5 else str(count)


def collect_events(snap: dict) -> list[dict]:
    """Per-symbol bucket assignment for one snapshot.

    The control group needs every union member, but build_report_from_snapshot
    only returns symbols clearing the >= MIN_SELECTIVE gate. So the union and
    per-symbol selective counts are recomputed here from the raw snapshot using
    the same BROAD_SCANNERS membership test; the qualifying buckets are taken
    from build_report_from_snapshot so the backtest cannot drift from the UI.

    The union is over ALL scanners, broad included, so bucket 0 (flagged only
    by one or more broad scanners) is populated. A control that excluded
    broad-flagged names would silently drop the very population the fixed
    BROAD_SCANNERS list was introduced to stop counting.
    """
    scan_date = snap["as_on_date"]
    scanners = snap.get("scanners") or {}

    selective_counts: dict[str, int] = defaultdict(int)
    selective_flagged: dict[str, set[str]] = defaultdict(set)
    broad_flagged: dict[str, set[str]] = defaultdict(set)
    for name, entry in scanners.items():
        if entry.get("error"):
            continue
        is_broad = name in BROAD_SCANNERS
        for cand in entry.get("candidates") or []:
            sym = cand.get("symbol")
            if not sym:
                continue
            if is_broad:
                broad_flagged[sym].add(name)
            else:
                selective_counts[sym] += 1
                selective_flagged[sym].add(name)

    union = set(selective_counts) | set(broad_flagged)
    union_size = len(union)

    events: list[dict] = []
    for sym in union:
        n = selective_counts.get(sym, 0)
        events.append(
            {
                "symbol": sym,
                "scan_date": scan_date,
                "selective_scanner_count": n,
                "selective_scanners": sorted(selective_flagged[sym]),
                "broad_scanners": sorted(broad_flagged[sym]),
                "union_size": union_size,
                "bucket": bucket_label(n),
                "in_report": n >= MIN_SELECTIVE_FOR_INCLUSION,
            }
        )

    # Cross-check the two independent derivations of the qualifying set.
    report = build_report_from_snapshot(snap)
    by_sym = {r["symbol"]: r for r in report["symbols"]}
    report_syms = set(by_sym)
    derived = {e["symbol"] for e in events if e["in_report"]}
    if report_syms != derived:
        raise SystemExit(
            f"{scan_date}: bucketing disagrees with build_report_from_snapshot "
            f"(report={len(report_syms)} derived={len(derived)}, "
            f"sym_diff={len(report_syms ^ derived)}) — aborting rather than "
            "reporting numbers from a broken bucketing path"
        )
    for e in events:
        if e["in_report"]:
            e["broad_scanners"] = by_sym[e["symbol"]]["broad_scanners"]
    return events


# -- STEP 3: build_forward_row -----------------------------------------------
def build_forward_rows(
    events: list[dict], scan_date: str, horizons: list[int], use_costs: bool
) -> tuple[list[dict], list[dict]]:
    """Forward returns per event per horizon. Returns (rows, unresolved)."""
    # Exact trading dates needed; bulk-load them once instead of per-symbol SQL.
    wanted: dict[int, str | None] = {h: nth_trading_day(scan_date, h) for h in horizons}
    live = {h: d for h, d in wanted.items() if d}
    missing_horizons = sorted(h for h, d in wanted.items() if not d)
    if not live:
        return [], [
            {
                "symbol": e["symbol"],
                "bucket": e["bucket"],
                "reason": f"no forward trading data beyond {scan_date}",
                "horizon": None,
            }
            for e in events
        ]

    need_dates = sorted({scan_date, *live.values()})
    placeholders = ",".join("?" * len(need_dates))
    conn = sqlite3.connect(TECH_DB)
    px: dict[tuple, float] = {
        (r[0], r[1]): float(r[2])
        for r in conn.execute(
            f"SELECT symbol, date, close FROM technical_data WHERE date IN ({placeholders})",
            need_dates,
        )
    }
    conn.close()

    bench_in = get_benchmark_close(scan_date)
    bench_out = {h: get_benchmark_close(d) for h, d in live.items()}

    rows: list[dict] = []
    unresolved: list[dict] = []
    for e in events:
        sym = e["symbol"]
        entry = px.get((sym, scan_date))
        for h in horizons:
            if h in missing_horizons:
                unresolved.append(
                    {
                        "symbol": sym,
                        "bucket": e["bucket"],
                        "reason": f"horizon {h}d beyond available data",
                        "horizon": h,
                    }
                )
                continue
            exit_date = live[h]
            exit_px = px.get((sym, exit_date))
            if entry is None or exit_px is None or entry <= 0:
                unresolved.append(
                    {
                        "symbol": sym,
                        "bucket": e["bucket"],
                        "reason": (
                            f"no close on {scan_date}"
                            if entry is None
                            else f"no close on {exit_date} (h={h})"
                        ),
                        "horizon": h,
                    }
                )
                continue
            gross = (exit_px - entry) / entry * 100
            ret = cost_adjusted_return(gross) if use_costs else gross
            bi, bo = bench_in, bench_out.get(h)
            bench_ret = (bo - bi) / bi * 100 if bi and bo and bi > 0 else None
            rows.append(
                {
                    "symbol": sym,
                    "scan_date": scan_date,
                    "entry_date": scan_date,
                    "exit_date": exit_date,
                    "horizon": h,
                    "bucket": e["bucket"],
                    "selective_scanner_count": e["selective_scanner_count"],
                    "in_report": e["in_report"],
                    "entry_close": round(entry, 2),
                    "exit_close": round(exit_px, 2),
                    "gross_return_pct": round(gross, 2),
                    "return_pct": round(ret, 2),
                    "benchmark_return_pct": (
                        round(bench_ret, 2) if bench_ret is not None else None
                    ),
                    "excess_return_pct": (
                        round(ret - bench_ret, 2) if bench_ret is not None else None
                    ),
                    "union_size": e["union_size"],
                }
            )
    return rows, unresolved


# -- STEP 4: print_summary ----------------------------------------------------
def summarise(rows: list[dict], horizon: int) -> dict:
    by_bucket: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        if r["horizon"] == horizon:
            by_bucket[r["bucket"]].append(r)
    out = {}
    for b in BUCKET_LABELS:
        rs = by_bucket.get(b) or []
        if not rs:
            out[b] = None
            continue
        rets = [r["return_pct"] for r in rs]
        exc = [r["excess_return_pct"] for r in rs if r["excess_return_pct"] is not None]
        out[b] = {
            "n": len(rs),
            "dates": len({r["scan_date"] for r in rs}),
            "mean": sum(rets) / len(rets),
            "mean_excess": (sum(exc) / len(exc)) if exc else None,
            "win_rate": 100.0 * sum(1 for x in rets if x > 0) / len(rets),
            "win_rate_vs_bench": (
                100.0 * sum(1 for x in exc if x > 0) / len(exc) if exc else None
            ),
        }
    return out


def fmt(v, nd=1, suffix="%"):
    return "  n/a" if v is None else f"{v:{nd}.1f}{suffix}"


def print_table(summary: dict, horizons: list[int], use_costs: bool) -> None:
    print()
    print(
        "SURVIVORSHIP-BIASED: population drawn from current-date scanner unions, "
        "not point-in-time membership."
    )
    print(
        "Compare BUCKETS to each other via excess return, not absolute returns "
        "to zero."
    )
    print(
        "Control = buckets 0/1 (in the day's union, below the "
        f">= {MIN_SELECTIVE_FOR_INCLUSION} selective-scanner gate)."
    )
    print(f"Costs: {'ON (0.5%/side + 15% STCG)' if use_costs else 'OFF'}")
    print("=" * 108)
    for h in horizons:
        s = summary.get(h) or {}
        print(f"\nHORIZON {h} TRADING DAYS   (excess vs ^NSEI)")
        print("-" * 108)
        print(
            f"{'bucket':<8}{'n':>7}{'dates':>7}{'mean':>10}"
            f"{'mean excess':>14}{'win%':>8}{'win% vs NSEI':>16}"
        )
        print("-" * 108)
        for b in BUCKET_LABELS:
            d = s.get(b)
            if d is None:
                print(f"{b:<8}{'-':>7}{'-':>7}{'no data':>10}")
                continue
            tag = f"{b} (ctrl)" if b in CONTROL_BUCKETS else b
            print(
                f"{tag:<8}{d['n']:>7}{d['dates']:>7}{fmt(d['mean']):>10}"
                f"{fmt(d['mean_excess']):>14}{fmt(d['win_rate']):>8}"
                f"{fmt(d['win_rate_vs_bench']):>16}"
            )
        # Q5 - Q1 spread, where Q1 is the CONTROL bucket, not bucket "2".
        # Deliberate deviation from tools/backtest_wyckoff.py, which takes
        # Q1 as the lowest quintile OF CANDIDATES. Here the meaningful baseline
        # is "flagged by at least one scanner but scanners did not agree", so
        # the spread measures the marginal value of agreement.
        q5 = s.get("5+")
        ctl = [s[b] for b in CONTROL_BUCKETS if s.get(b)]
        ctl_n = sum(d["n"] for d in ctl)
        ctl_exc = sum(d["mean_excess"] * d["n"] for d in ctl) / ctl_n if ctl_n else None
        ctl_wr = sum(d["win_rate"] * d["n"] for d in ctl) / ctl_n if ctl_n else None
        if q5 and ctl_exc is not None:
            print("-" * 108)
            print(
                f"{'Q5-Q1':<8}{'':>7}{'':>7}{'':>10}"
                f"{fmt(q5['mean_excess'] - ctl_exc):>14}"
                f"{'':>8}{fmt(q5['win_rate'] - (ctl_wr or 0.0)):>16}"
            )
            print(f"        (Q5 = bucket 5+, Q1 = pooled 0/1 control, n={ctl_n})")


def write_csv(rows: list[dict], path: str) -> None:
    if not rows:
        print(f"\nNo rows to write to {path}")
        return
    fields = list(rows[0])
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    print(f"\nWrote {len(rows)} rows -> {path}")


def parse_args(argv):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--no-costs", action="store_true", help="skip cost adjustment")
    p.add_argument(
        "--horizons",
        type=int,
        nargs="+",
        default=DEFAULT_HORIZONS,
        help="trading-day forward horizons",
    )
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    paths = discover_snapshots()
    print(f"Found {len(paths)} calibration snapshot(s) in {CAL_DIR}")

    all_rows: list[dict] = []
    unresolved: list[dict] = []

    for p in paths:
        with open(p, encoding="utf-8") as fh:
            snap = json.load(fh)
        scan_date = snap["as_on_date"]
        events = collect_events(snap)
        n_ctrl = sum(1 for e in events if not e["in_report"])
        n_rep = sum(1 for e in events if e["in_report"])
        rows, unres = build_forward_rows(
            events, scan_date, args.horizons, not args.no_costs
        )
        all_rows.extend(rows)
        unresolved.extend(unres)
        print(
            f"  {scan_date}: union={len(events):5d} "
            f"control={n_ctrl:5d} qualifying={n_rep:4d} "
            f"rows={len(rows):6d} unresolved={len(unres)}"
        )

    # Summarise ONCE over every pooled row. Per-date summarising and merging
    # would either need weighting by n or silently report only the last date.
    summary = {h: summarise(all_rows, h) for h in args.horizons}

    print_table(summary, args.horizons, not args.no_costs)

    if unresolved:
        by_reason: dict[str, int] = defaultdict(int)
        for u in unresolved:
            by_reason[u["reason"]] += 1
        print(f"\nUNRESOLVED (excluded, not silently dropped): {len(unresolved)}")
        for reason, n in sorted(by_reason.items(), key=lambda kv: -kv[1])[:12]:
            print(f"  {n:7d}  {reason}")

    write_csv(all_rows, CSV_OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
