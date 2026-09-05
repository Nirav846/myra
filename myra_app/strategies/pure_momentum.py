"""
Pure Momentum baseline strategy.

Phase 1 Task 3 of the backtest refactor.

Purpose
-------
``PureMomentum`` is a *price-only* momentum signal that scores each
candidate symbol by its trailing N-day total return. It is intentionally
trivial — no fundamental filter, no volume screen, no regime gate —
because its job is to serve as a **baseline** that future, more
sophisticated strategies (RS, volume-confirmed, RS-relative) must beat.

Hypothesis
----------
Cross-sectional momentum is positive on average (Jegadeesh & Titman,
1993). On the NSE universe, a 6-month (126 trading-day) lookback has
historically produced positive Q5-Q1 spreads with win rates above 55%.
The pre-implementation backtest in ``_bt_momentum.py`` confirmed:

  * 60d  : spread = +1.34%,  WR = 60.9%
  * 120d : spread = +3.02%,  WR = 68.8%
  * 180d : spread = +12.36%, WR = 65.2%

Protocol
--------
Conforms to ``myra_app.backtest_engine.SignalFunction``:

  * ``requires_delivery: bool = False``  → price-only, full date range.
  * ``score(date, universe, conn) -> pd.Series``  → series indexed by
    symbol, value = momentum score, higher = stronger momentum.

Performance notes
-----------------
* Single bulk fetch with ``WHERE symbol IN (...)`` — no per-symbol N+1.
* Vectorized pandas over the loaded DataFrame, not Python loops.
* Connection opened by the caller (engine) is reused, not re-opened.
* Guarded against: empty universe, missing data, zero volume,
  insufficient history, identical prices.

Edge cases
----------
* Empty universe → empty Series.
* Symbol absent from DB → NaN.
* Symbol with all-zero volume in the lookback window → NaN.
* Symbol with fewer than ``min_history`` rows in the window → NaN.
* First-close == 0 → NaN (avoids divide-by-zero).
* Identical prices (zero return) is allowed (zero is a valid score).
"""
from __future__ import annotations

import sqlite3
from typing import Iterable, Optional

import numpy as np
import pandas as pd


# Threshold below which we treat the lookback as "insufficient history".
# 30 rows ≈ ~6 weeks of trading days — too noisy to trust.
_MIN_HISTORY_ROWS = 30


