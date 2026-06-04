import logging
import sqlite3
import os
import numpy as np
import pandas as pd
from datetime import datetime, date
from myra_app.constants import DB_DIR
from myra_app.librarian_core import LibrarianCore

logger = logging.getLogger(__name__)


class AccumulationBaseScanner:
    def __init__(
        self,
        base_days=21,
        min_dar=0.2,
        target_dar=None,
        min_mcap=500,
        max_mcap=20000,
        tightness_full_score_pct=None,
        tightness_zero_score_pct=None,
        volume_ratio_strong=1.5,
        volume_ratio_weak=0.8,
        dar_weight=0.35,
        tightness_weight=0.25,
        volume_weight=0.20,
        trend_weight=0.20,
        use_dynamic_bear_flag=True,
    ):
        self.base_days = base_days
        self.min_dar = min_dar
        self.target_dar = target_dar
        self.min_mcap = min_mcap
        self.max_mcap = max_mcap
        self.tightness_full_score_pct = tightness_full_score_pct
        self.tightness_zero_score_pct = tightness_zero_score_pct
        self.volume_ratio_strong = volume_ratio_strong
        self.volume_ratio_weak = volume_ratio_weak
        self.dar_weight = dar_weight
        self.tightness_weight = tightness_weight
        self.volume_weight = volume_weight
        self.trend_weight = trend_weight
        self.use_dynamic_bear_flag = use_dynamic_bear_flag
        self.bear_market = False

    def _db_path(self, key: str) -> str:
        return os.path.join(DB_DIR, LibrarianCore.DB_MAP[key])

    def _get_universe(self) -> list[tuple]:
        val_db = self._db_path("valuation")
        if not os.path.exists(val_db):
            logger.warning("Valuation DB not found at %s", val_db)
            return []
        with sqlite3.connect(val_db) as conn:
            rows = conn.execute(
                """
                SELECT symbol, COALESCE(market_cap, 0) as mcap,
                       COALESCE(free_float_pct, 40.0) as ff_pct
                FROM fundamentals
                WHERE COALESCE(market_cap, 0) > 0
                  AND COALESCE(market_cap, 0) / 1e7 BETWEEN ? AND ?
                """,
                (self.min_mcap, self.max_mcap),
            ).fetchall()
        return rows

    def _get_tech_data(self, symbol: str, min_date: str) -> list[tuple]:
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
                    WHERE symbol = ? AND date >= ?
                    ORDER BY date ASC
                    """,
                    (symbol, min_date),
                ).fetchall()
            except sqlite3.OperationalError:
                rows = conn.execute(
                    """
                    SELECT date, open, high, low, close, volume, delivery,
                           delivery_pct, nifty_outperformance_score,
                           NULL AS sma_50, NULL AS high_52w, NULL AS low_52w
                    FROM technical_data
                    WHERE symbol = ? AND date >= ?
                    ORDER BY date ASC
                    """,
                    (symbol, min_date),
                ).fetchall()
        return rows

    def _compute_atr(self, highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int = 14) -> float:
        if len(highs) < 2:
            return 0.0
        tr = np.maximum(highs[1:] - lows[1:],
                        np.maximum(np.abs(highs[1:] - closes[:-1]),
                                   np.abs(lows[1:] - closes[:-1])))
        if len(tr) < period:
            return float(np.mean(tr)) if len(tr) > 0 else 0.0
        return float(np.mean(tr[-period:]))

    def _compute_sma(self, closes: np.ndarray, period: int = 50) -> float:
        if len(closes) < period:
            return float(np.mean(closes)) if len(closes) > 0 else 0.0
        return float(np.mean(closes[-period:]))

    def _compute_linear_slope(self, values: np.ndarray) -> float:
        n = len(values)
        if n < 2:
            return 0.0
        x = np.arange(n)
        if np.std(x) == 0:
            return 0.0
        slope = np.polyfit(x, values, 1)[0]
        return slope

    def scan(self, as_on_date: str | None = None) -> pd.DataFrame:
        rows = self._get_universe()
        if not rows:
            logger.warning("No symbols found in universe (mcap %.0f-%.0f Cr)", self.min_mcap, self.max_mcap)
            return pd.DataFrame()

        if as_on_date is None:
            as_on_date = date.today().isoformat()

        ref_date = pd.Timestamp(as_on_date)
        min_date = (ref_date - pd.Timedelta(days=max(self.base_days * 3, 90))).strftime("%Y-%m-%d")

        nifty_scores_all: list[float] = []
        candidates: list[dict] = []

        # Auto-scale tightness thresholds with sqrt(base_days)
        effective_full = self.tightness_full_score_pct
        effective_zero = self.tightness_zero_score_pct
        if effective_full is None:
            effective_full = 3.0 * np.sqrt(self.base_days / 21)
        if effective_zero is None:
            effective_zero = 8.0 * np.sqrt(self.base_days / 21)

        for idx, (symbol, mcap, ff_pct) in enumerate(rows):
            symbol = symbol.strip()
            ff_mcap = mcap * ff_pct / 100.0

            # Auto-bucket DAR target by free-float market cap
            effective_target_dar = self.target_dar
            if effective_target_dar is None:
                free_float_mcap_cr = ff_mcap / 1e7
                if free_float_mcap_cr < 1000:
                    effective_target_dar = 1.0
                elif free_float_mcap_cr < 5000:
                    effective_target_dar = 0.6
                elif free_float_mcap_cr < 20000:
                    effective_target_dar = 0.35
                else:
                    effective_target_dar = 0.20

            tech = self._get_tech_data(symbol, min_date)
            if len(tech) < self.base_days:
                continue

            col_count = len(tech[0]) if tech else 0
            if col_count >= 12:
                df = pd.DataFrame(
                    tech,
                    columns=["date", "open", "high", "low", "close", "volume",
                             "delivery", "delivery_pct", "nifty_outperformance_score",
                             "sma_50", "high_52w", "low_52w"],
                )
            else:
                df = pd.DataFrame(
                    tech,
                    columns=["date", "open", "high", "low", "close", "volume",
                             "delivery", "delivery_pct", "nifty_outperformance_score"],
                )
                df["sma_50"] = None
                df["high_52w"] = None
                df["low_52w"] = None
            df["date"] = pd.to_datetime(df["date"])
            df = df.sort_values("date").reset_index(drop=True)

            base = df.iloc[-self.base_days:].copy()
            if len(base) < self.base_days:
                continue

            closes = base["close"].values.astype(float)
            highs = base["high"].values.astype(float)
            lows = base["low"].values.astype(float)
            volumes = base["volume"].values.astype(float)
            deliveries = base["delivery"].values.astype(float)
            del_pcts = base["delivery_pct"].values.astype(float)
            nifty_scores = base["nifty_outperformance_score"].values.astype(float)

            for sc in nifty_scores:
                if not np.isnan(sc):
                    nifty_scores_all.append(sc)

            # 1. DAR: Delivery Absorption Rate
            dar_values = np.where(
                ff_mcap > 0,
                (deliveries * closes) / ff_mcap * 100,
                0.0,
            )
            dar_median = float(np.median(dar_values))

            if dar_median < self.min_dar:
                continue

            # 2. Base Tightness
            price_range_pct = ((highs.max() - lows.min()) / lows.min()) * 100
            tightness = price_range_pct / np.sqrt(self.base_days)
            if tightness <= effective_full:
                tightness_score = 100.0
            elif tightness >= effective_zero:
                tightness_score = 0.0
            else:
                tightness_score = (
                    (effective_zero - tightness)
                    / (effective_zero - effective_full)
                    * 100
                )

            # 3. Delivery Trend (linear slope of delivery_pct over 60 days)
            del_trend_window = min(60, len(df))
            del_trend_data = df.iloc[-del_trend_window:]["delivery_pct"].values.astype(float)
            delivery_slope = self._compute_linear_slope(del_trend_data)
            if delivery_slope >= 0.10:
                delivery_trend_score = 100.0
            elif delivery_slope <= -0.10:
                delivery_trend_score = 0.0
            else:
                delivery_trend_score = ((delivery_slope + 0.10) / 0.20) * 100.0

            # 4. Volume Character: median up-day volume / median down-day volume
            up_days = base[base["close"] >= base["close"].shift(1).fillna(base["close"].iloc[0])]
            down_days = base[base["close"] < base["close"].shift(1).fillna(base["close"].iloc[0])]
            up_vol = up_days["volume"].values.astype(float) if len(up_days) > 0 else np.array([])
            down_vol = down_days["volume"].values.astype(float) if len(down_days) > 0 else np.array([])
            up_vol_med = float(np.median(up_vol)) if len(up_vol) > 0 else 0
            down_vol_med = float(np.median(down_vol)) if len(down_vol) > 0 else 1
            volume_ratio = up_vol_med / down_vol_med if down_vol_med > 0 else 1.0

            if volume_ratio >= self.volume_ratio_strong:
                volume_score = 100.0
            elif volume_ratio <= self.volume_ratio_weak:
                volume_score = 0.0
            else:
                volume_score = (
                    (volume_ratio - self.volume_ratio_weak)
                    / (self.volume_ratio_strong - self.volume_ratio_weak)
                    * 100
                )

            # 5. 52-week position
            latest_row = df.iloc[-1]
            high_52w = float(latest_row["high_52w"]) if pd.notna(latest_row.get("high_52w")) else None
            low_52w = float(latest_row["low_52w"]) if pd.notna(latest_row.get("low_52w")) else None
            latest_close = float(closes[-1])
            if high_52w is not None and low_52w is not None:
                wk52_pos = ((latest_close - low_52w) / (high_52w - low_52w)) * 100 if (high_52w - low_52w) > 0 else 50
            else:
                all_tech = df.copy()
                all_high = float(all_tech["high"].max())
                all_low = float(all_tech["low"].min())
                wk52_pos = ((latest_close - all_low) / (all_high - all_low)) * 100 if (all_high - all_low) > 0 else 50
            position_penalty = 0
            if wk52_pos > 75:
                position_penalty = 20
            elif wk52_pos < 15:
                position_penalty = 15

            # 6. Price vs 50-SMA
            all_closes = df["close"].values.astype(float)
            sma50_pre = float(latest_row["sma_50"]) if pd.notna(latest_row.get("sma_50")) else None
            if sma50_pre is not None:
                sma50 = sma50_pre
            else:
                # Use full history (all_closes) for a valid 50-day SMA fallback,
                # not the base window which is only base_days bars.
                sma50 = self._compute_sma(all_closes, 50)
            if sma50 > 0 and latest_close < sma50 * 0.95:
                continue
            sma_penalty = 0
            if sma50 > 0 and latest_close < sma50 * 0.98:
                sma_penalty = 15

            # Composite Score (0-100)
            # DAR score: 100 at target_dar, capped at 100.
            dar_score = min(1.0, dar_median / effective_target_dar) * 100

            raw_score = (
                dar_score * self.dar_weight
                + tightness_score * self.tightness_weight
                + volume_score * self.volume_weight
                + delivery_trend_score * self.trend_weight
            )
            composite_score = max(0, min(100, raw_score - position_penalty - sma_penalty))

            # Grade
            if composite_score >= 80:
                grade = "A"
            elif composite_score >= 60:
                grade = "B"
            elif composite_score >= 40:
                grade = "C"
            else:
                grade = "D"

            # Entry / SL / Targets
            base_high = float(highs.max())
            base_low = float(lows.min())
            risk = base_high - base_low

            all_closes = df["close"].values.astype(float)
            all_highs = df["high"].values.astype(float)
            all_lows = df["low"].values.astype(float)
            atr14 = self._compute_atr(all_highs, all_lows, all_closes, 14)

            entry = base_high + 0.1 * atr14
            sl = base_low
            t1 = entry + 1.0 * risk
            t2 = entry + 2.5 * risk
            t3 = entry + 5.0 * risk if grade == "A" else None

            # Status
            if latest_close > base_high:
                if dar_median >= self.min_dar * 2:
                    status = "Triggered"
                else:
                    status = "Breakout Pending"
            else:
                status = "In Base"

            mcap_cr = mcap / 1e7
            candidates.append({
                "symbol": symbol,
                "market_cap_cr": round(mcap_cr, 1),
                "base_days": self.base_days,
                "dar_median": round(dar_median, 3),
                "base_range_pct": round(price_range_pct, 2),
                "volume_ratio": round(volume_ratio, 2),
                "delivery_slope": round(delivery_slope, 4),
                "composite_score": round(composite_score, 1),
                "grade": grade,
                "entry": round(entry, 2),
                "sl": round(sl, 2),
                "t1": round(t1, 2),
                "t2": round(t2, 2),
                "t3": round(t3, 2) if t3 is not None else None,
                "status": status,
                "close": round(latest_close, 2),
            })

        # Dynamic bear flag
        self.bear_market = False
        if self.use_dynamic_bear_flag and nifty_scores_all:
            median_nifty = float(np.median(nifty_scores_all))
            if median_nifty < -0.5:
                self.bear_market = True
                logger.info("Bear market detected (median nifty_outperformance=%.2f) — tightening criteria", median_nifty)
                effective_base_days = max(self.base_days, 30)
                effective_min_dar = max(self.min_dar, 0.4)
                candidates = [c for c in candidates
                              if c.get("base_days_override", self.base_days) >= effective_base_days
                              and c["dar_median"] >= effective_min_dar]

        candidates.sort(key=lambda x: x["composite_score"], reverse=True)
        logger.info("Scan complete: %d candidates found", len(candidates))
        return pd.DataFrame(candidates)
