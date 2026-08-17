import logging
import math
from myra_app.strategies.scanner_utils import sanitize_float
import sqlite3
import os
import numpy as np
import pandas as pd
from datetime import date
from myra_app.constants import DB_DIR
from myra_app.librarian_core import LibrarianCore
from myra_app.db.bulk_loader import (
    load_ohlcv_for_universe,
    rows_for_symbol,
    COLUMNS_12,
)

logger = logging.getLogger(__name__)


class TriggerScanner:
    _bulk_data = None
    _BULK_COLUMNS = COLUMNS_12

    def __init__(
        self,
        min_mcap=300,
        max_mcap=50000,
        min_float_util_pct=8.0,
        vol_pinch_ratio=0.75,
        price_range_max_pct=10.0,
        min_smart_float_ratio=0.55,
    ):
        self.min_mcap = min_mcap
        self.max_mcap = max_mcap
        self.min_float_util_pct = min_float_util_pct
        self.vol_pinch_ratio = vol_pinch_ratio
        self.price_range_max_pct = price_range_max_pct
        self.min_smart_float_ratio = min_smart_float_ratio

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
                       COALESCE(f.free_float_pct, 40.0) AS ff_pct,
                       COALESCE(f.promoter_holding_pct, 0.0) AS promoter_pct,
                       f.promoter_holding_pct AS raw_promoter_pct,
                       f.free_float_pct AS raw_ff_pct
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
        if self._bulk_data is not None:
            return rows_for_symbol(
                self._bulk_data, symbol, self._BULK_COLUMNS, min_date, max_date
            )
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
        return sanitize_float(value)

    def scan(self, as_on_date: str | None = None) -> list[dict]:
        rows = self._get_universe()
        if not rows:
            logger.warning(
                "No symbols found in universe (mcap %.0f-%.0f Cr)",
                self.min_mcap,
                self.max_mcap,
            )
            return []

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
        min_date = f"{(ref_date - pd.Timedelta(days=45)):%Y-%m-%d}"

        # Single bulk load replaces per-symbol sqlite connections.
        self._bulk_data = load_ohlcv_for_universe(min_date, as_on_date)

        candidates: list[dict] = []

        for idx, (
            symbol,
            mcap,
            ff_pct,
            promoter_pct,
            raw_promoter_pct,
            raw_ff_pct,
        ) in enumerate(rows):
            symbol = symbol.strip()

            tech = self._get_tech_data(symbol, min_date, max_date=as_on_date)
            if len(tech) < 25:
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

            if len(df) < 25:
                continue

            latest_close = float(df["close"].iloc[-1])
            if latest_close <= 0:
                continue

            # Skip symbols where we lack both free-float and promoter data
            raw_ff = raw_ff_pct
            raw_prom = raw_promoter_pct
            if (raw_ff is None or raw_ff <= 0) and (raw_prom is None or raw_prom <= 0):
                continue

            shares_total_approx = mcap / latest_close
            available_float_pct = (
                ff_pct if ff_pct > 0 else max(5.0, 100 - promoter_pct - 15)
            )
            free_float_shares = shares_total_approx * available_float_pct / 100
            if free_float_shares <= 0:
                continue

            # Gate 1: Float Absorption (Supply Physics)
            w20 = df.tail(20)
            cum_delivery_20d = float(np.nansum(w20["delivery"].values.astype(float)))
            float_util_pct = cum_delivery_20d / free_float_shares * 100

            gate1_pass = float_util_pct >= self.min_float_util_pct

            gate1_score = min(100.0, float_util_pct / 40.0 * 100)

            # Gate 2: Seller Extinction (Behavioural)
            w15 = df.tail(15).reset_index(drop=True)
            closes_15 = w15["close"].values.astype(float)
            del_pcts_15 = w15["delivery_pct"].values.astype(float)

            prev_closes = np.roll(closes_15, 1)
            prev_closes[0] = closes_15[0]
            session_returns = (closes_15 - prev_closes) / prev_closes * 100

            down_idx = np.where(session_returns < -0.15)[0]
            down_del = del_pcts_15[down_idx]

            if len(down_idx) < 3:
                gate2_pass = True
                gate2_score = 70.0
                seller_slope = 0.0
                avg_down_del = float(np.nanmean(del_pcts_15)) * 0.85
            else:
                x = np.arange(len(down_del), dtype=float)
                if len(down_del) >= 2:
                    seller_slope = float(np.polyfit(x, down_del, 1)[0])
                else:
                    seller_slope = 0.0
                avg_down_del = float(np.nanmean(down_del))

                cond_a = seller_slope < -0.20
                cond_b = avg_down_del < 38.0
                gate2_pass = cond_a or cond_b

                slope_score = (
                    min(50.0, max(0.0, -seller_slope / 0.25 * 50)) if cond_a else 0.0
                )
                avg_score = (
                    min(50.0, max(0.0, (45 - avg_down_del) / 45 * 50))
                    if cond_b
                    else 0.0
                )
                gate2_score = min(100.0, slope_score + avg_score)

            # Gate 3: Volume Pinch (Mechanical)
            w20_vols = df["volume"].values.astype(float)[-20:]
            w5_vols = w20_vols[-5:]
            w20_highs = df["high"].values.astype(float)[-20:]
            w5_highs = w20_highs[-5:]
            w5_lows = df["low"].values.astype(float)[-5:]

            vol_ratio_5_20 = (
                float(np.nanmean(w5_vols)) / float(np.nanmean(w20_vols))
                if np.nanmean(w20_vols) > 0
                else 1.0
            )

            price_range_5d_pct = (
                (float(np.nanmax(w5_highs)) - float(np.nanmin(w5_lows)))
                / latest_close
                * 100
                if latest_close > 0
                else 99.0
            )

            gate3_pass = (
                vol_ratio_5_20 < self.vol_pinch_ratio
                and price_range_5d_pct < self.price_range_max_pct
            )

            vol_score = min(
                50.0,
                max(
                    0.0,
                    (self.vol_pinch_ratio - vol_ratio_5_20)
                    / self.vol_pinch_ratio
                    * 50
                    / 0.5,
                ),
            )
            range_score = min(
                50.0,
                max(
                    0.0,
                    (self.price_range_max_pct - price_range_5d_pct)
                    / self.price_range_max_pct
                    * 50
                    / 0.5,
                ),
            )
            gate3_score = min(100.0, vol_score + range_score)

            if not (gate1_pass and gate2_pass and gate3_pass):
                continue

            # Gate 4: Smart Float Ratio — delivery must be concentrated on up days
            recent = df.tail(20)
            cum_delivery = float(np.nansum(recent["delivery"].values.astype(float)))
            up_day_delivery = 0
            for j in range(len(recent)):
                if recent.iloc[j]["close"] > recent.iloc[j]["open"]:
                    up_day_delivery += recent.iloc[j]["delivery"]
            smart_float_ratio = (
                up_day_delivery / cum_delivery if cum_delivery > 0 else 0.0
            )

            if smart_float_ratio < self.min_smart_float_ratio:
                continue

            # Bonus Signals
            w20_df = df.tail(20)
            opens20 = w20_df["open"].values.astype(float)
            closes20 = w20_df["close"].values.astype(float)
            highs20 = w20_df["high"].values.astype(float)
            dels20 = w20_df["delivery_pct"].values.astype(float)

            defense_bars = 0
            for i in range(1, len(w20_df)):
                gap_down = (opens20[i] - closes20[i - 1]) / closes20[i - 1] * 100
                recovery = (
                    (closes20[i] - opens20[i]) / (highs20[i] - opens20[i] + 0.01) * 100
                )
                if gap_down < -0.3 and recovery > 50 and dels20[i] > 50:
                    defense_bars += 1

            # Base Duration
            base_duration = 0
            all_highs = df["high"].values.astype(float)
            all_lows = df["low"].values.astype(float)
            all_closes = df["close"].values.astype(float)
            for i in range(len(df) - 1, -1, -1):
                rng_pct = (
                    (all_highs[i] - all_lows[i]) / all_closes[i] * 100
                    if all_closes[i] > 0
                    else 99.0
                )
                if rng_pct < 3.5:
                    base_duration += 1
                else:
                    break

            # Breakout proximity
            w20_highs_vals = df["high"].values.astype(float)[-20:]
            base_high_20 = float(np.nanmax(w20_highs_vals))
            base_low_20 = float(np.nanmin(df["low"].values.astype(float)[-20:]))
            breakout_prox = (
                (latest_close - base_low_20) / (base_high_20 - base_low_20)
                if (base_high_20 - base_low_20) > 0
                else 0.5
            )

            # Trigger Score
            trigger_score = (
                gate1_score * 0.30
                + gate2_score * 0.25
                + gate3_score * 0.25
                + defense_bars * 4.0
                + breakout_prox * 10.0
                + min(base_duration, 10) * 0.5
            )
            trigger_score = min(100.0, trigger_score)

            if trigger_score >= 75:
                grade = "A"
            elif trigger_score >= 55:
                grade = "B"
            elif trigger_score >= 35:
                grade = "C"
            else:
                grade = "D"

            # 52-week position
            high_52w = (
                float(df["high_52w"].iloc[-1])
                if pd.notna(df["high_52w"].iloc[-1])
                else float(df["high"].max())
            )
            low_52w = (
                float(df["low_52w"].iloc[-1])
                if pd.notna(df["low_52w"].iloc[-1])
                else float(df["low"].min())
            )
            wk52_pos = (
                (latest_close - low_52w) / (high_52w - low_52w) * 100
                if (high_52w - low_52w) > 0
                else 50.0
            )

            candidates.append(
                {
                    "symbol": symbol,
                    "sector": _sector_map.get(symbol, "Unknown"),
                    "market_cap_cr": round(mcap / 1e7, 1),
                    "float_util_pct": round(float_util_pct, 1),
                    "gate1_score": round(gate1_score, 1),
                    "avg_down_del": round(avg_down_del, 1),
                    "seller_slope": round(seller_slope, 3),
                    "gate2_score": round(gate2_score, 1),
                    "vol_ratio_5_20": round(vol_ratio_5_20, 3),
                    "price_range_5d_pct": round(price_range_5d_pct, 2),
                    "gate3_score": round(gate3_score, 1),
                    "smart_float_ratio": round(smart_float_ratio, 3),
                    "defense_bars": defense_bars,
                    "base_duration": base_duration,
                    "breakout_prox": round(breakout_prox, 3),
                    "trigger_score": round(trigger_score, 1),
                    "grade": grade,
                    "close": round(latest_close, 2),
                    "wk52_pos": round(wk52_pos, 1),
                }
            )

        float_fields = [
            "market_cap_cr",
            "float_util_pct",
            "gate1_score",
            "avg_down_del",
            "seller_slope",
            "gate2_score",
            "vol_ratio_5_20",
            "price_range_5d_pct",
            "gate3_score",
            "smart_float_ratio",
            "trigger_score",
            "close",
            "wk52_pos",
        ]
        for c in candidates:
            for f in float_fields:
                if f in c:
                    c[f] = self._sanitize_float(c[f])

        candidates.sort(key=lambda x: x["trigger_score"], reverse=True)
        logger.info("Trigger scan complete: %d candidates found", len(candidates))
        return candidates
