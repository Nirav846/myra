import traceback

import numpy as np
import pandas as pd

from myra_app.librarian import Librarian

lib = Librarian(read_only=True)
df = lib.get_ohlcv("DIVISLAB")
print("DF is None:", df is None)
if df is None:
    raise SystemExit("No OHLCV data; check technical.db for DIVISLAB")

print("cols:", list(df.columns))
print("tail:\n", df.tail())

# replicate IAS block
try:
    df = df.copy()
    df.columns = [c.capitalize() for c in df.columns]
    df.index = pd.to_datetime(df.index)
    df.sort_index(inplace=True)
    df = df.loc[~df.index.duplicated(keep="last")]
    print("after uniq rows=", len(df))

    import pandas_ta as ta

    df["sma20"] = ta.sma(df["Close"], length=20)
    df["atr20"] = ta.atr(df["High"], df["Low"], df["Close"], length=20)

    if "Delivery_qty" in df.columns and "Volume" in df.columns:
        df["delivery_pct"] = (
            df["Delivery_qty"] / df["Volume"].replace(0, np.nan)
        ).fillna(0.0) * 100.0
    else:
        df["delivery_pct"] = 0.0

    df["vcp"] = 1.0 - (df["atr20"] / df["sma20"].replace(0, np.nan)).fillna(0.0)
    df["vcp"] = df["vcp"].clip(0.0, 1.0)
    rolling_mean = df["delivery_pct"].rolling(20, min_periods=1).mean()
    rolling_std = (
        df["delivery_pct"]
        .rolling(20, min_periods=1)
        .std()
        .replace(0, np.nan)
        .fillna(1.0)
    )
    df["delivery_divergence_score"] = (
        (df["delivery_pct"] - rolling_mean) / rolling_std
    ).fillna(0.0) * df["vcp"]
    print(
        "last delivery_divergence_score:",
        df["delivery_divergence_score"].iloc[-5:].tolist(),
    )
    print("last delivery_pct:", df["delivery_pct"].iloc[-5:].tolist())
except Exception:
    traceback.print_exc()
