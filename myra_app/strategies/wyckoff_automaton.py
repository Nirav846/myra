import logging
import math
import sqlite3
import os
import numpy as np
import pandas as pd
from datetime import date, timedelta
from myra_app.constants import DB_DIR
from myra_app.librarian_core import LibrarianCore

logger = logging.getLogger(__name__)


class WyckoffAutomaton:
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

            # Spring — Undercut & Recovery
            is_spring = (
                low_p < range_low * 0.99 and close_p > range_low and del_pct > 35
            )

            if is_spring:
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
                    }
                )
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

        candidates: list[dict] = []

        for idx, (symbol, mcap, ff_pct) in enumerate(rows):
            symbol = symbol.strip()

            tech = self._get_tech_data(symbol, min_date, max_date=as_on_date)
            if len(tech) < max(55, int(self.lookback_days * 0.6) + 5):
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
