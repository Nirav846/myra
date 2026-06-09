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
                       COALESCE(f.market_cap, f.marketCap, 0) AS mcap,
                       COALESCE(f.free_float_pct, 40.0) AS ff_pct
                FROM fundamentals f
                INNER JOIN (
                    SELECT symbol, MAX(date) as max_date
                    FROM fundamentals
                    WHERE COALESCE(market_cap, marketCap, 0) > 0
                    GROUP BY symbol
                ) latest ON f.symbol = latest.symbol AND f.date = latest.max_date
                WHERE COALESCE(f.market_cap, f.marketCap, 0) / 1e7 BETWEEN ? AND ?
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
                    SELECT date, open, high, low, close, volume, delivery_pct
                    FROM technical_data
                    WHERE symbol = ? AND date >= ?
                    ORDER BY date ASC
                    """,
                    (symbol, min_date),
                ).fetchall()
            except sqlite3.OperationalError:
                try:
                    rows = conn.execute(
                        """
                        SELECT date, open, high, low, close, volume, delivery_pct
                        FROM technical_data
                        WHERE symbol = ? AND date >= ?
                        ORDER BY date ASC
                        """,
                        (symbol, min_date),
                    ).fetchall()
                except sqlite3.OperationalError:
                    rows = []
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

    def _detect_events(self, df: pd.DataFrame) -> list[dict]:
        events = []
        n = len(df)
        if n < 30:
            return events

        avg_vol = float(df["volume"].mean())
        vol_std = float(df["volume"].std())
        avg_del = float(df["delivery_pct"].mean())
        range_low = float(df["low"].min())
        range_high = float(df["high"].max())

        if avg_vol == 0:
            return events

        # Scan last 30 sessions
        scan_df = df.tail(30).reset_index(drop=True)
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
                volume_p > avg_vol * 2.5
                and close_p > (low_p + (high_p - low_p) * 0.35)
                and del_pct > 60
                and close_p <= range_low * 1.07
            )

            if is_sc:
                quality = min(vol_ratio * 40 + del_ratio * 30 + del_pct * 0.3, 100)
                events.append({
                    "symbol": str(df["symbol"].iloc[0]),
                    "event": "SC",
                    "phase": "Phase A",
                    "phase_pct": 25,
                    "event_date": row_date,
                    "del_pct": del_pct,
                    "vol_ratio": vol_ratio,
                    "quality": round(quality, 1),
                    "close": close_p,
                    "range_low_90": range_low,
                    "range_high_90": range_high,
                    "event_close": close_p,
                })
                continue

            # Spring — Undercut & Recovery
            is_spring = (
                low_p < range_low * 0.985
                and close_p > range_low
                and del_pct > 55
            )

            if is_spring:
                quality = min(vol_ratio * 40 + del_ratio * 30 + del_pct * 0.3, 100)
                events.append({
                    "symbol": str(df["symbol"].iloc[0]),
                    "event": "Spring",
                    "phase": "Phase C",
                    "phase_pct": 75,
                    "event_date": row_date,
                    "del_pct": del_pct,
                    "vol_ratio": vol_ratio,
                    "quality": round(quality, 1),
                    "close": close_p,
                    "range_low_90": range_low,
                    "range_high_90": range_high,
                    "event_close": close_p,
                })
                continue

            # SOS — Sign of Strength
            is_sos = (
                close_p > (range_low + (range_high - range_low) * 0.55)
                and volume_p > avg_vol * 1.5
                and del_pct > avg_del * 1.3
                and close_p > open_p
            )

            if is_sos:
                quality = min(vol_ratio * 40 + del_ratio * 30 + del_pct * 0.3, 100)
                events.append({
                    "symbol": str(df["symbol"].iloc[0]),
                    "event": "SOS",
                    "phase": "Phase D",
                    "phase_pct": 90,
                    "event_date": row_date,
                    "del_pct": del_pct,
                    "vol_ratio": vol_ratio,
                    "quality": round(quality, 1),
                    "close": close_p,
                    "range_low_90": range_low,
                    "range_high_90": range_high,
                    "event_close": close_p,
                })
                continue

        # AR — Automatic Rally (post-SC) and ST — Secondary Test
        sc_events = [e for e in events if e["event"] == "SC"]
        for sc in sc_events:
            sc_idx = df[df["date"].astype(str) == sc["event_date"]]
            if sc_idx.empty:
                continue
            sc_pos = sc_idx.index[0]
            sc_close = sc["event_close"]

            # Look within 10 sessions after SC
            post_sc = df.loc[sc_pos + 1 : sc_pos + 11]
            for _, nrow in post_sc.iterrows():
                nclose = float(nrow["close"])
                nvol = float(nrow["volume"])
                ndel = float(nrow["delivery_pct"])

                # AR — Automatic Rally
                if nclose > sc_close * 1.03 and nvol <= post_sc["volume"].mean() * 0.9:
                    ar_vol_ratio = nvol / avg_vol if avg_vol > 0 else 0
                    ar_del_ratio = ndel / avg_del if avg_del > 0 else 0
                    ar_quality = min(ar_vol_ratio * 40 + ar_del_ratio * 30 + ndel * 0.3, 100)
                    # Only add if not already detected as other event
                    existing = [e for e in events if e["event_date"] == str(nrow["date"])]
                    if not existing:
                        events.append({
                            "symbol": str(df["symbol"].iloc[0]),
                            "event": "AR",
                            "phase": "Phase A",
                            "phase_pct": 30,
                            "event_date": str(nrow["date"]),
                            "del_pct": ndel,
                            "vol_ratio": round(ar_vol_ratio, 1),
                            "quality": round(ar_quality, 1),
                            "close": nclose,
                            "range_low_90": range_low,
                            "range_high_90": range_high,
                            "event_close": sc_close,
                        })

                # ST — Secondary Test
                ndel = float(nrow["delivery_pct"])
                if (
                    abs(nclose - sc_close) / sc_close <= 0.05
                    and nvol < avg_vol * 0.7
                    and ndel < avg_del
                ):
                    existing = [e for e in events if e["event_date"] == str(nrow["date"])]
                    if not existing:
                        st_vol_ratio = nvol / avg_vol if avg_vol > 0 else 0
                        st_del_ratio = ndel / avg_del if avg_del > 0 else 0
                        st_quality = min(st_vol_ratio * 40 + st_del_ratio * 30 + ndel * 0.3, 100)
                        events.append({
                            "symbol": str(df["symbol"].iloc[0]),
                            "event": "ST",
                            "phase": "Phase B",
                            "phase_pct": 50,
                            "event_date": str(nrow["date"]),
                            "del_pct": ndel,
                            "vol_ratio": round(st_vol_ratio, 1),
                            "quality": round(st_quality, 1),
                            "close": nclose,
                            "range_low_90": range_low,
                            "range_high_90": range_high,
                            "event_close": sc_close,
                        })

        return events

    def scan(self) -> pd.DataFrame:
        rows = self._get_universe()
        if not rows:
            logger.warning("No symbols found in universe")
            return pd.DataFrame()

        _sector_map: dict[str, str] = {}
        try:
            val_db = self._db_path("valuation")
            if os.path.exists(val_db):
                with sqlite3.connect(val_db) as conn:
                    for row in conn.execute(
                        "SELECT symbol, COALESCE(sector_name, 'Unknown') FROM fundamentals WHERE sector_name IS NOT NULL"
                    ):
                        _sector_map[row[0]] = row[1]
        except Exception:
            pass

        min_date = (date.today() - timedelta(days=self.lookback_days)).isoformat()
        candidates: list[dict] = []

        for symbol, mcap, _ in rows:
            try:
                mcap_cr = mcap / 1e7
                tech = self._get_tech_data(symbol, min_date)
                if len(tech) < 30:
                    continue

                cols = ["date", "open", "high", "low", "close", "volume", "delivery_pct"]
                df = pd.DataFrame(tech, columns=cols)
                df["date"] = pd.to_datetime(df["date"])
                df = df.sort_values("date").reset_index(drop=True)
                df["symbol"] = symbol

                events = self._detect_events(df)
                if not events:
                    continue

                # Most recent event per symbol
                best = max(events, key=lambda e: (e["phase_pct"], e["quality"]))
                days_since = (date.today() - pd.Timestamp(best["event_date"]).date()).days

                candidates.append({
                    "symbol": symbol,
                    "sector": _sector_map.get(symbol, "Unknown"),
                    "market_cap_cr": round(mcap_cr, 1),
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
                })

            except Exception as e:
                logger.debug("Wyckoff error for %s: %s", symbol, e)
                continue

        float_fields = [
            "market_cap_cr", "event_delivery_pct", "vol_ratio",
            "event_quality", "range_low_90", "range_high_90", "close",
        ]
        for c in candidates:
            for f in float_fields:
                if f in c:
                    c[f] = self._sanitize_float(c[f])

        candidates.sort(key=lambda x: (x.get("phase_complete_pct") or 0, x.get("event_quality") or 0), reverse=True)
        logger.info("Wyckoff scan complete: %d candidates found", len(candidates))
        return pd.DataFrame(candidates)
