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

STAGE_BUCKET_LABELS = ["0", "1", "2+"]

# ---------------------------------------------------------------------------
# STEP 1: early- vs late-stage scanner classification.
#
# The axis is: what must ALREADY be true in the price/volume/delivery record
# for the gate to pass?
#   "early" -> the gate permits or requires price to still be compressed /
#              un-confirmed (tight range, contracting volume, no breakout), or
#              measures a delivery/absorption signature with no price-structure
#              requirement at all.
#   "late"  -> the gate requires a completed price structure: a discrete
#              crossover, a close above a box ceiling, a reclaimed low, a
#              printed climax/distribution bar, or a completed advance.
#
# Every justification below cites the actual gating lines, not the scanner
# name. This is a judgment call and is meant to be reviewed and overridden.
# ---------------------------------------------------------------------------
SCANNER_STAGE: dict[str, str] = {
    "The Trigger": "early",
    "Bottom Hunter": "early",
    "Invisible Hand": "early",
    "Liquidity Flip": "early",
    "Operator Fingerprint": "early",
    "Seasonal Delivery": "early",
    "Recovery Ladder": "late",
    "Super Breakout": "late",
    "Float Exhaustion": "ambiguous",
    "Darvas Box Pro": "late",
    "Climax Accumulation": "late",
    "Wyckoff Automaton": "late",  # broad; excluded from selective buckets
    "Multibagger Pro": "early",  # broad; excluded from selective buckets
}

STAGE_JUSTIFICATION: dict[str, str] = {
    "The Trigger": (
        "gate3 REQUIRES compression: 5d/20d vol ratio < 0.75 AND 5d range < 10% "
        "(trigger_scanner.py:313-316), so price must NOT have moved. 20d float "
        "turnover >= 8% (L249) is accumulation, not confirmation."
    ),
    "Bottom Hunter": (
        "only gate is a 20-session up-day vs down-day delivery_pct differential "
        ">= 5pp plus ADTV (bottom_hunter.py:471-474). No price, trend, or 52w "
        "requirement whatsoever -> price-neutral, cannot fire on extension."
    ),
    "Invisible Hand": (
        "delivery-value-per-unit-price-drift ratio > 1.2 over a completed 20d "
        "window, keyed on QUIET high-delivery days (del>50 & |ret|<1.5%), and "
        "explicitly REJECTS extension via wk52_pos < 88 "
        "(invisible_hand_scanner.py:456-465). Anti-late by construction."
    ),
    "Liquidity Flip": (
        "30d mean delivery_pct must already have stepped up >= +8pp vs the prior "
        "120d (liquidity_flip_detector.py:238-245). SMA200 and 52w position are "
        "score multipliers, NOT gates (L271-305) -> no price structure required."
    ),
    "Operator Fingerprint": (
        "the strongest early-stage gate in the set: last 14 sessions' mean "
        "daily range must be < 80% of the older window (compression_ratio < 0.80, "
        "operator_fingerprint_scanner.py:298) with delivery drift > 0. Requires "
        "price to be coiling, i.e. the move has not started."
    ),
    "Seasonal Delivery": (
        "needs only >= 3 sessions of the current calendar month whose mean "
        "delivery_pct already exceeds that same month's multi-year norm "
        "(seasonal_delivery_harvester.py:216-239). Structurally cannot see OHLCV "
        "or volume (L83-92) -> no price confirmation possible."
    ),
    "Recovery Ladder": (
        "close must have JUST crossed up through year_low(252d) * 1.20 on this "
        "exact bar (bottom_hunter_m1_scanner.py:180). A discrete same-bar "
        "recovery crossover -- the +20% off the low has already happened."
    ),
    "Super Breakout": (
        "close must have JUST crossed up through SMA(50) on this exact bar "
        "(super_breakout.py:180-183). Definitionally a breakout."
    ),
    "Float Exhaustion": (
        "AMBIGUOUS, not late. 20 sessions of delivery must ALREADY have consumed "
        ">= 10% of free float (float_exhaustion_scanner.py:240-241) and NOTHING "
        "else -- no price, volume, or range-position gate at all. 10% of float "
        "turning over in 20 sessions occurs just as readily in quiet accumulation "
        "as in a blow-off top, so the metric is stage-agnostic BY CONSTRUCTION. "
        "Excluded from both stage counts rather than forced onto a side, because "
        "a guess here would land it in the wrong column of the fixed-agreement "
        "mix test and dilute the one comparison that matters."
    ),
    "Darvas Box Pro": (
        "precondition requires a COMPLETED advance: within 5% of the 52w high OR "
        ">= +20% over 60 sessions (darvas_box_scanner.py:145-156). Emits "
        "pre-breakout 'In Box'/'Breakout Pending' states too (L604-610), but "
        "those are consolidations that FOLLOW a move, so the flagged names have "
        "already run."
    ),
    "Climax Accumulation": (
        "a >= 10x-volume, sub-15%-delivery distribution climax bar must ALREADY "
        "have printed, with 3-15 sessions since and rising delivery across that "
        "window (climax_accumulation.py:170-175, 204-250). The capitulation "
        "event is definitionally late."
    ),
    "Wyckoff Automaton": (
        "all five event detectors (SC/Spring/SOS/AR/ST) are post-hoc structures "
        "already printed in the data (wyckoff_automaton.py:481, 522, 704, 764, "
        "795). BROAD - excluded from selective buckets, classified for review "
        "completeness only."
    ),
    "Multibagger Pro": (
        "gate is `if score >= 0` (multibagger_early_detection.py:69), which is "
        "ALWAYS TRUE because score is a sum of non-negative terms -- it fires on "
        "every symbol with >= 30 bars. BROAD - excluded from selective buckets. "
        "The vacuous gate is a pre-existing bug, flagged separately."
    ),
}


