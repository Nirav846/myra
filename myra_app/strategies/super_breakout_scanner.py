"""Super Breakout — live scanner.

Detects SMA-50 crossover entries with raw-delivery tie-break and manages
the two-phase exit lifecycle (protective stop → MA(50) trailing).

Regime caveat
-------------
Validated on NSE large-caps, 2015-2026 (delivery-dependent tie-break usable
from Oct 2019).  Price-only aspects trained 2015-2023; delivery-dependent
aspects only from 2019-10-01 onward.  Edge confirmed in both the 2015-2023
training period and a 2024-2026 out-of-sample holdout (percentile check
against 20 random sims, capture-ratio analysis, hand-verified trace).  Not
validated across a sustained bear market, a small-cap-led regime, or major
sector rotation.  Not all-weather validated.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from datetime import date
from typing import Optional

import numpy as np
import pandas as pd

from myra_app.constants import DB_DIR
from myra_app.librarian_core import LibrarianCore
from myra_app.db.bulk_loader import (
    COLUMNS_12,
    load_ohlcv_for_universe,
    rows_for_symbol,
)
from myra_app.strategies.super_breakout import (
    SMA_FAST,
    SMA_SLOW,
    check_precondition_context,
    detect_super_breakout_signals,
    _exit_ma_trailing,
    _rolling_mean,
)

logger = logging.getLogger(__name__)

TARGET_TABLE = "mcap_rank_daily"

VALIDATION_CAVEAT = (
    "CAVEAT: validated on NSE large-caps, 2015-2026 (delivery-dependent "
    "tie-break usable from Oct 2019). Edge confirmed in a 2015-2023 training "
    "period and a 2024-2026 out-of-sample holdout (percentile check against "
    "20 random sims). Not validated across a sustained bear market, small-cap-"
    "led regime, or major sector rotation. Not all-weather validated."
)


class SuperBreakoutScanner:
    """Live Super Breakout scanner (see module docstring)."""

    _bulk_data = None
    _BULK_COLUMNS = COLUMNS_12

    validation_caveat = VALIDATION_CAVEAT

    def __init__(
        self,
        top_n: int = 500,
        state_db: Optional[str] = None,
        as_on_date: Optional[str] = None,
        max_stale_days: int = 10,
    ):
        self.top_n = int(top_n)
        self.state_db = state_db
        self.as_on_date = as_on_date
        self.max_stale_days = int(max_stale_days)
        self.universe_source: Optional[str] = None

    # ── plumbing shared with the other scanners ─────────────────────────

    def _db_path(self, key: str) -> str:
        return os.path.join(DB_DIR, LibrarianCore.DB_MAP[key])

    def _get_universe(self) -> list[tuple]:
        """Return [(symbol, mcap_rank, mcap_cr)] for the top-N universe."""
        as_of = self.as_on_date or date.today().isoformat()
        score_db = self._db_path("scoring")
        src: Optional[str] = None
        rows: list[tuple] = []

        if os.path.exists(score_db):
            try:
                with sqlite3.connect(score_db) as conn:
                    has_table = conn.execute(
                        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
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

        # Fallback: current snapshot
        val_db = self._db_path("valuation")
        if os.path.exists(val_db):
            try:
                with sqlite3.connect(val_db) as vconn:
                    snap = vconn.execute(
                        "SELECT MAX(date) FROM symbols_master"
                    ).fetchone()
                    if snap and snap[0]:
                        rows = vconn.execute(
                            "SELECT symbol, mcap_rank, mcap_cr "
                            "FROM symbols_master WHERE date = ? "
                            "ORDER BY mcap_rank LIMIT ?",
                            (snap[0], self.top_n),
                        ).fetchall()
                        if rows:
                            src = f"symbols_master@{snap[0]}"
            except Exception:  # noqa: BLE001
                pass

        if rows:
            self.universe_source = src
            return [(r[0], int(r[1]), float(r[2])) for r in rows if r[2]]

        self.universe_source = None
        return []

    def _get_tech_data(
        self, symbol: str, min_date: str, max_date: Optional[str] = None
    ) -> list[tuple]:
        """Per-symbol rows (bulk path preferred; SQL fallback)."""
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

    def _resolve_as_on_date(self, as_on_date: Optional[str]) -> str:
        """Latest trading day <= requested date (or today)."""
        if as_on_date:
            ref = as_on_date
        elif self.as_on_date:
            ref = self.as_on_date
        else:
            ref = date.today().isoformat()
        tech_db = self._db_path("technical")
        if os.path.exists(tech_db):
            with sqlite3.connect(tech_db) as conn:
                row = conn.execute(
                    "SELECT MAX(date) FROM technical_data WHERE date <= ?",
                    (ref,),
                ).fetchone()
            if row and row[0]:
                return str(row[0])
        return ref

    def _db_min_date(self) -> str:
        """Earliest bar in technical_data."""
        tech_db = self._db_path("technical")
        if os.path.exists(tech_db):
            with sqlite3.connect(tech_db) as conn:
                row = conn.execute("SELECT MIN(date) FROM technical_data").fetchone()
            if row and row[0]:
                return str(row[0])
        return "1990-01-01"

    # ── the scan ────────────────────────────────────────────────────────

    def scan(self, as_on_date: Optional[str] = None) -> pd.DataFrame:
        """Surface Super Breakout candidates as of *as_on_date*.

        Returns a DataFrame with one row per active position or new signal,
        sorted by delivery_value (descending).  Columns include ``phase``
        (PENDING or TRAILING) for active positions.

        The live scan replays gap days bar-by-bar through the shared exit
        evaluator (O3 — no endpoint-only shortcuts).
        """
        from myra_app.strategies.super_breakout_state import (
            connect_meta,
            load_positions,
            upsert_position,
            delete_positions,
        )

        as_of = self._resolve_as_on_date(as_on_date)
        is_live = as_of == self._resolve_as_on_date(None)

        universe = self._get_universe()
        if not universe:
            logger.warning("Super Breakout: empty universe — nothing to scan")
            return pd.DataFrame()

        # Bulk load for progress tracking
        if is_live:
            min_date = f"{(pd.Timestamp(as_of) - pd.Timedelta(days=545)):%Y-%m-%d}"
        else:
            min_date = self._db_min_date()
        self._bulk_data = load_ohlcv_for_universe(min_date, as_of, list(u[0] for u in universe))

        # ── Canonical signal detection (same function as backtest) ───
        tech_db = self._db_path("technical")
        universe_by_date: dict[str, list[str]] = {as_of: [u[0] for u in universe]}
        conn_tech = sqlite3.connect(tech_db)
        try:
            all_signals = detect_super_breakout_signals(
                conn_tech, universe_by_date, "raw_delivery"
            )
        finally:
            conn_tech.close()
        scan_signals = all_signals.get(as_of, [])

        # Load persisted state (live only)
        conn_meta = None
        persisted: dict[str, dict] = {}
        if is_live:
            conn_meta = connect_meta(self.state_db)
            persisted = load_positions(conn_meta)

        # Detect new signals
        univ_by_date: dict[str, list[str]] = {}
        for sym, _, _ in universe:
            # Collect all trading dates this symbol appears on
            pass  # placeholder — signals are detected below per-symbol

        # Process each symbol
        candidates: list[dict] = []
        final_state: dict[str, dict] = {}
        exited_symbols: list[str] = []

        for sym, mcap_rank, mcap_cr in universe:
            rows = self._get_tech_data(sym, min_date, max_date=as_of)
            if len(rows) < SMA_SLOW + 1:
                continue

            dates = [r[0] for r in rows]
            closes = np.array([r[4] for r in rows], dtype=float)
            delivery_vals = np.array(
                [float(r[6]) if r[6] is not None else 0.0 for r in rows], dtype=float
            )

            # Compute SMA(50) and SMA(200) for display and stop-level
            sma50_arr = _rolling_mean(closes, SMA_FAST)
            sma200_arr = _rolling_mean(closes, SMA_SLOW)
            cur_sma50 = float(sma50_arr[-1]) if not np.isnan(sma50_arr[-1]) else None
            cur_sma200 = float(sma200_arr[-1]) if not np.isnan(sma200_arr[-1]) else None
            # Stop level = SMA(50) in both phases (protective + trailing both reference it)
            stop_level = cur_sma50

            # ── Gap replay for active positions (O3) ────────────────
            pos = persisted.get(sym)
            if pos and is_live:
                entry_date = pos["entry_date"]
                entry_price = pos["entry_price"]
                ever_activated = pos["ever_activated"]
                highest_close = pos["highest_close"]

                # Find entry_idx in the loaded window
                if entry_date in dates:
                    entry_idx = dates.index(entry_date)
                else:
                    # Entry date not in window — position too old, skip
                    continue

                # Build a DataFrame for the exit evaluator
                pos_df = pd.DataFrame({
                    "date": pd.to_datetime(dates[entry_idx:]),
                    "close": closes[entry_idx:],
                    "high": np.array([r[2] for r in rows[entry_idx:]], dtype=float),
                    "low": np.array([r[3] for r in rows[entry_idx:]], dtype=float),
                })

                # Seed the evaluator's activation state by replaying
                # If ever_activated is already True, the evaluator will
                # re-discover it (harmless); if False, it checks whether
                # activation happened in the gap.
                exit_idx, reason = _exit_ma_trailing(pos_df, 0, SMA_FAST)

                if exit_idx > 0:
                    # Position was held through some gap days
                    exit_price = float(pos_df["close"].iloc[exit_idx])
                    # Scalar Timestamp — .dt accessor N/A; NaT-raise behavior is intentional here.
                    exit_date_str = pos_df["date"].iloc[exit_idx].strftime("%Y-%m-%d")  # noqa: PG-STRFTIME

                # Determine if position survived to as_of
                last_close = float(closes[-1])
                last_date = dates[-1]

                # Re-run evaluator to check final state
                exit_idx_final, reason_final = _exit_ma_trailing(pos_df, 0, SMA_FAST)
                exited_on_latest = (exit_idx_final == len(pos_df) - 1) and reason_final != "ma_trail_eod"

                if not exited_on_latest and reason_final != "ma_trail_eod":
                    # Exited somewhere in the gap — record the exit
                    exit_close = float(pos_df["close"].iloc[exit_idx_final])
                    # Scalar Timestamp — .dt accessor N/A; NaT-raise behavior is intentional here.
                    exit_d = pos_df["date"].iloc[exit_idx_final].strftime("%Y-%m-%d")  # noqa: PG-STRFTIME
                    pnl_pct = (exit_close / entry_price - 1) * 100
                    candidates.append({
                        "symbol": sym,
                        "phase": "EXITED",
                        "entry_date": entry_date,
                        "entry_price": entry_price,
                        "exit_date": exit_d,
                        "exit_price": exit_close,
                        "exit_reason": reason_final,
                        "pnl_pct": round(pnl_pct, 2),
                        "n_hold_days": (pd.Timestamp(exit_d) - pd.Timestamp(entry_date)).days,
                        "delivery_value": float(delivery_vals[-1]),
                        "mcap_rank": mcap_rank,
                        "mcap_cr": mcap_cr,
                    })
                    exited_symbols.append(sym)
                    continue

                # Position still held — update state
                new_highest = max(highest_close, float(pos_df["close"].max()))
                # Check if ever activated during the gap
                activation_price = entry_price * 1.02
                activated_in_gap = bool(np.any(closes[entry_idx:] >= activation_price))

                final_state[sym] = {
                    "entry_date": entry_date,
                    "entry_price": entry_price,
                    "ever_activated": ever_activated or activated_in_gap,
                    "highest_close": new_highest,
                }

                # Surface as candidate
                current_pnl = (last_close / entry_price - 1) * 100
                phase = "TRAILING" if (ever_activated or activated_in_gap) else "PENDING"
                candidates.append({
                    "symbol": sym,
                    "phase": phase,
                    "entry_date": entry_date,
                    "entry_price": entry_price,
                    "current_price": last_close,
                    "activation_target": round(entry_price * 1.02, 2),
                    "pnl_pct": round(current_pnl, 2),
                    "n_hold_days": (pd.Timestamp(last_date) - pd.Timestamp(entry_date)).days,
                    "delivery_value": float(delivery_vals[-1]),
                    "mcap_rank": mcap_rank,
                    "mcap_cr": mcap_cr,
                    "sma_50": round(cur_sma50, 2) if cur_sma50 else None,
                    "sma_200": round(cur_sma200, 2) if cur_sma200 else None,
                    "stop_level": round(stop_level, 2) if stop_level else None,
                })
                continue

            # ── New signal detection (canonical, same as backtest) ────
            # Skip if already in an active position (no re-entry)
            if sym in persisted and is_live:
                continue

            # Look up canonical signal for this symbol
            signal_match = [s for s in scan_signals if s[0] == sym]
            if not signal_match:
                continue

            _sym, score = signal_match[0]

            candidates.append({
                "symbol": sym,
                "phase": "NEW",
                "signal_date": as_of,
                "entry_price": round(float(closes[-1]), 2),
                "activation_target": round(float(closes[-1]) * 1.02, 2),
                "delivery_value": score,
                "mcap_rank": mcap_rank,
                "mcap_cr": mcap_cr,
                "sma_50": round(cur_sma50, 2) if cur_sma50 else None,
                "sma_200": round(cur_sma200, 2) if cur_sma200 else None,
                "stop_level": round(stop_level, 2) if stop_level else None,
            })

        # ── Persist state (live only) ────────────────────────────────
        if conn_meta is not None:
            now = pd.Timestamp.now().isoformat()

            # Upsert surviving positions
            for sym, state in final_state.items():
                upsert_position(
                    conn_meta, sym,
                    entry_date=state["entry_date"],
                    entry_price=state["entry_price"],
                    ever_activated=state["ever_activated"],
                    highest_close=state["highest_close"],
                    updated_at=now,
                )

            # Remove exited positions
            if exited_symbols:
                delete_positions(conn_meta, exited_symbols)

            conn_meta.close()

        result = pd.DataFrame(candidates)
        if not result.empty:
            # Separate active positions from new signals
            active = result[result["phase"].isin(["PENDING", "TRAILING"])]
            new_signals = result[result["phase"] == "NEW"]
            exited = result[result["phase"] == "EXITED"]

            # Sort active by phase (PENDING first, then TRAILING), then by pnl
            if not active.empty:
                active = active.sort_values(
                    ["phase", "pnl_pct"], ascending=[True, False]
                )
            # Sort new signals by delivery_value (descending)
            if not new_signals.empty:
                new_signals = new_signals.sort_values(
                    "delivery_value", ascending=False
                )

            result = pd.concat([active, new_signals, exited], ignore_index=True)

        n_pending = int((result["phase"] == "PENDING").sum()) if not result.empty else 0
        n_trailing = int((result["phase"] == "TRAILING").sum()) if not result.empty else 0
        n_new = int((result["phase"] == "NEW").sum()) if not result.empty else 0
        logger.info(
            "Super Breakout scan complete: %d candidates "
            "(%d PENDING, %d TRAILING, %d NEW) — %s",
            len(candidates),
            n_pending,
            n_trailing,
            n_new,
            self.validation_caveat,
        )
        return result

    def scan_near_trigger(
        self,
        as_on_date: Optional[str] = None,
        band_pct: float = 5.0,
    ) -> pd.DataFrame:
        """Surface stocks approaching the SMA(50) crossover but not yet crossed.

        Returns symbols where:
        - Precondition holds: close > SMA(5), SMA(10), SMA(15)
        - Context holds: close < SMA(200), SMA(50) < SMA(200)
        - Close is below SMA(50) but within band_pct% of it
        - No existing entry in sb_positions (not already in the lifecycle)

        Sorted by proximity to SMA(50) (closest first).
        """
        as_of = self._resolve_as_on_date(as_on_date)
        universe = self._get_universe()
        if not universe:
            return pd.DataFrame()

        min_date = f"{(pd.Timestamp(as_of) - pd.Timedelta(days=545)):%Y-%m-%d}"

        # Load persisted positions — exclude symbols already in the lifecycle
        from myra_app.strategies.super_breakout_state import (
            connect_meta,
            load_positions,
        )

        active_positions: set[str] = set()
        try:
            conn_meta = connect_meta(self.state_db)
            active_positions = set(load_positions(conn_meta).keys())
            conn_meta.close()
        except Exception:
            pass

        results: list[dict] = []
        for sym, mcap_rank, mcap_cr in universe:
            if sym in active_positions:
                continue

            rows = self._get_tech_data(sym, min_date, max_date=as_of)
            if len(rows) < SMA_SLOW + 1:
                continue

            closes = np.array([r[4] for r in rows], dtype=float)
            delivery_vals = np.array(
                [float(r[6]) if r[6] is not None else 0.0 for r in rows], dtype=float
            )

            # Compute SMAs at the latest bar
            sma5 = _rolling_mean(closes, 5)
            sma10 = _rolling_mean(closes, 10)
            sma15 = _rolling_mean(closes, 15)
            sma50 = _rolling_mean(closes, SMA_FAST)
            sma200 = _rolling_mean(closes, SMA_SLOW)

            close = float(closes[-1])
            cur_sma50 = float(sma50[-1])
            cur_sma200 = float(sma200[-1])

            if np.isnan(cur_sma50) or np.isnan(cur_sma200):
                continue
            if np.isnan(sma5[-1]) or np.isnan(sma10[-1]) or np.isnan(sma15[-1]):
                continue

            # Reuse canonical precondition + context check
            if not check_precondition_context(
                close, float(sma5[-1]), float(sma10[-1]),
                float(sma15[-1]), cur_sma50, cur_sma200,
            ):
                continue

            # Must be BELOW SMA(50) (not yet triggered)
            if close >= cur_sma50:
                continue

            # Proximity band: within band_pct% below SMA(50)
            pct_to_trigger = (cur_sma50 - close) / close * 100
            if pct_to_trigger <= 0 or pct_to_trigger > band_pct:
                continue

            results.append({
                "symbol": sym,
                "close": round(close, 2),
                "sma_50": round(cur_sma50, 2),
                "sma_200": round(cur_sma200, 2),
                "pct_to_trigger": round(pct_to_trigger, 2),
                "delivery_value": round(float(delivery_vals[-1]), 0),
                "mcap_rank": mcap_rank,
                "mcap_cr": round(mcap_cr, 2),
            })

        df = pd.DataFrame(results)
        if not df.empty:
            df = df.sort_values("pct_to_trigger").reset_index(drop=True)
        return df
