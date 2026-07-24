import logging
import math
import sqlite3
import os
import numpy as np
import pandas as pd
from datetime import date
from typing import Optional

from myra_app.constants import DB_DIR
from myra_app.librarian_core import LibrarianCore

logger = logging.getLogger(__name__)


class BottomHunter:
    def __init__(
        self,
        min_mcap=200,
        max_mcap=50000,
        min_delivery_absorption=5.0,
        adtv_min_cr=1.0,
        lookback_days=260,
    ):
        self.min_mcap = min_mcap
        self.max_mcap = max_mcap
        self.min_delivery_absorption = min_delivery_absorption
        self.adtv_min_cr = adtv_min_cr
        self.lookback_days = lookback_days

    def _db_path(self, key: str) -> str:
        return os.path.join(DB_DIR, LibrarianCore.DB_MAP[key])

    def _get_universe(self) -> list[tuple]:
        val_db = self._db_path("valuation")
        if not os.path.exists(val_db):
            return []
        with sqlite3.connect(val_db) as conn:
            rows = conn.execute(
                """
                SELECT f.symbol,
                       COALESCE(f.market_cap, 0) AS mcap,
                       COALESCE(f.free_float_pct, 40.0) AS ff_pct
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
        max_date = max_date or date.today().isoformat()
        tech_db = self._db_path("technical")
        if not os.path.exists(tech_db):
            return []
        with sqlite3.connect(tech_db) as conn:
            try:
                rows = conn.execute(
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
                rows = conn.execute(
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
        return rows

    @staticmethod
    def _sanitize_float(value):
        if value is None:
            return None
        try:
            if math.isnan(value) or math.isinf(value):
                return None
        except TypeError:
            pass
        return value

    def scan(self, as_on_date: str | None = None) -> pd.DataFrame:
        rows = self._get_universe()
        if not rows:
            logger.warning(
                "No symbols found in universe (mcap %.0f-%.0f Cr)",
                self.min_mcap,
                self.max_mcap,
            )
            return pd.DataFrame()

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
            as_on_date = date.today().isoformat()

        ref_date = pd.Timestamp(as_on_date)
        min_date = f"{(ref_date - pd.Timedelta(days=self.lookback_days + 30)):%Y-%m-%d}"

        candidates: list[dict] = []

        for idx, (symbol, mcap, ff_pct) in enumerate(rows):
            symbol = symbol.strip()

            tech = self._get_tech_data(symbol, min_date, max_date=as_on_date)
            if len(tech) < max(30, int(self.lookback_days * 0.6) + 5):
                continue

            col_count = len(tech[0]) if tech else 0
            if col_count >= 12:
                df = pd.DataFrame(
                    tech,
                    columns=[
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
                    ],
                )
            else:
                df = pd.DataFrame(
                    tech,
                    columns=[
                        "date",
                        "open",
                        "high",
                        "low",
                        "close",
                        "volume",
                        "delivery",
                        "delivery_pct",
                        "nifty_outperformance_score",
                    ],
                )
                df["sma_50"] = None
                df["high_52w"] = None
                df["low_52w"] = None
            df["date"] = pd.to_datetime(df["date"])
            df = df.sort_values("date").reset_index(drop=True)

            if len(df) < max(30, int(self.lookback_days * 0.6) + 5):
                continue

            # Get last 20 days for calculations
            last_20 = df.tail(20)
            if len(last_20) < 20:
                continue

            # Separate up and down days (close > open = up, close < open = down)
            up_days = last_20[last_20["close"] > last_20["open"]]
            down_days = last_20[last_20["close"] < last_20["open"]]

            # Calculate delivery absorption
            up_del_avg = up_days["delivery_pct"].mean() if len(up_days) > 0 else 0
            down_del_avg = down_days["delivery_pct"].mean() if len(down_days) > 0 else 0
            delivery_absorption = up_del_avg - down_del_avg

            # Calculate ADTV (average daily turnover in Cr) over last 20 days
            adtv_cr = ((last_20["close"] * last_20["volume"]) / 1e7).mean()

            # Apply filters
            if adtv_cr < self.adtv_min_cr:
                continue
            if delivery_absorption < self.min_delivery_absorption:
                continue

            # Calculate % above 52w low
            latest_close = float(last_20["close"].iloc[-1])
            high_52w = (
                float(last_20["high_52w"].iloc[-1])
                if pd.notna(last_20["high_52w"].iloc[-1])
                else float(df["high"].max())
            )
            low_52w = (
                float(last_20["low_52w"].iloc[-1])
                if pd.notna(last_20["low_52w"].iloc[-1])
                else float(df["low"].min())
            )
            pct_above_52w_low = (
                ((latest_close - low_52w) / low_52w) * 100
                if low_52w > 0
                else 0
            )

            # Entry signal based on recovery from 52-week low
            if pct_above_52w_low >= 10:
                entry_signal = "Above 10% of 52W Low"
            elif pct_above_52w_low >= 5:
                entry_signal = "Near 52W Low (5-10%)"
            else:
                entry_signal = "At 52W Low (<5%)"

            # Stop-loss: anchor to entry price, not historical lows
            # Compute ATR
            prev_close = last_20["close"].shift(1)
            tr = pd.concat([
                last_20["high"] - last_20["low"],
                (last_20["high"] - prev_close).abs(),
                (last_20["low"] - prev_close).abs()
            ], axis=1).max(axis=1)
            atr_20d = float(tr.mean())
            swing_low_20d = float(last_20["low"].min())

            # Base SL: 2x ATR below entry — always tight and volatility-adjusted
            sl_base = latest_close - 2 * atr_20d
            sl_type = "Entry - 2×ATR"

            # If a relevant swing low exists between entry and 2xATR, use that instead
            if swing_low_20d < latest_close and swing_low_20d > sl_base:
                sl_price = swing_low_20d - atr_20d * 0.5
                sl_type = "Below 20d Swing Low"
                sl_base = swing_low_20d
            else:
                sl_price = sl_base

            candidates.append({
                "symbol": symbol,
                "sector": _sector_map.get(symbol, "Unknown"),
                "close": latest_close,
                "market_cap_cr": mcap / 1e7,
                "delivery_absorption": delivery_absorption,
                "pct_above_52w_low": pct_above_52w_low,
                "adtv_cr": adtv_cr,
                "entry_signal": entry_signal,
                "sl_price": round(sl_price, 2),
                "sl_type": sl_type,
                "swing_low_20d": round(swing_low_20d, 2),
            })

        # Now calculate percentile rank of delivery_absorption for the composite score
        if len(candidates) > 0:
            candidate_df = pd.DataFrame(candidates)
            candidate_df["score"] = (
                candidate_df["delivery_absorption"].rank(pct=True, ascending=True) * 100
            )
            # Assign tier: HIGH >=80, MOD >=50, LOW <50
            candidate_df["tier"] = pd.cut(
                candidate_df["score"],
                bins=[-1, 50, 80, 101],
                labels=["LOW", "MOD", "HIGH"],
                right=False
            ).astype(str)
            # Sanitize floats
            float_fields = [
                "close",
                "market_cap_cr",
                "delivery_absorption",
                "pct_above_52w_low",
                "adtv_cr",
                "score",
                "sl_price",
                "swing_low_20d",
            ]
            for field in float_fields:
                candidate_df[field] = candidate_df[field].apply(self._sanitize_float)
            # Sort by score descending
            candidate_df = candidate_df.sort_values("score", ascending=False).reset_index(drop=True)
        else:
            candidate_df = pd.DataFrame(
                columns=[
                    "symbol",
                    "sector",
                    "close",
                    "market_cap_cr",
                    "delivery_absorption",
                    "pct_above_52w_low",
                    "adtv_cr",
                    "score",
                    "tier",
                ]
            )

        logger.info("Bottom Hunter scan complete: %d candidates found", len(candidate_df))
        return candidate_df