def print_classification() -> None:
    """Print the STEP 1 judgment call for review before any numbers are read."""
    print("=" * 100)
    print("STEP 1 -- EARLY- vs LATE-STAGE SCANNER CLASSIFICATION (judgment call)")
    print("=" * 100)
    for stage in ("early", "late"):
        names = sorted(n for n, s in SCANNER_STAGE.items() if s == stage)
        print(f"\n{stage.upper()}-STAGE ({len(names)}):")
        for n in names:
            broad = (
                " [BROAD - excluded from selective buckets]"
                if n in BROAD_SCANNERS
                else ""
            )
            print(f"  {n}{broad}")
            print(f"      {STAGE_JUSTIFICATION[n]}")
    print()
    sel_e = sum(
        1 for n, s in SCANNER_STAGE.items() if s == "early" and n not in BROAD_SCANNERS
    )
    sel_l = sum(
        1 for n, s in SCANNER_STAGE.items() if s == "late" and n not in BROAD_SCANNERS
    )
    print(f"Selective scanners: {sel_e} early-stage, {sel_l} late-stage")
    print("=" * 100)


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


def stage_bucket_label(count: int) -> str:
    return "2+" if count >= 2 else str(count)


# Ambiguous scanners are excluded from both stage counts by default. Set
# AMBIGUOUS_AS to "late" to reproduce the earlier run that forced Float
# Exhaustion onto the late side, so the two mappings can be compared directly.
AMBIGUOUS_AS = "exclude"


def stage_of(name: str) -> str:
    s = SCANNER_STAGE[name]
    if s == "ambiguous" and AMBIGUOUS_AS in ("late", "early"):
        return AMBIGUOUS_AS
    return s


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

    # Every selective scanner in a snapshot must be classified, else the stage
    # split silently drops it and the counts stop summing.
    all_selective_names: set[str] = set()
    for names in selective_flagged.values():
        all_selective_names |= names
    unclassified = all_selective_names - set(SCANNER_STAGE)
    if unclassified:
        raise SystemExit(
            f"{scan_date}: selective scanners missing a stage classification: "
            f"{sorted(unclassified)} — refusing to report a stage split that "
            "silently omits scanners"
        )

    union = set(selective_counts) | set(broad_flagged)
    union_size = len(union)

    events: list[dict] = []
    for sym in union:
        n = selective_counts.get(sym, 0)
        early = sorted(x for x in selective_flagged[sym] if stage_of(x) == "early")
        late = sorted(x for x in selective_flagged[sym] if stage_of(x) == "late")
        ambig = sorted(x for x in selective_flagged[sym] if stage_of(x) == "ambiguous")
        if len(early) + len(late) + len(ambig) != n:
            raise SystemExit(
                f"{scan_date}/{sym}: early+late+ambiguous="
                f"{len(early) + len(late) + len(ambig)} != selective={n} — "
                "stage map is inconsistent"
            )
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
                "early_count": len(early),
                "late_count": len(late),
                "ambiguous_count": len(ambig),
                "early_scanners": early,
                "late_scanners": late,
                "ambiguous_scanners": ambig,
                "early_bucket": stage_bucket_label(len(early)),
                "late_bucket": stage_bucket_label(len(late)),
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
                    "early_count": e["early_count"],
                    "late_count": e["late_count"],
                    "ambiguous_count": e["ambiguous_count"],
                    "early_bucket": e["early_bucket"],
                    "late_bucket": e["late_bucket"],
                    "early_scanners": json.dumps(e["early_scanners"]),
                    "late_scanners": json.dumps(e["late_scanners"]),
                    "ambiguous_scanners": json.dumps(e["ambiguous_scanners"]),
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


def _stats(rs: list[dict]) -> dict | None:
    if not rs:
        return None
    rets = [r["return_pct"] for r in rs]
    exc = [r["excess_return_pct"] for r in rs if r["excess_return_pct"] is not None]
    return {
        "n": len(rs),
        "dates": len({r["scan_date"] for r in rs}),
        "mean": sum(rets) / len(rets),
        "mean_excess": (sum(exc) / len(exc)) if exc else None,
        "win_rate": 100.0 * sum(1 for x in rets if x > 0) / len(rets),
        "win_rate_vs_bench": (
            100.0 * sum(1 for x in exc if x > 0) / len(exc) if exc else None
        ),
    }


def summarise_stage(rows: list[dict], horizon: int, field: str) -> dict:
    by: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        if r["horizon"] == horizon:
            by[r[field]].append(r)
    return {b: _stats(by.get(b) or []) for b in STAGE_BUCKET_LABELS}


