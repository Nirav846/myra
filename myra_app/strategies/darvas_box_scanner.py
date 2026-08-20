import logging
import math
import sqlite3
import os
import numpy as np
import pandas as pd
from datetime import date
from myra_app.constants import DB_DIR
from myra_app.librarian_core import LibrarianCore
from myra_app.strategies.accumulation_base_scanner import AccumulationBaseScanner
from myra_app.db.bulk_loader import load_ohlcv_for_universe

logger = logging.getLogger(__name__)


# Market-cap tiered thresholds. Keys are explicit so downstream code can
# index by mcap without repeating conditional logic everywhere.
TIER_THRESHOLDS = {
    "small": {
        "max_mcap_cr": 2_000,
        "min_box_age": 5,
        "min_sar": None,
        "min_am": 4.0,
        "breakout_dar_floor": 1.5,
    },
    "mid": {
        "max_mcap_cr": 20_000,
        "min_box_age": 6,
        "min_sar": 1.10,
        "min_am": 2.2,
        "breakout_dar_floor": None,
    },
    "large": {
        "max_mcap_cr": float("inf"),
        "min_box_age": 7,
        "min_sar": 1.15,
        "min_am": 1.5,
        "breakout_dar_floor": None,
    },
}

ENTRY_BUFFER_PCT = 0.005  # 0.5% above ceiling to confirm breakout
MAX_BOX_RANGE_PCT = 15.0
MAX_BOX_AGE_DAYS = 60
VOLUME_CONFIRM_MULT = 1.5


def _tier_for_mcap(mcap_cr: float) -> str:
    if mcap_cr <= TIER_THRESHOLDS["small"]["max_mcap_cr"]:
        return "small"
    if mcap_cr <= TIER_THRESHOLDS["mid"]["max_mcap_cr"]:
        return "mid"
    return "large"


