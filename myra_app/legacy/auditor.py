#!/usr/bin/env python
"""
MYRA Auditor - The Performance Verification Layer (v1.0)
Calculates MFE, MAE, and T+N returns for the 'Trust Loop'.
"""

import pandas as pd
from rich.console import Console


class TrustLoopAuditor:
    def __init__(self, librarian, console=None):
        self.lib = librarian
        self.console = console if console else Console()

    def run_audit(self):
        """Main entry point for performance verification."""
        self.console.print(
            "[info][*] Trust Loop: Auditing institutional signal performance...[/info]"
        )

        # 1. Fetch pending signals
        query = "SELECT * FROM performance_audit WHERE is_matured = FALSE"
        pending = self.lib.safe_execute(query).df()

        if pending.empty:
            self.console.print(
                "[dim][*] Trust Loop: No pending signals for audit.[/dim]"
            )
            return

        updated_count = 0
        for signal in pending.itertuples(index=False):
            # Convert tuple back to dict for easier access if needed
            signal_dict = signal._asdict()
            if self._audit_one_signal(signal_dict):
                updated_count += 1

        if updated_count > 0:
            self.console.print(
                f"[success][*] Trust Loop: Updated {updated_count} signals with latest performance metrics.[/success]"
            )

    def _audit_one_signal(self, signal):
        symbol = signal["symbol"]
        strategy_id = signal["strategy_id"]
        signal_date = signal["signal_date"]
        signal_price = signal["signal_price"]

        # Load prices since signal_date
        sql = "SELECT date, close, high, low FROM prices WHERE symbol = ? AND date >= ? ORDER BY date ASC"
        df = self.lib.safe_execute(sql, (symbol, signal_date)).df()

        if df.empty or len(df) < 2:
            return False

        # 1. Calculate MFE and MAE
        # MFE: Max high reached since signal
        # MAE: Min low reached since signal
        max_high = df["high"].max()
        min_low = df["low"].min()

        mfe = round(max_high, 2)
        mae = round(min_low, 2)

        # 2. T+N Prices
        price_t10 = round(df["close"].iloc[10], 2) if len(df) > 10 else None
        price_t20 = round(df["close"].iloc[20], 2) if len(df) > 20 else None
        price_t30 = round(df["close"].iloc[30], 2) if len(df) > 30 else None

        # 3. R/R Ratio and Grading
        # We define Grade based on T+20 or latest price
        latest_price = df["close"].iloc[-1]
        sl_price = signal["sl_price"]
        tp_price = signal["tp_price"]

        risk = (
            signal_price - sl_price
            if signal_price > sl_price
            else (signal_price * 0.05)
        )
        current_reward = latest_price - signal_price
        rr_ratio = round(current_reward / risk, 2) if risk > 0 else 0

        # Institutional Grading:
        # Grade A: R/R > 3.0 OR > 15% gain
        # Grade B: R/R > 1.5 OR > 7% gain
        # Grade C: Positive return but R/R < 1.5
        # Grade F: Negative return OR Hit SL

        gain_pct = (latest_price - signal_price) / signal_price
        grade = "C"
        if latest_price <= sl_price:
            grade = "F"
        elif gain_pct >= 0.15 or rr_ratio >= 3.0:
            grade = "A"
        elif gain_pct >= 0.07 or rr_ratio >= 1.5:
            grade = "B"
        elif gain_pct < 0:
            grade = "F"

        is_matured = len(df) >= 31  # Matured after 30 trading days

        # Update Table
        update_sql = """
            UPDATE performance_audit SET
                max_favorable = ?, max_adverse = ?,
                price_t10 = ?, price_t20 = ?, price_t30 = ?,
                rr_ratio = ?, grade = ?, is_matured = ?
            WHERE symbol = ? AND strategy_id = ? AND signal_date = ?
        """
        try:
            self.lib.safe_execute(
                update_sql,
                (
                    mfe,
                    mae,
                    price_t10,
                    price_t20,
                    price_t30,
                    rr_ratio,
                    grade,
                    is_matured,
                    symbol,
                    strategy_id,
                    signal_date,
                ),
            )
            return True
        except Exception:
            return False

    def get_hardening_suggestions(self):
        """
        Analyzes failed signals (Grade F) to find common technical denominators.
        """
        sql = "SELECT * FROM performance_audit WHERE grade = 'F'"
        failures = self.lib.safe_execute(sql).df()
        if failures.empty:
            return None

        # Simple Semi-Auto Logic: If a strategy has > 40% failure rate, flag it
        summary_sql = """
            SELECT strategy_id, 
                   COUNT(*) as total,
                   SUM(CASE WHEN grade = 'F' THEN 1 ELSE 0 END) as fails
            FROM performance_audit
            GROUP BY strategy_id
            HAVING total >= 5
        """
        summary = self.lib.safe_execute(summary_sql).df()

        # Optimized suggestion generation
        suggestions = [
            {
                "strategy": row.strategy_id,
                "fail_rate": round((row.fails / row.total) * 100, 1),
                "action": "Increase RDV threshold by 0.5 or add VIX Stability filter.",
            }
            for row in summary.itertuples(index=False)
            if (row.fails / row.total) * 100 > 40
        ]
        return suggestions
