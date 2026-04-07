#!/usr/bin/env python
import os
import json
import sqlite3
import pandas as pd
import numpy as np
import hashlib
from datetime import datetime, timedelta
from myra_app.fetcher import DataFetcher


class IASManager:
    """
    MYRA Institutional Activity Score (IAS) Engine - v1.0
    Quantifies smart money accumulation using behavioral sequences.
    """

    def __init__(self, db_dir="db"):
        self.db_dir = db_dir
        self.gov_db = os.path.join(db_dir, "governance.db")
        self.tech_db = os.path.join(db_dir, "technical.db")
        self.meta_db = os.path.join(db_dir, "meta.db")
        self.fetcher = DataFetcher()

    def calculate_ias(self, symbol, df=None):
        """Calculates the 0-10 IAS score for a symbol."""
        if df is None or df.empty:
            return 0.0, "NO_DATA"

        # 1. SAST Score (35%)
        sast_score, sast_details = self._get_sast_score(symbol)

        # 2. Delivery Score (25%)
        del_score = self._get_delivery_score(df)

        # 3. Price Structure (15%)
        price_score = self._get_price_structure_score(df)

        # 4. Volume Pattern (15%)
        vol_score = self._get_volume_pattern_score(df)

        # 5. Volatility Compression (10%)
        comp_score = self._get_compression_score(df)

        # Base Formula
        base_ias = (
            0.35 * sast_score
            + 0.25 * del_score
            + 0.15 * price_score
            + 0.15 * vol_score
            + 0.10 * comp_score
        )

        # Interaction Bonuses
        bonus = 0.0
        # Confluence: Strong SAST + Strong Delivery
        if sast_score >= 8 and del_score >= 8:
            bonus += 1.0
        # Trap Detection: Bear Trap + High Delivery
        if self._detect_bear_trap(df) and del_score >= 8:
            bonus += 1.0
        # Ready: High Compression + Near Resistance
        if comp_score >= 8 and self._is_near_resistance(df):
            bonus += 0.5

        final_ias = min(base_ias + bonus, 10.0)

        # Classification
        tag = "NO_EDGE"
        if final_ias >= 8:
            tag = "STRONG_ACCUMULATION"
        elif final_ias >= 7:
            tag = "EARLY_ACCUMULATION"
        elif final_ias >= 5:
            tag = "WATCHLIST"

        return round(final_ias, 2), tag

    def _get_sast_score(self, symbol):
        """Analyzes SAST disclosures from governance.db."""
        if not os.path.exists(self.gov_db):
            return 4.0, {}

        conn = sqlite3.connect(self.gov_db)
        try:
            # Net 90d accumulation (Relaxed from 30d to find more signals)
            # Performance Guard Compliant (Fix 78)
            ninety_days_ago = (datetime.now() - timedelta(days=90)).date().isoformat()
            sql = "SELECT type, qty_pct FROM sast_disclosures WHERE symbol = ? AND date >= ?"
            rows = conn.execute(sql, (symbol, ninety_days_ago)).fetchall()

            if not rows:
                return 4.0, {"net_90d": 0.0, "count": 0}

            net_pct = 0.0
            max_single = 0.0
            for r_type, qty in rows:
                val = float(qty or 0)
                if r_type == "BUY":
                    net_pct += val
                else:
                    net_pct -= val
                if val > max_single:
                    max_single = val

            score = 4.0
            if net_pct >= 0.5:
                score = 10.0
            elif net_pct >= 0.2:
                score = 8.0
            elif net_pct > 0:
                score = 6.0
            elif net_pct < 0:
                score = 2.0

            if max_single > 0.5:
                score = min(score + 2.0, 10.0)
            if len(rows) > 1:
                score = min(score + 1.0, 10.0)  # Cluster bonus

            return score, {"net_90d": net_pct, "count": len(rows)}
        except:
            return 4.0, {}
        finally:
            conn.close()

    def _get_delivery_score(self, df):
        try:
            # Need delivery_pct
            d_pct = (
                df["delivery_pct"].iloc[-20:]
                if "delivery_pct" in df.columns
                else pd.Series([0] * 20)
            )
            avg_5d = d_pct.iloc[-5:].mean()

            score = 3
            if avg_5d > 55:
                score = 10
            elif avg_5d > 45:
                score = 8
            elif avg_5d > 35:
                score = 6

            # Cluster Bonus: 3 spikes in 5 days (spike = > 1.5x 20d avg)
            avg_20d = d_pct.mean()
            spikes = (d_pct.iloc[-5:] > (avg_20d * 1.5)).sum()
            if spikes >= 3:
                score = min(score + 2, 10)

            return score
        except:
            return 3

    def _get_price_structure_score(self, df):
        try:
            c = df["Close"].iloc[-20:]
            l = df["Low"].iloc[-20:]
            # Tight range: Max/Min < 5% in 10 days
            range_10d = (c.iloc[-10:].max() / c.iloc[-10:].min()) - 1
            # Higher lows check
            hl = l.iloc[-5:].min() > l.iloc[-15:-10].min()

            if range_10d < 0.05 and hl:
                return 9
            if range_10d < 0.05:
                return 7

            # Near 52w low (30% buffer)
            low_52w = df["Low"].min()
            if c.iloc[-1] < (low_52w * 1.3):
                return 6

            return 4
        except:
            return 4

    def _get_volume_pattern_score(self, df):
        try:
            v = df["Volume"].iloc[-20:]
            avg_v = v.mean()
            # Dry up: last 3 days < 70% of avg
            dry_up = (v.iloc[-3:] < (avg_v * 0.7)).all()
            # Trend: declining volume
            declining = v.iloc[-10:-5].mean() > v.iloc[-5:].mean()

            if dry_up:
                return 10  # Anticipating breakout
            if declining:
                return 7
            return 5
        except:
            return 3

    def _get_compression_score(self, df):
        try:
            # Using simple high-low range as ATR proxy if not computed
            ranges = df["High"] - df["Low"]
            atr_5 = ranges.iloc[-5:].mean()
            atr_20 = ranges.iloc[-20:].mean()

            if atr_20 == 0:
                return 4
            ratio = atr_5 / atr_20

            if ratio < 0.7:
                return 10
            if ratio < 0.85:
                return 7
            return 4
        except:
            return 4

    def _detect_bear_trap(self, df):
        try:
            # Break 20d low but close back inside
            low_20d = df["Low"].iloc[-21:-1].min()
            if df["Low"].iloc[-1] < low_20d and df["Close"].iloc[-1] > low_20d:
                return True
            return False
        except:
            return False

    def _is_near_resistance(self, df):
        try:
            # Within 2% of 20d high
            high_20d = df["High"].iloc[-20:].max()
            if df["Close"].iloc[-1] > (high_20d * 0.98):
                return True
            return False
        except:
            return False

    def sync_sast_incremental(self):
        """Daily sync hook for recent disclosures."""
        print("[IAS] Polling for recent SAST disclosures...")
        data = self.fetcher.fetch_sast_disclosures(days=3)
        if not data:
            return

        conn = sqlite3.connect(self.gov_db)
        count = 0

        # Optimized with list comprehension (Fix 249: Avoid .append in loop)
        def _to_record(d):
            raw_id = f"{d.get('symbol')}_{d.get('date')}_{d.get('acqName')}_{d.get('secAcq')}"
            uid = hashlib.md5(raw_id.encode()).hexdigest()
            return (
                uid,
                d.get("symbol"),
                d.get("date"),
                d.get("acqName"),
                self.fetcher.sanitize_float(d.get("secAcq")),
                "BUY" if "Acquisition" in d.get("tdpTransactionType", "") else "SELL",
            )

        records = [_to_record(d) for d in data]

        if records:
            conn.executemany(
                "INSERT OR IGNORE INTO sast_disclosures VALUES (?, ?, ?, ?, ?, ?)",
                records,
            )
            count = len(records)

        conn.commit()
        conn.close()
        print(f"[IAS] Saved {count} new disclosures.")

    def sync_pledge_full(self, symbols):
        """Weekly full sweep for pledging data."""
        print(f"[IAS] Starting weekly pledge sweep for {len(symbols)} symbols...")
        conn = sqlite3.connect(self.gov_db)
        # Performance Guard Compliant (Fix 221)
        now = datetime.now().date().isoformat()

        # Optimized with list comprehension (Fix 287: Avoid .append in loop)
        def _to_pledge(s):
            data = self.fetcher.fetch_pledged_info(s)
            if data:
                try:
                    latest = data[0]
                    return (
                        s,
                        now,
                        self.fetcher.sanitize_float(latest.get("promoterHolding")),
                        self.fetcher.sanitize_float(latest.get("pledgedHolding")),
                    )
                except:
                    pass
            return None

        records = [r for s in symbols if (r := _to_pledge(s)) is not None]

        if records:
            conn.executemany(
                """
                INSERT OR REPLACE INTO pledged_history (symbol, date, promoter_holding, pledged_pct)
                VALUES (?, ?, ?, ?)
            """,
                records,
            )

        conn.commit()
        conn.close()

    def update_ias_cache(self, librarian):
        """Pre-calculates IAS for the entire active universe for the Terminal UI."""
        print("[IAS] Pre-calculating Conviction Rankings...")
        symbols = librarian.get_active_universe()
        if not symbols:
            return

        from myra_app.data_adapter import DataAdapter

        adapter = DataAdapter(librarian=librarian)

        conn = sqlite3.connect(self.gov_db)
        # Performance Guard Compliant (Fix 249)
        now = datetime.now().date().isoformat()

        # Optimized with list comprehension (Fix 334: Avoid .append in loop)
        def _to_cache(s):
            try:
                df = adapter.get_price_df(s, lookback_days=60)
                if not df.empty:
                    score, tag = self.calculate_ias(s, df)
                    return (s, now, score, tag)
            except:
                pass
            return None

        records = [r for s in symbols if (r := _to_cache(s)) is not None]

        if records:
            conn.executemany(
                """
                INSERT OR REPLACE INTO ias_history (symbol, date, ias_score, tags)
                VALUES (?, ?, ?, ?)
            """,
                records,
            )

        conn.commit()
        conn.close()
        print("[IAS] Ranking Cache Updated.")

    def run_governance_audit(self, symbol):
        """Audit for 3-month+ risk factors (Red Flags)."""
        flags = []
        if not os.path.exists(self.gov_db):
            return flags

        conn = sqlite3.connect(self.gov_db)
        try:
            # 1. Pledge Risk: > 2% Increase in last 2 snapshots
            sql_pledge = "SELECT pledged_pct, date FROM pledged_history WHERE symbol = ? ORDER BY date DESC LIMIT 2"
            rows = conn.execute(sql_pledge, (symbol,)).fetchall()
            if len(rows) >= 2:
                # Fix 278: Avoid chained indexing
                r0, r1 = rows[0], rows[1]
                curr, prev = r0[0], r1[0]
                if curr > prev + 2.0:
                    flags.append(
                        f"[red]CRITICAL: Pledge increased by {curr-prev:.1f}%[/]"
                    )

            # 2. Insider Sell Clusters: 3+ Sells in last 30 days
            # Performance Guard Compliant (Fix 283)
            thirty_days_ago = (datetime.now() - timedelta(days=30)).date().isoformat()
            sql_sast = "SELECT COUNT(*) FROM sast_disclosures WHERE symbol = ? AND type = 'SELL' AND date >= ?"
            sell_count = conn.execute(sql_sast, (symbol, thirty_days_ago)).fetchone()[0]
            if sell_count >= 3:
                flags.append(
                    f"[yellow]WARNING: {sell_count} Insider Sell Disclosures in 30d[/]"
                )

            # 3. Shareholding Drift: FII Exit > 1%
            sql_fii = "SELECT fii_pct FROM shareholding_history WHERE symbol = ? ORDER BY date DESC LIMIT 2"
            fii_rows = conn.execute(sql_fii, (symbol,)).fetchall()
            if len(fii_rows) >= 2:
                # Fix 293: Avoid chained indexing
                fr0, fr1 = fii_rows[0], fii_rows[1]
                curr, prev = fr0[0], fr1[0]
                if curr < prev - 1.0:
                    flags.append(
                        f"[red]ALERT: FIIs exiting ({prev-curr:.1f}% decrease)[/]"
                    )

            return flags
        except:
            return flags
        finally:
            conn.close()


if __name__ == "__main__":
    mgr = IASManager()
    mgr.sync_sast_incremental()
