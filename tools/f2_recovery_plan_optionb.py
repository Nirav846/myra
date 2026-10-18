"""Phase 25 - generate the Option B (three-column) recovery plan. READ-ONLY.

Produces a self-contained manifest for recovering ONLY ``sma_50``, ``high_52w``
and ``low_52w`` across the 12 degenerate sessions. ``sma_200`` and
``delivery_ma_60`` are recorded as deferred and can never enter the write set.

This tool has no code path that opens a writable connection. Applying the plan
is a separate, owner-authorised step (``recovery_f2.apply_write_plan``).

Usage:
    python tools/f2_recovery_plan_optionb.py                # summary
    python tools/f2_recovery_plan_optionb.py --json         # full payload
    python tools/f2_recovery_plan_optionb.py --sql-out F    # proposed SQL (not run)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import myra_app.constants as K  # noqa: E402
from myra_app.librarian_core import LibrarianCore  # noqa: E402
from myra_app.recovery_f2 import (  # noqa: E402
    DEFERRED_COLUMNS,
    HISTORY_LOOKBACK_CALENDAR_DAYS,
    OPTION_B_COLUMNS,
    TARGET_SESSIONS,
    build_write_plan,
)

READ_ONLY = True  # never opens a writable connection

DB = os.path.join(K.DB_DIR, LibrarianCore.DB_MAP["technical"])
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _code_fingerprint() -> str:
    """SHA-256 of the module that defines the calculation and the scope.

    Editing ``recovery_f2`` invalidates every generated plan, which is the
    intent: an approved hash must not survive a change to the maths.
    """
    path = os.path.join(REPO, "myra_app", "recovery_f2.py")
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def _scope_digest(conn, sessions) -> str:
    """Deterministic digest over exactly the three columns' current contents."""
    ph = ",".join("?" * len(sessions))
    rows = conn.execute(
        f"SELECT symbol, date, sma_50, high_52w, low_52w FROM technical_data "
        f"WHERE date IN ({ph}) ORDER BY symbol, date",
        tuple(sessions),
    ).fetchall()
    h = hashlib.sha256()
    for r in rows:
        h.update(("|".join("" if v is None else repr(v) for v in r) + "\n").encode())
    return h.hexdigest()


def _db_identity(conn) -> dict:
    cols = {r[1]: r[2] for r in conn.execute("PRAGMA table_info(technical_data)")}
    return {
        "filename": LibrarianCore.DB_MAP["technical"],
        "table": "technical_data",
        "journal_mode": conn.execute("PRAGMA journal_mode").fetchone()[0],
        "column_types": {
            c: cols.get(c)
            for c in ("sma_50", "high_52w", "low_52w", "sma_200", "delivery_ma_60")
        },
        "row_count": conn.execute("SELECT COUNT(*) FROM technical_data").fetchone()[0],
        "integrity_check": conn.execute("PRAGMA quick_check").fetchone()[0],
    }


def collect(conn, sessions):
    """Bounded history window plus the stored values for the target rows."""
    start = (
        (
            pd.Timestamp(min(sessions))
            - pd.Timedelta(days=HISTORY_LOOKBACK_CALENDAR_DAYS)
        )
        .date()
        .isoformat()
    )
    last = max(sessions)

    symbols = [
        r[0]
        for r in conn.execute(
            "SELECT DISTINCT symbol FROM technical_data WHERE date BETWEEN ? AND ? "
            "ORDER BY symbol",
            (min(sessions), last),
        )
    ]
    ph = ",".join("?" * len(sessions))
    stored_rows = conn.execute(
        f"SELECT symbol, date, sma_50, high_52w, low_52w FROM technical_data "
        f"WHERE date IN ({ph})",
        tuple(sessions),
    ).fetchall()
    stored = {
        (r[0], r[1]): {"sma_50": r[2], "high_52w": r[3], "low_52w": r[4]}
        for r in stored_rows
    }

    history_by_symbol = {}
    chunk = 400
    for i in range(0, len(symbols), chunk):
        part = symbols[i : i + chunk]
        p2 = ",".join("?" * len(part))
        df = pd.read_sql_query(
            f"SELECT symbol, date, high, low, close FROM technical_data "
            f"WHERE symbol IN ({p2}) AND date BETWEEN ? AND ? ORDER BY symbol, date",
            conn,
            params=(*part, start, last),
        )
        if df.empty:
            continue
        for sym, grp in df.groupby("symbol"):
            history_by_symbol[sym] = grp
    return history_by_symbol, stored, symbols


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=DB)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--sql-out", default=None)
    ap.add_argument("--limit-symbols", type=int, default=None)
    args = ap.parse_args(argv)

    conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    try:
        history, stored, symbols = collect(conn, TARGET_SESSIONS)
        if args.limit_symbols:
            keep = sorted(history)[: args.limit_symbols]
            history = {k: history[k] for k in keep}

        plan = build_write_plan(
            db_path=args.db,
            db_identity=_db_identity(conn),
            sessions=TARGET_SESSIONS,
            history_by_symbol=history,
            stored_by_key=stored,
            baseline_scope_digest=_scope_digest(conn, TARGET_SESSIONS),
            symbol_count=len(history),
            code_sha256=_code_fingerprint(),
        )
        payload = dict(plan.payload)
        payload["content_sha256"] = plan.content_hash
        payload["read_only_generated"] = True
        payload["symbols_in_scope"] = len(plan.rows)
        payload["sql_out"] = args.sql_out

        if args.sql_out:
            cols = ", ".join(plan.columns)
            # Write the header first so the file is self-describing even if the
            # body is streamed afterwards.
            with open(args.sql_out, "w", encoding="utf-8") as fh:
                fh.write("-- Option B recovery plan (PROPOSED - NOT EXECUTED)\n")
                fh.write("-- content sha256: %s\n" % plan.content_hash)
                fh.write(
                    "-- columns: %s   (sma_200 / delivery_ma_60 excluded)\n" % cols
                )
                fh.write("BEGIN IMMEDIATE;\n")
            for r in plan.rows:
                assignments = ", ".join(
                    f"{c} = {v!r}" for c, v in zip(plan.columns, r[2:])
                )
                with open(args.sql_out, "a", encoding="utf-8") as fh:
                    fh.write(
                        f"UPDATE technical_data SET {assignments} "
                        f"WHERE symbol={r[0]!r} AND date={r[1]!r};\n"
                    )
            with open(args.sql_out, "a", encoding="utf-8") as fh:
                fh.write("COMMIT;\n")

        if args.json:
            print(json.dumps(payload, indent=2))
        else:
            print("OPTION B WRITE PLAN (three columns) - read-only generation")
            print("=" * 66)
            print("db                : %s" % payload["db_path"])
            print("integrity_check   : %s" % payload["db_identity"]["integrity_check"])
            print("sessions          : %d" % payload["session_count"])
            print("symbols in scope  : %d" % payload["symbols_in_scope"])
            print("write columns     : %s" % ", ".join(plan.columns))
            print("deferred columns  : %s" % ", ".join(DEFERRED_COLUMNS))
            print("target rows       : %d" % payload["row_count"])
            print(
                "cell changes      : %d (value->value %d, null->value %d)"
                % (
                    payload["cell_changes"],
                    payload["value_to_value"],
                    payload["null_to_value"],
                )
            )
            print("baseline digest   : %s" % payload["baseline_scope_digest"])
            print("code sha256       : %s" % payload["code_sha256"][:32])
            print("CONTENT SHA256    : %s" % plan.content_hash)
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
