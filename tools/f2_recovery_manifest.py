"""F2 recovery preflight - generate the execution manifest. READ-ONLY.

Answers, without writing anything:
  * the exact target set (sessions, rows, per-session counts);
  * a SHA-256 over the ordered symbol|date keys, so a later approved execution
    can prove the live target set has not drifted;
  * how many rows each column would change, and how many NULL -> value;
  * the exact UPDATE statements that *would* run.

This tool has no code path that writes to any database. Applying the plan is a
separate, owner-authorised step (see the Phase 23 report).

Usage:
    python tools/f2_recovery_manifest.py                 # human-readable
    python tools/f2_recovery_manifest.py --json          # machine-readable
    python tools/f2_recovery_manifest.py --sql-out FILE   # emit the UPDATE plan
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from myra_app.constants import DB_DIR  # noqa: E402
from myra_app.recovery_f2 import (  # noqa: E402
    EXCLUDED_COLUMNS,
    HISTORY_LOOKBACK_CALENDAR_DAYS,
    RECOVERABLE_COLUMNS,
    TARGET_SESSIONS,
    build_manifest,
    merge_plan,
)

READ_ONLY = True  # this module never opens a writable connection


def _history_start(sessions) -> str:
    earliest = pd.Timestamp(min(sessions))
    start = earliest - pd.Timedelta(days=HISTORY_LOOKBACK_CALENDAR_DAYS)
    return start.date().isoformat()


def collect(conn, sessions):
    """Fetch a bounded history window plus the stored derived values."""
    start = _history_start(sessions)
    last = max(sessions)

    symbols = [
        r[0]
        for r in conn.execute(
            "SELECT DISTINCT symbol FROM technical_data WHERE date BETWEEN ? AND ? "
            "ORDER BY symbol",
            (min(sessions), last),
        )
    ]
    placeholders = ",".join("?" * len(sessions))
    stored_rows = conn.execute(
        f"SELECT symbol, date, sma_50, sma_200, high_52w, low_52w FROM technical_data "
        f"WHERE date IN ({placeholders})",
        tuple(sessions),
    ).fetchall()
    stored = {
        (r[0], r[1]): {
            "sma_50": r[2],
            "sma_200": r[3],
            "high_52w": r[4],
            "low_52w": r[5],
        }
        for r in stored_rows
    }

    history_by_symbol = {}
    chunk = 400
    for i in range(0, len(symbols), chunk):
        part = symbols[i : i + chunk]
        ph = ",".join("?" * len(part))
        df = pd.read_sql_query(
            f"SELECT symbol, date, high, low, close FROM technical_data "
            f"WHERE symbol IN ({ph}) AND date BETWEEN ? AND ? ORDER BY symbol, date",
            conn,
            params=(*part, start, last),
        )
        if df.empty:
            continue
        for sym, grp in df.groupby("symbol"):
            history_by_symbol[sym] = grp
    return history_by_symbol, stored


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--db", default=None, help="technical database (default: the live one)"
    )
    ap.add_argument("--json", action="store_true", help="emit JSON")
    ap.add_argument(
        "--sql-out", default=None, help="write the proposed UPDATE plan here"
    )
    ap.add_argument(
        "--limit-symbols",
        type=int,
        default=None,
        help="restrict to N symbols (sampling)",
    )
    args = ap.parse_args(argv)

    db_path = args.db or os.path.join(DB_DIR, "myra_technical.db")
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        history, stored = collect(conn, TARGET_SESSIONS)
        if args.limit_symbols:
            keep = sorted(history)[: args.limit_symbols]
            history = {k: history[k] for k in keep}
        manifest = build_manifest(history, stored)
        summary = manifest.summary()
        summary["symbols_considered"] = len(history)
        summary["read_only"] = True
        plan = merge_plan(manifest)
        summary["planned_statements"] = len(plan)

        if args.sql_out:
            with open(args.sql_out, "w", encoding="utf-8") as fh:
                fh.write("-- F2 recovery plan (PROPOSED - not executed)\n")
                fh.write("-- target sha256: %s\n" % manifest.target_hash)
                fh.write("BEGIN IMMEDIATE;\n")
                for sym, dte, s50, s200, hi, lo in plan:
                    fh.write(
                        "UPDATE technical_data SET sma_50=%r, sma_200=%r, high_52w=%r, "
                        "low_52w=%r WHERE symbol=%r AND date=%r;\n"
                        % (s50, s200, hi, lo, sym, dte)
                    )
                fh.write("COMMIT;\n")
            summary["sql_out"] = args.sql_out

        if args.json:
            print(json.dumps(summary, indent=2))
        else:
            print("F2 RECOVERY MANIFEST (read-only preflight)")
            print("=" * 64)
            print("sessions            : %d" % len(summary["sessions"]))
            print("symbols considered  : %d" % summary["symbols_considered"])
            print("target rows         : %d" % summary["total_rows"])
            print("would change total  : %d" % summary["would_change_total"])
            print("excluded columns    : %s" % ", ".join(EXCLUDED_COLUMNS))
            print("target sha256       : %s" % summary["target_sha256"])
            print("\nper column:")
            for c in RECOVERABLE_COLUMNS:
                print(
                    "  %-10s change=%-6d null->value=%d"
                    % (
                        c,
                        summary["would_change_per_column"][c],
                        summary["null_to_value_per_column"][c],
                    )
                )
            print("\nrows per session:")
            for d, n in summary["rows_per_session"].items():
                print("  %s : %d" % (d, n))
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
