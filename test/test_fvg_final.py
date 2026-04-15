import pandas as pd
import numpy as np
import os
import sys
from unittest.mock import patch

# Add current dir to path
sys.path.append(os.getcwd())

from myra_app.librarian import Librarian
from myra_app.engine import SMCManager


def test_fvg_final():
    db_name = "test_fvg_tech.db"
    db_dir = os.path.join(os.getcwd(), "db")
    os.makedirs(db_dir, exist_ok=True)
    db_path = os.path.join(db_dir, db_name)

    if os.path.exists(db_path):
        os.remove(db_path)

    symbol = "ECLERX"
    print(f"\n--- DEEP FVG AUDIT for {symbol} ---")

    with patch.dict(Librarian.DB_MAP, {"technical": db_name}):
        lib = Librarian(read_only=False)
        lib._create_tables()

        # Seed mock data for technical_data table
        dates = pd.date_range("2023-01-01", periods=10).strftime("%Y-%m-%d")
        highs = [100, 102, 105, 110, 115, 120, 125, 130, 135, 140]
        lows =  [95,  98,  100, 106, 110, 115, 120, 125, 130, 135]
        closes = [98, 100, 103, 108, 113, 118, 123, 128, 133, 138]

        for i in range(10):
            lib._tech_conn.execute(
                "INSERT INTO technical_data (symbol, date, open, high, low, close, volume, delivery) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (symbol, dates[i], 98, highs[i], lows[i], closes[i], 1000, 500)
            )
        lib._tech_conn.commit()

        # Fetch full price history as dataframe
        res = lib._tech_conn.execute(
            f"SELECT * FROM technical_data WHERE symbol='{symbol}' ORDER BY date ASC"
        ).fetchall()

        columns = ["symbol", "date", "open", "high", "low", "close", "volume", "delivery", "trades", "vwap", "delivery_pct", "delivery_ratio"]
        df = pd.DataFrame(res, columns=columns)

        print(f"[*] History Depth: {len(df)} bars.")

        res_fvg = SMCManager.get_fvg_buy_zone(df)
        if res_fvg:
            print(f"[✔] FVG FOUND: {res_fvg}")
        else:
            print("[!] No Unmitigated Bullish FVG found below price in 3 years.")

            # Manual scan to see why
            highs = df["high"].values
            lows = df["low"].values
            closes = df["close"].values
            ltp = closes[-1]

            found_any = 0
            for i in range(len(df) - 1, max(2, len(df) - 756), -1):
                if lows[i] > highs[i - 2]:
                    found_any += 1
                    zone = (highs[i - 2], lows[i])
                    if ltp < zone[0]:
                        print(
                            f"  [*] FVG @ idx {i} ({zone[0]:.2f}-{zone[1]:.2f}) is ABOVE price (LTP {ltp:.2f}). Ignored."
                        )
                    else:
                        # Check mitigation
                        for j in range(i + 1, len(df)):
                            if closes[j] < (zone[0] * 0.995):
                                # print(f"  [*] FVG @ idx {i} was MITIGATED at idx {j}.")
                                break
                        else:
                            print(
                                f"  [✔] VIRGIN FVG FOUND @ idx {i} ({zone[0]:.2f}-{zone[1]:.2f})!"
                            )

        lib.close()

    if os.path.exists(db_path):
        os.remove(db_path)

if __name__ == "__main__":
    test_fvg_final()