def print_stage_tables(rows: list[dict], horizons: list[int], use_costs: bool) -> None:
    """STEP 3: early-stage and late-stage counts, each bucketed INDEPENDENTLY of
    total selective_scanner_count.

    Deliberately not overwriting the agreement-count table: that result stands
    on its own and conflating the two would hide which one is driving what.
    """
    print()
    print("#" * 108)
    print("# STAGE SPLIT -- does the negative gradient come from LATE-STAGE scanners?")
    print("# Same survivorship bias and cost assumptions as the table above.")
    print("#" * 108)
    for field, title in (
        ("early_bucket", "EARLY-STAGE scanner count"),
        ("late_bucket", "LATE-STAGE scanner count"),
    ):
        print(
            f"\n{'=' * 108}\nBUCKETED BY {title} (independent of total agreement)\n{'=' * 108}"
        )
        for h in horizons:
            s = summarise_stage(rows, h, field)
            print(f"\n  horizon {h} trading days")
            print("  " + "-" * 104)
            print(
                f"  {'bucket':<8}{'n':>8}{'dates':>7}{'mean':>10}"
                f"{'mean excess':>14}{'win%':>8}{'win% vs NSEI':>16}"
            )
            print("  " + "-" * 104)
            for b in STAGE_BUCKET_LABELS:
                d = s.get(b)
                if d is None:
                    print(f"  {b:<8}{'no data':>8}")
                    continue
                print(
                    f"  {b:<8}{d['n']:>8}{d['dates']:>7}{fmt(d['mean']):>10}"
                    f"{fmt(d['mean_excess']):>14}{fmt(d['win_rate']):>8}"
                    f"{fmt(d['win_rate_vs_bench']):>16}"
                )
            lo, hi = s.get("0"), s.get("2+")
            if (
                lo
                and hi
                and lo["mean_excess"] is not None
                and hi["mean_excess"] is not None
            ):
                print(
                    f"  {'2+ minus 0':<8}{'':>8}{'':>7}{'':>10}"
                    f"{fmt(hi['mean_excess'] - lo['mean_excess']):>14}"
                    f"{'':>8}{fmt(hi['win_rate'] - lo['win_rate']):>16}"
                )


def print_pair_test(rows: list[dict], horizons: list[int]) -> None:
    """STEP 4: among symbols with exactly ONE late-stage flag, does adding an
    early-stage co-flag help? This is the actionable question and is different
    from raw agreement count: it isolates the late-stage signal as a constant
    and varies only whether an earlier-stage condition was also present.
    """
    print()
    print("#" * 108)
    print("# PAIR TEST -- late-stage flag alone  vs  late-stage + early-stage co-flag")
    print(
        "# Population: symbols with EXACTLY 1 late-stage scanner flag. The late-stage"
    )
    print(
        "# signal is held constant; only the presence of an early-stage co-flag varies."
    )
    print("#" * 108)
    print(
        f"\n{'h':>4}{'grp':>28}{'n':>8}{'dates':>7}{'mean':>10}"
        f"{'mean excess':>14}{'win%':>8}{'win% vs NSEI':>16}"
    )
    print("-" * 108)
    deltas: dict[int, tuple] = {}
    for h in horizons:
        for grp, sel in (
            (
                "late=1, early=0",
                lambda r: r["late_count"] == 1 and r["early_count"] == 0,
            ),
            (
                "late=1, early>=1",
                lambda r: r["late_count"] == 1 and r["early_count"] >= 1,
            ),
        ):
            rs = [r for r in rows if r["horizon"] == h and sel(r)]
            d = _stats(rs)
            if d is None:
                print(f"{h:>4}{grp:>28}{'no data':>8}")
                continue
            print(
                f"{h:>4}{grp:>28}{d['n']:>8}{d['dates']:>7}{fmt(d['mean']):>10}"
                f"{fmt(d['mean_excess']):>14}{fmt(d['win_rate']):>8}"
                f"{fmt(d['win_rate_vs_bench']):>16}"
            )
        a = [
            r
            for r in rows
            if r["horizon"] == h and r["late_count"] == 1 and r["early_count"] == 0
        ]
        b = [
            r
            for r in rows
            if r["horizon"] == h and r["late_count"] == 1 and r["early_count"] >= 1
        ]
        da, db = _stats(a), _stats(b)
        if (
            da
            and db
            and da["mean_excess"] is not None
            and db["mean_excess"] is not None
        ):
            d_exc = db["mean_excess"] - da["mean_excess"]
            d_wr = db["win_rate"] - da["win_rate"]
            deltas[h] = (d_exc, d_wr, da["n"], db["n"])
            print(
                f"{'':>4}{'DELTA (+early)':>28}{'':>8}{'':>7}{'':>10}"
                f"{fmt(d_exc):>14}{'':>8}{fmt(d_wr):>16}"
            )
        print("-" * 108)
    if deltas:
        print("\n  Reading: a POSITIVE delta means adding an early-stage co-flag to a")
        print("  late-stage signal IMPROVED forward excess return. Negative means the")
        print(
            "  early-stage co-flag made it worse -- i.e. late-stage crowding dominates."
        )


