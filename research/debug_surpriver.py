import pandas as pd
import numpy as np
import os
import sys
from rich.console import Console

# Add current dir to path
sys.path.append(os.getcwd())

from myra_app.librarian import Librarian
from myra_app.strategies.surpriver_v2 import Strategy


def debug_surpriver():
    console = Console()
    lib = Librarian(read_only=True)
    strat = Strategy()

    symbols = ["RELIANCE", "AXISBANK", "ECLERX", "ICICIBANK", "BLUESTONE"]

    for symbol in symbols:
        console.print(f"\n--- DEBUGGING Surpriver v2 for {symbol} ---")
        df = lib.get_ohlcv(symbol)
        if df is None or df.empty:
            console.print(f"[!] No data for {symbol}")
            continue

        # Get indicators for this stock from DB
        ind = lib.conn.execute(  # noqa: performance
            "SELECT * FROM calculated_indicators WHERE symbol = ? ORDER BY date DESC LIMIT 1",
            (symbol,),
        ).df()
        if ind.empty:
            console.print(f"[!] No indicators for {symbol}")
            continue

        funda = ind.iloc[0].to_dict()
        # Ensure AD_Flow is available (case-insensitive check)
        funda["AD_Flow"] = funda.get("ad_flow", 0)

        res = strat.run(df, funda)

        console.print(
            f"[*] Anomaly Score: {res.get('metrics', {}).get('Anomaly_Score', 0)}"
        )
        console.print(
            f"[*] Active Windows: {res.get('metrics', {}).get('Active_Windows', '0/5')}"
        )
        console.print(f"[*] AD_Flow: {funda.get('AD_Flow', 0)}")
        console.print(f"[*] Signal: {res.get('signal')}")

    lib.close()


if __name__ == "__main__":
    debug_surpriver()
