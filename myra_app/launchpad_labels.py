#!/usr/bin/env python
"""
Launchpad Detection - Event Labelling System
Scans technical_data for trigger → digestion → launchpad → breakout patterns.
"""

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
from rich.console import Console
from rich.table import Table

from myra_app.constants import DB_DIR
from myra_app.librarian_core import LibrarianCore

console = Console()

DEFAULT_CONFIG = {
    "trigger_zscore_min": 2.0,
    "trigger_del_pct_min": 55.0,
    "trigger_window_days": 5,
    "digestion_min_days": 10,
    "digestion_max_days": 120,
    "breakout_range_mult": 1.0,
    "breakout_vol_mult": 1.5,
    "breakout_del_pct_min": 50.0,
    "confirm_window": 5,
}


class LaunchpadLabeler:
    def __init__(self, config: dict = None):
        self.cfg = self._load_config(config)
        self.tech_db = os.path.join(DB_DIR, LibrarianCore.DB_MAP["technical"])
        self.conn = None
        self.events = []

    def _load_config(self, config: dict = None):
        if config:
            return {**DEFAULT_CONFIG, **config}
        config_path = "models/launchpad_config.json"
        if os.path.exists(config_path):
            with open(config_path) as f:
                return {**DEFAULT_CONFIG, **json.load(f)}
        return DEFAULT_CONFIG.copy()

    def _get_conn(self):
        if self.conn is None:
            self.conn = sqlite3.connect(self.tech_db)
        return self.conn

    def _close_conn(self):
        if self.conn:
            self.conn.close()
            self.conn = None

    def _compute_delivery_zscore(self, df: pd.DataFrame) -> pd.Series:
        roll_mean = df["delivery_pct"].rolling(window=20, min_periods=10).mean()
        roll_std = df["delivery_pct"].rolling(window=20, min_periods=10).std()
        zscore = (df["delivery_pct"] - roll_mean) / (roll_std + 1e-9)
        return zscore

    def _compute_atr(self, df: pd.DataFrame, period: int = 14) -> pd.Series:
        high_low = df["high"] - df["low"]
        high_close = (df["high"] - df["close"].shift(1)).abs()
        low_close = (df["low"] - df["close"].shift(1)).abs()
        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        atr = tr.rolling(window=period, min_periods=5).mean()
        return atr

    def _rolling_avg_volume(self, df: pd.DataFrame, period: int = 20) -> pd.Series:
        return df["volume"].rolling(window=period, min_periods=10).mean()

    def _find_trigger_clusters(self, df: pd.DataFrame) -> list:
        df = df.sort_values("date").reset_index(drop=True)
        if len(df) < 5:
            return []

        df["del_zscore"] = self._compute_delivery_zscore(df)
        df["atr"] = self._compute_atr(df)
        df["vol_avg"] = self._rolling_avg_volume(df)
        df["high_20"] = df["high"].rolling(20, min_periods=10).max()
        df["ret_10"] = df["close"].pct_change(10)

        z_min = self.cfg["trigger_zscore_min"]
        del_min = self.cfg["trigger_del_pct_min"]
        w_days = self.cfg["trigger_window_days"]

        triggers = []
        i = 0
        while i < len(df):
            row = df.iloc[i]
            zscore_ok = row["del_zscore"] >= z_min if pd.notna(row["del_zscore"]) else False
            del_ok = row["delivery_pct"] >= del_min
            not_extended = row["close"] < row["high_20"] * 0.95 if pd.notna(row["high_20"]) else True
            not_rallied = row["ret_10"] < 0.10 if pd.notna(row["ret_10"]) else True

            if zscore_ok and del_ok and not_extended and not_rallied:
                cluster_end = min(i + w_days, len(df))
                peak_price = df.loc[i:cluster_end - 1, "close"].max()
                peak_idx = df.loc[i:cluster_end - 1, "close"].idxmax()
                triggers.append({
                    "trigger_date": df.loc[peak_idx, "date"],
                    "trigger_idx": peak_idx,
                    "trigger_peak_price": float(peak_price),
                    "cluster_start": i,
                    "cluster_end": cluster_end,
                })
                i = cluster_end
            else:
                i += 1

        return triggers

    def _scan_digestion_and_breakout(self, df: pd.DataFrame, trigger: dict, trigger_start: int) -> dict | None:
        trig_idx = trigger["trigger_idx"]
        trig_date = trigger["trigger_date"]
        trig_price = trigger["trigger_peak_price"]

        digest_start = trig_idx + 1
        min_days = self.cfg["digestion_min_days"]
        max_days = self.cfg["digestion_max_days"]

        if digest_start >= len(df):
            return None

        search_end = min(digest_start + max_days, len(df))

        digestion_low_price = None
        digestion_low_date = None
        digestion_low_idx = None
        digestion_high_price = trig_price
        digestion_high_idx = trig_idx
        min_range_atr = float("inf")
        min_vol_ratio = float("inf")
        lowest_close_before_breakout = None
        lowest_close_before_breakout_date = None

        digestion_complete = False
        breakout_candidate_idx = None

        for j in range(digest_start, search_end):
            row = df.iloc[j]

            if j == digest_start:
                digestion_low_price = float(row["close"])
                digestion_low_date = row["date"]
                digestion_low_idx = j

            current_low = float(row["close"])
            if current_low < digestion_low_price:
                digestion_low_price = current_low
                digestion_low_date = row["date"]
                digestion_low_idx = j

            current_high = float(row["high"])
            if current_high > digestion_high_price:
                digestion_high_price = current_high
                digestion_high_idx = j

            atr_value = row["atr"] if "atr" in df.columns and pd.notna(row["atr"]) else (row["high"] - row["low"])
            if atr_value > 0:
                day_range = row["high"] - row["low"]
                rr = day_range / (atr_value + 1e-9)
                if rr < min_range_atr:
                    min_range_atr = rr

            vol_avg = row["vol_avg"] if "vol_avg" in df.columns and pd.notna(row["vol_avg"]) else row["volume"]
            if vol_avg > 0:
                vr = row["volume"] / vol_avg
                if vr < min_vol_ratio:
                    min_vol_ratio = vr

            lowest_close_before_breakout = float(row["close"])
            lowest_close_before_breakout_date = row["date"]

            days_since_trigger = j - trig_idx
            if days_since_trigger >= min_days:
                breakout_threshold = digestion_high_price * self.cfg["breakout_range_mult"]
                vol_threshold_mult = self.cfg["breakout_vol_mult"]

                vol_avg_val = row["vol_avg"] if "vol_avg" in df.columns and pd.notna(row["vol_avg"]) else row["volume"]
                is_breakout = (
                    row["close"] > breakout_threshold
                    and atr_value > 0
                    and vol_avg_val > 0
                    and row["volume"] >= vol_avg_val * vol_threshold_mult
                    and row["delivery_pct"] >= self.cfg["breakout_del_pct_min"]
                )

                if is_breakout:
                    confirm_end = min(j + 1 + self.cfg["confirm_window"], len(df))
                    confirm_df = df.loc[j + 1:confirm_end - 1]
                    if len(confirm_df) >= 1 and confirm_df["close"].is_monotonic_increasing:
                        breakout_candidate_idx = j
                        digestion_complete = True
                        break

        if breakout_candidate_idx is None:
            return None

        breakout_idx = breakout_candidate_idx
        breakout_row = df.iloc[breakout_idx]

        launchpad_idx = breakout_idx - 1
        launchpad_row = df.iloc[launchpad_idx]

        days_to_breakout = breakout_idx - trig_idx
        return_pct = ((float(breakout_row["close"]) - float(launchpad_row["close"])) / float(launchpad_row["close"])) * 100
        max_drawdown = ((trig_price - digestion_low_price) / trig_price) * 100

        return {
            "trigger_date": trig_date,
            "trigger_peak_price": trig_price,
            "digestion_low_price": digestion_low_price,
            "digestion_low_date": digestion_low_date,
            "launchpad_date": launchpad_row["date"],
            "launchpad_close": float(launchpad_row["close"]),
            "breakout_date": breakout_row["date"],
            "breakout_close": float(breakout_row["close"]),
            "return_pct": round(return_pct, 4),
            "days_to_breakout": days_to_breakout,
            "success": 1,
            "max_drawdown_pct": round(max_drawdown, 4),
            "min_range_atr_ratio": round(min_range_atr, 4) if min_range_atr != float("inf") else None,
            "min_vol_ratio": round(min_vol_ratio, 4) if min_vol_ratio != float("inf") else None,
            "lowest_close_before_breakout": lowest_close_before_breakout,
            "lowest_close_before_breakout_date": lowest_close_before_breakout_date,
        }

    def _process_symbol(self, symbol: str) -> list:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT date, open, high, low, close, volume, delivery, delivery_pct "
            "FROM technical_data WHERE symbol = ? ORDER BY date",
            (symbol,),
        ).fetchall()

        if len(rows) < 50:
            return []

        df = pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume", "delivery", "delivery_pct"])
        triggers = self._find_trigger_clusters(df)
        events = []

        for trigger in triggers:
            result = self._scan_digestion_and_breakout(df, trigger, trigger["cluster_start"])
            if result:
                result["symbol"] = symbol
                events.append(result)

        return events

    def label_events(self) -> dict:
        conn = self._get_conn()
        symbols = pd.read_sql("SELECT DISTINCT symbol FROM technical_data", conn)["symbol"].tolist()
        console.print(f"[cyan]Processing {len(symbols)} symbols...[/cyan]")

        all_events = []
        success_count = 0
        failure_count = 0

        for i, symbol in enumerate(symbols):
            if (i + 1) % 50 == 0:
                console.print(f"  Progress: {i + 1}/{len(symbols)}")

            events = self._process_symbol(symbol)
            all_events.extend(events)

        console.print(f"[green]Found {len(all_events)} launchpad events[/green]")

        if all_events:
            df_events = pd.DataFrame(all_events)
            required_cols = [
                "symbol", "trigger_date", "trigger_peak_price", "digestion_low_price",
                "digestion_low_date", "launchpad_date", "launchpad_close",
                "breakout_date", "breakout_close", "return_pct", "days_to_breakout",
                "success", "max_drawdown_pct", "min_range_atr_ratio", "min_vol_ratio"
            ]
            for col in required_cols:
                if col not in df_events.columns:
                    df_events[col] = None

            df_events = df_events[required_cols]
            df_events.to_sql("launchpad_events", conn, if_exists="replace", index=False)
            console.print(f"[green]Saved {len(df_events)} events to launchpad_events table[/green]")
        else:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS launchpad_events ("
                "symbol TEXT, trigger_date TEXT, trigger_peak_price REAL, digestion_low_price REAL, "
                "digestion_low_date TEXT, launchpad_date TEXT, launchpad_close REAL, "
                "breakout_date TEXT, breakout_close REAL, return_pct REAL, days_to_breakout INTEGER, "
                "success INTEGER, max_drawdown_pct REAL, min_range_atr_ratio REAL, min_vol_ratio REAL)"
            )
            conn.commit()

        stats = {
            "total_events": len(all_events),
            "symbols_processed": len(symbols),
        }

        if all_events:
            df_all = pd.DataFrame(all_events)
            stats.update({
                "avg_return_pct": round(float(df_all["return_pct"].mean()), 2),
                "avg_days_to_breakout": round(float(df_all["days_to_breakout"].mean()), 1),
                "avg_max_drawdown_pct": round(float(df_all["max_drawdown_pct"].mean()), 2),
                "success_rate": 1.0,
                "min_return_pct": round(float(df_all["return_pct"].min()), 2),
                "max_return_pct": round(float(df_all["return_pct"].max()), 2),
            })

        return stats

    def run(self) -> dict:
        console.print("[bold cyan]Launchpad Event Labeller[/bold cyan]")
        console.print(f"Config: {json.dumps(self.cfg, indent=2)}")
        result = self.label_events()
        self._close_conn()

        table = Table(title="Launchpad Labelling Summary")
        table.add_column("Metric", style="cyan")
        table.add_column("Value", style="green")
        for k, v in result.items():
            table.add_row(k, str(v))
        console.print(table)

        return result


def main():
    parser = argparse.ArgumentParser(description="Launchpad Event Labeller")
    parser.add_argument("--config", type=str, help="Path to custom JSON config file")
    args = parser.parse_args()

    config = None
    if args.config:
        with open(args.config) as f:
            config = json.load(f)

    labeler = LaunchpadLabeler(config)
    labeler.run()


if __name__ == "__main__":
    main()