class DarvasBoxScanner(AccumulationBaseScanner):
    """Darvas Box breakout scanner with tiered DAR validation.

    Inherits _get_universe / _get_tech_data / _compute_atr / _compute_sma /
    _sanitize_float from AccumulationBaseScanner. We do NOT call the parent's
    scan() because it would apply accumulation-base filters that would
    silently eliminate valid Darvas candidates.
    """

    def __init__(
        self,
        base_days=120,
        min_dar=0.2,
        min_mcap=100,
        max_mcap=50000,
    ):
        super().__init__(
            base_days=base_days,
            min_dar=min_dar,
            min_mcap=min_mcap,
            max_mcap=max_mcap,
        )
        self.bear_market = False

    # --- Universe override (Blocker 1: include free_float_market_cap) --------

    def _get_universe(self) -> list[tuple]:
        val_db = self._db_path("valuation")
        if not os.path.exists(val_db):
            logger.warning("Valuation DB not found at %s", val_db)
            return []
        with sqlite3.connect(val_db) as conn:
            rows = conn.execute(
                """
                SELECT f.symbol,
                       COALESCE(f.market_cap, 0)             AS mcap,
                       COALESCE(f.free_float_pct, 40.0)      AS ff_pct,
                       COALESCE(f.free_float_market_cap, 0)   AS ff_mcap
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

    # --- Grade helper (Blocker 5) --------------------------------------------

    @staticmethod
    def _compute_grade(score: float) -> str:
        if score >= 75:
            return "A"
        if score >= 55:
            return "B"
        if score >= 40:
            return "C"
        return "D"

    # --- Box detection --------------------------------------------------------

    def _detect_box(self, df: pd.DataFrame) -> dict | None:
        if df is None or len(df) < 6:
            return None

        df = df.sort_values("date").reset_index(drop=True)
        closes = df["close"].values.astype(float)
        highs = df["high"].values.astype(float)
        lows = df["low"].values.astype(float)

        high_52w_col = (
            df["high_52w"].iloc[-1]
            if "high_52w" in df.columns and pd.notna(df["high_52w"].iloc[-1])
            else None
        )
        high_52w = (
            float(high_52w_col)
            if high_52w_col is not None and float(high_52w_col) > 0
            else float(np.nanmax(highs))
        )
        latest_close = float(closes[-1])

        # Prior-run check: within 5% of 52w high OR >=20% rally in last 60 days.
        if high_52w <= 0:
            return None
        near_high = (high_52w - latest_close) / high_52w <= 0.05
        window_60 = df.tail(60)
        if len(window_60) >= 2:
            earliest_close = float(window_60["close"].iloc[0])
            rallied_20 = (
                earliest_close > 0
                and ((high_52w - earliest_close) / earliest_close) >= 0.20
            )
        else:
            rallied_20 = False
        if not (near_high or rallied_20):
            return None

        # Box ceiling: scan backwards from latest candle. Find the highest high
        # that has been touched at least twice (within 1% tolerance) and never
        # exceeded by a subsequent daily close. A wick above the ceiling is OK.
        ceiling = None
        ceiling_idx = None
        ceiling_date = None
        touches_ceiling = 0
        # Build a sorted list of candidate ceilings (high values, descending),
        # then test the "touched >=2 and not exceeded by a later close" rule.
        unique_highs = sorted({float(h) for h in highs}, reverse=True)
        for candidate in unique_highs:
            tol = candidate * 0.01
            touches = np.where((highs >= candidate - tol) & (highs <= candidate + tol))[
                0
            ]
            if len(touches) < 2:
                continue
            # "Not exceeded by a subsequent daily close" → no close > candidate
            # strictly after the first touch.
            first_touch = int(touches[0])
            if np.any(closes[first_touch + 1 :] > candidate + tol):
                continue
            ceiling = candidate
            ceiling_idx = first_touch
            ceiling_date = df["date"].iloc[first_touch]
            touches_ceiling = int(len(touches))
            break

        if ceiling is None:
            return None

        # Box floor: after the ceiling's first touch, find the lowest low
        # touched at least twice that has not been breached by a subsequent
        # close.
        sub = df.iloc[ceiling_idx + 1 :].reset_index(drop=True)
        sub_lows = sub["low"].values.astype(float)
        sub_closes = sub["close"].values.astype(float)
        floor = None
        floor_idx = None
        floor_date = None
        touches_floor = 0
        unique_lows = sorted({float(l) for l in sub_lows})
        for candidate in unique_lows:
            tol = candidate * 0.01
            touches = np.where(
                (sub_lows >= candidate - tol) & (sub_lows <= candidate + tol)
            )[0]
            if len(touches) < 2:
                continue
            first_touch = int(touches[0])
            if np.any(sub_closes[first_touch + 1 :] < candidate - tol):
                continue
            floor = candidate
            floor_idx = first_touch + ceiling_idx  # absolute index in df
            floor_date = df["date"].iloc[floor_idx]
            touches_floor = int(len(touches))
            break

        if floor is None or floor >= ceiling:
            return None

        box_range_pct = (ceiling - floor) / floor * 100
        if box_range_pct > MAX_BOX_RANGE_PCT:
            return None

        # Box age: trading days from day after the first ceiling touch to the
        # most recent candle (inclusive).
        box_end_idx = len(df) - 1
        box_age_days = max(0, box_end_idx - ceiling_idx)
        if box_age_days > MAX_BOX_AGE_DAYS:
            return None

        # Box floor integrity: no daily close below floor from the first floor
        # touch to the latest candle.
        if np.any(closes[floor_idx + 1 :] < floor - floor * 0.01):
            return None

        # Box start index = day after first ceiling touch (entry into the box).
        box_start_idx = ceiling_idx + 1

        return {
            "ceiling": float(ceiling),
            "floor": float(floor),
            "ceiling_date": str(ceiling_date),
            "floor_date": str(floor_date),
            "box_age_days": int(box_age_days),
            "touches_ceiling": int(touches_ceiling),
            "touches_floor": int(touches_floor),
            "box_range_pct": float(box_range_pct),
            "is_valid": True,
            "box_start_idx": int(box_start_idx),
            "box_end_idx": int(box_end_idx),
        }

    # --- Intra-box analytics --------------------------------------------------

    def _compute_box_dar(
        self,
        df: pd.DataFrame,
        box_start_idx: int,
        box_end_idx: int,
        ff_mcap: float,
        ceiling: float,
    ) -> dict:
        # DAR = (delivery * close) / free_float_mcap * 100
        # free_float_mcap is now populated via BSE shareholding backfill (2,295 symbols)
        if ff_mcap is None or ff_mcap <= 0:
            return {
                "dar_box_median": 0.0,
                "sar": 1.0,
                "breakout_dar": 0.0,
                "am": 0.0,
                "ftc": 1.0,
                "sar_z": 0.0,
            }

        box_df = df.iloc[box_start_idx : box_end_idx + 1].copy()
        if box_df.empty:
            return {
                "dar_box_median": 0.0,
                "sar": 1.0,
                "breakout_dar": 0.0,
                "am": 0.0,
                "ftc": 1.0,
                "sar_z": 0.0,
            }

        deliveries = box_df["delivery"].values.astype(float)
        closes = box_df["close"].values.astype(float)
        dar_series = (deliveries * closes) / ff_mcap * 100
        dar_series = dar_series[~np.isnan(dar_series)]
        if len(dar_series) == 0:
            return {
                "dar_box_median": 0.0,
                "sar": 1.0,
                "breakout_dar": 0.0,
                "am": 0.0,
                "ftc": 1.0,
                "sar_z": 0.0,
            }

        dar_box_median = float(np.median(dar_series))
        last3 = dar_series[-3:] if len(dar_series) >= 3 else dar_series
        last3_mean = float(np.mean(last3))

        sar = (last3_mean / dar_box_median) if dar_box_median > 0 else 1.0

        # SAR_z: statistical significance of recent delivery acceleration
        dar_mean = float(np.mean(dar_series))
        dar_std = float(np.std(dar_series))
        sar_z = ((last3_mean - dar_mean) / dar_std) if dar_std > 0 else 0.0

        # FTC (Float Turnover Compression): falling volume + rising DAR = accumulation
        volumes = box_df["volume"].values.astype(float)
        median_vol_box = float(np.median(volumes))
        median_vol_last5 = (
            float(np.median(volumes[-5:])) if len(volumes) >= 5 else median_vol_box
        )
        ftc = median_vol_last5 / median_vol_box if median_vol_box > 0 else 1.0

        # Breakout day = only a candle whose CLOSE exceeded the ceiling
        # (not merely a touch from below). Add the ceiling param.
        breakout_threshold = ceiling * (1 + ENTRY_BUFFER_PCT)
        breakout_candles = box_df[box_df["close"] >= breakout_threshold]
        if breakout_candles.empty:
            breakout_dar = 0.0
        else:
            last_bo = breakout_candles.iloc[-1]
            delivery = (
                float(last_bo["delivery"]) if pd.notna(last_bo["delivery"]) else 0.0
            )
            close_val = float(last_bo["close"])
            breakout_dar = (delivery * close_val) / ff_mcap * 100

        am = (breakout_dar / dar_box_median) if dar_box_median > 0 else 0.0
        am = min(am, 5.0)

        return {
            "dar_box_median": float(dar_box_median),
            "sar": float(sar),
            "sar_z": float(sar_z),
            "ftc": float(ftc),
            "breakout_dar": float(breakout_dar),
            "am": float(am),
        }

    # --- Entry / SL / targets -------------------------------------------------

    def _compute_entry_sl_targets(
        self,
        ceiling: float,
        floor: float,
        box_volumes: np.ndarray,
        breakout_volume: float,
    ) -> dict:
        entry = float(ceiling * (1.0 + ENTRY_BUFFER_PCT))
        sl = float(floor) * 0.995  # 0.5% below floor, gives room for stop hunts
        height = entry - sl
        t1 = entry + height
        t2 = entry + 2 * height

        # Volume confirmation: breakout day >= 1.5x the average inside the box.
        avg_box_vol = float(np.nanmean(box_volumes)) if len(box_volumes) > 0 else 0.0
        volume_ok = (
            breakout_volume is not None
            and avg_box_vol > 0
            and breakout_volume >= VOLUME_CONFIRM_MULT * avg_box_vol
        )

        return {
            "entry": entry,
            "sl": sl,
            "t1": t1,
            "t2": t2,
            "volume_ok": bool(volume_ok),
            "status": "Triggered" if volume_ok else "Low Volume",
        }

    # --- Composite score -------------------------------------------------------

    @staticmethod
    def _composite_score(
        am: float,
        sar_z: float,
        ftc: float,
        rs_mean: float,
        box_range_pct: float,
        tier: str,
        is_pre_breakout: bool,
    ) -> tuple[float, str]:
        th = TIER_THRESHOLDS[tier]

        if is_pre_breakout:
            # No AM yet — weight the observable signals
            score_sar = max(0.0, min(100.0, 50.0 + sar_z * 20.0))
            score_ftc = 100.0 if ftc < 0.7 else (0.0 if ftc > 1.2 else 50.0)
            score_rs = max(0.0, min(100.0, 50.0 + rs_mean * 20.0))
            score_range = max(0.0, min(100.0, 100.0 * (1.0 - box_range_pct / 15.0)))
            total = (
                score_sar * 0.30
                + score_ftc * 0.20
                + score_rs * 0.30
                + score_range * 0.20
            )
        else:
            # Full scoring with AM
            score_am = 100.0 * min(am / (th["min_am"] or 4.0), 2.0) / 2.0
            score_sar = max(0.0, min(100.0, 50.0 + sar_z * 20.0))
            score_ftc = 100.0 if ftc < 0.7 else (0.0 if ftc > 1.2 else 50.0)
            score_rs = max(0.0, min(100.0, 50.0 + rs_mean * 20.0))
            score_range = max(0.0, min(100.0, 100.0 * (1.0 - box_range_pct / 15.0)))
            total = (
                score_am * 0.30
                + score_sar * 0.25
                + score_rs * 0.20
                + score_range * 0.15
                + score_ftc * 0.10
            )

        grade = DarvasBoxScanner._compute_grade(total)
        return total, grade

    @staticmethod
    def _passes_tier(
        am: float,
        sar: float,
        breakout_dar: float,
        box_age_days: int,
        tier: str,
    ) -> tuple[bool, str]:
        th = TIER_THRESHOLDS[tier]
        if box_age_days < th["min_box_age"]:
            return (
                False,
                f"Box age {box_age_days} below minimum {th['min_box_age']} for {tier}",
            )
        dar_floor = th.get("breakout_dar_floor")
        if dar_floor is not None:
            # Small cap: AM >= min_am OR breakout_dar >= dar_floor (either passes)
            if am < (th["min_am"] or 0) and breakout_dar < dar_floor:
                return False, (
                    f"AM {am:.2f} < {th['min_am']} and breakout DAR {breakout_dar:.2f}% "
                    f"< {dar_floor}% — neither threshold met for {tier}"
                )
        else:
            # Mid / large cap: AM must meet threshold
            if th["min_am"] is not None and am < th["min_am"]:
                return False, f"AM {am:.2f} below threshold {th['min_am']} for {tier}"
        if th["min_sar"] is not None and sar < th["min_sar"]:
            return False, f"SAR {sar:.2f} below threshold {th['min_sar']} for {tier}"
        return True, ""

    # --- Scan ------------------------------------------------------------------

    def scan(self, as_on_date: str | None = None) -> pd.DataFrame:
        rows = self._get_universe()
        if not rows:
            logger.warning(
                "No symbols found in universe (mcap %.0f-%.0f Cr)",
                self.min_mcap,
                self.max_mcap,
            )
            return pd.DataFrame()

        # Build a sector map from valuation DB.
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
        # Fetch base_days * 3 worth of history to compute 52w high reliably.
        min_date = (
            f"{(ref_date - pd.Timedelta(days=max(self.base_days * 2, 200))):%Y-%m-%d}"
        )

        # Single bulk load replaces per-symbol sqlite connections.
        # darvas calls _get_tech_data WITHOUT max_date -> effective max = today.
        self._bulk_data = load_ohlcv_for_universe(min_date, date.today().isoformat())

        candidates: list[dict] = []

        for idx, (symbol, mcap, ff_pct, ff_mcap_col) in enumerate(rows):
            symbol = symbol.strip()
            mcap_cr = mcap / 1e7
            tier = _tier_for_mcap(mcap_cr)
            th = TIER_THRESHOLDS[tier]

            tech = self._get_tech_data(symbol, min_date)
            if len(tech) < 30:
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

            box = self._detect_box(df)
            if box is None:
                continue

            ff_mcap = (
                float(ff_mcap_col)
                if ff_mcap_col and float(ff_mcap_col) > 0
                else mcap * ff_pct / 100.0
            )
            box_dar = self._compute_box_dar(
                df,
                box["box_start_idx"],
                box["box_end_idx"],
                ff_mcap,
                ceiling=box["ceiling"],
            )
            dar_box_median = box_dar["dar_box_median"]
            sar = box_dar["sar"]
            breakout_dar = box_dar["breakout_dar"]
            am = box_dar["am"]
            ftc = box_dar["ftc"]
            sar_z = box_dar["sar_z"]

            # Skip candidates whose baseline DAR is too low.
            if dar_box_median < self.min_dar:
                continue

            latest_close = float(df["close"].iloc[-1])
            ceiling = box["ceiling"]
            floor = box["floor"]
            breakout_threshold = ceiling * (1.0 + ENTRY_BUFFER_PCT)

            # Relative Strength: stock return vs Nifty return over the box period
            box_df_rs = df.iloc[box["box_start_idx"] : box["box_end_idx"]]
            nifty_scores = box_df_rs["nifty_outperformance_score"].values.astype(float)
            rs_mean = float(np.mean(nifty_scores)) if len(nifty_scores) > 0 else 0.0

            # Status decision tree — price position MUST be checked before
            # tier validation, otherwise stocks still inside the box are
            # marked "Failed Validation" because their breakout_dar is 0.0
            # (no close has crossed the ceiling yet).
            entry = None
            sl = None
            t1 = None
            t2 = None
            volume_ok = False
            failure_reason = ""
            validation_passed = False

            if latest_close < floor:
                status = "Invalidated"
            elif latest_close <= ceiling:
                # Still inside the box — display metrics, do not validate.
                status = "In Box"
            elif latest_close < breakout_threshold:
                # Above the ceiling but the 0.5% buffer hasn't been reached
                # yet. Display metrics, do not validate.
                status = "Breakout Pending"
            else:
                # Price has confirmed the breakout — run tier validation
                # and, if it passes, set up the trade.
                validation_passed, failure_reason = self._passes_tier(
                    am=am,
                    sar=sar,
                    breakout_dar=breakout_dar,
                    box_age_days=box["box_age_days"],
                    tier=tier,
                )
                if validation_passed:
                    box_vols = df.iloc[box["box_start_idx"] : box["box_end_idx"] + 1][
                        "volume"
                    ].values.astype(float)
                    breakout_volume = float(df["volume"].iloc[-1])
                    trade = self._compute_entry_sl_targets(
                        ceiling=ceiling,
                        floor=floor,
                        box_volumes=box_vols,
                        breakout_volume=breakout_volume,
                    )
                    entry = trade["entry"]
                    sl = trade["sl"]
                    t1 = trade["t1"]
                    t2 = trade["t2"]
                    volume_ok = trade["volume_ok"]
                    status = trade["status"]  # "Triggered" or "Low Volume"
                else:
                    status = "Failed Validation"

            is_pre_breakout = status in ("In Box", "Breakout Pending")
            composite_score, grade = self._composite_score(
                am=am,
                sar_z=sar_z,
                ftc=ftc,
                rs_mean=rs_mean,
                box_range_pct=box["box_range_pct"],
                tier=tier,
                is_pre_breakout=is_pre_breakout,
            )

            candidates.append(  # noqa: PG-APPEND
                {
                    "symbol": symbol,
                    "sector": _sector_map.get(symbol, "Unknown"),
                    "market_cap_cr": round(mcap_cr, 1),
                    "tier": tier,
                    "ceiling_price": round(box["ceiling"], 2),
                    "floor_price": round(box["floor"], 2),
                    "ceiling_date": box["ceiling_date"],
                    "floor_date": box["floor_date"],
                    "box_age_days": box["box_age_days"],
                    "box_range_pct": round(box["box_range_pct"], 2),
                    "touches_ceiling": box["touches_ceiling"],
                    "touches_floor": box["touches_floor"],
                    "dist_to_ceiling_pct": round(
                        (box["ceiling"] - latest_close) / latest_close * 100, 2
                    )
                    if latest_close > 0 and latest_close <= box["ceiling"]
                    else 0.0,
                    "dar_box_median": round(dar_box_median, 3),
                    "sar": round(sar, 3),
                    "sar_z": round(sar_z, 3),
                    "ftc": round(ftc, 3),
                    "breakout_dar": round(breakout_dar, 3),
                    "am": round(am, 3),
                    "rs_mean": round(rs_mean, 2),
                    "entry": round(entry, 2) if entry is not None else None,
                    "sl": round(sl, 2) if sl is not None else None,
                    "t1": round(t1, 2) if t1 is not None else None,
                    "t2": round(t2, 2) if t2 is not None else None,
                    "volume_ok": volume_ok,
                    "close": round(latest_close, 2),
                    "status": status,
                    "failure_reason": failure_reason,
                    "composite_score": round(composite_score, 1),
                    "grade": grade,
                }
            )

        # Sanitize NaN/Inf for JSON compatibility.
        float_fields = [
            "market_cap_cr",
            "box_range_pct",
            "dar_box_median",
            "sar",
            "sar_z",
            "ftc",
            "breakout_dar",
            "am",
            "rs_mean",
            "entry",
            "sl",
            "t1",
            "t2",
            "close",
            "composite_score",
            "ceiling_price",
            "floor_price",
            "dist_to_ceiling_pct",
        ]
        for c in candidates:
            for f in float_fields:
                if f in c:
                    c[f] = AccumulationBaseScanner._sanitize_float(c[f])

        candidates.sort(key=lambda x: x["composite_score"], reverse=True)
        logger.info("Darvas scan complete: %d candidates found", len(candidates))
        return pd.DataFrame(candidates)
