"""Delivery-Divergence Scanner (backend).

Detects **bullish divergence**: price near recent lows while delivery
activity is trending higher — a classic institutional accumulation signal.

Backtested parameter sets (400 symbols, 23 scan dates):

    60d:  price_lookback=20, delivery_period=10, delivery_threshold=1.0
          avg_ret 8.24 %, win_rate 53.1 % (n=335)
   120d:  price_lookback=10, delivery_period= 5, delivery_threshold=0.0
          avg_ret 6.75 %, win_rate 50.0 % (n=484)
   180d:  price_lookback=10, delivery_period= 5, delivery_threshold=1.0
          avg_ret 12.29 %, win_rate 48.0 % (n=479)
"""

from __future__ import annotations

import logging
import math
import os
import sqlite3

import numpy as np
import pandas as pd
from datetime import date

from myra_app.constants import DB_DIR
from myra_app.librarian_core import LibrarianCore
from myra_app.strategies.scanner_utils import sanitize_float
from myra_app.db.bulk_loader import (
    load_ohlcv_for_universe,
    rows_for_symbol,
    COLUMNS_8,
)

logger = logging.getLogger(__name__)

# Backtested presets keyed by horizon label.
HORIZON_PRESETS: dict[str, dict] = {
    "60d":  {"price_lookback": 20, "delivery_period": 10, "delivery_threshold": 1.0},
    "120d": {"price_lookback": 10, "delivery_period":  5, "delivery_threshold": 0.0},
    "180d": {"price_lookback": 10, "delivery_period":  5, "delivery_threshold": 1.0},
}


