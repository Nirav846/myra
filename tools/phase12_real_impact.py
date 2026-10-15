"""Phase 12 — bounded, read-only decision-impact measurement on real data.

Answers "how many rows would change, and why" without touching the database and
without scanning the 1.9 GB technical store: only the `fundamentals` sidecar is
read, and only the columns the quality score actually uses.
"""

from __future__ import annotations

import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from myra_app import constants  # noqa: E402
from myra_app.librarian_core import LibrarianCore  # noqa: E402
from myra_web.routes.fund_traction import quality_score_sql  # noqa: E402

OLD = """
ROUND(
    COALESCE(f.roe, 0) * 0.4
    + COALESCE(f.net_margin, 0) * 0.3
    + (100 - MIN(COALESCE(f.pe, 100), 100)) * 0.3, 2
)
"""


def main() -> int:
    path = os.path.join(constants.DB_DIR, LibrarianCore.DB_MAP["valuation"])
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=60)
    new = quality_score_sql("f")

    total = conn.execute(
        "SELECT COUNT(*) FROM fundamentals WHERE market_cap > 0"
    ).fetchone()[0]

    # Ranking change: compare ordering by the two expressions.
    rows = conn.execute(f"""
        SELECT f.symbol, ({OLD}) AS old_q, ({new}) AS new_q
        FROM fundamentals f
        WHERE f.market_cap > 0
        ORDER BY old_q DESC
        """).fetchall()

    changed = sum(1 for _, o, n in rows if o != n)
    became_unavailable = sum(1 for _, o, n in rows if o is not None and n is None)
    neg_pe = conn.execute(
        "SELECT COUNT(*) FROM fundamentals WHERE market_cap > 0 AND (pe IS NULL OR pe <= 0)"
    ).fetchone()[0]

    old_top = [r[0] for r in rows[:10]]
    new_top = [
        r[0] for r in sorted(rows, key=lambda r: (r[2] is None, -(r[2] or 0)))[:10]
    ]

    print(f"rows scored (market_cap > 0)      : {total}")
    print(f"rows whose score changed          : {changed} ({100.0*changed/total:.1f}%)")
    print(f"rows now 'unavailable' (NULL)     : {became_unavailable}")
    print(f"old top-10 by quality             : {', '.join(old_top)}")
    print(f"new top-10 by quality             : {', '.join(new_top)}")
    overlap = len(set(old_top) & set(new_top))
    print(f"top-10 overlap                    : {overlap}/10")

    print("\nLargest absolute movements:")
    moves = sorted(rows, key=lambda r: -abs((r[2] or 0) - (r[1] or 0)))[:8]
    for sym, o, n in moves:
        print(f"   {sym:14} old={o!s:>12}  new={n!s:>10}")

    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
