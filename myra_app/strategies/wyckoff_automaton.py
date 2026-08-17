import logging
import math
from myra_app.strategies.scanner_utils import sanitize_float
import sqlite3
import os
import numpy as np
import pandas as pd
from datetime import date, timedelta
from myra_app.constants import DB_DIR
from myra_app.librarian_core import LibrarianCore
from myra_app.db.bulk_loader import (
    load_ohlcv_for_universe,
    rows_for_symbol,
    COLUMNS_13,
)

logger = logging.getLogger(__name__)


class WyckoffAutomaton:
    _bulk_data = None
    _BULK_COLUMNS = COLUMNS_13

    def __init__(self, min_mcap=200, max_mcap=50000, lookback_days=90):
        self.min_mcap = min_mcap
        self.max_mcap = max_mcap
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
                           delivery_pct, swing_low,
                           nifty_outperformance_score,
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

    @staticmethod
    def _delivery_absorption_score(del_abs: float) -> float:
        """0-30. Linear: <=0 -> 0, +5 -> 15, +10 -> 30."""
        return round(min(max(del_abs, 0.0) / 10.0 * 30.0, 30.0), 1)

    @staticmethod
    def _lower_wick_score(ratio: float) -> float:
        """0-30. Piecewise-linear through (0.20,0), (0.40,15), (0.60,22), (0.75,30)."""
        pts = [(0.20, 0.0), (0.40, 15.0), (0.60, 22.0), (0.75, 30.0)]
        if ratio <= pts[0][0]:
            return 0.0
        if ratio >= pts[-1][0]:
            return 30.0
        for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
            if x0 <= ratio < x1:
                return round(y0 + (ratio - x0) / (x1 - x0) * (y1 - y0), 1)
        return 0.0

    @staticmethod
    def _close_location_score(ratio: float) -> float:
        """0-20. Step: <0.5 -> 5, 0.5-0.75 -> 10, >0.75 -> 20."""
        if ratio > 0.75:
            return 20.0
        if ratio >= 0.5:
            return 10.0
        return 5.0

    @staticmethod
    def _grab_depth_score(depth_pct: float) -> float:
        """0-10. Step: <0.5 -> 7, 0.5-1.5 -> 10, >1.5 -> 5."""
        if depth_pct > 1.5:
            return 5.0
        if depth_pct >= 0.5:
            return 10.0
        return 7.0

    @staticmethod
    def _spring_grade(score: float) -> str:
        """A+ >= 65, B >= 50, C >= 35, D < 35."""
        if score >= 65:
            return "A+"
        if score >= 50:
            return "B"
        if score >= 35:
            return "C"
        return "D"

    @staticmethod
    def _compute_spring_score(
        del_score,
        wick_score,
        close_score,
        depth_score,
        equal_low_bonus,
        two_candle_confirm: bool = False,
    ) -> float:
        """Sum of factors + 5 if two_candle_confirm, clamped to [0, 100]."""
        total = del_score + wick_score + close_score + depth_score + equal_low_bonus
        if two_candle_confirm:
            total += 5.0
        return round(min(max(total, 0.0), 100.0), 1)

    @staticmethod
    def _event_quality(
        event_type: str,
        vol_ratio: float,
        del_pct: float,
        avg_del: float,
        extra: dict | None = None,
    ) -> float:
        """
        Event-specific quality score (0–100).
        Each event type has a different definition of 'quality'.
        """
        extra = extra or {}
        if event_type == "SC":
            vol_score = min(vol_ratio / 4.0 * 50, 50)
            del_score = min(del_pct / 80.0 * 50, 50)
            return round(min(vol_score + del_score, 100), 1)

        elif event_type == "AR":
            rally_pct = float(extra.get("rally_pct", 0))
            rally_score = min(rally_pct / 8.0 * 40, 40)
            vol_score = min(max(0, (1.0 - vol_ratio)) * 40, 40)
            del_score = min(del_pct / 60.0 * 20, 20)
            return round(min(rally_score + vol_score + del_score, 100), 1)

        elif event_type == "ST":
            vol_score = min(max(0, (1.0 - vol_ratio)) / 0.5 * 50, 50)
            del_score = min(max(0, (1.0 - del_pct / avg_del)) * 50, 50)
            return round(min(vol_score + del_score, 100), 1)

        elif event_type == "Spring":
            recovery_pct = float(extra.get("recovery_pct", 0))
            del_score = min(del_pct / 75.0 * 50, 50)
            rec_score = min(recovery_pct / 5.0 * 50, 50)
            return round(min(del_score + rec_score, 100), 1)

        elif event_type == "SOS":
            close_pos = float(extra.get("close_position", 0.5))
            vol_score = min(vol_ratio / 3.0 * 40, 40)
            del_score = min(del_pct / 70.0 * 40, 40)
            pos_score = min(close_pos * 20, 20)
            return round(min(vol_score + del_score + pos_score, 100), 1)

        return 0.0

    def _detect_events(self, df: pd.DataFrame, symbol: str = "") -> list[dict]:
        events = []
        n = len(df)
        if n < 55:
            return events

        avg_vol = float(df["volume"].mean())
        avg_del = float(df["delivery"].values.astype(float).mean())
        range_low = float(df["low"].min())
        range_high = float(df["high"].max())

        if avg_vol == 0:
            return events
        if n > 0:
            logger.debug(
                "rows=%d, avg_vol=%.0f, avg_del=%.1f",
                n,
                avg_vol,
                avg_del,
            )

        # Scan last 30 sessions
        scan_df = df.tail(90).reset_index(drop=True)
        for i in range(len(scan_df)):
            abs_i = i + n - len(scan_df)
            row = scan_df.iloc[i]
            row_date = str(row["date"])
            open_p = float(row["open"])
            high_p = float(row["high"])
            low_p = float(row["low"])
            close_p = float(row["close"])
            volume_p = float(row["volume"])
            del_pct = float(row["delivery_pct"])

            if volume_p == 0:
                continue

            vol_ratio = volume_p / avg_vol if avg_vol > 0 else 0
            del_ratio = del_pct / avg_del if avg_del > 0 else 0

            # SC — Selling Climax
            is_sc = (
                volume_p > avg_vol * 1.8
                and close_p > (low_p + (high_p - low_p) * 0.35)
                and del_pct > 40
                and close_p <= range_low * 1.15
            )

            if is_sc:
                quality = self._event_quality("SC", vol_ratio, del_pct, avg_del)
                events.append(
                    {
                        "symbol": symbol,
                        "event": "SC",
                        "phase": "Phase A",
                        "phase_pct": 25,
                        "event_date": row_date,
                        "del_pct": del_pct,
                        "vol_ratio": vol_ratio,
                        "quality": quality,
                        "close": close_p,
                        "range_low_90": range_low,
                        "range_high_90": range_high,
                        "sc_reference_close": close_p,
                    }
                )
                continue

            # Spring — Undercut & Recovery.
            # Undercut reference = prior running low (excludes the grab candle itself),
            # otherwise no candle could ever dip below the window's global minimum.
            prior_low = (
                float(df["low"].iloc[max(0, abs_i - 60) : abs_i].min())
                if abs_i > 0
                else float(range_low)
            )
            # Dynamic delivery threshold: must be 20% above the stock's own 50-day average
            # rather than an arbitrary absolute number that doesn't fit Indian markets
            avg_del_50d = float(
                df["delivery_pct"].iloc[max(0, abs_i - 50) : abs_i].mean()
            )
            del_threshold = max(25.0, avg_del_50d * 1.2)  # floor at 25% to avoid noise
            is_spring = (
                low_p < prior_low * 0.99
                and close_p > prior_low
                and del_pct > del_threshold
            )

            if is_spring:
                try:
                    recovery_pct = (
                        (close_p - low_p) / (high_p - low_p) * 100
                        if (high_p - low_p) > 0
                        else 0.0
                    )
                    quality = self._event_quality(
                        "Spring",
                        vol_ratio,
                        del_pct,
                        avg_del,
                        extra={"recovery_pct": recovery_pct},
                    )

                    # --- Structured Spring Scoring ---
                    # 1. Delivery absorption
                    start_idx = max(0, abs_i - 50)
                    del_slice = df["delivery_pct"].values.astype(float)[start_idx:abs_i]
                    avg_del_50 = (
                        float(np.nanmean(del_slice)) if len(del_slice) > 0 else del_pct
                    )
                    del_abs = del_pct - avg_del_50
                    del_score = self._delivery_absorption_score(del_abs)

                    # 2. Lower wick ratio + close location
                    denom = high_p - low_p
                    if denom > 0:
                        lower_wick_ratio = (min(open_p, close_p) - low_p) / denom
                        close_location = (close_p - low_p) / denom
                    else:
                        lower_wick_ratio = 0.5
                        close_location = 0.5
                    wick_score = self._lower_wick_score(lower_wick_ratio)
                    close_score = self._close_location_score(close_location)

                    # 3. Grab depth (uses SMC swing_low at the grab candle)
                    has_swing = "swing_low" in df.columns
                    swing_low_val = None
                    if has_swing:
                        _sl = df["swing_low"].iloc[abs_i]
                        if pd.notna(_sl):
                            swing_low_val = float(_sl)
                    if swing_low_val is not None and swing_low_val > 0:
                        grab_depth_pct = (swing_low_val - low_p) / swing_low_val * 100
                    else:
                        grab_depth_pct = 0.0
                    depth_score = self._grab_depth_score(grab_depth_pct)

                    # 4. Equal-low detection
                    equal_low_zone = False
                    if swing_low_val is not None:
                        lo = max(0, abs_i - 20)
                        hi = min(n, abs_i + 21)
                        for j in range(lo, hi):
                            if j == abs_i:
                                continue
                            sl_j_raw = df["swing_low"].iloc[j] if has_swing else None
                            if sl_j_raw is not None and pd.notna(sl_j_raw):
                                sl_j = float(sl_j_raw)
                                low_j = float(df["low"].iloc[j])
                                if abs(low_j - sl_j) < 1e-9:
                                    if (
                                        abs(sl_j - swing_low_val) / swing_low_val
                                        <= 0.005
                                    ):
                                        equal_low_zone = True
                                        break
                    equal_low_bonus = 10.0 if equal_low_zone else 0.0

                    # 5. Two-candle confirmation
                    two_candle_confirm = False
                    if close_p < open_p and abs_i + 1 < n:
                        nrow = df.iloc[abs_i + 1]
                        nclose = float(nrow["close"])
                        nopen = float(nrow["open"])
                        ref_level = (
                            swing_low_val
                            if swing_low_val is not None
                            else float(prior_low)
                        )
                        if nclose > nopen and nclose > ref_level:
                            two_candle_confirm = True

                    # 6. Compute score and grade
                    spring_score = self._compute_spring_score(
                        del_score,
                        wick_score,
                        close_score,
                        depth_score,
                        equal_low_bonus,
                        two_candle_confirm,
                    )
                    grade = self._spring_grade(spring_score)

                    # 7. Skip grade D
                    if grade == "D":
                        continue

                    events.append(
                        {
                            "symbol": symbol,
                            "event": "Spring",
                            "phase": "Phase C",
                            "phase_pct": 75,
                            "event_date": row_date,
                            "del_pct": del_pct,
                            "vol_ratio": vol_ratio,
                            "quality": quality,
                            "close": close_p,
                            "range_low_90": range_low,
                            "range_high_90": range_high,
                            "sc_reference_close": close_p,
                            "spring_score": self._sanitize_float(spring_score),
                            "grade": grade,
                            "lower_wick_ratio": self._sanitize_float(
                                round(lower_wick_ratio, 3)
                            ),
                            "close_location": self._sanitize_float(
                                round(close_location, 3)
                            ),
                            "grab_depth_pct": self._sanitize_float(
                                round(grab_depth_pct, 2)
                            ),
                            "equal_low_zone": bool(equal_low_zone),
                            "two_candle_confirm": bool(two_candle_confirm),
                        }
                    )
                except Exception as exc:
                    logger.warning("Spring scoring failed for %s: %s", symbol, exc)
                continue

            # SOS — Sign of Strength
            is_sos = (
                close_p > (range_low + (range_high - range_low) * 0.45)
                and volume_p > avg_vol * 1.2
                and del_pct > avg_del * 1.0
                and close_p > open_p
            )

            if is_sos:
                close_position = (
                    (close_p - low_p) / (high_p - low_p)
                    if (high_p - low_p) > 0
                    else 0.5
                )
                quality = self._event_quality(
                    "SOS",
                    vol_ratio,
                    del_pct,
                    avg_del,
                    extra={"close_position": close_position},
                )
                events.append(
                    {
                        "symbol": symbol,
                        "event": "SOS",
                        "phase": "Phase D",
                        "phase_pct": 90,
                        "event_date": row_date,
                        "del_pct": del_pct,
                        "vol_ratio": vol_ratio,
                        "quality": quality,
                        "close": close_p,
                        "range_low_90": range_low,
                        "range_high_90": range_high,
                        "sc_reference_close": close_p,
                    }
                )
                continue

        # AR — Automatic Rally (post-SC) and ST — Secondary Test
        sc_events = [e for e in events if e["event"] == "SC"]
        for sc in sc_events:
            sc_idx = df[df["date"].astype(str) == sc["event_date"]]
            if sc_idx.empty:
                continue
            sc_pos = sc_idx.index[0]
            sc_close = sc["sc_reference_close"]

            # Look within 10 sessions after SC
            post_sc = df.loc[sc_pos + 1 : sc_pos + 11]
            for _, nrow in post_sc.iterrows():
                nclose = float(nrow["close"])
                nvol = float(nrow["volume"])
                ndel = float(nrow["delivery_pct"])

                # AR — Automatic Rally
                if nclose > sc_close * 1.03 and nvol <= avg_vol * 1.0:
                    ar_vol_ratio = nvol / avg_vol if avg_vol > 0 else 0
                    ar_del_ratio = ndel / avg_del if avg_del > 0 else 0
                    rally_pct = (
                        (nclose - sc_close) / sc_close * 100 if sc_close > 0 else 0.0
                    )
                    ar_quality = self._event_quality(
                        "AR",
                        ar_vol_ratio,
                        ndel,
                        avg_del,
                        extra={"rally_pct": rally_pct},
                    )
                    existing = [
                        e for e in events if e["event_date"] == str(nrow["date"])
                    ]
                    if not existing:
                        events.append(
                            {
                                "symbol": symbol,
                                "event": "AR",
                                "phase": "Phase A",
                                "phase_pct": 30,
                                "event_date": str(nrow["date"]),
                                "del_pct": ndel,
                                "vol_ratio": round(ar_vol_ratio, 1),
                                "quality": ar_quality,
                                "close": nclose,
                                "range_low_90": range_low,
                                "range_high_90": range_high,
                                "sc_reference_close": sc_close,
                            }
                        )

                # ST — Secondary Test
                if (
                    abs(nclose - sc_close) / sc_close <= 0.05
                    and nvol < avg_vol * 0.7
                    and ndel < avg_del
                ):
                    existing = [
                        e for e in events if e["event_date"] == str(nrow["date"])
                    ]
                    if not existing:
                        st_vol_ratio = nvol / avg_vol if avg_vol > 0 else 0
                        st_del_ratio = ndel / avg_del if avg_del > 0 else 0
                        st_quality = self._event_quality(
                            "ST", st_vol_ratio, ndel, avg_del
                        )
                        events.append(
                            {
                                "symbol": symbol,
                                "event": "ST",
                                "phase": "Phase B",
                                "phase_pct": 50,
                                "event_date": str(nrow["date"]),
                                "del_pct": ndel,
                                "vol_ratio": round(st_vol_ratio, 1),
                                "quality": st_quality,
                                "close": nclose,
                                "range_low_90": range_low,
                                "range_high_90": range_high,
                                "sc_reference_close": sc_close,
                            }
                        )

        return events

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
        min_date = f"{(ref_date - pd.Timedelta(days=self.lookback_days)):%Y-%m-%d}"

        # Single bulk load replaces per-symbol sqlite connections.
        self._bulk_data = load_ohlcv_for_universe(min_date, as_on_date)

        candidates: list[dict] = []

        for idx, (symbol, mcap, ff_pct) in enumerate(rows):
            symbol = symbol.strip()

            tech = self._get_tech_data(symbol, min_date, max_date=as_on_date)
            if len(tech) < max(55, int(self.lookback_days * 0.6) + 5):
                continue

            col_count = len(tech[0]) if tech else 0
            if col_count >= 13:
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
                        "swing_low",
                        "nifty_outperformance_score",
                        "sma_50",
                        "high_52w",
                        "low_52w",
                    ],
                )
            elif col_count >= 12:
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
                df["swing_low"] = None
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
                df["swing_low"] = None
            df["date"] = pd.to_datetime(df["date"])
            df = df.sort_values("date").reset_index(drop=True)

            if len(df) < max(55, int(self.lookback_days * 0.6) + 5):
                continue

            events = self._detect_events(df, symbol=symbol)
            if not events:
                continue

            def _event_score(e: dict) -> float:
                recency_penalty = e.get("days_since", 0) / 3.0
                return e["phase_pct"] - recency_penalty + e["quality"] * 0.1

            for e in events:
                e["days_since"] = (
                    date.today() - pd.Timestamp(e["event_date"]).date()
                ).days

            best = max(events, key=_event_score)
            days_since = best.get("days_since", 0)

            candidates.append(
                {
                    "symbol": symbol,
                    "sector": _sector_map.get(symbol, "Unknown"),
                    "market_cap_cr": round(mcap / 1e7, 1),
                    "wyckoff_event": best["event"],
                    "phase": best["phase"],
                    "phase_complete_pct": best["phase_pct"],
                    "event_date": best["event_date"],
                    "event_delivery_pct": round(best["del_pct"], 1),
                    "vol_ratio": round(best["vol_ratio"], 1),
                    "event_quality": best["quality"],
                    "range_low_90": round(best["range_low_90"], 2),
                    "range_high_90": round(best["range_high_90"], 2),
                    "close": round(best["close"], 2),
                    "days_since_event": days_since,
                    "spring_score": best.get("spring_score"),
                    "grade": best.get("grade"),
                    "lower_wick_ratio": best.get("lower_wick_ratio"),
                    "close_location": best.get("close_location"),
                    "grab_depth_pct": best.get("grab_depth_pct"),
                    "equal_low_zone": best.get("equal_low_zone", False),
                    "two_candle_confirm": best.get("two_candle_confirm", False),
                }
            )

        float_fields = [
            "market_cap_cr",
            "event_delivery_pct",
            "vol_ratio",
            "event_quality",
            "range_low_90",
            "range_high_90",
            "close",
            "spring_score",
            "lower_wick_ratio",
            "close_location",
            "grab_depth_pct",
        ]
        for c in candidates:
            for f in float_fields:
                if f in c:
                    c[f] = self._sanitize_float(c[f])

        candidates.sort(
            key=lambda x: (
                x.get("phase_complete_pct") or 0,
                x.get("event_quality") or 0,
            ),
            reverse=True,
        )
        logger.info("Wyckoff scan complete: %d candidates found", len(candidates))
        return pd.DataFrame(candidates)