def print_mix_test(rows: list[dict], horizons: list[int]) -> None:
    """DECISIVE TEST: hold total agreement FIXED, vary only the early/late mix.

    This is the comparison that removes the confound in the stage tables above,
    whose bucket-0 is dominated by the control group. Within a single
    selective_scanner_count, comparing the homogeneous mixes against the mixed
    pair isolates stage composition from raw agreement.
    """
    print()
    print("#" * 108)
    print(f"# MIX TEST -- total agreement HELD FIXED, only the early/late mix varies")
    print(f"# ambiguous-stage scanners: {AMBIGUOUS_AS} (Float Exhaustion)")
    print("#" * 108)
    for total in (2, 3):
        print(f"\n{'=' * 100}\nselective_scanner_count == {total}\n{'=' * 100}")
        print(
            f"{'h':>4}{'mix':>18}{'n':>8}{'dates':>7}{'mean':>9}"
            f"{'mean exc':>10}{'win%':>8}{'win%vsNSEI':>12}"
        )
        print("-" * 100)
        for h in horizons:
            base = [
                r
                for r in rows
                if r["horizon"] == h and r["selective_scanner_count"] == total
            ]
            # (early, late) composition pairs, deduped: at total==2 both
            # "mixed" labels describe the SAME bucket (1 early + 1 late), so
            # listing both would double-count it and corrupt the comparison.
            compositions = [
                (total, 0),
                (total - 1, 1),
                (1, total - 1),
                (0, total),
            ]
            mixes = []
            for e_cnt, l_cnt in dict.fromkeys(compositions):
                mixes.append(
                    (
                        f"early{e_cnt}/late{l_cnt}",
                        (
                            lambda e_cnt, l_cnt: (
                                lambda r: r["early_count"] == e_cnt
                                and r["late_count"] == l_cnt
                            )
                        )(e_cnt, l_cnt),
                    )
                )
            mixed_labels = [
                f"early{e}/late{l}"
                for e, l in ((total - 1, 1),)
                if (e, l) in compositions
            ]
            stats: dict[str, dict | None] = {}
            for lab, sel in mixes:
                g = [r for r in base if sel(r)]
                d = _stats(g)
                stats[lab] = d
                if d is None:
                    print(f"{h:>4}{lab:>18}{'no data':>8}")
                    continue
                print(
                    f"{h:>4}{lab:>18}{d['n']:>8}{d['dates']:>7}{fmt(d['mean']):>9}"
                    f"{fmt(d['mean_excess']):>10}{fmt(d['win_rate']):>8}"
                    f"{fmt(d['win_rate_vs_bench']):>12}"
                )
            # Mixed is the worst of the two available mixes at this total?
            mixed = [stats[k] for k in dict.fromkeys(mixed_labels) if stats.get(k)]
            mixed = [d for d in mixed if d["mean_excess"] is not None]
            worst_label = (
                min(
                    dict.fromkeys(mixed_labels),
                    key=lambda k: stats[k]["mean_excess"],
                )
                if mixed
                else "n/a"
            )
            homo = [
                stats[k]["mean_excess"]
                for k in (f"early{total}/late0", f"early0/late{total}")
                if stats.get(k) and stats[k]["mean_excess"] is not None
            ]
            if homo and mixed:
                # "Mixed is worst" requires mixed to lose to BOTH homogeneous
                # groups: max(mixed) < min(homo). Comparing the best
                # homogeneous against mixed instead would call mixed "worst"
                # even when it beat one of them.
                verdict = (
                    "MIXED WORST"
                    if max(d["mean_excess"] for d in mixed) < min(homo)
                    else "mixed NOT worst"
                )
                print(f"{'':>4}{'-> ' + verdict:>18}  (worst mix: {worst_label})")
            print("-" * 100)


def check_stage_invariants(rows: list[dict]) -> bool:
    """Same class of arithmetic/assignment cross-check as the agreement table."""
    ok = True
    bad_sum = [
        r
        for r in rows
        if r["early_count"] + r["late_count"] + r["ambiguous_count"]
        != r["selective_scanner_count"]
    ]
    if bad_sum:
        print(f"  FAIL: early+late+ambiguous != selective on {len(bad_sum)} rows")
        ok = False
    for h in sorted({r["horizon"] for r in rows}):
        hs = [r for r in rows if r["horizon"] == h]
        for field, labels in (
            ("early_bucket", STAGE_BUCKET_LABELS),
            ("late_bucket", STAGE_BUCKET_LABELS),
        ):
            g: dict[str, list[float]] = defaultdict(list)
            for r in hs:
                g[r[field]].append(r["return_pct"])
            tot = sum(len(v) for v in g.values())
            if tot != len(hs):
                print(f"  FAIL h={h} {field}: sum(bucket n)={tot} != rows={len(hs)}")
                ok = False
                continue
            pooled = sum(sum(v) for v in g.values()) / tot
            direct = sum(r["return_pct"] for r in hs) / len(hs)
            if abs(pooled - direct) > 1e-9:
                print(f"  FAIL h={h} {field}: pooled={pooled} != direct={direct}")
                ok = False
    return ok


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


# =============================================================================
# FLOAT EXHAUSTION DECOMPOSITION
#
# Read-only analysis. The early/late stage-mix result reversed when Float
# Exhaustion was excluded, so the question is no longer "is mixed worse?" but
# "what is Float Exhaustion actually contributing?".
#
# All comparators below are pure functions and are exercised against
# hand-constructed toy cases (with the expected answer written down BEFORE
# the comparator body) by run_selftest(), which main() runs before any real
# data is touched. A comparator that silently returns the convenient answer is
# exactly the failure mode already hit twice in this investigation.
# =============================================================================

FLOAT_SCANNER = "Float Exhaustion"
THIN_N = 100  # below this, a bucket's mean is not interpretable
MIN_DATES_FOR_PAIR_RANKING = 4


def _max_dates(by_pair, horizon: int) -> int:
    best = 0
    for grp in by_pair.values():
        st = _stats([r for r in grp if r["horizon"] == horizon])
        if st:
            best = max(best, st["dates"])
    return best


# -- comparators --------------------------------------------------------------
# NOTE: expectations are declared first, on purpose.


def mixed_worst_verdict(mixed: list[float], homo: list[float]) -> str:
    """Does every mixed bucket lose to every homogeneous bucket?

    Requires max(mixed) < min(homo). The earlier buggy version compared
    max(homo) > min(mixed), i.e. the BEST homogeneous against the WORST mixed,
    which reports "worst" whenever mixed beats only one of them.
    """
    if not mixed or not homo:
        return "no data"
    return "MIXED WORST" if max(mixed) < min(homo) else "mixed NOT worst"


