"""
Confluence Breadth Calibration
==============================
Decides which confluence scanners are "broad" (flag so much of the symbol
universe that their agreement carries little information) versus "selective".

Reads only the calibration snapshot JSONs written by a multi-date
run_confluence_batch() sweep — it deliberately does NOT import any live
scanner code, so it can be run against archived snapshots at any time.

For every calibration date it computes each scanner's coverage as a share of
that date's symbol union, then reports mean / min / max and how many dates
each scanner would classify as broad at the given threshold. Scanners whose
broad/selective verdict is not stable across dates are called out explicitly:
those need a human judgement call, not an automatic one.

Usage:
    # Default threshold from myra_web.utils.BROAD_THRESHOLD equivalent (40.0)
    python tools/calibrate_confluence_breadth.py

    # Try a different threshold
    python tools/calibrate_confluence_breadth.py --threshold 30

    # Machine-readable output
    python tools/calibrate_confluence_breadth.py --json

    # Point at a different snapshot directory
    python tools/calibrate_confluence_breadth.py --cal-dir models/calibration
"""

import argparse
import glob
import json
import os
import statistics
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_CAL_DIR = os.path.join(REPO_ROOT, "models", "calibration")
DEFAULT_THRESHOLD = 40.0


def load_snapshots(cal_dir: str) -> list[dict]:
    """Load every confluence_*.json in cal_dir, oldest date first."""
    snaps = []
    for path in sorted(glob.glob(os.path.join(cal_dir, "confluence_*.json"))):
        try:
            with open(path, encoding="utf-8") as fh:
                snap = json.load(fh)
        except Exception as e:
            print(
                f"  ! skipping unreadable {os.path.basename(path)}: {e}",
                file=sys.stderr,
            )
            continue
        if "scanners" not in snap:
            print(
                f"  ! skipping {os.path.basename(path)}: no 'scanners' key",
                file=sys.stderr,
            )
            continue
        snap["_path"] = path
        snaps.append(snap)
    snaps.sort(key=lambda s: s.get("as_on_date") or "")
    return snaps


