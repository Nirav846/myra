"""Generate confluence calibration snapshots for historical dates.

The 2025-09..2026-09 calibration set was originally produced by a throwaway
script in TEMP, which made the snapshots unreproducible. This is the durable
equivalent, so the analysis in ``backtest_confluence.py`` can be regenerated
from a clean checkout.

Idempotent: a date whose snapshot already exists is skipped, so re-running to
fill gaps is safe and never double-writes or clobbers the live snapshot.

Usage:
    python tools/calibrate_confluence.py --start 2023-01-05 --end 2023-12-29 --count 8
    python tools/calibrate_confluence.py --dates 2023-06-15 2023-12-29
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from myra_app.constants import DB_DIR  # noqa: E402
from myra_web.confluence_batch import (
    CONFLUENCE_SCANNERS,
    run_confluence_batch,
)  # noqa: E402

CAL_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "models",
    "calibration",
)
TECH_DB = os.path.join(DB_DIR, "myra_technical.db")


def trading_days() -> list[str]:
    conn = sqlite3.connect(TECH_DB)
    try:
        rows = conn.execute(
            "SELECT DISTINCT date FROM technical_data WHERE date >= '2015-01-01' ORDER BY date"
        ).fetchall()
    finally:
        conn.close()
    return [r[0] for r in rows]


def pick_dates(days: list[str], start: str, end: str, count: int) -> list[str]:
    """Evenly spaced trading days in [start, end].

    Even spacing matters: clustered dates would silently re-weight any
    multi-date average and make the window look narrower than it is.
    """
    window = [d for d in days if start <= d <= end]
    if not window:
        raise SystemExit(
            f"No trading days between {start} and {end} in {TECH_DB}. "
            f"Available range: {days[0]} .. {days[-1]}"
        )
    if count >= len(window):
        return window
    if count == 1:
        return [window[len(window) // 2]]
    step = (len(window) - 1) / (count - 1)
    idx = sorted({round(i * step) for i in range(count)})
    return [window[i] for i in idx]


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--start", help="first trading day to consider (YYYY-MM-DD)")
    p.add_argument("--end", help="last trading day to consider (YYYY-MM-DD)")
    p.add_argument(
        "--count", type=int, default=8, help="how many dates to space evenly"
    )
    p.add_argument(
        "--dates",
        nargs="+",
        help="explicit dates, overrides --start/--end/--count",
    )
    p.add_argument(
        "--out-dir", default=CAL_DIR, help="destination directory for snapshots"
    )
    p.add_argument(
        "--force", action="store_true", help="regenerate dates that already exist"
    )
    args = p.parse_args(argv)

    if args.dates:
        targets = list(args.dates)
    else:
        if not args.start or not args.end:
            raise SystemExit("Provide --dates, or both --start and --end.")
        targets = pick_dates(trading_days(), args.start, args.end, args.count)

    os.makedirs(args.out_dir, exist_ok=True)
    print(f"CONFLUENCE_SCANNERS: {len(CONFLUENCE_SCANNERS)} registered")
    print(f"Target window: {targets[0]} .. {targets[-1]}  ({len(targets)} dates)")
    print(f"Output: {args.out_dir}\n")

    results = []
    for i, d in enumerate(targets, 1):
        out_path = os.path.join(args.out_dir, f"confluence_{d.replace('-', '_')}.json")
        if os.path.exists(out_path) and not args.force:
            print(f"[{i}/{len(targets)}] {d}: exists, skipping (use --force to redo)")
            results.append((d, "skipped", 0, 0))
            continue
        t0 = time.time()
        try:
            summary = run_confluence_batch(as_on_date=d, out_path=out_path)
        except Exception as exc:  # noqa: BLE001
            # One bad date must not abort the window; record and continue.
            print(f"[{i}/{len(targets)}] {d}: FAILED {type(exc).__name__}: {exc}")
            results.append((d, "error", 0, 0))
            continue
        per = summary.get("scanners", summary)
        ok = sum(
            1 for v in per.values() if isinstance(v, dict) and v.get("status") == "ok"
        )
        bad = [
            f"{k}={v.get('error') or v.get('status')}"
            for k, v in per.items()
            if not (isinstance(v, dict) and v.get("status") == "ok")
        ]
        print(
            f"[{i}/{len(targets)}] {d}: {ok}/{len(CONFLUENCE_SCANNERS)} ok "
            f"in {time.time() - t0:.0f}s" + (f"  FAILED: {bad}" if bad else "")
        )
        results.append(
            (d, "ok" if not bad else "partial", ok, len(CONFLUENCE_SCANNERS))
        )

    full = [r for r in results if r[1] == "ok"]
    print(f"\n{len(full)}/{len(targets)} dates fully clean")
    return 0 if len(full) == len(targets) else 1


if __name__ == "__main__":
    raise SystemExit(main())
