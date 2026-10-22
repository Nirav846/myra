"""Bulk OHLCV loader for scanners.

Replaces the per-symbol ``sqlite3.connect()`` + indexed SELECT pattern
inside each scanner's ``_get_tech_data`` (measured ~31 ms/symbol → ~83 s
of pure DB I/O per full scan) with ONE bulk query per scan run.

Usage (inside a scanner's ``scan()``)::

    self._bulk_data = load_ohlcv_for_universe(min_date, as_on_date)

Then ``_get_tech_data`` consults ``self._bulk_data`` first and only falls
back to the DB when the symbol is not present in the bulk window.
"""

from __future__ import annotations

import math
import os
import sqlite3
import threading
from contextlib import closing, contextmanager
from datetime import date
from typing import Dict, Iterator, Optional, Sequence

import numpy as np
import pandas as pd

from myra_app.constants import DB_DIR
from myra_app.librarian_core import LibrarianCore

# ---------------------------------------------------------------------------
# Batch-scoped shared snapshot
# ---------------------------------------------------------------------------
# A confluence batch runs ~12 scanners that each call
# ``load_ohlcv_for_universe`` for the same ``as_on_date``. Those calls overlap
# heavily: for a live run the widest window any constituent scanner requests is
# 545 calendar days (Bottom Hunter M1 / Super Breakout) and five of them ask for
# the full symbol universe, so without sharing the batch reads the same OHLCV
# rows from SQLite a dozen times.
#
# ``batch_snapshot_scope`` bounds that duplication to a single batch. While the
# scope is open, ``load_ohlcv_for_universe`` serves any request that falls
# inside an already-loaded window from one in-memory snapshot instead of
# re-reading the DB. A request outside the window widens it (union of dates and
# symbols) and reloads, so correctness is never traded for the optimisation.
#
# The state is *thread-local* and live only inside the explicit ``with`` block:
# concurrent standalone scans on other threads (the FastAPI worker pool) always
# take the untouched DB path, and the snapshot is released the moment the batch
# ends. There is no persistent cache and therefore no invalidation window to
# reason about -- a new batch re-reads the source and sees updated data.

_local = threading.local()


class _BatchSnapshot:
    """One loaded universe window shared by every scan in a batch."""

    __slots__ = ("start", "end", "symbols", "data", "uniform_dtypes")

    def __init__(
        self,
        start: str,
        end: str,
        symbols: Optional[Sequence[str]],
        data: Dict[str, pd.DataFrame],
    ):
        self.start = start
        self.end = end
        # None => the snapshot holds every symbol in the window.
        self.symbols: Optional[set[str]] = None if not symbols else set(symbols)
        self.data = data
        # ``_read_bulk`` builds every frame from one result set, so dtypes are
        # uniform by construction. Verifying it once per snapshot (not once per
        # slice) lets the dtype pass skip its per-symbol scan.
        self.uniform_dtypes = _frames_share_dtypes(data)

    def covers(self, start: str, end: str, symbols: Optional[Sequence[str]]) -> bool:
        """True when the snapshot can answer the request without a reload."""
        if start < self.start or end > self.end:
            return False
        if self.symbols is None:
            return True
        if not symbols:
            # The caller wants every symbol, but this snapshot is a subset.
            return False
        return set(symbols) <= self.symbols

    def slice(
        self, start: str, end: str, symbols: Optional[Sequence[str]]
    ) -> Dict[str, pd.DataFrame]:
        """Per-symbol frames for ``[start, end]`` as fresh, independent copies.

        Boolean-mask indexing plus ``reset_index`` yields a new frame, so a
        scanner mutating what it receives can never reach the snapshot or
        another scanner's view.
        """
        wanted = None if not symbols else set(symbols)
        out: Dict[str, pd.DataFrame] = {}
        for symbol, df in self.data.items():
            if wanted is not None and symbol not in wanted:
                continue
            left = df["date"].searchsorted(start, side="left")
            right = df["date"].searchsorted(end, side="right")
            if left >= right:
                continue
            out[symbol] = df.iloc[left:right].reset_index(drop=True)
        _match_request_dtypes(out, uniform=self.uniform_dtypes)
        return out


def _frames_share_dtypes(data: Dict[str, pd.DataFrame]) -> bool:
    """True when every frame carries the same column dtypes.

    ``slice`` reindexes frames with ``iloc``, which preserves dtypes, so a
    uniform snapshot yields uniform slices. Empty or single-frame snapshots are
    trivially uniform.
    """
    frames = iter(data.values())
    try:
        first = next(frames).dtypes
    except StopIteration:
        return True
    return all(frame.dtypes.equals(first) for frame in frames)


