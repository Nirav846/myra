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
import json
import os
from dataclasses import dataclass, field as _field
from typing import Iterable, Sequence

import pandas as pd

#: Bumped whenever the write scope or the payload contract changes, so an older
#: manifest can never authorise a newer operation.
TOOL_VERSION = "phase25-optionB-v1"

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

#: Weekend-dated sessions that are **declared NSE live trading sessions** and
#: must never be treated as spurious.
#:
#: eod2 records these in ``eod2/src/eod2_data/meta.json`` under
#: ``special_sessions``, populated from NSE circulars whose subject contains
#: "live trading session" (``eod2/src/defs/defs.py`` ``check_special_sessions``).
#: 2026-02-01 is a Sunday in this set: NSE ran a live session on it, so the row
#: is legitimate data, not a calendar artefact.
#:
#: Phases 20 and 21 recorded 2026-02-01 as a "phantom session" requiring the
#: source maintainer to resolve. Phase 24 established that label was wrong.
DECLARED_SPECIAL_SESSIONS: tuple[str, ...] = ("2026-02-01",)

#: Weekend-dated sessions with no declared provenance. These remain unexplained
#: and sit inside 252-row rolling windows, so removing them would change
#: already-stored rolling values. Never delete them as part of a recovery; the
#: question belongs to the eod2 source owner.
UNEXPLAINED_WEEKEND_SESSIONS: tuple[str, ...] = (
    "2025-02-01",
    "2024-05-18",
    "2024-03-02",
    "2024-01-20",
    "2023-11-12",
    "2016-10-30",
    "2015-02-28",
    "2014-03-22",
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

    Deliberately not wired to any CLI in this phase: Phase 23 was recovery
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


# ─────────────────────────────────────────────────────────────────────────────
# Phase 25 — the Option B write plan (strictly three columns)
# ─────────────────────────────────────────────────────────────────────────────

#: The only columns an Option B write may touch.
OPTION_B_COLUMNS: tuple[str, ...] = ("sma_50", "high_52w", "low_52w")

#: Columns that must remain byte-identical. ``sma_200`` is deferred (Phase 24
#: §D), ``delivery_ma_60`` has no trustworthy producer (Phase 23 §3).
DEFERRED_COLUMNS: tuple[str, ...] = ("sma_200", "delivery_ma_60")

#: Columns the plan reads (never writes).
READ_ONLY_COLUMNS: tuple[str, ...] = (
    "symbol",
    "date",
    "close",
    "high",
    "low",
    "delivery",
    "volume",
    "delivery_pct",
)

#: The calculation fingerprint. Every value in the write set is a function of
#: this, so changing any entry must change the manifest hash.
CALCULATION_RULE: dict = {
    "sma_50": {"agg": "mean", "source": "close", "window_rows": 50, "min_periods": 1},
    "high_52w": {"agg": "max", "source": "high", "window_rows": 252, "min_periods": 1},
    "low_52w": {"agg": "min", "source": "low", "window_rows": 252, "min_periods": 1},
}

#: Ordering, de-duplication and price-basis requirements the plan assumes.
EXECUTION_CONTRACT: dict = {
    "ordering": "ascending by date per symbol",
    "duplicate_dates": "collapse keep=last",
    "warm_up": "min_periods=1 (validated Phase 23 contract; NOT a new rule)",
    "window_unit": "trading sessions (row count), never calendar days",
    "price_basis": (
        "corporate-action adjusted, back-adjusted (technical_data via eod2); "
        "the unadjusted OHLCV cache is never read"
    ),
    "null_handling": "raw OHLC NULL propagates; no value is fabricated or defaulted",
}


class ScopeError(RuntimeError):
    """Raised when a plan's write set does not match its declared scope."""


@dataclass(frozen=True)
class WritePlan:
    """An Option B recovery plan: the payload plus the exact rows it writes.

    ``content_hash`` covers the whole payload *including* every target value, so
    altering a symbol, a date, a value, a column, the calculation fingerprint or
    any scope metadata changes it. The previous four-column Phase 23 hash can
    never authorise this operation: it is a different hash over a different
    write set.
    """

    payload: dict
    rows: tuple[tuple, ...]

    @property
    def columns(self) -> tuple[str, ...]:
        return tuple(self.payload["write_columns"])

    @property
    def sessions(self) -> tuple[str, ...]:
        return tuple(self.payload["sessions"])

    @property
    def content_hash(self) -> str:
        """SHA-256 over the payload **and** every target row.

        The rows must be included: a hash over the payload alone would be blind
        to a changed symbol, date or value, which is precisely the drift the
        plan exists to detect.
        """
        blob = json.dumps(
            self.payload, sort_keys=True, separators=(",", ":"), default=str
        )
        h = hashlib.sha256(blob.encode("utf-8"))
        for row in self.rows:
            h.update(
                ("\n" + "|".join("" if v is None else repr(v) for v in row)).encode()
            )
        return h.hexdigest()

    def assert_scope(self) -> None:
        """Refuse anything that is not exactly the three-column Option B scope."""
        if set(self.columns) != set(OPTION_B_COLUMNS):
            raise ScopeError(
                f"write columns {sorted(self.columns)} do not match the approved "
                f"Option B scope {sorted(OPTION_B_COLUMNS)}"
            )
        forbidden = set(self.columns) & set(DEFERRED_COLUMNS)
        if forbidden:
            raise ScopeError(
                f"deferred columns present in the write set: {sorted(forbidden)}"
            )
        if len(self.columns) != len(OPTION_B_COLUMNS):
            raise ScopeError("duplicate entries in the write column list")
        for row in self.rows:
            if len(row) != len(self.columns) + 2:
                raise ScopeError(
                    f"row arity {len(row)} does not match "
                    f"{len(self.columns)} values + symbol + date"
                )


def build_write_plan(
    *,
    db_path: str,
    db_identity: dict,
    sessions: Sequence[str],
    history_by_symbol: dict[str, pd.DataFrame],
    stored_by_key: dict[tuple[str, str], dict],
    baseline_scope_digest: str,
    symbol_count: int,
    code_sha256: str,
    version: str = TOOL_VERSION,
) -> WritePlan:
    """Assemble an Option B plan from fetched history plus stored values.

    Pure: no database handle, no I/O. The caller supplies the read-only data.
    """
    sessions = tuple(sorted(sessions))
    if not sessions:
        raise ScopeError("a plan needs at least one target session")

    wanted = set(sessions)
    rows: list[tuple] = []
    per_session: dict[str, int] = {d: 0 for d in sessions}
    value_to_value = 0
    null_to_value = 0
    unchanged = 0

    for symbol in sorted(history_by_symbol):
        history = history_by_symbol[symbol]
        if history is None or history.empty:
            continue
        recomputed = recompute_frame(history)
        iso = recomputed["date"].dt.strftime("%Y-%m-%d")
        for pos in recomputed.index[iso.isin(wanted)]:
            rec = recomputed.loc[pos]
            dte = rec["date"].date().isoformat()
            before = dict(stored_by_key.get((symbol, dte), {}))
            after = {c: float(rec[c]) for c in OPTION_B_COLUMNS}
            changed = []
            for c in OPTION_B_COLUMNS:
                b = before.get(c)
                if b is None and after[c] is not None:
                    null_to_value += 1
                    changed.append(c)
                elif b is not None and abs(b - after[c]) > 1e-9:
                    value_to_value += 1
                    changed.append(c)
                elif b is None and after[c] is None:
                    pass
                else:
                    unchanged += 1
            if changed:
                rows.append((symbol, dte) + tuple(after[c] for c in OPTION_B_COLUMNS))
                per_session[dte] = per_session.get(dte, 0) + 1

    rows.sort(key=lambda r: (r[1], r[0]))
    payload = {
        "tool_version": version,
        "option": "B",
        "db_path": os.path.abspath(db_path),
        "db_identity": db_identity,
        "sessions": list(sessions),
        "session_count": len(sessions),
        "symbol_count": symbol_count,
        "write_columns": list(OPTION_B_COLUMNS),
        "deferred_columns": list(DEFERRED_COLUMNS),
        "read_only_columns": list(READ_ONLY_COLUMNS),
        "calculation_rule": CALCULATION_RULE,
        "execution_contract": EXECUTION_CONTRACT,
        "code_sha256": code_sha256,
        "baseline_scope_digest": baseline_scope_digest,
        "row_count": len(rows),
        "cell_changes": value_to_value + null_to_value,
        "value_to_value": value_to_value,
        "null_to_value": null_to_value,
        "unchanged_cells": unchanged,
        "rows_per_session": dict(sorted(per_session.items())),
        "postconditions": {
            "sma_200_unchanged": True,
            "delivery_ma_60_unchanged": True,
            "row_count_unchanged": True,
            "schema_unchanged": True,
        },
    }
    plan = WritePlan(payload=payload, rows=tuple(rows))
    plan.assert_scope()
    return plan


def validate_plan(
    plan: WritePlan,
    *,
    live_scope_digest: str,
    expected_columns: Sequence[str] = OPTION_B_COLUMNS,
) -> None:
    """Fail closed unless the plan and the live data still match what was signed.

    * any unexpected/short column list;
    * a plan whose payload no longer describes the live target scope;
    * a caller expecting a different column set (e.g. the old four-column run);
    * a code fingerprint or calculation rule that has moved since generation.
    """
    plan.assert_scope()
    if tuple(plan.columns) != tuple(expected_columns):
        raise ScopeError(
            f"plan writes {sorted(plan.columns)} but the operation expects "
            f"{sorted(expected_columns)}"
        )
    if plan.payload["baseline_scope_digest"] != live_scope_digest:
        raise ScopeError(
            "production content moved since the manifest was generated "
            f"(recorded {plan.payload['baseline_scope_digest'][:16]}..., "
            f"live {live_scope_digest[:16]}...); regenerate the plan"
        )
    if plan.payload["calculation_rule"] != CALCULATION_RULE:
        raise ScopeError("the calculation rule changed since the plan was generated")
    if not plan.payload["code_sha256"]:
        raise ScopeError("the plan carries no calculation-code fingerprint")


def apply_write_plan(conn, plan: WritePlan, *, live_scope_digest: str) -> int:
    """Apply a validated plan inside one transaction.

    Refuses to write unless :func:`validate_plan` passes first, so a stale or
    out-of-scope plan cannot reach the database. Only the three approved columns
    are ever assigned; ``sma_200`` and ``delivery_ma_60`` are not in the SET
    clause at all.
    """
    validate_plan(
        plan, live_scope_digest=live_scope_digest, expected_columns=OPTION_B_COLUMNS
    )
    if not plan.rows:
        return 0
    set_clause = ", ".join(f"{c} = ?" for c in plan.columns)
    sql = f"UPDATE technical_data SET {set_clause} WHERE symbol = ? AND date = ?"
    values = [tuple(r[2:]) + (r[0], r[1]) for r in plan.rows]
    cur = conn.cursor()
    cur.execute("BEGIN IMMEDIATE")
    try:
        cur.executemany(sql, values)
        matched = cur.rowcount
        # A row whose (symbol, date) no longer exists matches nothing and fails
        # *silently* - SQLite does not raise. Rehearsing the three-column plan
        # against a copy with one malformed key committed the other 35 rows
        # without complaint, so the affected count is verified explicitly and a
        # partial application aborts the whole transaction.
        if matched is not None and matched != len(values):
            raise ScopeError(
                f"plan expects {len(values)} rows to match but {matched} did; "
                "the target set has changed since the plan was generated"
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return matched if matched is not None else len(values)