class DeliveryDivergenceScanner:
    """Scan for bullish price-delivery divergence.

    Parameters
    ----------
    price_lookback : int
        Number of trading days to look back for the price low (default 20).
    delivery_period : int
        Number of trading days over which delivery % is measured (default 10).
    delivery_threshold : float
        Minimum % point increase in delivery_pct to qualify (default 1.0).
    min_mcap / max_mcap : float
        Market-cap filter in ₹ Cr (default 200–50 000).
    horizon : str | None
        If set to one of "60d", "120d", "180d", the three numeric params
        are **overridden** by the backtested preset for that horizon.
    min_abs_delivery_pct : float
        Minimum absolute delivery % on the latest bar (default 0 = off).
    min_adtv_cr : float
        Minimum average daily turnover in ₹ Cr (default 0 = off).
    """

    _bulk_data = None
    _BULK_COLUMNS = COLUMNS_8

    def __init__(
        self,
        price_lookback: int = 20,
        delivery_period: int = 10,
        delivery_threshold: float = 1.0,
        min_mcap: float = 200.0,
        max_mcap: float = 50000.0,
        horizon: str | None = None,
        min_abs_delivery_pct: float = 0.0,
        min_adtv_cr: float = 0.0,
    ):
        # If a preset horizon is given, override the three core params.
        if horizon and horizon in HORIZON_PRESETS:
            preset = HORIZON_PRESETS[horizon]
            price_lookback = preset["price_lookback"]
            delivery_period = preset["delivery_period"]
            delivery_threshold = preset["delivery_threshold"]

        self.price_lookback = price_lookback
        self.delivery_period = delivery_period
        self.delivery_threshold = delivery_threshold
        self.min_mcap = min_mcap
        self.max_mcap = max_mcap
        self.min_abs_delivery_pct = min_abs_delivery_pct
        self.min_adtv_cr = min_adtv_cr
        self.horizon = horizon

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _db_path(self, key: str) -> str:
        return os.path.join(DB_DIR, LibrarianCore.DB_MAP[key])

    def _get_universe(self) -> list[tuple]:
        """Return list of (symbol, mcap) tuples within the market-cap band."""
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
        self, symbol: str, min_date: str, max_date: str | None = None
    ) -> list[tuple]:
        if self._bulk_data is not None:
            return rows_for_symbol(
                self._bulk_data, symbol, self._BULK_COLUMNS, min_date, max_date
            )
        tech_db = self._db_path("technical")
        if not os.path.exists(tech_db):
            return []
        with sqlite3.connect(tech_db) as conn:
            try:
                if max_date:
                    rows = conn.execute(
                        """SELECT date, open, high, low, close, volume, delivery,
                                  delivery_pct
                           FROM technical_data
                           WHERE symbol = ? AND date >= ? AND date <= ?
                           ORDER BY date ASC""",
                        (symbol, min_date, max_date),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        """SELECT date, open, high, low, close, volume, delivery,
                                  delivery_pct
                           FROM technical_data
                           WHERE symbol = ? AND date >= ?
                           ORDER BY date ASC""",
                        (symbol, min_date),
                    ).fetchall()
            except sqlite3.OperationalError:
                if max_date:
                    rows = conn.execute(
                        """SELECT date, open, high, low, close, volume, delivery,
                                  NULL AS delivery_pct
                           FROM technical_data
                           WHERE symbol = ? AND date >= ? AND date <= ?
                           ORDER BY date ASC""",
                        (symbol, min_date, max_date),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        """SELECT date, open, high, low, close, volume, delivery,
                                  NULL AS delivery_pct
                           FROM technical_data
                           WHERE symbol = ? AND date >= ?
                           ORDER BY date ASC""",
                        (symbol, min_date),
                    ).fetchall()
        return rows

    @staticmethod
    def _sanitize(value):
        return sanitize_float(value)

    # ------------------------------------------------------------------
    # Core scan
    # ------------------------------------------------------------------

    def scan(self, as_on_date: str | None = None) -> pd.DataFrame:
        """Run divergence scan and return candidates as a DataFrame."""
        rows = self._get_universe()
        if not rows:
            logger.warning(
                "DDivergence: no universe symbols (mcap %.0f–%.0f Cr)",
                self.min_mcap, self.max_mcap,
            )
            return pd.DataFrame()

        if as_on_date is None:
            as_on_date = date.today().isoformat()

        ref_date = pd.Timestamp(as_on_date)

        # We need enough daily bars to cover the longest lookback window
        # plus a small buffer for ADTV and delivery calculations.
        lookback_needed = max(self.price_lookback, self.delivery_period) + 30
        total_calendar_days = int(lookback_needed * 1.8) + 20
        min_date = f"{(ref_date - pd.Timedelta(days=total_calendar_days)):%Y-%m-%d}"

        self._bulk_data = load_ohlcv_for_universe(min_date, as_on_date)

        candidates: list[dict] = []

        for symbol, mcap in rows:
            symbol = symbol.strip()
            try:
                tech = self._get_tech_data(symbol, min_date, max_date=as_on_date)
                if len(tech) < self.price_lookback + 5:
                    continue

                col_count = len(tech[0]) if tech else 0
                if col_count < 8:
                    continue

                df = pd.DataFrame(
                    tech,
                    columns=[
                        "date", "open", "high", "low", "close",
                        "volume", "delivery", "delivery_pct",
                    ],
                )
                df["date"] = pd.to_datetime(df["date"])
                df = df.sort_values("date").reset_index(drop=True)

                if len(df) < self.price_lookback + 5:
                    continue

                # --- Latest bar values ---
                last = df.iloc[-1]
                close = float(last["close"])
                if close <= 0:
                    continue

                latest_del_pct = float(last["delivery_pct"]) if pd.notna(last["delivery_pct"]) else 0.0

                # --- Min absolute delivery filter ---
                if self.min_abs_delivery_pct > 0 and latest_del_pct < self.min_abs_delivery_pct:
                    continue

                # --- Price near low? ---
                price_window = df["close"].values.astype(float)[-self.price_lookback:]
                low_in_window = float(np.min(price_window))
                # "Near the low" = within 3% of the lookback low
                near_low = close <= low_in_window * 1.03

                if not near_low:
                    continue

                # --- Delivery trend ---
                if len(df) < self.delivery_period + 2:
                    continue
                del_window = df["delivery_pct"].values.astype(float)
                # Ensure NaN → 0 for trend calc
                del_window = np.nan_to_num(del_window, nan=0.0)

                latest_del = float(del_window[-1])
                past_del = float(del_window[-(self.delivery_period + 1)])
                delivery_change = latest_del - past_del  # % point change

                if delivery_change < self.delivery_threshold:
                    continue

                # --- ADTV (average daily turnover in ₹ Cr) ---
                adtv_cr = 0.0
                if self.min_adtv_cr > 0:
                    prices = df["close"].values.astype(float)
                    vols = df["volume"].values.astype(float)
                    adtv_cr = float(np.nanmean(prices * vols)) / 1e7
                    if adtv_cr < self.min_adtv_cr:
                        continue

                # --- Strength of divergence ---
                # delivery_change relative to price distance from low
                price_dist_pct = ((close - low_in_window) / low_in_window * 100) if low_in_window > 0 else 0.0
                divergence_strength = delivery_change / max(price_dist_pct, 0.1)

                # --- Score (higher = stronger signal) ---
                score = (
                    delivery_change * 0.5
                    + divergence_strength * 0.3
                    + min(latest_del_pct, 100) * 0.2
                )

                candidates.append({  # noqa: PG-APPEND
                    "symbol": symbol,
                    "close": round(close, 2),
                    "low_in_window": round(low_in_window, 2),
                    "price_dist_pct": round(price_dist_pct, 2),
                    "latest_del_pct": round(latest_del_pct, 2),
                    "delivery_change": round(delivery_change, 2),
                    "divergence_strength": round(divergence_strength, 2),
                    "price_lookback": self.price_lookback,
                    "delivery_period": self.delivery_period,
                    "delivery_threshold": self.delivery_threshold,
                    "adtv_cr": round(adtv_cr, 2) if self.min_adtv_cr > 0 else None,
                    "score": round(score, 2),
                    "divergence_type": "bullish",
                    "horizon": self.horizon or "custom",
                })

            except Exception as e:
                logger.debug("DDivergence: %s failed: %s", symbol, e)
                continue

        # Sanitize float fields
        float_fields = [
            "close", "low_in_window", "price_dist_pct", "latest_del_pct",
            "delivery_change", "divergence_strength", "adtv_cr", "score",
        ]
        for c in candidates:
            for f in float_fields:
                if f in c and c[f] is not None:
                    c[f] = self._sanitize(c[f])

        candidates.sort(key=lambda x: x["score"], reverse=True)

        logger.info(
            "DDivergence scan complete: %d candidates (lb=%d, dp=%d, thr=%.1f)",
            len(candidates),
            self.price_lookback,
            self.delivery_period,
            self.delivery_threshold,
        )
        return pd.DataFrame(candidates)
