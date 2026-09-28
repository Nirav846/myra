"""
Regime-Robust Per-Scanner Standalone Ranking
============================================
Answers one question: **which individual scanners hold up across BOTH regimes?**

Why this replaced the confluence-count question
------------------------------------------------
Two earlier conclusions force it:
  1. The confluence agreement count is not a buy signal post-2025 (more
     agreement predicted worse excess return).
  2. The early/late stage-mix story was a Float Exhaustion artifact, not a
     stage effect -- reclassifying one scanner reversed it.
But "a scanner is useful" is still a per-scanner question, and the observed
spread between individual scanners is far larger than the spread between
agreement levels.

Why windows are structural, not a flag
---------------------------------------
2023 (8 dates) was a bull year: nearly every scanner looked good. 2025-09..
2026-09 (8 dates) was weak: nearly every scanner looked bad. Absolute excess
therefore measures mostly *the regime*, and a scanner that returned -3% in a
-5% window beat the market while one returning +2% in a +10% window lagged it.

So this tool:
  * runs each window as a SEPARATE pass, and never pools rows across windows
    (windows are declared as repeated --window args, so pooling is not
    expressible);
  * reports raw excess AND `relative excess` = scanner excess MINUS the mean
    excess of all scanner-flagged symbols in the SAME window, which subtracts
    the regime tide;
  * requires sign agreement across windows before calling anything robust.

Population: every symbol a scanner flagged, on any snapshot date, WITHOUT
restricting to sole-flagged names -- maximises n. A symbol flagged by two
scanners counts in both scanners' rows, which is intended (the question is
per-scanner quality, not per-flag exclusivity).

Survivorship caveat: the union is built from current-date scanner output, so
results are not point-in-time. Compare buckets to each other via excess; never
treat an absolute return as a forecast.

Usage
-----
    python tools/rank_scanners.py
    python tools/rank_scanners.py --no-costs
    python tools/rank_scanners.py --window 2023:2023-01-01:2023-12-31

The hand-built toy cases in run_selftest() run and must pass BEFORE any
snapshot is read or any comparator prints a real number.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import backtest_confluence as bc  # noqa: E402

HORIZONS = [20, 40, 60, 90, 120, 180]
ROBUST_HORIZONS = [60, 120]
DEFAULT_WINDOWS = [
    ("2023-bull", "2023-01-01", "2023-12-31"),
    ("2025-26-weak", "2025-01-01", "2026-12-31"),
]
THIN_N = 100
CONTROL_SEED = 42


# =============================================================================
# STEP 4 -- comparators. Pure functions, each with hand-built toy cases whose
# expected answers were written down before the bodies.
# =============================================================================
def relative_excess(scanner_excess, window_mean):
    """Scanner excess minus the window's all-flagged mean excess.

    None-safe: a missing benchmark in EITHER operand yields None, never a
    number built from a partially-None subtraction.
    """
    if scanner_excess is None or window_mean is None:
        return None
    return scanner_excess - window_mean


def rank_of(value, values):
    """1-based descending rank (1 = best = highest), ties share the best rank.

    None in `values` is EXCLUDED from ranking rather than sorted to the
    bottom: a scanner with no benchmark has no measured rank, and pretending
    it is worst would let a data gap masquerade as a bad result.
    """
    if value is None:
        return None
    usable = [v for v in values if v is not None]
    if value not in usable:
        return None
    return 1 + sum(1 for v in usable if v > value)


def rank_change(rank_a, rank_b):
    """rank_b - rank_a. POSITIVE = slipped (worse in window B); negative = improved."""
    if rank_a is None or rank_b is None:
        return None
    return rank_b - rank_a


def sign_agreement(e_a, e_b):
    """Do the two windows even point the same way?"""
    if e_a is None or e_b is None:
        return "unknown"
    if e_a == 0 or e_b == 0:
        return "flat"
    if (e_a > 0) == (e_b > 0):
        return "agree+" if e_a > 0 else "agree-"
    return "disagree"


def robustness_min(rel_a, rel_b):
    """A scanner is only as good as its WORSE window. None in either -> None."""
    if rel_a is None or rel_b is None:
        return None
    return min(rel_a, rel_b)


def parse_window(spec: str) -> tuple[str, str, str]:
    parts = spec.split(":")
    if len(parts) != 3:
        raise argparse.ArgumentTypeError(
            f"--window wants NAME:SINCE:UNTIL (YYYY-MM-DD), got {spec!r}"
        )
    name, since, until = (p.strip() for p in parts)
    if not (name and len(since) == 10 and len(until) == 10):
        raise argparse.ArgumentTypeError(f"malformed --window {spec!r}")
    return name, since, until


# -- toy cases: (args, expected) ------------------------------------------------
RELATIVE_EXCESS_CASES = [
    # The motivating example: -3% inside a -5% window is +2 relative.
    ((-3.0, -5.0), 2.0),
    # +2% inside a +10% window is -8 relative.
    ((2.0, 10.0), -8.0),
    ((5.0, 5.0), 0.0),
    ((0.0, -4.0), 4.0),
    # None propagation: never a partial answer.
    ((None, -5.0), None),
    ((-3.0, None), None),
    ((None, None), None),
]

RANK_CASES = [
    # (value, all_values, expected_rank)
    (5.0, [5.0, 3.0, 1.0], 1),
    (3.0, [5.0, 3.0, 1.0], 2),
    (1.0, [5.0, 3.0, 1.0], 3),
    # Ties share the better rank; the tie does NOT push the next one down.
    (5.0, [5.0, 5.0, 1.0], 1),
    (1.0, [5.0, 5.0, 1.0], 3),
    # Unmeasured values are excluded, not ranked last.
    (3.0, [3.0, None, 1.0], 1),
    (1.0, [3.0, None, 1.0], 2),
    # A value that is not in the pool has no rank.
    (9.0, [3.0, 1.0], None),
    (None, [3.0, 1.0], None),
]

RANK_CHANGE_CASES = [
    ((1, 1), 0),  # held #1
    ((1, 4), 3),  # slipped three places
    ((4, 1), -3),  # climbed
    ((2, 2), 0),
    ((None, 2), None),
    ((2, None), None),
]

SIGN_AGREEMENT_CASES = [
    ((3.0, 4.0), "agree+"),
    ((-3.0, -1.0), "agree-"),
    ((3.0, -1.0), "disagree"),
    ((-3.0, 1.0), "disagree"),
    ((0.0, 5.0), "flat"),
    ((5.0, 0.0), "flat"),
    ((None, 5.0), "unknown"),
    ((5.0, None), "unknown"),
]

ROBUST_MIN_CASES = [
    ((4.0, 2.0), 2.0),  # the worse window governs
    ((-1.0, 3.0), -1.0),
    ((1.0, 1.0), 1.0),
    ((None, 3.0), None),
    ((3.0, None), None),
]

WINDOW_PARSE_CASES = [
    ("2023:2023-01-01:2023-12-31", ("2023", "2023-01-01", "2023-12-31")),
    (" spaced : 2019-01-01 : 2019-12-31 ", ("spaced", "2019-01-01", "2019-12-31")),
]


def run_selftest() -> bool:
    """Toy cases FIRST. Any failure aborts before real data is read."""
    failures = []

    for args, want in RELATIVE_EXCESS_CASES:
        got = relative_excess(*args)
        if got != want:
            failures.append(f"relative_excess{args} -> {got}, want {want}")
    for value, pool, want in RANK_CASES:
        got = rank_of(value, pool)
        if got != want:
            failures.append(f"rank_of({value}, {pool}) -> {got}, want {want}")
    for args, want in RANK_CHANGE_CASES:
        got = rank_change(*args)
        if got != want:
            failures.append(f"rank_change{args} -> {got}, want {want}")
    for args, want in SIGN_AGREEMENT_CASES:
        got = sign_agreement(*args)
        if got != want:
            failures.append(f"sign_agreement{args} -> {got}, want {want}")
    for args, want in ROBUST_MIN_CASES:
        got = robustness_min(*args)
        if got != want:
            failures.append(f"robustness_min{args} -> {got}, want {want}")
    for spec, want in WINDOW_PARSE_CASES:
        got = parse_window(spec)
        if got != want:
            failures.append(f"parse_window({spec!r}) -> {got}, want {want}")
    for bad in ("nope", "a:b:c:d", "name:notadate:2023-12-31"):
        try:
            parse_window(bad)
        except argparse.ArgumentTypeError:
            continue
        failures.append(f"parse_window({bad!r}) did not raise")

    total = (
        len(RELATIVE_EXCESS_CASES)
        + len(RANK_CASES)
        + len(RANK_CHANGE_CASES)
        + len(SIGN_AGREEMENT_CASES)
        + len(ROBUST_MIN_CASES)
        + len(WINDOW_PARSE_CASES)
        + 3
    )
    print(f"TOY CASES: {len(failures)} failure(s) of {total}")
    for f in failures:
        print(f"  FAIL {f}")
    if failures:
        return False
    print("  ALL PASSED -- comparators verified, proceeding to real data")
    return True


# =============================================================================
# STEP 1 -- per-scanner standalone, one window at a time
# =============================================================================
def row_scanners_all(r: dict) -> set[str]:
    """Every scanner that flagged this symbol on this date (selective + broad)."""
    out: set[str] = set()
    for key in ("early_scanners", "late_scanners", "ambiguous_scanners"):
        out |= set(json.loads(r[key] or "[]"))
    out |= set(json.loads(r["broad_scanners"] or "[]"))
    return out


def mean_excess(rows: list[dict]) -> float | None:
    vals = [r["excess_return_pct"] for r in rows if r["excess_return_pct"] is not None]
    return sum(vals) / len(vals) if vals else None


def window_rows(name, since, until, use_costs) -> tuple[list[dict], list[str]]:
    """Forward rows for ONE window. Windows are never combined."""
    paths = bc.discover_snapshots()
    dated = bc.filter_snapshots(paths, since, until)
    if not dated:
        raise SystemExit(
            f"Window {name} ({since}..{until}) matched 0 of {len(paths)} "
            f"snapshot(s). Refusing to report an empty window."
        )
    dates = [d for d, _ in dated]
    rows: list[dict] = []
    for _, p in dated:
        with open(p, encoding="utf-8") as fh:
            snap = json.load(fh)
        events = bc.collect_events(snap)
        fwd, _unresolved = bc.build_forward_rows(
            events, snap["as_on_date"], HORIZONS, use_costs
        )
        rows.extend(fwd)
    return rows, dates


def scanner_stats(rows: list[dict], horizon: int) -> dict:
    """Per-scanner n / mean excess / win%-vs-NSEI for one window+horizon."""
    pool = [r for r in rows if r["horizon"] == horizon]
    union_mean = mean_excess(pool)
    by_scanner: dict[str, list[dict]] = defaultdict(list)
    for r in pool:
        for s in row_scanners_all(r):
            by_scanner[s].append(r)

    out = {}
    for s, rs in by_scanner.items():
        exc = [r["excess_return_pct"] for r in rs if r["excess_return_pct"] is not None]
        n = len(rs)
        out[s] = {
            "n": n,
            "n_exc": len(exc),
            "mean_excess": sum(exc) / len(exc) if exc else None,
            "win_vs_bench": 100.0 * sum(1 for x in exc if x > 0) / len(exc)
            if exc
            else None,
            "relative": relative_excess(
                (sum(exc) / len(exc)) if exc else None, union_mean
            ),
            "thin": n < THIN_N,
        }
    return {"union_n": len(pool), "union_mean": union_mean, "scanners": out}


def print_step1(stats: dict, horizon: int) -> None:
    u = stats["union_mean"]
    tag = "  THIN" if stats["union_n"] < THIN_N else ""
    print(
        f"\n  All scanner-flagged symbols: n={stats['union_n']}, mean excess "
        f"{bc.fmt(u)}{tag}"
    )
    print(f"  {'scanner':<24}{'n':>7}{'mean exc':>11}{'win%vsNSEI':>12}{'rel exc':>10}")
    for s, d in sorted(
        stats["scanners"].items(), key=lambda kv: -(kv[1]["mean_excess"] or -1e9)
    ):
        thin = "  THIN" if d["thin"] else ""
        print(
            f"  {s:<24}{d['n']:>7}{bc.fmt(d['mean_excess']):>11}"
            f"{bc.fmt(d['win_vs_bench']):>12}{bc.fmt(d['relative']):>10}{thin}"
        )


def print_step2(by_window: dict, names: list[str]) -> None:
    print("\n" + "=" * 110)
    print("STEP 2 -- REGIME ROBUSTNESS at h60 / h120 (the best-powered horizons)")
    print("=" * 110)
    (wa, wb) = names
    for h in ROBUST_HORIZONS:
        sa, sb = by_window[wa][h], by_window[wb][h]
        pool_a = [d["mean_excess"] for d in sa["scanners"].values()]
        pool_b = [d["mean_excess"] for d in sb["scanners"].values()]
        print(f"\n  --- horizon {h}d ---")
        print(
            f"  regime tide ({wa} union mean): {bc.fmt(sa['union_mean'])}   "
            f"({wb}): {bc.fmt(sb['union_mean'])}"
        )
        print(
            f"  {'scanner':<24}{'excA':>8}{'excB':>8}{'relA':>8}{'relB':>8}"
            f"{'rkA':>5}{'rkB':>5}{'chg':>5}  {'signs':<9}{'both+':<6}"
        )
        recs = []
        for s in sorted(sa["scanners"]):
            da = sa["scanners"][s]
            db = sb["scanners"].get(s)
            if db is None:
                continue
            ra = rank_of(da["mean_excess"], pool_a)
            rb = rank_of(db["mean_excess"], pool_b)
            both = (da["relative"] or 0) > 0 and (db["relative"] or 0) > 0
            recs.append(
                {
                    "s": s,
                    "da": da,
                    "db": db,
                    "ra": ra,
                    "rb": rb,
                    "chg": rank_change(ra, rb),
                    "signs": sign_agreement(da["mean_excess"], db["mean_excess"]),
                    "both": both,
                    "robust": robustness_min(da["relative"], db["relative"]),
                }
            )
        for r in sorted(
            recs, key=lambda x: -(x["robust"] if x["robust"] is not None else -1e9)
        ):
            thin = " THIN" if r["da"]["thin"] or r["db"]["thin"] else ""
            print(
                f"  {r['s']:<24}{bc.fmt(r['da']['mean_excess']):>8}"
                f"{bc.fmt(r['db']['mean_excess']):>8}"
                f"{bc.fmt(r['da']['relative']):>8}{bc.fmt(r['db']['relative']):>8}"
                f"{r['ra'] or '-':>5}{r['rb'] or '-':>5}{r['chg'] if r['chg'] is not None else '-':>5}"
                f"  {r['signs']:<9}{'YES' if r['both'] else '-':<6}{thin}"
            )
        n_robust = sum(1 for r in recs if r["both"])
        print(
            f"  robust (relative excess > 0 in BOTH windows): {n_robust} of {len(recs)}"
        )
        agree = sum(1 for r in recs if r["signs"] == "agree+") + sum(
            1 for r in recs if r["signs"] == "agree-"
        )
        print(f"  same-sign across windows: {agree} of {len(recs)}")
    print(
        "\n  NOTE: rank within a window is identical whether computed on mean excess\n"
        "  or on relative excess -- relative_excess subtracts a per-window constant,\n"
        "  so it shifts every scanner equally and cannot reorder them."
    )


# =============================================================================
# STEP 3 -- same-date random-symbol control for the top 3
# =============================================================================
def random_control(
    rows: list[dict], horizon: int, scanners: list[str], seed: int
) -> dict:
    """Draw matched-count random symbols from each date's own union.

    Holds constant 'was flagged on this date' and varies only 'which name', so
    a scanner's edge cannot be a property of the favourable slice of the union
    it happened to pick from.
    """
    pool: dict[str, dict[str, float]] = defaultdict(dict)
    for r in rows:
        if r["horizon"] == horizon and r["excess_return_pct"] is not None:
            pool[r["scan_date"]][r["symbol"]] = r["excess_return_pct"]
    flagged: dict[tuple, set[str]] = defaultdict(set)
    for r in rows:
        if r["horizon"] == horizon:
            for s in row_scanners_all(r):
                flagged[(r["scan_date"], s)].add(r["symbol"])

    rng = random.Random(seed)
    out = {}
    for s in sorted(scanners):
        actual, control, used = [], [], 0
        for d in sorted(pool):
            universe = sorted(pool[d])
            picks = sorted(flagged.get((d, s), ()))
            vals = [pool[d][p] for p in picks if p in pool[d]]
            if not vals:
                continue
            actual.append((len(vals), sum(vals) / len(vals)))
            n_draw = min(len(vals), len(universe))
            if n_draw:
                draw = rng.sample(universe, n_draw)
                dv = [pool[d][x] for x in draw]
                control.append((len(dv), sum(dv) / len(dv)))
            used += 1
        n_a = sum(n for n, _ in actual)
        n_c = sum(n for n, _ in control)
        ma = sum(n * m for n, m in actual) / n_a if n_a else None
        mc = sum(n * m for n, m in control) / n_c if n_c else None
        out[s] = {
            "dates": used,
            "n_actual": n_a,
            "n_control": n_c,
            "mean_actual": ma,
            "mean_control": mc,
            "delta": (ma - mc) if (ma is not None and mc is not None) else None,
        }
    return out


def print_step3(ctrl: dict, by_window: dict, names: list[str]) -> None:
    print("\n" + "=" * 110)
    print(
        f"STEP 3 -- SAME-DATE RANDOM-SYMBOL CONTROL (seed={CONTROL_SEED}, "
        "matched count per date, run per window)"
    )
    print("=" * 110)
    for name in names:
        print(f"\n  ### window {name}")
        for h in ROBUST_HORIZONS:
            st = by_window[name][h]
            print(f"  --- horizon {h}d ---")
            print(
                f"  {'scanner':<24}{'n act':>7}{'n ctrl':>7}{'actual':>9}"
                f"{'random':>9}{'delta':>9}{'share':>8}  verdict"
            )
            for s, d in ctrl[name][h].items():
                if d["delta"] is None:
                    verdict = "n/a (no resolvable draw)"
                elif d["delta"] > 0:
                    verdict = "beats random from same date"
                elif d["delta"] < 0:
                    verdict = "LOSES to random from same date"
                else:
                    verdict = "ties random"
                union_n = st["union_n"]
                share = 100.0 * d["n_actual"] / union_n if union_n else 0.0
                if share >= 50.0:
                    # The draw comes from a pool that is mostly this scanner's
                    # own picks, so a small delta is expected by construction and
                    # the test has little power to reject.
                    verdict += "  [SELF-DOMINATED -- control near-tautological]"
                print(
                    f"  {s:<24}{d['n_actual']:>7}{d['n_control']:>7}"
                    f"{bc.fmt(d['mean_actual']):>9}{bc.fmt(d['mean_control']):>9}"
                    f"{bc.fmt(d['delta']):>9}{share:>7.0f}%  {verdict}"
                )
    print(
        "\n  A scanner that loses to its own same-date random draw is selecting\n"
        "  names, not timing: its forward return was a property of which slice of\n"
        "  the flagged universe it reached for, not of the flag itself.\n"
        "  'share' is the scanner's n as a % of that window's union. Above ~50%\n"
        "  the random draw is mostly the scanner's own picks back, so a small\n"
        "  delta is arithmetic rather than evidence."
    )


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--no-costs", action="store_true", help="skip cost adjustment")
    p.add_argument(
        "--min-n",
        type=int,
        default=THIN_N,
        help=f"n floor for STEP 3 top-3 selection (default {THIN_N}). Scanners "
        "below this in EITHER window are excluded from the top 3 and listed "
        "separately rather than ranked on 1-2 observations.",
    )
    p.add_argument(
        "--window",
        action="append",
        type=parse_window,
        metavar="NAME:SINCE:UNTIL",
        help="repeatable; each window is analysed separately and never pooled",
    )
    args = p.parse_args(argv)

    # BEFORE any snapshot is opened or any real comparator output is produced.
    if not run_selftest():
        print("\nSelf-test failed. Aborting before reading real data.")
        return 1

    windows = args.window or DEFAULT_WINDOWS
    names = [w[0] for w in windows]
    use_costs = not args.no_costs
    print(f"\nCosts: {'ON' if use_costs else 'OFF'}  |  THIN threshold: n < {THIN_N}")
    print(f"Windows (never pooled): {names}")

    by_window: dict[str, dict] = {}
    print("\n" + "=" * 110)
    print(
        "STEP 1 -- PER-SCANNER STANDALONE, PER WINDOW (all flagged symbols, not sole-only)"
    )
    print("=" * 110)
    for name, since, until in windows:
        rows, dates = window_rows(name, since, until, use_costs)
        stats = {h: scanner_stats(rows, h) for h in HORIZONS}
        by_window[name] = stats
        print(f"\n### window {name}  ({since}..{until})  dates={dates}")
        for h in HORIZONS:
            print_step1(stats[h], h)
        # keep rows for STEP 3
        by_window[name]["_rows"] = rows

    if len(names) != 2:
        print(f"\nSTEP 2/3 need exactly 2 windows to compare; got {len(names)}.")
        return 1

    print_step2(by_window, names)

    # Top 3 by the WORSE window's relative excess at h60, with an n floor.
    # The floor is not cosmetic: ranked purely on relative excess, Recovery
    # Ladder takes #1 on n=2 in the bull window and n=0 at h120 in the weak
    # one, where its same-date draw was a single symbol returning +99.5% and
    # its "beats random" delta swung from +29.9% to -89.7%. A weekly pick
    # cannot rest on 1-2 observations, so THIN buckets are excluded from the
    # top-3 selection and reported separately instead.
    wa, wb = names
    h0 = ROBUST_HORIZONS[0]
    robust, gated_out = {}, []
    for s in by_window[wa][h0]["scanners"]:
        if s not in by_window[wb][h0]["scanners"]:
            continue
        n_a = by_window[wa][h0]["scanners"][s]["n"]
        n_b = by_window[wb][h0]["scanners"][s]["n"]
        rel_a = by_window[wa][h0]["scanners"][s]["relative"]
        rel_b = by_window[wb][h0]["scanners"][s]["relative"]
        if n_a < args.min_n or n_b < args.min_n:
            gated_out.append((s, n_a, n_b, robustness_min(rel_a, rel_b)))
            continue
        robust[s] = robustness_min(rel_a, rel_b)
    ranked = sorted(
        (s for s, v in robust.items() if v is not None), key=lambda s: -robust[s]
    )
    top3 = ranked[:3]
    print(
        f"\nTop 3 by worse-window relative excess at h{h0} (n >= {args.min_n} in BOTH windows): {top3}"
    )
    for s, n_a, n_b, r in sorted(gated_out, key=lambda x: -(x[3] or -1e9)):
        print(
            f"  EXCLUDED as THIN despite scoring: {s} "
            f"(n {n_a} / {n_b}, worse-window rel {bc.fmt(r)})"
        )
    if not top3:
        print("  none -- every scanner lacks a relative excess in both windows")
        return 1

    # Control is run per window: "random from this date's own union" only means
    # something against the union that date actually offered.
    ctrl = {
        name: {
            h: random_control(by_window[name]["_rows"], h, top3, CONTROL_SEED)
            for h in ROBUST_HORIZONS
        }
        for name in names
    }
    print_step3(ctrl, by_window, names)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