def both_underperform(candidates: list[float], reference: float) -> str:
    """Do BOTH candidate buckets underperform the reference bucket?"""
    if not candidates:
        return "no data"
    return "BOTH UNDERPERFORM" if all(c < reference for c in candidates) else "not both"


# -- row -> pair / group ------------------------------------------------------


def row_scanners(r: dict) -> list[str]:
    """Contributing selective scanners, deduped and sorted.

    Accepts JSON strings (forward-row/CSV form) or lists (event form).
    """
    names: list[str] = []
    for key in ("early_scanners", "late_scanners", "ambiguous_scanners"):
        raw = r.get(key)
        if not raw:
            continue
        names.extend(json.loads(raw) if isinstance(raw, str) else list(raw))
    return sorted(set(names))


def float_group(r: dict) -> str | None:
    """STEP 2 grouping for a total==2 row.

    Independent of AMBIGUOUS_AS: Float Exhaustion is detected BY NAME and the
    other scanner's stage is looked up, so the grouping cannot flip when the
    ambiguous mapping changes. Verified in run_selftest().
    """
    if r.get("selective_scanner_count") != 2:
        return None
    names = row_scanners(r)
    if len(names) != 2:
        return None
    has_float = FLOAT_SCANNER in names
    others = [n for n in names if n != FLOAT_SCANNER]
    if has_float:
        if len(others) != 1:
            return None  # Float+Float is impossible (one scanner, one flag)
        st = stage_of(others[0])
        return {"early": "b_early+float", "late": "c_late+float"}.get(st)
    st = {stage_of(n) for n in others}
    if st == {"early", "late"}:
        return "a_early+late"
    if st == {"early"}:
        return "h_early+early"
    if st == {"late"}:
        return "h_late+late"
    return None


def sole_scanner(r: dict) -> str | None:
    """STEP 3: the one contributing scanner of a total==1 row."""
    if r.get("selective_scanner_count") != 1:
        return None
    names = row_scanners(r)
    return names[0] if len(names) == 1 else None


def pair_class(a: str, b: str) -> str:
    """Compact stage class for a pair: e+e, e+l, e+F, l+l, l+F, ..."""

    def tag(n: str) -> str:
        return "F" if n == FLOAT_SCANNER else stage_of(n)[0]

    return "".join(sorted([tag(a), tag(b)]))


# -- self-test ----------------------------------------------------------------

MIXED_WORST_CASES = [
    # (description, mixed excesses, homogeneous excesses, expected)
    ("mixed loses to both", [-3.0], [-1.0, -2.0], "MIXED WORST"),
    ("mixed loses to both (2 mixes)", [-5.0, -4.0], [-1.0, -2.0], "MIXED WORST"),
    ("mixed beats ONE homogeneous", [-3.0], [-4.0, -2.0], "mixed NOT worst"),
    ("mixed beats both", [-1.0], [-3.0, -2.0], "mixed NOT worst"),
    ("one mix better than homo", [-5.0, -1.0], [-3.0], "mixed NOT worst"),
    ("ties are not 'worst'", [-2.0], [-2.0, -2.0], "mixed NOT worst"),
    ("no mixed data", [], [-1.0], "no data"),
    ("no homogeneous data", [-1.0], [], "no data"),
    ("both empty", [], [], "no data"),
]

BOTH_UNDERPERFORM_CASES = [
    # (description, candidates, reference, expected)
    ("both under", [-3.0, -4.0], -1.0, "BOTH UNDERPERFORM"),
    ("one under one over", [-3.0, 0.0], -1.0, "not both"),
    ("both under, one marginal", [-3.0, -1.5], -1.0, "BOTH UNDERPERFORM"),
    ("both over", [-1.0, -2.0], -3.0, "not both"),
    ("single candidate under", [-3.0], -1.0, "BOTH UNDERPERFORM"),
    ("no candidates", [], -1.0, "no data"),
]


def _toy_row(early=(), late=(), ambig=(), n=None) -> dict:
    return {
        "selective_scanner_count": n
        if n is not None
        else len(early) + len(late) + len(ambig),
        "early_scanners": json.dumps(list(early)),
        "late_scanners": json.dumps(list(late)),
        "ambiguous_scanners": json.dumps(list(ambig)),
    }


FLOAT_GROUP_CASES = [
    # (description, row, expected group)
    (
        "early+late, no float",
        _toy_row(early=["Bottom Hunter"], late=["Super Breakout"]),
        "a_early+late",
    ),
    (
        "early+float",
        _toy_row(early=["Bottom Hunter"], ambig=[FLOAT_SCANNER]),
        "b_early+float",
    ),
    (
        "late+float",
        _toy_row(late=["Super Breakout"], ambig=[FLOAT_SCANNER]),
        "c_late+float",
    ),
    (
        "float's position in lists is irrelevant",
        _toy_row(early=["Bottom Hunter", FLOAT_SCANNER], n=2),
        "b_early+float",
    ),
    (
        "early+early",
        _toy_row(early=["Bottom Hunter", "Seasonal Delivery"]),
        "h_early+early",
    ),
    ("late+late", _toy_row(late=["Super Breakout", "Darvas Box Pro"]), "h_late+late"),
    ("float+float impossible -> None", _toy_row(ambig=[FLOAT_SCANNER], n=2), None),
    (
        "total != 2 -> None",
        _toy_row(early=["Bottom Hunter", "Seasonal Delivery", "The Trigger"], n=3),
        None,
    ),
    (
        "total 2 but one distinct name -> None",
        _toy_row(early=["Bottom Hunter", "Bottom Hunter"], n=2),
        None,
    ),
]