def breadth_for_snapshot(snap: dict) -> tuple[dict[str, float], int, dict[str, str]]:
    """Return ({scanner: pct_of_universe}, union_size, {scanner: error}).

    pct_of_universe = distinct candidate symbols / total distinct symbols
    flagged by any scanner in that snapshot. Scanners that errored contribute
    0 candidates, so they land at 0.0%.
    """
    per_scanner: dict[str, set] = {}
    errors: dict[str, str] = {}
    union: set = set()
    for name, entry in (snap.get("scanners") or {}).items():
        entry = entry or {}
        if entry.get("error"):
            errors[name] = entry["error"]
        syms = {
            c.get("symbol")
            for c in (entry.get("candidates") or [])
            if isinstance(c, dict) and c.get("symbol")
        }
        per_scanner[name] = syms
        union |= syms
    n = len(union)
    if n == 0:
        return {name: 0.0 for name in per_scanner}, 0, errors
    return (
        {name: round(100.0 * len(s) / n, 1) for name, s in per_scanner.items()},
        n,
        errors,
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1].strip())
    ap.add_argument("--cal-dir", default=DEFAULT_CAL_DIR, help="snapshot directory")
    ap.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    ap.add_argument("--json", action="store_true", help="emit JSON instead of a table")
    args = ap.parse_args()

    snaps = load_snapshots(args.cal_dir)
    if not snaps:
        print(f"No calibration snapshots found in {args.cal_dir}", file=sys.stderr)
        print(
            "Generate them first with run_confluence_batch(as_on_date=..., out_path=...)",
            file=sys.stderr,
        )
        return 1

    dates = [s.get("as_on_date") or "?" for s in snaps]
    per_date: list[dict[str, float]] = []
    unions: list[int] = []
    all_errors: dict[str, list] = {}
    for snap in snaps:
        pct, union_n, errs = breadth_for_snapshot(snap)
        per_date.append(pct)
        unions.append(union_n)
        for name, msg in errs.items():
            all_errors.setdefault(name, []).append(snap.get("as_on_date"))

    names = sorted({n for d in per_date for n in d})
    stats: dict[str, dict] = {}
    for name in names:
        vals = [d.get(name) for d in per_date if d.get(name) is not None]
        if not vals:
            continue
        n_broad = sum(1 for v in vals if v > args.threshold)
        stats[name] = {
            "values": vals,
            "mean": round(statistics.fmean(vals), 1),
            "min": min(vals),
            "max": max(vals),
            "n_dates": len(vals),
            "n_broad": n_broad,
            "pct_dates_broad": round(100.0 * n_broad / len(vals), 1),
            "stable_broad": n_broad == len(vals),
            "stable_selective": n_broad == 0,
            "errors": all_errors.get(name, []),
        }

    if args.json:
        print(
            json.dumps(
                {
                    "threshold": args.threshold,
                    "dates": dates,
                    "unions": unions,
                    "scanners": stats,
                },
                indent=2,
            )
        )
        return 0

    print("=" * 100)
    print("CONFLUENCE BREADTH CALIBRATION")
    print("=" * 100)
    print(f"threshold      : {args.threshold}% of union")
    print(f"dates          : {len(dates)}  ({dates[0]} .. {dates[-1]})")
    print(
        f"union size     : min {min(unions)}  max {max(unions)}  mean {statistics.fmean(unions):.0f}"
    )
    print()

    # per-date matrix
    hdr = (
        f"{'scanner':<24}"
        + "".join(f"{d[2:]:>9}" for d in dates)
        + f"{'mean':>8}{'min':>7}{'max':>7}{'broad':>8}"
    )
    print(hdr)
    print("-" * len(hdr))
    for name in sorted(stats, key=lambda n: -stats[n]["mean"]):
        s = stats[name]
        row = f"{name:<24}"
        for d in per_date:
            v = d.get(name)
            row += f"{v:>9.1f}" if v is not None else f"{'--':>9}"
        row += f"{s['mean']:>8.1f}{s['min']:>7.1f}{s['max']:>7.1f}{s['n_broad']:>5}/{s['n_dates']:<3}"
        print(row)
    print()

    consistent_broad = sorted(n for n, s in stats.items() if s["stable_broad"])
    consistent_sel = sorted(n for n, s in stats.items() if s["stable_selective"])
    unstable = sorted(
        n
        for n, s in stats.items()
        if not s["stable_broad"] and not s["stable_selective"]
    )

    def names_at(sort_key):
        return ", ".join(sorted(stats, key=lambda n: -stats[n][sort_key])) or "none"

    print("-" * 100)
    print(
        f"CONSISTENTLY BROAD ({len(consistent_broad)}/{len(stats)}) — same verdict on every date:"
    )
    for n in consistent_broad:
        print(
            f"    {n:<24} mean {stats[n]['mean']:>5.1f}%  range {stats[n]['min']:.1f}-{stats[n]['max']:.1f}%"
        )
    print()
    print(f"CONSISTENTLY SELECTIVE ({len(consistent_sel)}/{len(stats)}):")
    for n in consistent_sel:
        print(
            f"    {n:<24} mean {stats[n]['mean']:>5.1f}%  range {stats[n]['min']:.1f}-{stats[n]['max']:.1f}%"
        )
    print()
    if unstable:
        print(
            f"!! UNSTABLE — verdict flips across dates, needs a judgement call ({len(unstable)}):"
        )
        for n in unstable:
            s = stats[n]
            print(
                f"    {n:<24} broad on {s['n_broad']}/{s['n_dates']} dates"
                f"  mean {s['mean']:.1f}%  range {s['min']:.1f}-{s['max']:.1f}%"
            )
    else:
        print("UNSTABLE: none — every scanner keeps the same verdict on every date.")
    print()

    errored = {n: s["errors"] for n, s in stats.items() if s["errors"]}
    if errored:
        print("-" * 100)
        print(
            "SCANNERS THAT ERRORED ON AT LEAST ONE DATE (counted as 0% — treat with care):"
        )
        for n, ds in errored.items():
            print(f"    {n:<24} failed on {len(ds)} date(s): {', '.join(ds)}")
        print()

    # threshold sensitivity across the observed range
    print("-" * 100)
    print("THRESHOLD SENSITIVITY (which scanners would be broad):")
    observed = sorted({v for s in stats.values() for v in s["values"]})
    for t in sorted({round(x) for x in observed} | {int(args.threshold)}):
        b = sorted(n for n, s in stats.items() if s["mean"] > t)
        marker = "  <-- current" if t == args.threshold else ""
        print(
            f"    mean-based threshold {t:>3}%  -> {len(b):>2} broad: {', '.join(b) or 'none'}{marker}"
        )
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
