#!/usr/bin/env python
import glob
import os
import sys

import numpy as np
import pandas as pd

# Fix path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

from myra_core.utils.myra_log import myra_log

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def analyze_factor_importance(lake_dir=os.path.join(PROJECT_ROOT, "data", "lake")):
    """
    Analyzes which factors actually lead to 3-month (60-day) forward returns.
    Inspired by AlphaPy.
    """
    files = glob.glob(os.path.join(lake_dir, "*.parquet"))
    if not files:
        print("[!] No data in lake. Run DataExporter first.")
        return

    # Optimized with list comprehension (Fix 39: Avoid .append in loop)
    def _get_corr(f):
        try:
            df = pd.read_parquet(f)
            if len(df) < 100:
                return None

            # 1. Target: 60-day Forward Returns
            df["target_return"] = df["Close"].shift(-60) / df["Close"] - 1

            # 2. Factors (Computed on the fly for backtest)
            df["f_delivery"] = df["delivery_percent"].rolling(5).mean()
            df["f_rs"] = df["Close"] / df["Close"].shift(252)
            df["f_volatility"] = (df["High"] - df["Low"]).rolling(20).mean() / df[
                "Close"
            ]

            # 3. Correlation
            cols = ["f_delivery", "f_rs", "f_volatility"]
            return (
                df[cols + ["target_return"]]
                .corr()["target_return"]
                .drop("target_return")
            )
        except:
            return None

    total_files = len(files)
    all_correlations = [
        c
        for i, f in enumerate(files, 1)
        if (myra_log(i, total_files, desc="Analyzing Factors")) is not None
        and (c := _get_corr(f)) is not None
    ]

    if all_correlations:
        results = pd.DataFrame(all_correlations).mean()
        print("\n--- FACTOR ALPHA REPORT (Institutional) ---")
        print("Correlation with 3-Month Forward Returns:")
        for factor, val in results.items():
            print(f" • {factor:15}: {val:.4f}")
        print("------------------------------------------")
    else:
        print("[!] Insufficient data for correlation analysis.")


if __name__ == "__main__":
    analyze_factor_importance()