# An unknown scanner name must raise LOUDLY, not be silently bucketed: a silent
# "unclassified" fallback is how a scanner quietly vanishes from a comparison.
FLOAT_GROUP_RAISES = [
    (
        "unknown scanner name raises KeyError",
        _toy_row(early=["Not A Real Scanner"], ambig=[FLOAT_SCANNER], n=2),
        KeyError,
    ),
]

SOLE_SCANNER_CASES = [
    ("sole early", _toy_row(early=["Bottom Hunter"]), "Bottom Hunter"),
    ("sole float", _toy_row(ambig=[FLOAT_SCANNER]), FLOAT_SCANNER),
    ("total 2 -> None", _toy_row(early=["A"], late=["B"]), None),
]

ROW_SCANNERS_CASES = [
    (
        "merges all three lists",
        _toy_row(early=["B"], late=["C"], ambig=[FLOAT_SCANNER]),
        ["B", "C", FLOAT_SCANNER],
    ),
    ("dedupes", _toy_row(early=["A", "A"]), ["A"]),
    (
        "accepts list form",
        {"selective_scanner_count": 1, "early_scanners": ["X"]},
        ["X"],
    ),
    ("empty row", {}, []),
]


def run_selftest() -> bool:
    """Verify every comparator/grouping on hand-built cases with known answers."""
    global AMBIGUOUS_AS
    print("#" * 108)
    print("# SELF-TEST -- toy cases with known answers, run BEFORE any real data")
    print("#" * 108)
    failures: list[str] = []

    def check(desc, got, want):
        ok = got == want
        print(f"  [{'PASS' if ok else 'FAIL'}] {desc}: got {got!r}, want {want!r}")
        if not ok:
            failures.append(desc)

    for desc, mixed, homo, want in MIXED_WORST_CASES:
        check(f"mixed_worst/{desc}", mixed_worst_verdict(mixed, homo), want)
    for desc, cands, ref, want in BOTH_UNDERPERFORM_CASES:
        check(f"both_underperform/{desc}", both_underperform(cands, ref), want)

    saved = AMBIGUOUS_AS
    for mode in ("exclude", "late", "early"):
        AMBIGUOUS_AS = mode
        for desc, row, want in FLOAT_GROUP_CASES:
            check(f"float_group[{mode}]/{desc}", float_group(row), want)
    AMBIGUOUS_AS = saved

    for desc, row, want in FLOAT_GROUP_RAISES:
        try:
            got = float_group(row)
        except Exception as exc:  # noqa: BLE001
            got = type(exc)
        check(f"float_group/raises/{desc}", got, want)

    for desc, row, want in SOLE_SCANNER_CASES:
        check(f"sole_scanner/{desc}", sole_scanner(row), want)
    for desc, row, want in ROW_SCANNERS_CASES:
        check(f"row_scanners/{desc}", row_scanners(row), want)

    print(f"\n  {len(failures)} failure(s)")
    if failures:
        raise SystemExit(
            "Self-test FAILED — refusing to run the analysis on real data with a "
            f"comparator that does not do what it claims: {failures}"
        )
    print("  ALL PASSED — comparators verified, proceeding to real data\n")
    return True


def check_pair_invariants(rows: list[dict]) -> bool:
    """Real-data cross-check of the pair/grouping plumbing."""
    ok = True
    total2 = [r for r in rows if r["selective_scanner_count"] == 2]
    bad_len = [r for r in total2 if len(row_scanners(r)) != 2]
    if bad_len:
        print(f"  FAIL: {len(bad_len)} total==2 rows do not name exactly 2 scanners")
        ok = False
    bad_cnt = [
        r
        for r in total2
        if r["early_count"] + r["late_count"] + r["ambiguous_count"] != 2
    ]
    if bad_cnt:
        print(f"  FAIL: {len(bad_cnt)} total==2 rows have stage counts != 2")
        ok = False
    ungrouped = [r for r in total2 if float_group(r) is None]
    if ungrouped:
        print(
            f"  NOTE: {len(ungrouped)} total==2 rows match no STEP-2 group "
            f"(expected only float+float / unclassified) -- e.g. "
            f"{row_scanners(ungrouped[0])}"
        )
    total1 = [r for r in rows if r["selective_scanner_count"] == 1]
    bad_sole = [r for r in total1 if sole_scanner(r) is None]
    if bad_sole:
        print(f"  FAIL: {len(bad_sole)} total==1 rows do not name exactly 1 scanner")
        ok = False
    print("  PAIR INVARIANTS: " + ("PASSED" if ok else "FAILED"))
    return ok


# -- STEP 1: every actual pair -----------------------------------------------