class PureMomentum:
    """Pure trailing N-day price-momentum signal.

    Attributes
    ----------
    requires_delivery : bool
        False. Momentum is price-only and may use the full historical
        range (TRAIN_START_PRICE_ONLY = 2015-01-01).
    lookback_days : int
        Calendar-day window over which total return is computed. The
        pre-implementation backtest used 126 trading days (≈ 6 months
        using a 0.6 calendar-trading conversion factor → ~210 days,
        but the spec specifies 126 calendar-day lookback directly to
        mirror academic momentum literature). Default 126.
    min_history_rows : int
        Minimum number of rows required inside the lookback window for
        a symbol to receive a score. Below this, return NaN.
    """

    requires_delivery: bool = False
    min_history_rows: int = _MIN_HISTORY_ROWS

    def __init__(self, lookback_days: int = 126) -> None:
        if lookback_days <= 0:
            raise ValueError(
                f"lookback_days must be positive, got {lookback_days}"
            )
        self.lookback_days: int = int(lookback_days)

    # ──────────────────────────────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────────────────────────────

    def score(
        self,
        date: pd.Timestamp,
        universe: Iterable[str],
        conn: sqlite3.Connection,
    ) -> pd.Series:
        """Score each symbol in ``universe`` by trailing total return.

        Parameters
        ----------
        date : pd.Timestamp
            The signal date. The window is ``[date - lookback_days,
            date]`` in calendar days.
        universe : iterable of str
            Candidate symbols. The returned Series is indexed by these
            symbols (any symbols absent from the result get NaN — they
            are excluded from the engine's selection).
        conn : sqlite3.Connection
            Open connection with a ``technical_data(symbol, date,
            close, volume)`` table.

        Returns
        -------
        pd.Series
            Float, indexed by symbol (name="symbol"). Value =
            trailing total return. Higher = stronger momentum.
            NaN = insufficient / missing data, symbol excluded.
        """
        # Normalise universe — preserve order, dedupe.
        sym_list = list(dict.fromkeys(universe))
        if not sym_list:
            return pd.Series(dtype=float, name="symbol")

        as_of = pd.Timestamp(date)
        # Calendar-day window. We add a small buffer so we still find
        # ~``lookback_days`` trading days inside (weekends + holidays
        # compress the count).
        buf_days = int(self.lookback_days * 0.45) + 5  # ~ 45% extra
        start_ts = as_of - pd.Timedelta(days=self.lookback_days + buf_days)
        # noqa: PG-STRFTIME — single Timestamp, f-string per the rule's exception.
        start_s = f"{start_ts.year:04d}-{start_ts.month:02d}-{start_ts.day:02d}"  # noqa: PG-STRFTIME
        as_of_s = f"{as_of.year:04d}-{as_of.month:02d}-{as_of.day:02d}"  # noqa: PG-STRFTIME

        # Bulk single-query fetch — no N+1.
        placeholders = ",".join("?" * len(sym_list))
        rows = conn.execute(
            f"SELECT symbol, date, close, volume "
            f"FROM technical_data "
            f"WHERE symbol IN ({placeholders}) "
            f"AND date BETWEEN ? AND ?",
            (*sym_list, start_s, as_of_s),
        ).fetchall()

        if not rows:
            # No data at all → all NaN.
            return pd.Series(
                np.nan, index=pd.Index(sym_list, name="symbol"), dtype=float
            )

        df = pd.DataFrame(rows, columns=["symbol", "date", "close", "volume"])
        df["date"] = pd.to_datetime(df["date"])

        # Vectorised per-symbol momentum.
        scores = self._compute_per_symbol(df, as_of)

        # Reindex back to the original universe order; any missing
        # symbols stay NaN. Use float dtype so downstream .dropna() works.
        return scores.reindex(sym_list).astype(float).rename("symbol")

    # ──────────────────────────────────────────────────────────────────────
    # Internal helpers
    # ──────────────────────────────────────────────────────────────────────

    def _compute_per_symbol(
        self,
        df: pd.DataFrame,
        as_of: pd.Timestamp,
    ) -> pd.Series:
        """Compute trailing total return per symbol in a vectorised style.

        Strategy
        ~~~~~~~~
        For each symbol we keep rows where:
          * date <= as_of
          * date >= as_of - (lookback_days + buffer)

        Then take the *first* and *last* close inside that window as
        the reference pair. The buffer covers weekends and holidays so
        the calendar window reliably contains at least ``lookback_days``
        trading days.

        Exclusion rules
        ~~~~~~~~~~~~~~~
        * Volume sum over the window == 0 → NaN (illiquid).
        * Row count < ``min_history_rows`` → NaN.
        * First close is 0, NaN, or non-positive → NaN.
        """
        # Discard future rows (shouldn't happen, but guard).
        df = df[df["date"] <= as_of]
        if df.empty:
            return pd.Series(dtype=float, name="symbol")

        # Per-symbol aggregate: first close, last close, total volume, row count.
        # We use groupby on a sorted frame; the "first"/"last" are based on
        # ordering within the group which we ensure below.
        df = df.sort_values(["symbol", "date"])
        agg = df.groupby("symbol", sort=False).agg(
            first_close=("close", "first"),
            last_close=("close", "last"),
            total_volume=("volume", "sum"),
            row_count=("date", "size"),
        )
        # Compute momentum. Vectorised: returns NaN where any guard fails.
        first_close = agg["first_close"].to_numpy(dtype=float)
        last_close = agg["last_close"].to_numpy(dtype=float)
        total_volume = agg["total_volume"].to_numpy(dtype=float)
        row_count = agg["row_count"].to_numpy(dtype=int)

        # Build result array; default NaN.
        n = len(agg)
        ret = np.full(n, np.nan, dtype=float)

        # Mask of valid rows
        valid = (
            np.isfinite(first_close)
            & np.isfinite(last_close)
            & (first_close > 0.0)
            & (total_volume > 0.0)
            & (row_count >= self.min_history_rows)
        )
        ret[valid] = (last_close[valid] / first_close[valid]) - 1.0

        out = pd.Series(ret, index=agg.index, name="symbol")
        return out


# ──────────────────────────────────────────────────────────────────────────────
# Registry hook-up.
# ──────────────────────────────────────────────────────────────────────────────
#
# Importing this module re-binds the ``momentum`` key in
# ``myra_app.backtest_engine.SIGNAL_REGISTRY`` to ``PureMomentum``,
# replacing the legacy ``MomentumSignal`` stub (which returned a
# placeholder 20-day "last close" score). Existing tests that import
# ``MomentumSignal`` directly continue to work because the stub class
# is left in place under its old name — we only re-point the registry.
def _register() -> None:
    """Replace the legacy 'momentum' registry entry with PureMomentum."""
    from myra_app.backtest_engine import SIGNAL_REGISTRY

    SIGNAL_REGISTRY["momentum"] = PureMomentum


_register()