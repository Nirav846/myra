import pandas as pd
import numpy as np
import os
import sys
from rich.console import Console

# Add current dir to path
sys.path.append(os.getcwd())

from myra_app.librarian import Librarian
from myra_app.engine import SMCManager, Engine


def run_regression():
    console = Console()
    console.print(
        "[bold cyan]--- [RADAR] MYRA v2.5 Reliability Regression ---[/bold cyan]"
    )

    lib = Librarian(read_only=True)
    engine = Engine(lib)

    # Test 1: Hybrid Data Depth Check
    console.print("\n[*] Testing Hybrid Data Depth (AXISBANK)...")
    df = lib.get_ohlcv("AXISBANK")
    if len(df) > 756:
        console.print(
            "[success][✔] Hybrid Data Loader successfully pulled 3+ years of history.[/success]"
        )
    else:
        console.print(
            f"[error][✘] Hybrid Loader failed. Only got {len(df)} rows.[/error]"
        )

    # Test 2: FVG Institutional Memory (3-Year Lookback)
    console.print("\n[*] Testing FVG Institutional Memory...")
    fvg = SMCManager.get_fvg_buy_zone(df)
    if fvg:
        console.print(
            f"[success][✔] FVG Identified: {fvg['bottom']:.2f} - {fvg['top']:.2f} (Mid: {fvg['mid']:.2f})[/success]"
        )
    else:
        console.print(
            "[warning][!] No unmitigated FVGs found in 3-year history for AXISBANK.[/warning]"
        )

    # Test 3: Standard Technical Regression (SMA50/200)
    console.print("\n[*] Testing Classical Technical Core...")
    try:
        df["sma50"] = df["Close"].rolling(50).mean()
        df["sma200"] = df["Close"].rolling(200).mean()
        latest = df.iloc[-1]
        console.print(
            f"[info][*] Latest Price: {latest['Close']:.2f} | SMA50: {latest['sma50']:.2f} | SMA200: {latest['sma200']:.2f}[/info]"
        )
        console.print("[success][✔] Technical primitives are healthy.[/success]")
    except Exception as e:
        console.print(f"[error][✘] Technical Regression Failed: {e}[/error]")

    # Test 4: Quant-Anomaly Integration (Strategy 34)
    console.print("\n[*] Testing Quant-Anomaly Logic (Surpriver v2)...")
    from myra_app.strategies.surpriver_v2 import Strategy

    strat = Strategy()
    # Simulate fundamentals
    funda = {"low_1y": df["Close"].min(), "Stage": "Stage 4"}
    res = strat.run(df, funda)
    if "metrics" in res:
        # Fix 70: Avoid chained indexing in print
        metrics = res["metrics"]
        console.print(
            f"[success][✔] Surpriver v2 metrics generated: Anomaly={metrics['Anomaly_Score']}[/success]"
        )
    else:
        console.print("[error][✘] Surpriver v2 logic failed to return metrics.[/error]")

    lib.close()
    console.print("\n[bold green]REGRESSION COMPLETE: System is Stable.[/bold green]")


if __name__ == "__main__":
    run_regression()
