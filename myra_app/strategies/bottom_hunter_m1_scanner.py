"""Bottom Hunter M1 — scanner (Kaushik 'Bottom Out Hunting' Method 1, live).

Port of the backtest signal ``kaushik_boh_m1``
(``myra_app/backtest_engine.py::_precompute_kaushik_events`` +
``KaushikBOHMethod1``) from the event-calendar world into the live-scan
world.  The crossing/cooldown math in :func:`detect_events` is a verbatim
port of the backtest's per-symbol sweep, so a scan at date T reproduces the
backtest's event calendar for symbols in the top-500 universe at T.

Signal definition (unchanged from the backtest)
-----------------------------------------------
A symbol signals on day ``t`` iff::

    close[t-1] < 1.20 * year_low[t]   and   close[t] >= 1.20 * year_low[t]

where ``year_low[t]`` is the rolling 252-trading-day low of the LOW price
recomputed daily (window includes today).  This is a discrete crossing
event, not a "currently above" condition.  Cooldown: once a symbol signals
on day ``s`` (floor = year_low[s]), it cannot signal again until year_low
drops *strictly* below that floor — a fresh 52-week low, which restarts the
cycle (mirrors Kaushik's one-signal-per-genuine-bottom behavior).

VALIDATION CAVEAT — read before acting on any candidate
-------------------------------------------------------
The validated edge for this signal is **specific to the 2024-2026 holdout
period** (a large-cap dip-recovery regime): Phase-5 backtest on the
reconstructed top-500-by-market-cap universe, holdout 2024-01-01..2026-09-04,
all 6 configs positive (+366%..+809% vs +124% matched random; train train
negative/weak).  The signal has **NOT** been validated across a genuine bear
market or a small-cap-led regime — the profitable regime may not persist.
Do NOT present this scanner as all-weather validated.  Re-run
``_phase5_mcap_run.py`` on fresh holdout data before sizing decisions.

Why delivery is NOT part of this signal (D5 — closed)
-----------------------------------------------------
Kaushik's original method-1 was tested three independent ways with a
delivery element (elevation-ranked tie-break ``kaushik_boh_m1_delivery``,
entry filter ``kaushik_boh_m1_delivery_filter``, plus a maintenance test).
**None survived the holdout.**  The delivery augmentation is therefore NOT
included here and must NOT be casually re-added: a future contributor who
wants delivery back must re-run the Phase-2/Phase-5 backtest comparison with
delivery variants to prove an edge, not assume one.  (``delivery_pct`` is
still surfaced per candidate as reference info only; it does not influence
ranking, which uses the backtest tie-break — smallest overshoot first.)

Live-tool semantics (D3 / D4)
-----------------------------
* The scanner surfaces **fresh crossings only** (standard ``candidates``
  format; D3).
* Every crossing is classified ``signal_type``:
  - ``"NEW"`` — a fresh crossing on a symbol with no position on the
    book (a *new opportunity*).
  - ``"ADD"`` — a fresh crossing on a symbol the user holds
    (``bhm1_positions``); it is an *add-to-an-existing-position* signal per
    the backtest's ``average_in`` semantics.  ``at_tranche_cap`` is True
    when ``n_tranches >= max_tranches`` — the crossing is real but the
    averaging book would refuse another tranche.
  The two classes are unambiguous for a UI: render NEW and ADD differently.
* The scanner does NOT open, close, or simulate positions (D4: no full
  tranche simulation live).  ``bhm1_positions`` is read-only input,
  maintained by the user (helpers in ``bottom_hunter_m1_state.py``).
* Cooldown persists across runs in ``bhm1_cooldown`` so a live restart
  cannot double-signal a cycle whose threshold crossing day already passed.
  Persistence happens ONLY when scanning the latest trading day (live);
  back-dated scans start the sweep unseeded — exactly reproducing the
  backtest — and never write (see ``bottom_hunter_m1_state.py``).

Universe (D1)
-------------
Preferred: ``mcap_rank_daily`` (the PIT rank table built by
:mod:`myra_app.mcap_rank_builder`); the scan uses the table's latest date
<= the scan date and takes ranks 1..top_n.  Fallback (table absent or
empty): current-snapshot top-N by ``fundamentals.market_cap`` — a single-day
snapshot, not PIT.  The fallback is logged loudly every scan so nobody
mistakes it for PIT.

Parameters mirror the confirmed matrix defaults (Phase 5):
``top_n=500``, ``lookback=252``, ``recovery_mult=1.20``, ``max_tranches=3``.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from datetime import date, timedelta
from typing import Optional, Sequence

import numpy as np
import pandas as pd

from myra_app.constants import DB_DIR
from myra_app.librarian_core import LibrarianCore
from myra_app.db.bulk_loader import (
    COLUMNS_12,
    load_ohlcv_for_universe,
    rows_for_symbol,
)

logger = logging.getLogger(__name__)

# Backtest constants — do NOT change without re-validating (mirror
# backtest_engine.KAUSHIK_*).
BHM1_LOOKBACK = 252  # rolling 52-week window (trading days)
BHM1_RECOVERY_MULT = 1.20  # recover to year_low * 1.20 to fire
BHM1_MAX_TRANCHES = 3  # per-symbol tranche cap in the averaging book

TARGET_TABLE = "mcap_rank_daily"

VALIDATION_CAVEAT = (
    "CAVEAT: validated edge is specific to the 2024-2026 holdout "
    "(large-cap dip-recovery regime). NOT validated across a genuine bear "
    "market or a small-cap-led regime. Not all-weather validated."
)


# ── pure detection (port of the backtest sweep) ─────────────────────────


def detect_events(
    closes: np.ndarray,
    lows: np.ndarray,
    lookback: int = BHM1_LOOKBACK,
    recovery_mult: float = BHM1_RECOVERY_MULT,
    floor: Optional[float] = None,
    run_min: Optional[float] = None,
) -> tuple[list[tuple[int, float]], dict]:
    """Run the BOH-M1 sweep over one symbol's series.

    Parameters
    ----------
    closes, lows : 1-D float arrays, aligned, ascending by date.  ``≤ n``
        rows is fine: the sweep needs at least ``lookback + 1`` trades.
    floor, run_min : persisted cooldown state (values from
        ``bhm1_cooldown``).  ``(None, None)`` starts the sweep unseeded —
        identical to the backtest's fresh per-symbol flush.  ``floor`` not
        None places the symbol inside a cooldown cycle that began *before*
        the window (with ``run_min`` = min year_low observed since the
        signal; treat persisted None as "no days processed since signal").

    Returns
    -------
    (events, final_state): ``events`` = list of ``(index, overshoot)`` for
    every crossing detected in the window (ascending); ``final_state`` is a
    dict ``{"in_cooldown": bool, "floor": float|None, "run_min": float|None}``
    describing the state at the END of the sweep.
    """
    closes = np.asarray(closes, dtype=float)
    lows = np.asarray(lows, dtype=float)
    n = len(closes)
    events: list[tuple[int, float]] = []
    if n < lookback + 1:
        # Not even one full 52-week window (and a prior close).
        return events, {"in_cooldown": False, "floor": None, "run_min": None}

    # Rolling `lookback`-day low of LOW, valid from index lookback-1 on.
    year_low = np.full(n, np.nan)
    year_low[lookback - 1 :] = np.lib.stride_tricks.sliding_window_view(
        lows, lookback
    ).min(axis=-1)

    threshold = recovery_mult
    # Seed cooldown state (None -> not in cooldown / inf observables).
    in_cd = floor is not None
    fl = floor if in_cd else np.inf
    run = run_min if (in_cd and run_min is not None) else np.inf

    for i in range(lookback, n):
        y = year_low[i]
        if np.isnan(y):
            continue
        if in_cd:
            if y < run:
                run = y
            if run < fl:
                in_cd = False  # fresh 52-week low -> cycle restarts
                # Fall through: a same-day reversal could also cross.
            else:
                continue
        if closes[i - 1] < threshold * y and closes[i] >= threshold * y:
            overshoot = float(closes[i] / (threshold * y) - 1.0)
            events.append((i, overshoot))  # noqa: PG-APPEND
            in_cd = True
            fl = y
            run = np.inf

    final = {
        "in_cooldown": in_cd,
        "floor": fl if in_cd else None,
        "run_min": (None if run == np.inf else run) if in_cd else None,
    }
    return events, final


class BottomHunterM1Scanner:
    """Live Bottom Hunter M1 scanner (see module docstring)."""

    _bulk_data = None
    _BULK_COLUMNS = COLUMNS_12

    validation_caveat = VALIDATION_CAVEAT

    def __init__(
        self,
        top_n: int = 500,
        lookback: int = BHM1_LOOKBACK,
        recovery_mult: float = BHM1_RECOVERY_MULT,
        max_tranches: int = BHM1_MAX_TRANCHES,
        state_db: Optional[str] = None,
        as_on_date: Optional[str] = None,
        max_stale_days: int = 10,
    ):
        self.top_n = int(top_n)
        self.lookback = int(lookback)
        self.recovery_mult = float(recovery_mult)
        self.max_tranches = int(max_tranches)
        self.state_db = state_db  # overridable for tests; None -> meta sidecar
        self.as_on_date = as_on_date  # parse-time hint (mirrors other scanners)
        self.max_stale_days = int(max_stale_days)
        self.universe_source: Optional[str] = None  # set by _get_universe()

    # ── plumbing shared with the other scanners ─────────────────────────

    def _db_path(self, key: str) -> str:
        return os.path.join(DB_DIR, LibrarianCore.DB_MAP[key])

    def _get_universe(self) -> list[tuple]:
        """Return [(symbol, mcap_rank, mcap_cr)] for the top-N universe.

        Prefers the PIT ``mcap_rank_daily`` table (D1) for the scan date;
        falls back to the current-snapshot top-N by latest market cap.  The
        tuple shape matches the other scanners (the web layer iterates it
        for progress).  The rank is 1-based.
        """
        as_of = self.as_on_date or date.today().isoformat()
        score_db = self._db_path("scoring")
        src: Optional[str] = None
        rows: list[tuple] = []

        if os.path.exists(score_db):
            try:
                with sqlite3.connect(score_db) as conn:
                    has_table = conn.execute(
                        "SELECT 1 FROM sqlite_master WHERE type='table' " "AND name=?",
                        (TARGET_TABLE,),
                    ).fetchone()
                    if has_table:
                        snap = conn.execute(
                            f"SELECT MAX(date) FROM {TARGET_TABLE} WHERE date <= ?",
                            (as_of,),
                        ).fetchone()
                        if snap and snap[0]:
                            snap_date = str(snap[0])
                            if (
                                pd.Timestamp(as_of) - pd.Timestamp(snap_date)
                            ).days > 10:
                                logger.warning(
                                    "mcap_rank_daily snapshot %s is >10d older "
                                    "than as_of %s — table may be stale",
                                    snap_date,
                                    as_of,
                                )
                            rows = conn.execute(
                                f"SELECT symbol, mcap_rank, mcap_cr "
                                f"FROM {TARGET_TABLE} WHERE date = ? "
                                f"ORDER BY mcap_rank LIMIT ?",
                                (snap[0], self.top_n),
                            ).fetchall()
                            if rows:
                                src = f"mcap_rank_daily@{snap[0]}"
            except Exception as e:  # noqa: BLE001
                logger.warning("mcap_rank_daily read failed: %s", e)

        if rows:
            self.universe_source = src
            return [(r[0], int(r[1]), float(r[2])) for r in rows if r[2]]

        # Fallback: current snapshot (NOT PIT) — loud so it cannot be
        # mistaken for the D1 table.  Mirrors the Phase-4 latest-date anchor
        # (the ranking-fidelity gate verified this snapshot at ~500/515).
        val_db = self._db_path("valuation")
        if not os.path.exists(val_db):
            self.universe_source = "EMPTY-UNIVERSE"
            return []
        with sqlite3.connect(val_db) as conn:
            rows = conn.execute(
                """
                SELECT symbol, rnk, mcap_cr FROM (
                    SELECT f.symbol,
                           f.market_cap / 1e7 AS mcap_cr,
                           ROW_NUMBER() OVER (
                               ORDER BY f.market_cap DESC, f.symbol ASC
                           ) AS rnk
                    FROM fundamentals f
                    INNER JOIN (
                        SELECT symbol, MAX(date) AS md
                        FROM fundamentals
                        WHERE COALESCE(market_cap, 0) > 0
                        GROUP BY symbol
                    ) latest ON f.symbol = latest.symbol AND f.date = latest.md
                ) WHERE rnk <= ?
                """,
                (self.top_n,),
            ).fetchall()
        self.universe_source = "fundamentals-snapshot (NOT PIT - run mcap_rank_builder)"
        return [(r[0], int(r[1]), float(r[2])) for r in rows if r[2]]

    def _get_tech_data(
        self, symbol: str, min_date: str, max_date: Optional[str] = None
    ) -> list[tuple]:
        """Per-symbol rows (bulk path preferred; SQL fallback mirrors the
        other scanners and is what the web layer patches for progress)."""
        max_date = max_date or date.today().isoformat()
        if self._bulk_data is not None:
            return rows_for_symbol(
                self._bulk_data, symbol, self._BULK_COLUMNS, min_date, max_date
            )
        tech_db = self._db_path("technical")
        if not os.path.exists(tech_db):
            return []
        with sqlite3.connect(tech_db) as conn:
            try:
                return conn.execute(
                    """
                    SELECT date, open, high, low, close, volume, delivery,
                           delivery_pct, nifty_outperformance_score,
                           sma_50, high_52w, low_52w
                    FROM technical_data
                    WHERE symbol = ? AND date >= ? AND date <= ?
                    ORDER BY date ASC
                    """,
                    (symbol, min_date, max_date),
                ).fetchall()
            except sqlite3.OperationalError:
                return conn.execute(
                    """
                    SELECT date, open, high, low, close, volume, delivery,
                           delivery_pct, nifty_outperformance_score,
                           NULL AS sma_50, NULL AS high_52w, NULL AS low_52w
                    FROM technical_data
                    WHERE symbol = ? AND date >= ? AND date <= ?
                    ORDER BY date ASC
                    """,
                    (symbol, min_date, max_date),
                ).fetchall()

    # ── the scan ────────────────────────────────────────────────────────

    def _resolve_as_on_date(self, as_on_date: Optional[str]) -> str:
        """Latest trading day <= requested date (or today), from the data."""
        if as_on_date:
            ref = as_on_date
        elif self.as_on_date:
            ref = self.as_on_date
        else:
            ref = date.today().isoformat()
        tech_db = self._db_path("technical")
        if os.path.exists(tech_db):
            row = None
            with sqlite3.connect(tech_db) as conn:
                row = conn.execute(
                    "SELECT MAX(date) FROM technical_data WHERE date <= ?",
                    (ref,),
                ).fetchone()
            if row and row[0]:
                return str(row[0])
        return ref

    def _db_min_date(self) -> str:
        """Earliest bar in technical_data (full-history sweep floor)."""
        tech_db = self._db_path("technical")
        if os.path.exists(tech_db):
            with sqlite3.connect(tech_db) as conn:
                row = conn.execute("SELECT MIN(date) FROM technical_data").fetchone()
            if row and row[0]:
                return str(row[0])
        return "1990-01-01"

    def scan(self, as_on_date: Optional[str] = None) -> pd.DataFrame:
        """Surface fresh BOH-M1 crossings as of *as_on_date* (or the latest
        trading day).  Returns a DataFrame (one row per crossing) or an
        empty DataFrame when nothing crossed."""
        as_of = self._resolve_as_on_date(as_on_date)

        universe = self._get_universe()
        if not universe:
            logger.warning(
                "BHM1 scan: empty universe (source=%s) — returning empty",
                self.universe_source,
            )
            return pd.DataFrame()
        univ_meta = {s: (rank, mcap_cr) for s, rank, mcap_cr in universe}
        logger.info(
            "BHM1 scan: universe=%d source=%s as_of=%s",
            len(universe),
            self.universe_source,
            as_of,
        )

        # Calendar buffer: 252 trading days + margin ≈ 545 calendar days.
        # LIVE scans use the window + persisted cooldown seed (fast path,
        # exact as long as runs are not ~>1.3 years apart — the window must
        # contain the cycle's floor so an off-window fresh low can end it).
        # BACK-DATED scans sweep the FULL history unseeded so they reproduce
        # the backtest bit-exactly for any date (the window alone cannot see
        # cooldown cycles that began before it).
        is_live = as_of == self._resolve_as_on_date(None)
        if is_live:
            min_date = f"{(pd.Timestamp(as_of) - pd.Timedelta(days=545)):%Y-%m-%d}"
        else:
            min_date = self._db_min_date()
        self._bulk_data = load_ohlcv_for_universe(min_date, as_of, list(univ_meta))
        conn = None
        cooldown_seed: dict[str, dict] = {}
        positions: dict[str, dict] = {}
        if is_live:
            from myra_app.strategies.bottom_hunter_m1_state import (
                connect_meta,
                load_cooldown,
                load_positions,
            )

            conn = connect_meta(self.state_db)
            cooldown_seed = load_cooldown(conn)
            positions = load_positions(conn)
            if not cooldown_seed:
                # Cold start: run the scan-scheduled bootstrap ONCE so the
                # live sweep starts from the TRUE full-history cooldown state
                # (see _bootstrap_from_full_history) — otherwise the first
                # run would miss every cycle that began before the window.
                cooldown_seed = self._bootstrap_from_full_history(
                    conn, list(univ_meta), as_of
                )

        stale_cutoff = (
            (pd.Timestamp(as_of) - pd.Timedelta(days=self.max_stale_days))
            .date()
            .isoformat()
        )

        candidates: list[dict] = []
        final_cooldown: dict[str, dict] = {}
        cycle_signal_date: dict[str, str] = {}
        for symbol, rank, mcap_cr in universe:
            # Single per-symbol data path (bulk-backed when a bulk load is
            # present) so the web layer's progress patch on _get_tech_data
            # ticks per symbol.  COLUMNS_12 order: date, open, high, low,
            # close, volume, delivery, delivery_pct, nifty..., sma_50, hi52,
            # lo52.
            rows = self._get_tech_data(symbol, min_date, max_date=as_of)
            if len(rows) < self.lookback + 1:
                continue  # insufficient window (matches backtest n<252+1 skip)
            closes = np.array([r[4] for r in rows], dtype=float)
            lows = np.array([r[3] for r in rows], dtype=float)
            dates = [r[0] for r in rows]

            seed = cooldown_seed.get(symbol)
            events, final = detect_events(
                closes,
                lows,
                lookback=self.lookback,
                recovery_mult=self.recovery_mult,
                floor=seed["floor"] if seed else None,
                run_min=seed["run_min"] if seed else None,
            )
            final_cooldown[symbol] = final
            if final["in_cooldown"] and events:
                # The current cycle's signal = the last crossing in-window.
                cycle_signal_date[symbol] = dates[events[-1][0]]
            elif final["in_cooldown"] and seed and seed.get("signal_date"):
                cycle_signal_date[symbol] = seed["signal_date"]
            elif final["in_cooldown"]:
                cycle_signal_date[symbol] = as_of  # defensively bookend it

            # Surface only a crossing ON the symbol's latest bar and only if
            # that bar is not stale relative to the scan date.
            if not events or events[-1][0] != len(closes) - 1:
                continue
            idx, overshoot = events[-1]
            sig_date = dates[idx]
            if sig_date < stale_cutoff:
                continue  # last bar too old to be a live crossing

            # Rolling year_low at the signal index (window includes today).
            y = float(np.min(lows[idx - self.lookback + 1 : idx + 1]))
            close = float(closes[idx])
            pos = positions.get(symbol)
            if pos is not None:
                at_cap = (pos["n_tranches"] or 0) >= self.max_tranches
                candidates.append(  # noqa: PG-APPEND
                    {
                        "symbol": symbol,
                        "signal_date": sig_date,
                        "signal_type": "ADD",
                        "at_tranche_cap": at_cap,
                        "close": round(close, 2),
                        "year_low": round(y, 2),
                        "recovery_line": round(self.recovery_mult * y, 2),
                        "overshoot_pct": round(overshoot * 100, 3),
                        "mcap_rank": rank,
                        "mcap_cr": round(mcap_cr, 2),
                        "delivery_pct": self._last_delivery_pct(rows),
                        "n_tranches": pos.get("n_tranches") or 0,
                        "blended_basis": pos.get("blended_basis"),
                        "last_tranche_date": pos.get("last_tranche_date"),
                    }
                )
            else:
                candidates.append(  # noqa: PG-APPEND
                    {
                        "symbol": symbol,
                        "signal_date": sig_date,
                        "signal_type": "NEW",
                        "at_tranche_cap": False,
                        "close": round(close, 2),
                        "year_low": round(y, 2),
                        "recovery_line": round(self.recovery_mult * y, 2),
                        "overshoot_pct": round(overshoot * 100, 3),
                        "mcap_rank": rank,
                        "mcap_cr": round(mcap_cr, 2),
                        "delivery_pct": self._last_delivery_pct(rows),
                        "n_tranches": None,
                        "blended_basis": None,
                        "last_tranche_date": None,
                    }
                )

        # Persist cooldown ONLY on a live scan; back-dated scans are
        # read-only reproductions of the backtest (state module contract).
        if conn is not None:
            self._persist_cooldown(
                conn, cooldown_seed, final_cooldown, cycle_signal_date, as_of
            )
            conn.close()

        result = pd.DataFrame(candidates)
        n_new = int((result["signal_type"] == "NEW").sum()) if not result.empty else 0
        n_add = int((result["signal_type"] == "ADD").sum()) if not result.empty else 0
        logger.info(
            "BHM1 scan complete: %d crossings (NEW=%d ADD=%d) — %s",
            len(candidates),
            n_new,
            n_add,
            self.validation_caveat,
        )
        if not result.empty:
            result = result.sort_values(["overshoot_pct", "signal_type"]).reset_index(
                drop=True
            )
        return result

    def scan_near_trigger(
        self,
        as_on_date: Optional[str] = None,
        band_pct: float = 5.0,
    ) -> pd.DataFrame:
        """Surface stocks approaching the recovery line but not yet crossed.

        Returns symbols where close is within `band_pct`% below the
        recovery_line (year_low * recovery_mult), sorted by proximity
        (closest to trigger first).  Symbols currently in cooldown
        (haven't set a fresh lower low since their last signal) are
        excluded — a near-trigger stock in cooldown cannot actually fire.
        """
        as_of = self._resolve_as_on_date(as_on_date)
        universe = self._get_universe()
        if not universe:
            return pd.DataFrame()

        min_date = f"{(pd.Timestamp(as_of) - pd.Timedelta(days=545)):%Y-%m-%d}"
        # Intentionally do NOT call load_ohlcv_for_universe here —
        # scan_near_trigger uses _get_tech_data directly (which respects
        # the bulk path set by scan(), or falls back to SQL).

        # Load cooldown state — symbols in cooldown are excluded.
        from myra_app.strategies.bottom_hunter_m1_state import (
            connect_meta,
            load_cooldown,
        )

        cooldown: dict[str, dict] = {}
        meta_path = self.state_db or self._db_path("meta")
        if os.path.exists(meta_path):
            try:
                conn = connect_meta(meta_path)
                cooldown = load_cooldown(conn)
                conn.close()
            except Exception:
                pass

        results: list[dict] = []
        for symbol, rank, mcap_cr in universe:
            # Skip symbols currently in cooldown — they cannot fire a signal yet.
            if symbol in cooldown:
                continue

            rows = self._get_tech_data(symbol, min_date, max_date=as_of)
            if len(rows) < self.lookback + 1:
                continue
            closes = np.array([r[4] for r in rows], dtype=float)
            lows = np.array([r[3] for r in rows], dtype=float)

            # Rolling 252-day low at the latest bar.
            y = float(np.min(lows[-self.lookback :]))
            close = float(closes[-1])
            recovery_line = self.recovery_mult * y
            pct_to_trigger = (recovery_line - close) / close * 100

            # Only show stocks that haven't crossed yet and are within band.
            if pct_to_trigger <= 0 or pct_to_trigger > band_pct:
                continue

            results.append(
                {
                    "symbol": symbol,
                    "close": round(close, 2),
                    "year_low": round(y, 2),
                    "recovery_line": round(recovery_line, 2),
                    "pct_to_trigger": round(pct_to_trigger, 2),
                    "mcap_rank": rank,
                    "mcap_cr": round(mcap_cr, 2),
                    "delivery_pct": self._last_delivery_pct(rows),
                }
            )

        df = pd.DataFrame(results)
        if not df.empty:
            df = df.sort_values("pct_to_trigger").reset_index(drop=True)
        return df

    @staticmethod
    def _last_delivery_pct(rows: list[tuple]) -> Optional[float]:
        """Latest delivery_pct as reference info ONLY (D5 — never ranked on).

        ``rows`` are COLUMNS_12 tuples (delivery_pct = index 7).
        """
        if not rows:
            return None
        val = rows[-1][7]
        if val is None:
            return None
        val = float(val)
        if np.isnan(val) or np.isinf(val):
            return None
        return round(val, 2)

    def _bootstrap_from_full_history(
        self,
        conn: sqlite3.Connection,
        symbols: list[str],
        as_of: str,
    ) -> dict[str, dict]:
        """Cold-start seed: reproduce the backtest's FULL-history cooldown
        state as of *as_of* for every universe symbol.

        The per-symbol window-based sweep used by :meth:`scan` cannot see
        cooldown cycles that began before the 545-day window, which would
        make the FIRST live run emit false fresh crossings and miss symbols
        whose refresh-low predates the window.  Running the detector over the
        symbol's ENTIRE history (no window) yields the same final cooldown
        state the backtest's event sweep maintains, so persisting it once
        makes the first live scan exact.  Subsequent scans maintain the state.
        """
        from myra_app.strategies.bottom_hunter_m1_state import save_cooldown

        logger.info(
            "BHM1 bootstrap: full-history cooldown seed for %d symbols (one-time)",
            len(symbols),
        )
        to_save: dict[str, dict] = {}
        for symbol in symbols:
            rows = self._get_tech_data(symbol, "1900-01-01", max_date=as_of)
            if len(rows) < self.lookback + 1:
                continue
            closes = np.array([r[4] for r in rows], dtype=float)
            lows = np.array([r[3] for r in rows], dtype=float)
            dates = [r[0] for r in rows]
            events, final = detect_events(closes, lows)
            if not final["in_cooldown"]:
                continue
            # If the last crossing IS on the last bar (today), don't seed:
            # the live scan must surface it fresh, then persist post-scan.
            if events and events[-1][0] == len(closes) - 1:
                continue
            if events:
                sig_date = dates[events[-1][0]]
            else:
                sig_date = as_of  # defensively bookend it
            to_save[symbol] = {
                "signal_date": sig_date,
                "floor": final["floor"],
                "run_min": final["run_min"],
            }
        save_cooldown(conn, to_save, as_of)
        logger.info("BHM1 bootstrap: %d symbols in cooldown seeded", len(to_save))
        return to_save

    def _persist_cooldown(
        self,
        conn: sqlite3.Connection,
        seed: dict[str, dict],
        final: dict[str, dict],
        cycle_signal_date: dict[str, str],
        as_of: str,
    ) -> None:
        """Write post-scan cooldown state back to the meta sidecar.

        Live scans only.  Symbols whose cycle ended (final not in cooldown)
        are cleared; symbols in cooldown are upserted with their cycle's
        signal date (the last crossing date in the window, the seeded date
        when the cycle predates the window, or the as-of date defensively).
        """
        from myra_app.strategies.bottom_hunter_m1_state import (
            clear_cooldown,
            save_cooldown,
        )

        to_save: dict[str, dict] = {}
        to_clear: list[str] = []
        for symbol, st in final.items():
            if st["in_cooldown"]:
                to_save[symbol] = {
                    "signal_date": cycle_signal_date.get(symbol, as_of),
                    "floor": st["floor"],
                    "run_min": st["run_min"],
                }
            elif symbol in seed:
                to_clear.append(symbol)  # noqa: PG-APPEND
        save_cooldown(conn, to_save, as_of)
        clear_cooldown(conn, to_clear, as_of)