def print_pair_decomposition(rows, horizons, use_costs):
    print()
    print("#" * 108)
    print("# STEP 1 -- PAIR DECOMPOSITION at total selective_scanner_count == 2")
    print("# Every actual unordered PAIR of contributing scanners, not stage buckets.")
    print(
        f"# THIN (n<{THIN_N}) flags buckets whose mean is not interpretable. "
        f"Costs: {'on' if use_costs else 'OFF'}."
    )
    print("#" * 108)
    by_pair: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in rows:
        if r["selective_scanner_count"] != 2:
            continue
        names = row_scanners(r)
        if len(names) == 2:
            by_pair[tuple(names)].append(r)

    for h in horizons:
        rows_h = [r for v in by_pair.values() for r in v if r["horizon"] == h]
        groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
        for r in rows_h:
            groups[tuple(row_scanners(r))].append(r)
        print(f"\n{'=' * 108}\nHORIZON {h}d -- all pairs at total==2\n{'=' * 108}")
        print(
            f"{'pair':<52}{'class':<8}{'n':>6}{'dates':>7}{'mean':>9}{'mean exc':>10}{'win%':>8}  flag"
        )
        recs = []
        for pk, grp in groups.items():
            st = _stats(grp)
            recs.append((st["n"], pk, pair_class(*pk), st))
        for n, pk, cls, st in sorted(recs, key=lambda x: (-x[0], x[1])):
            print(
                f"{pk[0] + ' + ' + pk[1]:<52}{cls:<8}{st['n']:>6}{st['dates']:>7}"
                f"{fmt(st['mean']):>9}{fmt(st['mean_excess']):>10}{fmt(st['win_rate']):>8}"
                f"  {'THIN' if n < THIN_N else ''}"
            )

    # Adequate-n ranking across the full horizon set.
    print(
        f"\n{'=' * 108}\nADEQUATE-N PAIR RANKING (n>={THIN_N} at EVERY horizon), by mean excess\n{'=' * 108}"
    )
    print(f"{'pair':<52}{'class':<8}{'min n':>7}{'max n':>7}{'mean exc (avg h)':>19}")
    ranking = []
    for pk, grp in by_pair.items():
        per_h = {}
        for h in horizons:
            st = _stats([r for r in grp if r["horizon"] == h])
            per_h[h] = st
        if any(st is None or st["n"] < THIN_N for st in per_h.values()):
            continue
        vals = [
            st["mean_excess"] for st in per_h.values() if st["mean_excess"] is not None
        ]
        if not vals:
            continue
        ns = [st["n"] for st in per_h.values()]
        ranking.append((sum(vals) / len(vals), pk, pair_class(*pk), min(ns), max(ns)))
    for avg, pk, cls, nmin, nmax in sorted(ranking):
        print(f"{pk[0] + ' + ' + pk[1]:<52}{cls:<8}{nmin:>7}{nmax:>7}{fmt(avg):>19}")
    if not ranking:
        max_d = (
            max(
                (_stats([r for r in v if r["horizon"] == max(horizons)]) or {}).get(
                    "dates", 0
                )
                for v in by_pair.values()
            )
            if by_pair
            else 0
        )
        print(
            f"  (EMPTY -- no single pair reaches n>={THIN_N} at all {len(horizons)} "
            f"horizons. The longest horizon rests on {max_d} distinct dates, so this "
            f"is a data-coverage limit, not a zero result.)"
        )

    # The same ranking restricted to horizons with enough distinct dates to
    # support a per-pair mean. Reporting the all-horizon table as empty and
    # stopping there would bury every adequately-sized comparison.
    dated = [
        h for h in horizons if _max_dates(by_pair, h) >= MIN_DATES_FOR_PAIR_RANKING
    ]
    if dated and dated != horizons:
        print(
            f"\n{'=' * 108}\nADEQUATE-N PAIR RANKING over horizons with "
            f">={MIN_DATES_FOR_PAIR_RANKING} distinct dates: h{dated[0]}-h{dated[-1]}\n{'=' * 108}"
        )
        print(
            f"{'pair':<52}{'class':<8}{'min n':>7}{'max n':>7}{'mean exc (avg h)':>19}"
        )
        rank2 = []
        for pk, grp in by_pair.items():
            per_h = {h: _stats([r for r in grp if r["horizon"] == h]) for h in dated}
            if any(st is None or st["n"] < THIN_N for st in per_h.values()):
                continue
            vals = [
                st["mean_excess"]
                for st in per_h.values()
                if st["mean_excess"] is not None
            ]
            if not vals:
                continue
            ns = [st["n"] for st in per_h.values()]
            rank2.append((sum(vals) / len(vals), pk, pair_class(*pk), min(ns), max(ns)))
        for avg, pk, cls, nmin, nmax in sorted(rank2):
            print(
                f"{pk[0] + ' + ' + pk[1]:<52}{cls:<8}{nmin:>7}{nmax:>7}{fmt(avg):>19}"
            )
        if not rank2:
            print(f"  (still empty at n>={THIN_N} over h{dated[0]}-h{dated[-1]})")


# -- STEP 2: the three Float groupings ---------------------------------------


