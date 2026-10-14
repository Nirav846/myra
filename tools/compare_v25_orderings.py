"""Shadow-compare the v2.5 ordering with and without live ranking inputs.

Phase 6 connects real ``avg_volume_20d`` / ``avg_delivery_20d`` / ``roe`` /
``ProfitGrowth`` values to the v2.5 scorer but keeps the current ordering
authoritative. This tool runs the comparison against a saved scan snapshot so
the operator can see what *would* change before deciding to opt in.

It never writes to the databases and never changes production ordering — the
opt-in switch (``MYRA_V25_RANKING_INPUTS=1``) is what activates the populated
ordering inside a real scan.

Usage::

    python tools/compare_v25_orderings.py --strategy 31
    python tools/compare_v25_orderings.py --snapshot myra_reports/scan_31.csv --json out.json
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

import pandas as pd  # noqa: E402

from myra_app.ranking_inputs import (  # noqa: E402
    compute_fundamental_inputs,
    compute_ranking_inputs,
)
from myra_app.shadow_ranking import MATERIAL_MOVE, compare_orderings  # noqa: E402


def _load_rows(snapshot: str):
    df = pd.read_csv(snapshot)
    return df.to_dict("records")


def _default_snapshot(strategy: str) -> str:
    return os.path.join(PROJECT_ROOT, "myra_reports", f"scan_{strategy}.csv")


def _ro_connect(db_filename):
    """Open a MYRA database strictly read-only (no WAL sidecar, no writes)."""
    from myra_app.constants import DB_DIR
    from myra_app.librarian_core import LibrarianCore

    path = os.path.join(DB_DIR, LibrarianCore.DB_MAP[db_filename])
    uri = "file:{}?mode=ro".format(path.replace("\\", "/"))
    return sqlite3.connect(uri, uri=True)


def _gather_inputs(symbols, as_of_date=None):
    tech_conn = _ro_connect("technical")
    val_conn = _ro_connect("valuation")
    try:
        inputs = compute_ranking_inputs(tech_conn, symbols, as_of_date=as_of_date)
        fundamentals = compute_fundamental_inputs(val_conn, symbols)
    finally:
        tech_conn.close()
        val_conn.close()

    merged = {}
    for symbol in set(inputs) | set(fundamentals):
        merged[symbol] = {**inputs.get(symbol, {}), **fundamentals.get(symbol, {})}
    return merged


def _print_report(report):
    print("=" * 68)
    print("MYRA v2.5 shadow ranking comparison (current vs populated inputs)")
    print("=" * 68)
    print(f"candidates             : {report['n']}")
    spearman = report["spearman"]
    print(
        f"spearman (score ranks) : {'n/a' if spearman is None else f'{spearman:.4f}'}"
    )
    print(f"identical ordering     : {report['identical_ordering']}")
    print(
        f"top-{report['top_n']} overlap      : "
        f"{report['top_n_overlap']}/{report['top_n']}"
        f"  (jaccard={report['top_n_jaccard']})"
    )
    print(
        f"moved / max delta      : {report['moved']} / {report['max_position_delta']}"
    )
    coverage = report["coverage"]
    print(
        "input coverage         : " + ", ".join(f"{k}={v}" for k, v in coverage.items())
    )
    if report["symbols_added"]:
        print(
            f"entered top-{report['top_n']}     : {', '.join(report['symbols_added'])}"
        )
    if report["symbols_dropped"]:
        print(
            f"left top-{report['top_n']}        : {', '.join(report['symbols_dropped'])}"
        )
    moves = report["material_moves"]
    if moves:
        print(f"\nmaterial moves (|delta| >= {MATERIAL_MOVE}, top 25 shown):")
        for move in moves[:25]:
            print(
                f"  {move['symbol']:<14} {move['base_position']:>3} -> "
                f"{move['new_position']:>3}  ({move['position_delta']:+d})  "
                f"score {move['base_score']} -> {move['new_score']}"
            )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--strategy", default="31", help="Strategy id whose snapshot to load"
    )
    parser.add_argument("--snapshot", default="", help="Explicit snapshot CSV path")
    parser.add_argument(
        "--as-of", default="", help="YYYY-MM-DD cutoff for the 20-session windows"
    )
    parser.add_argument("--top-n", type=int, default=20)
    parser.add_argument(
        "--json", dest="json_out", default="", help="Write the report as JSON"
    )
    args = parser.parse_args(argv)

    snapshot = args.snapshot or _default_snapshot(args.strategy)
    if not os.path.exists(snapshot):
        print(f"[!] Snapshot not found: {snapshot}")
        return 1

    rows = _load_rows(snapshot)
    if not rows:
        print(f"[!] Snapshot empty: {snapshot}")
        return 1

    symbols = [str(r.get("Stock", "")).split(".")[0].upper() for r in rows]
    inputs = _gather_inputs(symbols, as_of_date=args.as_of or None)
    regime = rows[0].get("Market_Mood", "NEUTRAL")

    report = compare_orderings(rows, regime, inputs, top_n=args.top_n)
    _print_report(report)

    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2, default=str)
        print(f"\nreport written to {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
