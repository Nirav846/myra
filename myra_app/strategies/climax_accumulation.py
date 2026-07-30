"""Climax Accumulation Scanner
=============================
Identifies stocks where a high-volume distribution climax was followed by
consolidation with rising delivery. The climax low acts as a structural
reference level — not a standalone entry signal.

2-year backtest (219 signals, entry at day 15):
  All signals: -0.6% gross / -2.0% net 40-day return, 42% win rate.
  Test set (Mar 2026+): +4.2% gross, 54% win rate.
  Second-chance (broke low, then recovered): entry at LOWEST point after
  break produced +35.5% avg 40-day return, 67% win rate.
"""

import logging
import math
import sqlite3
import os
from datetime import date
from typing import Optional

import numpy as np
import pandas as pd

from myra_app.constants import DB_DIR
from myra_app.librarian_core import LibrarianCore

logger = logging.getLogger(__name__)


class ClimaxAccumulationScanner:
    """Scan for climax accumulation setups in NSE stocks."""

    def __init__(
        self,
        min_mcap: float = 200,
        max_mcap: float = 50000,
        lookback_days: int = 90,
        vol_ratio_threshold: float = 10.0,
        delivery_pct_ceiling: float = 15.0,
        post_climax_min: int = 3,
        post_climax_max: int = 15,
        target_date: Optional[str] = None,
    ):
        self.min_mcap = min_mcap
        self.max_mcap = max_mcap
        self.lookback_days = lookback_days
        self.vol_ratio_threshold = vol_ratio_threshold
        self.delivery_pct_ceiling = delivery_pct_ceiling
        self.post_climax_min = post_climax_min
        self.post_climax_max = post_climax_max
        self.target_date = target_date

    # ------------------------------------------------------------------
    # DB helpers
    # ------------------------------------------------------------------

    def _db_path(self, key: str) -> str:
        return os.path.join(DB_DIR, LibrarianCore.DB_MAP[key])

    def _get_universe(self) -> list[tuple]:
        """Fetch symbols from valuation DB filtered by market cap."""
        val_db = self._db_path("valuation")
        if not os.path.exists(val_db):
            return []
        with sqlite3.connect(val_db) as conn:
            rows = conn.execute(
                """
                SELECT f.symbol,
                       COALESCE(f.market_cap, 0) AS mcap
                FROM fundamentals f
                INNER JOIN (
                    SELECT symbol, MAX(date) as max_date
                    FROM fundamentals
                    WHERE COALESCE(market_cap, 0) > 0
                    GROUP BY symbol
                ) latest ON f.symbol = latest.symbol AND f.date = latest.max_date
                WHERE COALESCE(f.market_cap, 0) / 1e7 BETWEEN ? AND ?
                """,
                (self.min_mcap, self.max_mcap),
            ).fetchall()
        return rows

    def _get_tech_data(
        self, symbol: str, min_date: str, max_date: Optional[str] = None
    ) -> list[tuple]:
        """Fetch technical_data rows for a symbol."""
        tech_db = self._db_path("technical")
        if not os.path.exists(tech_db):
            return []
        with sqlite3.connect(tech_db) as conn:
            try:
                if max_date:
                    rows = conn.execute(
                        """
                        SELECT date, open, high, low, close, volume, delivery,
                               delivery_pct
                        FROM technical_data
                        WHERE symbol = ? AND date >= ? AND date <= ?
                        ORDER BY date ASC
                        """,
                        (symbol, min_date, max_date),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        """
                        SELECT date, open, high, low, close, volume, delivery,
                               delivery_pct
                        FROM technical_data
                        WHERE symbol = ? AND date >= ?
                        ORDER BY date ASC
                        """,
                        (symbol, min_date),
                    ).fetchall()
            except sqlite3.OperationalError:
                if max_date:
                    rows = conn.execute(
                        """
                        SELECT date, open, high, low, close, volume, delivery,
                               delivery_pct
                        FROM technical_data
                        WHERE symbol = ? AND date >= ? AND date <= ?
                        ORDER BY date ASC
                        """,
                        (symbol, min_date, max_date),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        """
                        SELECT date, open, high, low, close, volume, delivery,
                               delivery_pct
                        FROM technical_data
                        WHERE symbol = ? AND date >= ?
                        ORDER BY date ASC
                        """,
                        (symbol, min_date),
                    ).fetchall()
        return rows

    # ------------------------------------------------------------------
    # Core logic
    # ------------------------------------------------------------------

    def _find_climax_days(self, df: pd.DataFrame) -> pd.DataFrame:
        """Identify climax days: vol_ratio >= threshold AND delivery_pct < ceiling.

        vol_ratio = volume / 20d SMA(volume).
        Returns rows where the climax conditions are met.
        """
        if len(df) < 20:
            return pd.DataFrame()

        df = df.copy()
        df["vol_sma20"] = df["volume"].rolling(window=20, min_periods=20).mean()
        df["vol_ratio"] = df["volume"] / df["vol_sma20"].replace(0, float("nan"))

        climax_mask = (
            (df["vol_ratio"] >= self.vol_ratio_threshold)
            & (df["delivery_pct"] < self.delivery_pct_ceiling)
            & (df["vol_sma20"].notna())
        )
        return df[climax_mask].copy()

    def _process_climax(
        self, df: pd.DataFrame, climax_row: pd.Series, sector: str
    ) -> dict | None:
        """Process a single climax day into a candidate record.

        Logic:
        1. Climax week low = MIN(low) over climax day ±2 trading days.
        2. Post-climax window: next 3-15 trading days.
        3. Stock qualifies if: low never broke below climax_low,
           OR broke below but recovered back above within 15 days.
        4. Delivery must be rising: del_start (first 30%) < del_end (last 30%).
        5. dist_pct = (climax_high / latest_close - 1) * 100.
        """
        climax_idx = df.index.get_loc(climax_row.name)
        n = len(df)

        # --- Climax week low (±2 trading days) ---
        window_start = max(0, climax_idx - 2)
        window_end = min(n, climax_idx + 3)  # +3 because slice is exclusive
        climax_week_slice = df.iloc[window_start:window_end]
        climax_low = float(climax_week_slice["low"].min())
        climax_high = float(climax_row["high"])

        # --- Post-climax window (3-15 trading days after climax) ---
        post_start = climax_idx + 1
        post_end = min(n, climax_idx + 1 + self.post_climax_max)  # max 15 days

        if post_end - post_start < self.post_climax_min:
            return None  # not enough post-climax data

        post_slice = df.iloc[post_start:post_end]
        post_lows = post_slice["low"].values.astype(float)
        post_highs = post_slice["high"].values.astype(float)
        post_closes = post_slice["close"].values.astype(float)
        post_delivery = post_slice["delivery_pct"].values.astype(float)

        # --- Second chance detection ---
        second_chance = False
        broke_low = False
        days_to_lowest: int | None = None
        for i, low_val in enumerate(post_lows):
            if low_val < climax_low:
                broke_low = True
                # Check if recovered back above climax_low within remaining days
                remaining_highs = post_highs[i:]
                if any(h >= climax_low for h in remaining_highs):
                    second_chance = True
                    # Find how many days from break to the lowest low after it
                    remaining_lows = post_lows[i:]
                    lowest_idx = int(np.argmin(remaining_lows))
                    days_to_lowest = (
                        lowest_idx + 1
                    )  # 1-indexed (day 1 = break day itself)
                break

        # --- Qualification ---
        # Option A: never broke below climax_low (clean base)
        # Option B: broke below but recovered (second chance)
        clean_base = not broke_low
        if not clean_base and not second_chance:
            return None  # broke and didn't recover — invalid

        # --- Delivery rising ---
        del_arr = post_delivery
        if len(del_arr) < 3:
            return None

        third = max(1, len(del_arr) // 3)
        del_start = float(del_arr[:third].mean()) if len(del_arr[:third]) > 0 else 0.0
        del_end = float(del_arr[-third:].mean()) if len(del_arr[-third:]) > 0 else 0.0
        del_delta = del_end - del_start

        if del_end <= del_start:
            return None  # delivery not rising

        # --- Latest close & distance ---
        latest_close = float(df.iloc[-1]["close"])
        if latest_close <= 0:
            return None
        dist_pct = (climax_high / latest_close - 1) * 100

        # --- Base days (post-climax window length) ---
        base_days = post_end - post_start

        return {
            "symbol": df.iloc[0]["symbol"] if "symbol" in df.columns else None,
            "sector": sector,
            "climax_date": str(climax_row["date"]),
            "base_days": base_days,
            "trigger_price": round(climax_high, 2),
            "last_close": round(latest_close, 2),
            "dist_pct": round(dist_pct, 2),
            "del_start": round(del_start, 1),
            "del_end": round(del_end, 1),
            "del_delta": round(del_delta, 1),
            "sl_price": round(climax_low, 2),
            "second_chance": second_chance,
            "days_to_lowest": days_to_lowest,
        }

    # ------------------------------------------------------------------
    # Main scan
    # ------------------------------------------------------------------

    def scan(self, as_on_date: str | None = None) -> pd.DataFrame:
        """Run the climax accumulation scan across the universe."""
        universe = self._get_universe()
        if not universe:
            logger.warning(
                "No symbols found in universe (mcap %.0f-%.0f Cr)",
                self.min_mcap,
                self.max_mcap,
            )
            return pd.DataFrame()

        # Build sector map
        _sector_map: dict[str, str] = {}
        try:
            val_db = self._db_path("valuation")
            with sqlite3.connect(val_db) as _sc:
                _sec_rows = _sc.execute(
                    """
                    SELECT f.symbol, f.sector
                    FROM fundamentals f
                    INNER JOIN (
                        SELECT symbol, MAX(date) as max_date
                        FROM fundamentals
                        WHERE sector IS NOT NULL
                        GROUP BY symbol
                    ) latest ON f.symbol = latest.symbol AND f.date = latest.max_date
                    WHERE f.sector IS NOT NULL
                    """
                ).fetchall()
                _sector_map = {r[0].strip(): r[1] for r in _sec_rows}
        except Exception:
            pass

        if as_on_date is None:
            as_on_date = self.target_date or date.today().isoformat()

        ref_date = pd.Timestamp(as_on_date)
        min_date = f"{(ref_date - pd.Timedelta(days=self.lookback_days + 60)):%Y-%m-%d}"

        candidates: list[dict] = []

        for symbol, _mcap in universe:
            symbol = symbol.strip()
            rows = self._get_tech_data(symbol, min_date, as_on_date)
            if len(rows) < 30:
                continue

            df = pd.DataFrame(
                rows,
                columns=[
                    "date",
                    "open",
                    "high",
                    "low",
                    "close",
                    "volume",
                    "delivery",
                    "delivery_pct",
                ],
            )
            for col in [
                "open",
                "high",
                "low",
                "close",
                "volume",
                "delivery",
                "delivery_pct",
            ]:
                df[col] = pd.to_numeric(df[col], errors="coerce")

            # Filter: close > 50, volume > 500000
            df = df[(df["close"] > 50) & (df["volume"] > 500000)]
            if len(df) < 30:
                continue

            # Find climax days
            climax_df = self._find_climax_days(df)
            if climax_df.empty:
                continue

            # Process each climax day (take the most recent qualifying one)
            best = None
            for _, cday in climax_df.iterrows():
                rec = self._process_climax(df, cday, _sector_map.get(symbol, "Unknown"))
                if rec is None:
                    continue
                if best is None:
                    best = rec
                else:
                    # Prefer the climax day closer to the reference date
                    if rec["climax_date"] > best["climax_date"]:
                        best = rec

            if best is not None:
                best["symbol"] = symbol
                candidates.append(best)

        if not candidates:
            return pd.DataFrame()

        result = pd.DataFrame(candidates)
        result = result.sort_values("dist_pct", ascending=True).reset_index(drop=True)
        return result
