# myra_app/debug_zero_scanners.py
"""Check why certain scanners return 0 hits"""
import warnings, traceback
import pandas as pd
from .data_adapter import DataAdapter
from .librarian import Librarian
from .scanners import primitives as prim_module

warnings.filterwarnings("ignore")

lib = Librarian(read_only=True)
adapter = DataAdapter(librarian=lib)

SYMBOLS = ["RELIANCE", "TCS"]
LOOKBACK = 300
SCANNERS_TO_CHECK = ["107", "109", "110", "2", "3", "12", "28", "35", "A5"]

for sym in SYMBOLS:
    print(f"\n{'='*60}")
    print(f"DEBUG {sym}")
    print("=" * 60)
    try:
        df = adapter.get_price_df(sym, lookback_days=LOOKBACK)
        if df.empty:
            print("No price data")
            continue
        funda = adapter.get_latest_funda(sym, df=df)
        # Show key columns on last row
        last = df.iloc[-1]
        c = df["Close"]
        vol = df["Volume"]

        for sid in SCANNERS_TO_CHECK:
            try:
                result = prim_module.run_scanner(df, sid, funda=funda)
                # Extra info for specific scanners
                extra = ""
                if sid == "107":
                    try:
                        import pandas_ta as ta

                        bb = ta.bbands(c, length=20)
                        width = (
                            bb["BBU_20_2.0"].iloc[-1] - bb["BBL_20_2.0"].iloc[-1]
                        ) / bb["BBM_20_2.0"].iloc[-1]
                        extra = f" | Bollinger width: {width:.4f} (<0.05 needed)"
                    except Exception as e:
                        extra = f" | Error computing width: {e}"
                elif sid == "109":
                    l1y = funda.get("low_1y", 0)
                    extra = f" | low_1y={l1y} | close={c.iloc[-1]:.2f}"
                elif sid == "110":
                    l2y = funda.get("low_2y", 0)
                    extra = f" | low_2y={l2y} | close={c.iloc[-1]:.2f}"
                elif sid in ("2", "3", "12", "28", "35", "A5"):
                    extra = f" | funda keys: {list(funda.keys())[:10]}..."
                print(f"  {sid}: {result}{extra}")
            except Exception as e:
                print(f"  {sid}: ERROR {type(e).__name__} - {str(e)[:100]}")
    except Exception as e:
        print(f"ERROR: {e}")
