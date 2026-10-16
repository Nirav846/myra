"""Shadow-compare the v2.5 ordering with and without live ranking inputs.

Phase 6 connects real ``avg_volume_20d`` / ``avg_delivery_20d`` / ``roe`` /
``ProfitGrowth`` values to the v2.5 scorer but keeps the current ordering
authoritative. This tool runs the comparison against a saved scan snapshot so
the operator can see what *would* change before deciding to opt in.

It never writes to the databases and never changes production ordering — the
opt-in switch (``MYRA_V25_RANKING_INPUTS=1``) is what activates the populated
ordering inside a real scan.

Phase 14 adds a second, explicitly-requested comparison mode that answers a
different question: what changes if the growth input alone is populated from
its *validated* source (``myra_app.growth_signals``, annual Upstox YoY) instead
of staying unavailable? It is opt-in via ``--growth-source`` and is **not** run
by the default invocation, which must not fetch or activate growth implicitly.

Usage::

    python tools/compare_v25_orderings.py --strategy 31
    python tools/compare_v25_orderings.py --snapshot myra_reports/scan_31.csv --json out.json
    python tools/compare_v25_orderings.py --snapshot x.csv --growth-source upstox-annual

Every database connection this tool opens is read-only (``mode=ro``), including
the growth source; it cannot write to a production database.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

import pandas as pd  # noqa: E402

from myra_app import growth_signals  # noqa: E402
from myra_app.ranking_inputs import (  # noqa: E402
    compute_fundamental_inputs,
    compute_ranking_inputs,
)
from myra_app.shadow_ranking import MATERIAL_MOVE, compare_orderings  # noqa: E402

#: Result-row fields the v2.5 scorer only receives when the Phase 6 opt-in flag
#: is on (plus the pre-Phase-12 placeholder columns). A *live* scan with the
#: deployed default leaves all of these absent, so removing them is what makes
#: the growth-mode baseline reproduce the production ordering instead of an
#: artefact of whichever fields the saved snapshot happens to carry.
LIVE_ABSENT_FIELDS = (
    "roe",
    "ProfitGrowth",
    "SalesGrowth",
    "avg_volume_20d",
    "avg_delivery_20d",
    "smart_money_score",
    "_rank_roe",
    "_rank_growth",
    "_rank_delivery",
    "_rank_volume",
    "_rank_sm_score",
    "MYRA_Score_v25",
)

#: Technical fields a scan must actually have populated for the comparison to
#: say anything about relative strength. A snapshot where these are blank is
#: reporting ties, not a ranking.
REQUIRED_TECHNICAL_FIELDS = ("close", "sma50", "sma150", "sma200")


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


def _is_number(value):
    """True when ``value`` is a usable number rather than None/NaN/blank."""
    if value is None:
        return False
    try:
        return not pd.isna(value)
    except (TypeError, ValueError):
        return False


def _populated_rows(rows):
    """Rows whose technical block is actually populated.

    A snapshot where ``close`` / ``sma50`` / ``sma150`` / ``sma200`` are blank
    ranks ties, not relative strength, so such rows are excluded from the
    comparison and counted separately in the report.
    """
    return [
        row
        for row in rows
        if all(_is_number(row.get(field)) for field in REQUIRED_TECHNICAL_FIELDS)
    ]


def _live_baseline_frame(rows):
    """Reduce rows to the fields a *live* default scan would actually supply.

    Returns a new list; the caller's rows are never mutated.
    """
    frame = []
    for row in rows:
        new = dict(row)
        for field in LIVE_ABSENT_FIELDS:
            new.pop(field, None)
        new["Stock"] = str(new.get("Stock", "")).split(".")[0].upper()
        frame.append(new)
    return frame


def _snapshot_age_days(path):
    if not os.path.exists(path):
        return None
    modified = datetime.fromtimestamp(os.path.getmtime(path), tz=timezone.utc)
    return (datetime.now(tz=timezone.utc) - modified).total_seconds() / 86400.0


def _regime_of(rows):
    """The market-mood string, defaulting to ``NEUTRAL`` when unusable."""
    value = rows[0].get("Market_Mood", "NEUTRAL") if rows else "NEUTRAL"
    if not isinstance(value, str) or not value.strip():
        return "NEUTRAL"
    return value


def _growth_shadow(snapshot, rows, regime, top_n, policy):
    """Compare the live-default ordering against one with validated growth.

    Both orderings are computed from the **same** frame inside
    :func:`~myra_app.shadow_ranking.compare_orderings`, so the only difference
    between them is the growth input.
    """
    frame = _live_baseline_frame(_populated_rows(rows))
    symbols = {row["Stock"] for row in frame}

    conn = _ro_connect(growth_signals.SOURCE_DB_KEY)
    try:
        records = growth_signals.read_annual_records(conn)
    finally:
        conn.close()

    observations = growth_signals.derive_growth(records, policy=policy)
    coverage = growth_signals.coverage_report(observations)

    # "Eligible in the source" and "supplied to this comparison" are different
    # numbers: a symbol can have a valid observation yet sit outside the
    # snapshot's universe. Both are reported rather than collapsed into one.
    supplied = {
        symbol: value
        for symbol, value in growth_signals.growth_inputs(observations).items()
        if symbol in symbols
    }

    return {
        "snapshot": snapshot,
        "snapshot_age_days": _snapshot_age_days(snapshot),
        "snapshot_rows": len(rows),
        "frame_rows": len(frame),
        "source_coverage": coverage,
        "frame_symbols": len(symbols),
        "frame_eligible": len(supplied),
        "baseline": compare_orderings(frame, regime, {}, top_n=top_n),
        "candidate": compare_orderings(frame, regime, supplied, top_n=top_n),
    }


def _print_growth_report(report):
    coverage = report["source_coverage"]
    reasons = coverage["reasons"]
    age = report["snapshot_age_days"]
    print("=" * 72)
    print("Phase 14 growth shadow: validated annual YoY vs growth-absent baseline")
    print("=" * 72)
    print("PRELIMINARY - a saved, stale snapshot; NOT a live-representative result.")
    print(f"snapshot               : {os.path.basename(report['snapshot'])}")
    print(
        f"snapshot age           : "
        f"{'unknown' if age is None else f'{age:.0f} days'}"
    )
    print(
        f"snapshot rows          : {report['snapshot_rows']} "
        f"(usable technical block: {report['frame_rows']})"
    )
    print(
        f"source eligible        : {coverage['eligible']} / {coverage['symbols']}"
        " symbols with annual data"
    )
    print(
        f"excluded               : "
        + ", ".join(
            f"{key}={value}"
            for key, value in reasons.items()
            if key != "eligible" and value
        )
    )
    print(
        f"frame symbols          : {report['frame_symbols']} "
        f"(of which with a valid growth observation: {report['frame_eligible']})"
    )
    for label in ("baseline", "candidate"):
        item = report[label]
        print("-" * 72)
        print(f"[{label}] {item['n']} rows scored")
        print(f"  growth coverage      : {item['coverage']['ProfitGrowth']}")
        print(f"  spearman             : {item['spearman']}")
        print(f"  identical ordering   : {item['identical_ordering']}")
        print(
            f"  top-{item['top_n']} overlap    : {item['top_n_overlap']}"
            f"  (jaccard {item['top_n_jaccard']})"
        )
        print(
            f"  moved / max delta    : {item['moved']} / "
            f"{item['max_position_delta']}"
        )
    print("-" * 72)
    candidate = report["candidate"]
    print(f"entered top-{candidate['top_n']}  : {candidate['symbols_added']}")
    print(f"left top-{candidate['top_n']}     : {candidate['symbols_dropped']}")
    print(f"representative moves (|delta| >= {MATERIAL_MOVE}):")
    for move in candidate["material_moves"][:15]:
        print(
            f"  {move['symbol']:<12} {move['base_position']:>4} -> "
            f"{move['new_position']:>4} ({move['position_delta']:+d})  "
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
    parser.add_argument(
        "--growth-source",
        choices=("none", "upstox-annual"),
        default="none",
        help=(
            "Optional candidate signal for the shadow comparison. 'none' "
            "(default) keeps the Phase 6 behaviour and never reads growth data; "
            "'upstox-annual' adds validated annual YoY growth read-only from "
            "upstox_income_statement."
        ),
    )
    parser.add_argument(
        "--growth-floor",
        type=float,
        default=growth_signals.EligibilityPolicy.min_prior_base_cr,
        help="Minimum prior-period net profit (Rs crore) for a growth base",
    )
    parser.add_argument(
        "--growth-freshness-years",
        type=int,
        default=growth_signals.EligibilityPolicy().freshness_years,
        help="How many fiscal years behind the newest annual period is tolerated",
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

    if args.growth_source != "none":
        policy = growth_signals.EligibilityPolicy(
            min_prior_base_cr=args.growth_floor,
            freshness_years=args.growth_freshness_years,
        )
        report = _growth_shadow(snapshot, rows, _regime_of(rows), args.top_n, policy)
        _print_growth_report(report)
        if args.json_out:
            with open(args.json_out, "w", encoding="utf-8") as handle:
                json.dump(report, handle, indent=2, default=str)
            print(f"\nreport written to {args.json_out}")
        return 0

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