def _match_request_dtypes(out: Dict[str, pd.DataFrame], uniform: bool = False) -> None:
    """Match the dtypes a direct SQL read of this exact request would infer.

    ``pd.read_sql_query`` infers each column's dtype from its *whole* result
    set, and an all-NULL REAL column comes back as ``object``/None rather than
    ``float64``/NaN. A snapshot slice must reproduce that, or a scanner reading
    (say) ``swing_low`` for a single symbol with no swing-low data would see a
    different dtype than the unshared path. Only columns that are entirely NA
    across every returned symbol are recast -- one symbol with a value is
    enough for the real query to infer a float column.

    ``uniform`` lets a snapshot assert what it already verified at construction:
    every frame shares the same dtypes, so the per-symbol dtype scan is
    redundant. The recast decision still depends on the requested window and
    symbol subset, so it stays per call.
    """
    if not out:
        return
    frames = list(out.values())
    candidates = [c for c, dt in frames[0].dtypes.items() if dt.kind == "f"]
    if not candidates:
        return
    for column in candidates:
        if not uniform and any(sub[column].dtype.kind != "f" for sub in frames):
            continue
        has_value = False
        for sub in frames:
            if sub[column].notna().any():
                has_value = True
                break
        if has_value:
            continue
        # No frame held a value, so every frame is all-NA for this column (the
        # loop above already proved it) -- recast all of them, as a direct read
        # would have inferred object.
        for sub in frames:
            sub[column] = pd.Series([None] * len(sub), index=sub.index, dtype=object)


class _Scope:
    """Mutable state for one ``batch_snapshot_scope`` block."""

    def __init__(self, load_all_symbols: bool):
        self.load_all_symbols = load_all_symbols
        self.snapshot: Optional[_BatchSnapshot] = None

    def prewarm(self, start: str, end: str) -> None:
        """Populate the snapshot up front with the batch's expected union."""
        self.snapshot = _BatchSnapshot(start, end, None, _read_bulk(start, end, None))

    def resolve(
        self, start: str, end: str, symbols: Optional[Sequence[str]]
    ) -> Dict[str, pd.DataFrame]:
        snap = self.snapshot
        if snap is not None and snap.covers(start, end, symbols):
            return snap.slice(start, end, symbols)

        if snap is None:
            new_start, new_end = start, end
            new_symbols = None if self.load_all_symbols else symbols
        else:
            new_start = min(start, snap.start)
            new_end = max(end, snap.end)
            if self.load_all_symbols or snap.symbols is None or not symbols:
                new_symbols = None
            else:
                new_symbols = list(snap.symbols | set(symbols))

        # Reload the widened union, then publish it only on success so a failed
        # load leaves the previous snapshot intact.
        self.snapshot = _BatchSnapshot(
            new_start, new_end, new_symbols, _read_bulk(new_start, new_end, new_symbols)
        )
        return self.snapshot.slice(start, end, symbols)


@contextmanager
def batch_snapshot_scope(load_all_symbols: bool = True) -> Iterator[_Scope]:
    """Share OHLCV loads across the scanners run inside this block.

    ``load_all_symbols`` reflects whether the batch contains scanners that
    request the full universe (true for the confluence batch: five of its
    scanners pass no symbol filter). When true the snapshot is always the full
    universe, so a filtered request is served as a subset rather than widening
    the snapshot and triggering a reload.

    Nested scopes are supported: the previous scope is restored on exit.
    """
    previous = getattr(_local, "scope", None)
    scope = _Scope(load_all_symbols)
    _local.scope = scope
    try:
        yield scope
    finally:
        _local.scope = previous


# Superset of columns consumed by all scanners (12-col base, 13-col
# wyckoff adds swing_low, 8-col climax/dcb use the first eight).
TECH_COLUMNS: tuple[str, ...] = (
    "date",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "delivery",
    "delivery_pct",
    "nifty_outperformance_score",
    "sma_50",
    "high_52w",
    "low_52w",
    "swing_low",
)