def print_float_grouping(rows, horizons, use_costs):
    print()
    print("#" * 108)
    print("# STEP 2 -- IS THE EFFECT SPECIFIC TO FLOAT EXHAUSTION?")
    print(
        "#   a_early+late    : one non-Float early + one non-Float late  <- reference"
    )
    print("#   b_early+float   : one non-Float early + Float Exhaustion")
    print("#   c_late+float    : one non-Float late  + Float Exhaustion")
    print("#   h_*             : homogeneous non-Float reference buckets")
    print(
        "# Grouping is by scanner NAME, so it is identical under every --ambiguous-as."
    )
    print("#" * 108)
    order = [
        "a_early+late",
        "b_early+float",
        "c_late+float",
        "h_early+early",
        "h_late+late",
    ]
    label = {
        "a_early+late": "a  early+late (no float)",
        "b_early+float": "b  early+float",
        "c_late+float": "c  late+float",
        "h_early+early": "-- early+early (homog)",
        "h_late+late": "-- late+late (homog)",
    }
    for h in horizons:
        groups = defaultdict(list)
        for r in rows:
            if r["horizon"] != h:
                continue
            g = float_group(r)
            if g:
                groups[g].append(r)
        print(f"\n{'=' * 108}\nHORIZON {h}d, total==2\n{'=' * 108}")
        print(
            f"{'group':<32}{'n':>6}{'dates':>7}{'mean':>9}{'mean exc':>10}{'win%':>8}{'win%vsNSEI':>12}  flag"
        )
        stats = {}
        for g in order:
            st = _stats(groups.get(g) or [])
            stats[g] = st
            if st is None:
                print(f"{label[g]:<32}{'no data':>6}")
                continue
            print(
                f"{label[g]:<32}{st['n']:>6}{st['dates']:>7}{fmt(st['mean']):>9}"
                f"{fmt(st['mean_excess']):>10}{fmt(st['win_rate']):>8}"
                f"{fmt(st['win_rate_vs_bench']):>12}  {'THIN' if st['n'] < THIN_N else ''}"
            )
        # Conclusion criteria (b) and (c) vs (a), using ONLY adequately-sized buckets.
        ref = stats.get("a_early+late")
        cands = [
            stats[g]["mean_excess"]
            for g in ("b_early+float", "c_late+float")
            if stats.get(g)
            and stats[g]["mean_excess"] is not None
            and stats[g]["n"] >= THIN_N
        ]
        thin = [
            g
            for g in ("b_early+float", "c_late+float")
            if stats.get(g) and stats[g]["n"] < THIN_N
        ]
        if (
            ref
            and ref["mean_excess"] is not None
            and ref["n"] >= THIN_N
            and len(cands) == 2
        ):
            verdict = both_underperform(cands, ref["mean_excess"])
            print(
                f"{'':>4}-> b&c vs a (n>={THIN_N} only): {verdict}"
                + (f"  [excluded as THIN: {thin}]" if thin else "")
            )
        else:
            print(f"{'':>4}-> b&c vs a: no data (thin or missing reference)")


# -- STEP 3: standalone profiles ---------------------------------------------


def print_standalone(rows, horizons, use_costs):
    print()
    print("#" * 108)
    print("# STEP 3 -- STANDALONE PROFILE per scanner (sole contributor, total==1)")
    print("# Is Float Exhaustion weak on its own, or specifically bad when paired?")
    print("#" * 108)
    by_scanner: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        s = sole_scanner(r)
        if s:
            by_scanner[s].append(r)

    curves: dict[str, dict[int, dict]] = {}
    for sc, grp in by_scanner.items():
        curves[sc] = {
            h: _stats([r for r in grp if r["horizon"] == h]) for h in horizons
        }

    print(f"{'scanner':<24}" + "".join(f"{'h' + str(h):>18}" for h in horizons))
    for sc in sorted(curves, key=lambda s: (s != FLOAT_SCANNER, s)):
        cells = []
        for h in horizons:
            st = curves[sc][h]
            cells.append(
                f"{fmt(st['mean_excess']):>8}(n{st['n']:>4})"
                if st
                else f"{'-':>8}(n   0)"
            )
        mark = "  <== " if sc == FLOAT_SCANNER else ""
        print(f"{sc:<24}" + "".join(cells) + mark)

    print(
        f"\n{'-' * 108}\nFloat vs the rest (mean excess across horizons with n>={THIN_N})"
    )
    means: dict[str, list[float]] = {}
    for sc, per_h in curves.items():
        vals = [
            st["mean_excess"]
            for h, st in per_h.items()
            if st and st["n"] >= THIN_N and st["mean_excess"] is not None
        ]
        means[sc] = vals
    for sc in sorted(
        means, key=lambda s: (sum(means[s]) / len(means[s])) if means[s] else 0
    ):
        vals = means[sc]
        avg = f"{sum(vals) / len(vals):.1f}%" if vals else "n/a (all horizons thin)"
        mark = "  <== FLOAT" if sc == FLOAT_SCANNER else ""
        # NB: no double space before "from" -- pycodestyle 6.x (pinned in
        # pre-commit) reports E272 on "  from" even inside an f-string.
        print(f"  {sc:<24}{avg:>10} over {len(vals)}/{len(horizons)} horizons{mark}")


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
    p.add_argument(
        "--ambiguous-as",
        choices=["exclude", "late", "early"],
        default="exclude",
        help="how to treat stage-ambiguous scanners (Float Exhaustion). 'late' "
        "reproduces the earlier forced classification, for comparison.",
    )
    p.add_argument(
        "--decompose-float",
        action="store_true",
        help="run the Float Exhaustion decomposition (selftest, pair "
        "decomposition, (a)/(b)/(c) grouping, standalone profiles)",
    )
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    global AMBIGUOUS_AS
    AMBIGUOUS_AS = args.ambiguous_as
    if args.decompose_float:
        # BEFORE touching any real data, and before printing any comparator
        # output, so a broken comparator can never produce a headline number.
        run_selftest()
    print_classification()
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

    print_stage_tables(all_rows, args.horizons, not args.no_costs)
    print_pair_test(all_rows, args.horizons)
    print_mix_test(all_rows, args.horizons)

    print("\nSTAGE BUCKET INVARIANT CHECKS (early+late == selective; no rows lost):")
    print("  PASSED" if check_stage_invariants(all_rows) else "  FAILED")

    if args.decompose_float:
        print("\nPAIR / GROUPING INVARIANT CHECKS:")
        check_pair_invariants(all_rows)
        print_pair_decomposition(all_rows, args.horizons, not args.no_costs)
        print_float_grouping(all_rows, args.horizons, not args.no_costs)
        print_standalone(all_rows, args.horizons, not args.no_costs)

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
