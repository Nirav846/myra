import pandas as pd
import numpy as np
import os
import duckdb
import sys

# Add current dir to path
sys.path.append(os.getcwd())

from myra_app.librarian import Librarian
from myra_app.engine import SMCManager


def debug_fvg():
    lib = Librarian(read_only=True)
    symbols = ["AXISBANK", "BLUESTONE", "CASTROLIND"]

    for symbol in symbols:
        print(f"\n--- DEBUGGING FVG for {symbol} ---")
        df = lib.get_ohlcv(symbol)
        if df is None or df.empty:
            print(f"[!] No data for {symbol}")
            continue

        print(f"[*] Data size: {len(df)} rows. Columns: {df.columns.tolist()}")

        # Test 1: Raw FVG Detection (Bullish: Low[i] > High[i-2])
        highs = df["High"].values
        lows = df["Low"].values

        gaps_found = 0
        for i in range(len(df) - 1, 2, -1):
            if lows[i] > highs[i - 2]:
                gaps_found += 1
                bottom = highs[i - 2]
                top = lows[i]
                mid = (top + bottom) / 2
                print(
                    f"  [Found Gap] Index {i} | Zone: {bottom:.2f} - {top:.2f} | Mid: {mid:.2f}"
                )

                # Check mitigation
                closes = df["Close"].values
                is_invalid = False
                for j in range(i + 1, len(df)):
                    if closes[j] < mid:
                        is_invalid = True
                        print(
                            f"    [!] Mitigated at index {j} (Close {closes[j]:.2f} < Mid {mid:.2f})"
                        )
                        break
                if not is_invalid:
                    print("    [✔] VALID VIRGIN FVG!")

        if gaps_found == 0:
            print("[!] No Bullish FVGs found in history.")

        # Test 2: Call the actual engine method
        res = SMCManager.get_fvg_buy_zone(df)
        print(f"[*] Engine Method Result: {res}")

    lib.close()


if __name__ == "__main__":
    debug_fvg()