# Column sets returned by each scanner family (must match the scanner's
# original SELECT column order so downstream pd.DataFrame(...) construction
# is byte-for-byte identical).
COLUMNS_12: tuple[str, ...] = (
    "date",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "delivery",
    "delivery_pct",
    "nifty_outperformance_score",
    "sma_50",
    "high_52w",
    "low_52w",
)
COLUMNS_13: tuple[str, ...] = (
    "date",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "delivery",
    "delivery_pct",
    "swing_low",
    "nifty_outperformance_score",
    "sma_50",
    "high_52w",
    "low_52w",
)
COLUMNS_8: tuple[str, ...] = (
    "date",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "delivery",
    "delivery_pct",
)


def _tech_db_path() -> str:
    """Return the filesystem path of the technical_data sidecar DB."""
    return os.path.join(DB_DIR, LibrarianCore.DB_MAP["technical"])


def _select_columns(conn) -> list[str]:
    """Return the subset of TECH_COLUMNS present in the live schema."""
    try:
        rows = conn.execute("PRAGMA table_info(technical_data)").fetchall()
    except sqlite3.OperationalError:
        return []
    existing = {r[1] for r in rows}
    return [c for c in TECH_COLUMNS if c in existing]


def latest_ohlcv_date() -> str | None:
    """Return the newest ``date`` present in ``technical_data``, else None.

    Cheap (single indexed MAX) and used by the confluence batch to decide
    whether a run is "live" -- i.e. whether the scanners that fall back to the
    full history for back-dated runs will instead use their short window.
    """
    tech_db = _tech_db_path()
    if not os.path.exists(tech_db):
        return None
    try:
        with sqlite3.connect(tech_db) as conn:
            row = conn.execute("SELECT MAX(date) FROM technical_data").fetchone()
    except sqlite3.OperationalError:
        return None
    if not row or row[0] is None:
        return None
    return str(row[0])


def _read_bulk(
    start_date: str,
    end_date: str,
    symbols: Optional[Sequence[str]],
) -> Dict[str, pd.DataFrame]:
    """Read OHLCV rows straight from SQLite (the unshared code path).

    The query orders by ``symbol, date`` so SQLite can walk
    ``idx_tech_symbol_date`` and read the table in storage order. Without the
    ORDER BY the date-only predicate drives ``idx_tech_date`` and every row
    costs a scattered page fetch. ``(symbol, date)`` is the table's primary
    key, so that ordering also makes each returned frame chronological.
    """
    tech_db = _tech_db_path()
    if not os.path.exists(tech_db):
        return {}

    with closing(sqlite3.connect(tech_db)) as conn:
        cols = _select_columns(conn)
        if not cols:
            return {}
        sql = (
            "SELECT symbol, " + ", ".join(cols) + " FROM technical_data "
            "WHERE date BETWEEN ? AND ?"
        )
        params: list = [start_date, end_date]
        if symbols:
            ph = ",".join("?" for _ in symbols)
            sql += f" AND symbol IN ({ph})"
            params.extend(symbols)
        sql += " ORDER BY symbol, date"
        df = pd.read_sql_query(sql, conn, params=params)

    if df is None or df.empty:
        return {}

    result: Dict[str, pd.DataFrame] = {}
    for symbol, group in df.groupby("symbol", sort=False):
        # Rows arrive in (symbol, date) order, so each group is already sorted
        # and only the per-symbol index needs rebuilding.
        result[symbol] = group.reset_index(drop=True)
    return result


def load_ohlcv_for_universe(
    start_date: str,
    end_date: str | None = None,
    symbols: Optional[Sequence[str]] = None,
) -> Dict[str, pd.DataFrame]:
    """Load OHLCV + delivery rows for symbols in one query.

    Returns ``{symbol: DataFrame}`` with each frame ordered by ``date``
    ascending and containing the columns that exist in the live schema
    (subset of ``TECH_COLUMNS``).

    ``end_date`` defaults to today when omitted.  When ``symbols`` is
    provided (an iterable of symbol strings), only those symbols are
    loaded via ``WHERE symbol IN (...)``.  When ``symbols`` is None or an
    empty list, all symbols are loaded (backward-compatible behaviour).

    When called inside a ``batch_snapshot_scope`` (see above) the request is
    served from the batch's shared snapshot wherever the snapshot already
    covers it; otherwise it hits the database exactly as before.
    """
    end_date = end_date or date.today().isoformat()
    scope: Optional[_Scope] = getattr(_local, "scope", None)
    if scope is not None:
        return scope.resolve(start_date, end_date, symbols)
    return _read_bulk(start_date, end_date, symbols)


