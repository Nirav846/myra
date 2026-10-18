"""F2 recovery: the recomputation rule for the degenerate rolling columns.

Phase 20 proved the defect: ``eod2_sync`` filtered to the rows newer than the
per-symbol cutoff *before* computing derived columns, so a normal one-session
sync took the ``len(df) < 2`` branch and stored the raw session values in place
of every rolling column. Twelve sessions in 2026-08/09/10 carry that shape, and
no later ingestion repaired them.

Phase 23's preflight pinned the rule this module implements. Two producers write
these columns with *different* frames, so the rule had to be validated rather
than assumed:

``eod2_sync._compute_derived``
    rolling means over the symbol's history tail (252 rows) plus the new row,
    ``min_periods=1``.

``feature_enrichment``
    ``rolling_*`` over a frame loaded with ``WHERE date >= date(latest,
    '-365 days')``, i.e. ~250 sessions, and it never writes ``delivery_ma_60``
    at all.

Validated against the newest clean reference session for each field:

===========  ==================  ==============================  ==========
Column       Reference session   Rule                           Result
===========  ==================  ==============================  ==========
``sma_50``   2026-10-16          50-session rolling mean         150/150
``sma_200``  2026-10-16          200-session rolling mean        143/150
``high_52w`` 2026-04-07          252-session rolling max         130/130*
``low_52w``  2026-08-04          252-session rolling min         124/124*
===========  ==================  ==============================  ==========

(*) excluding rows whose stored value is NULL.

The session-count rule reproduces every non-NULL stored value with **zero**
false matches, and beats the 365-calendar-day alternative (which missed 7-8 per
150). The seven ``sma_200`` exceptions are symbols with fewer than 200 sessions
inside the enrichment frame but more in full history, where the two frames
diverge by construction.

``delivery_ma_60`` is deliberately **excluded**: no session anywhere in the
table has a non-degenerate value for it, so no rule can be validated against
stored output. See ``docs/PHASE23_F2_RECOVERY_PREFLIGHT.md``.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field as _field
from typing import Iterable, Sequence

import pandas as pd

#: The sessions carrying the degenerate shape, in chronological order.
TARGET_SESSIONS: tuple[str, ...] = (
    "2026-08-17",
    "2026-08-20",
    "2026-08-24",
    "2026-08-25",
    "2026-08-27",
    "2026-08-31",
    "2026-09-08",
    "2026-09-11",
    "2026-09-21",
    "2026-09-28",
    "2026-10-07",
    "2026-10-13",
)

#: Columns this module is allowed to recompute. Anything not listed here is out
#: of scope by construction, so a future caller cannot widen the write scope by
#: accident.
RECOVERABLE_COLUMNS: tuple[str, ...] = (
    "sma_50",
    "sma_200",
    "high_52w",
    "low_52w",
)

#: Known-degenerate, deliberately not recovered (see module docstring).
EXCLUDED_COLUMNS: tuple[str, ...] = ("delivery_ma_60",)

#: Rolling windows, in trading sessions.
WINDOWS = {"sma_50": 50, "sma_200": 200, "high_52w": 252, "low_52w": 252}

#: Rolling aggregator per column.
AGGREGATORS = {"sma_50": "mean", "sma_200": "mean", "high_52w": "max", "low_52w": "min"}

#: Raw columns the rule reads. The wrong derived values on the target sessions
#: therefore cannot feed their own repair.
SOURCE_COLUMNS = ("date", "high", "low", "close")

#: How far back the recomputation must see. The longest window is 252 sessions,
#: roughly 365 calendar days; a generous margin covers holiday-heavy calendars.
HISTORY_LOOKBACK_CALENDAR_DAYS = 550


@dataclass(frozen=True)
class TargetRow:
    """One (symbol, date) the recovery would rewrite."""

    symbol: str
    date: str
    before: dict[str, float | None]
    after: dict[str, float | None]

    @property
    def changed(self) -> tuple[str, ...]:
        cols = []
        for name in RECOVERABLE_COLUMNS:
            b, a = self.before.get(name), self.after.get(name)
            if b is None and a is None:
                continue
            if b is None or a is None:
                if a is not None:
                    cols.append(name)
                continue
            if abs(b - a) > 1e-9:
                cols.append(name)
        return tuple(cols)


@dataclass
class Manifest:
    """Everything needed to review, re-derive and later detect drift."""

    sessions: tuple[str, ...]
    rows: list[TargetRow] = _field(default_factory=list)
    notes: list[str] = _field(default_factory=list)

    @property
    def target_hash(self) -> str:
        """SHA-256 over the ordered ``symbol|date`` keys.

        Recomputed immediately before an approved execution: if it differs, the
        live target set moved and the operator must re-review.
        """
        blob = "\n".join(f"{r.symbol}|{r.date}" for r in self.rows)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def summary(self) -> dict:
        changed_per_col = {c: 0 for c in RECOVERABLE_COLUMNS}
        null_to_value = {c: 0 for c in RECOVERABLE_COLUMNS}
        for r in self.rows:
            for c in r.changed:
                changed_per_col[c] += 1
                if r.before.get(c) is None:
                    null_to_value[c] += 1
        per_session = {}
        for r in self.rows:
            per_session[r.date] = per_session.get(r.date, 0) + 1
        return {
            "sessions": list(self.sessions),
            "total_rows": len(self.rows),
            "rows_per_session": dict(sorted(per_session.items())),
            "would_change_per_column": changed_per_col,
            "null_to_value_per_column": null_to_value,
            "would_change_total": sum(changed_per_col.values()),
            "excluded_columns": list(EXCLUDED_COLUMNS),
            "target_sha256": self.target_hash,
            "notes": list(self.notes),
        }


def recompute_frame(history: pd.DataFrame) -> pd.DataFrame:
    """Apply the validated rule to one symbol's history.

    ``history`` must carry :data:`SOURCE_COLUMNS`, one row per session, for a
    single symbol. Returns a frame indexed like ``history`` with the recoverable
    columns added. Pure: no database, no I/O.
    """
    missing = [c for c in SOURCE_COLUMNS if c not in history.columns]
    if missing:
        raise ValueError(f"history is missing required columns: {missing}")
    if history.empty:
        return history.copy()

    df = history.copy()
    # ``errors="coerce"`` + an explicit format: an unparseable date becomes NaT
    # and is caught below rather than silently dropped.
    parsed = pd.to_datetime(df["date"], format="%Y-%m-%d", errors="coerce")
    df["date"] = parsed.dt.normalize()
    if df["date"].isna().any():
        raise ValueError("history contains an unparseable date")
    df = (
        df.dropna(subset=["date"])
        .drop_duplicates(subset=["date"], keep="last")
        .sort_values("date")
        .reset_index(drop=True)
    )
    for col in RECOVERABLE_COLUMNS:
        window, how = WINDOWS[col], AGGREGATORS[col]
        rolling = df["close" if how == "mean" else ("high" if how == "max" else "low")]
        df[col] = getattr(rolling.rolling(window, min_periods=1), how)()
    return df


def build_manifest(
    history_by_symbol: dict[str, pd.DataFrame],
    stored_by_key: dict[tuple[str, str], dict[str, float | None]],
    sessions: Sequence[str] = TARGET_SESSIONS,
) -> Manifest:
    """Assemble the manifest from fetched history plus the stored values.

    Split from the fetching so the rule is unit-testable without a database, and
    so a caller can supply its own bounded history query.
    """
    manifest = Manifest(sessions=tuple(sessions))
    wanted = set(sessions)
    for symbol in sorted(history_by_symbol):
        history = history_by_symbol[symbol]
        if history.empty:
            continue
        recomputed = recompute_frame(history)
        recomputed = recomputed[recomputed["date"].dt.strftime("%Y-%m-%d").isin(wanted)]
        for row in recomputed.itertuples(index=False):
            iso = row.date.date().isoformat()
            key = (symbol, iso)
            before = dict(stored_by_key.get(key, {}))
            after = {c: getattr(row, c) for c in RECOVERABLE_COLUMNS}
            manifest.rows.append(
                TargetRow(symbol=symbol, date=iso, before=before, after=after)
            )
    manifest.rows.sort(key=lambda r: (r.date, r.symbol))
    return manifest


def merge_plan(manifest: Manifest) -> list[tuple]:
    """The exact ``(symbol, date, sma_50, sma_200, high_52w, low_52w)`` writes.

    Only rows whose value actually changes are included, so a rerun produces an
    empty plan and the operation is idempotent by construction.
    """
    plan = []
    for r in manifest.rows:
        if not r.changed:
            continue
        plan.append(
            (
                r.symbol,
                r.date,
                r.after["sma_50"],
                r.after["sma_200"],
                r.after["high_52w"],
                r.after["low_52w"],
            )
        )
    return plan


def apply_plan(conn, plan: Iterable[tuple]) -> int:
    """Execute the plan. **Callers must own authorisation and a backup.**

    Deliberately not wired to any CLI in this phase: Phase 23 is recovery
    preparation, and shipping a production-write path without owner approval
    would be the wrong default. Single transaction - an interrupted run rolls
    back rather than leaving a half-recovered session.
    """
    rows = list(plan)
    if not rows:
        return 0
    cur = conn.cursor()
    cur.execute("BEGIN IMMEDIATE")
    try:
        cur.executemany(
            "UPDATE technical_data SET sma_50 = ?, sma_200 = ?, "
            "high_52w = ?, low_52w = ? WHERE symbol = ? AND date = ?",
            [
                (sma50, sma200, hi52, lo52, sym, dte)
                for sym, dte, sma50, sma200, hi52, lo52 in rows
            ],
        )
        written = (
            cur.rowcount if cur.rowcount is not None and cur.rowcount > 0 else len(rows)
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return written
