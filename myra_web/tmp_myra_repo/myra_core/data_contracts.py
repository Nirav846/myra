"""
MYRA Data Contracts – Guarantees clean DataFrames before any scan or indicator.
"""

import pandas as pd


def enforce_ohlcv_contract(df, symbol="UNKNOWN"):
    """
    Ensures every DataFrame entering a strategy is clean, sorted, deduplicated,
    and contains the required OHLCV columns. Raises ValueError on critical failures.
    """
    if df is None or df.empty:
        return df

    df = df.copy()

    # Normalize index to clean dates
    if not isinstance(df.index, pd.DatetimeIndex):
        try:
            df.index = pd.to_datetime(df.index, errors="coerce")
        except Exception:
            pass

    df = df.sort_index()
    df = df.loc[~df.index.duplicated(keep="last")]

    # Ensure core columns exist (CamelCase)
    required = ["Open", "High", "Low", "Close", "Volume"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"[{symbol}] Missing columns: {missing}")

    # Coerce core columns to numeric
    for col in required:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    return df