def get_df_for_symbol(
    bulk: Dict[str, pd.DataFrame],
    symbol: str,
    columns: tuple[str, ...] | None = None,
    min_date: str | None = None,
    max_date: str | None = None,
) -> pd.DataFrame | None:
    """Return a per-symbol DataFrame slice from the pre-loaded bulk window.

    Returns ``None`` when the symbol is absent, empty, or outside the
    ``[min_date, max_date]`` window.  The returned frame is a copy (so the
    cached bulk frame is never mutated) and excludes any requested columns
    that are missing from the live schema.  The ``date`` column is already
    datetime and sorted ascending, so callers can skip
    ``pd.to_datetime``/``sort_values``.

    This is the DataFrame-preserving counterpart to ``rows_for_symbol`` —
    it avoids the tuple round-trip for scanners (e.g. DCB) that immediately
    reconstruct a ``pd.DataFrame`` from the returned tuples.
    """
    df = bulk.get(symbol)
    if df is None or df.empty:
        return None
    if min_date or max_date:
        mask = pd.Series(True, index=df.index)
        if min_date:
            mask &= df["date"] >= min_date
        if max_date:
            mask &= df["date"] <= max_date
        df = df.loc[mask]
        if df.empty:
            return None
    if columns:
        present = [c for c in columns if c in df.columns]
        df = df[present]
    df = df.copy()
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"])
    return df


def _pad_rows(
    sub: pd.DataFrame, columns: tuple[str, ...], present: list[str]
) -> list[tuple]:
    """Slow path: pad columns missing from the live schema with ``None``."""
    out = []
    for r in sub[present].itertuples(index=False, name=None):
        row = list(r)
        padded = []
        i = 0
        for c in columns:
            if c in sub.columns:
                value = row[i]
                i += 1
            else:
                value = None
            # sqlite3 returns None for NULL; pandas read_sql produces NaN
            # in float columns. Normalize so tuples match the per-symbol
            # SQL path byte-for-byte.
            if isinstance(value, float) and math.isnan(value):
                value = None
            padded.append(value)  # noqa: PG-APPEND
        out.append(tuple(padded))  # noqa: PG-APPEND
    return out


def rows_for_symbol(
    bulk: Dict[str, pd.DataFrame],
    symbol: str,
    columns: tuple[str, ...],
    min_date: str,
    max_date: str | None = None,
) -> list[tuple]:
    """Slice one symbol out of the pre-loaded bulk window.

    Returns ``list[tuple]`` filtered to ``[min_date, max_date]`` with the
    requested column order — identical shape to what the per-symbol SQL
    SELECT used to return. Columns missing from the live schema are padded
    with ``None`` so downstream ``col_count`` checks behave exactly like
    the original ``SELECT ... NULL AS ...`` fallback. Returns an empty list
    when the symbol is not present in the bulk window.
    """
    df = bulk.get(symbol)
    if df is None or df.empty:
        return []
    max_date = max_date or date.today().isoformat()
    mask = df["date"] >= min_date
    if max_date:
        mask = mask & (df["date"] <= max_date)
    sub = df.loc[mask]
    if sub.empty:
        return []
    present = [c for c in columns if c in sub.columns]
    if not present:
        return []
    if len(present) != len(columns):
        return _pad_rows(sub, columns, present)

    # Full schema: convert the whole frame in one C-level pass instead of
    # testing ``c in sub.columns`` and NaN-checking every cell in Python.
    # The result is the same tuple-of-Python-scalars shape: float columns
    # contribute Python floats (NaN -> None), everything else keeps its
    # native Python type. Object columns are swept explicitly because SQLite
    # is dynamically typed and a REAL column can arrive as object dtype with
    # a float NaN still inside it.
    frame = sub[present]
    values = frame.to_numpy(dtype=object)
    nan_mask = np.zeros(values.shape, dtype=bool)
    object_cols: list[int] = []
    for j, c in enumerate(present):
        kind = frame[c].dtype.kind
        if kind == "f":
            nan_mask[:, j] = np.isnan(frame[c].to_numpy())
        elif kind == "O":
            object_cols.append(j)  # noqa: PG-APPEND
    if nan_mask.any():
        values[nan_mask] = None
    for j in object_cols:
        col = values[:, j]
        for i, v in enumerate(col):
            if isinstance(v, float) and v != v:
                col[i] = None
    return [tuple(r) for r in values.tolist()]
