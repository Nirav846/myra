#!/usr/bin/env python
"""Operational tool: reconcile and inspect the NSE trading calendar.

The repair itself lives in :mod:`myra_app.nse_calendar`; this script is the
operator-facing entry point so it is auditable, bounded and repeatable rather
than a hidden side effect of an ingest run.

    # What would change (read-only):
    python tools/calendar_reconcile.py --dry-run

    # Apply the repair to heuristic "likely holiday" records only:
    python tools/calendar_reconcile.py --apply

    # Show the authoritative verdict for specific dates:
    python tools/calendar_reconcile.py --date 2026-10-13 --date 2026-10-02

Safety properties, all enforced in code:
  * only records whose ``holiday_name`` is exactly the zero-row heuristic are
    ever touched - curated/named holidays and genuine closures are left alone;
  * nothing is deleted, and the UPDATE re-checks ``holiday_name`` in its WHERE
    clause so a record repaired since the scan cannot be clobbered;
  * dates the official publication cannot verify are reported and skipped, never
    guessed;
  * ``--dry-run`` is the default.
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from myra_app import nse_calendar as nc  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--apply",
        action="store_true",
        help="write the reconciliation (default is a dry run)",
    )
    ap.add_argument("--dry-run", action="store_true", help="report only (the default)")
    ap.add_argument("--date", action="append", help="classify a date (repeatable)")
    args = ap.parse_args()

    meta = nc.load_official_meta()
    if meta:
        print("official calendar source :", nc.find_official_meta_path())
        print("  year                   :", meta.get("year"))
        print("  data-version           :", meta.get("data_version"))
        print("  official holidays      :", len(meta.get("holidays") or {}))
        print("  special sessions       :", len(meta.get("special_sessions") or []))
        print("  special sessions as of :", meta.get("special_sessions_last_update"))
    else:
        print(
            "official calendar source : UNAVAILABLE - no verdict can be made; "
            "nothing will be changed"
        )

    if args.date:
        print("\ndate classification:")
        for raw in args.date:
            from datetime import datetime

            try:
                day = datetime.strptime(raw, "%Y-%m-%d").date()
            except ValueError:
                print(f"  {raw}: unparseable (use YYYY-MM-DD)")
                continue
            v = nc.classify(day)
            weekday = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")[day.weekday()]
            print(
                f"  {raw} {weekday}  state={v['state']:<9} "
                f"verified={str(v['verified']):<5} {v['reason']}"
            )

    print("\nheuristic records to reconcile:")
    print(f"  total: {len(nc.heuristic_records())}")
    summary = nc.reconcile_heuristic_records(dry_run=not args.apply)
    unclassified = summary.pop("unclassified", [])
    preview = summary.pop("updates_preview", [])
    print(json.dumps(summary, indent=2, default=str))
    if preview:
        print("  first changes:")
        for item in preview:
            print(
                f"    {item['date']} -> is_trading_day={item['is_trading_day']} "
                f"{item['holiday_name']}"
            )
    if unclassified:
        print(
            f"  {len(unclassified)} record(s) could not be verified and were "
            "left untouched:"
        )
        for item in unclassified[:10]:
            print(f"    {item['date']}: {item['why']}")
        if len(unclassified) > 10:
            print(f"    ... and {len(unclassified) - 10} more")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
