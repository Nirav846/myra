import logging

import pandas as pd

logger = logging.getLogger(__name__)

STRICT_INDEX_MODE = True


def enforce_index_contract(df, symbol="UNKNOWN"):
    """
    Enforces the core invariant: df.index MUST be unique.
    Applied before any merge/concat/join/reindex or before passing df to strategies.
    """
    if df is None or df.empty:
        return df

    df = df.copy()

    # 1. Normalize Date Index (if applicable)
    if isinstance(df.index, pd.DatetimeIndex) or df.index.name in ("date", "Date"):
        df.index = pd.to_datetime(df.index, errors="coerce").normalize()
    # If date is column
    elif "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.normalize()
        df = df.dropna(subset=["date"])
        df = df.sort_values("date")
        df = df.groupby("date", as_index=False).last()
        df = df.set_index("date")

    # 2. Drop NA indices
    df = df.loc[~df.index.isna()]

    # 3. Sort & Deduplicate
    df = df.sort_index()
    df = df.loc[~df.index.duplicated(keep="last")]

    # 4. Final Assertion
    if STRICT_INDEX_MODE:
        assert df.index.is_unique, f"[{symbol}] duplicate index AFTER enforcement"

    return df


def validate_dataframe(df: pd.DataFrame, context: str = "General") -> pd.DataFrame:
    """
    Enforces the MYRA Data Contract.
    Loud failure is better than silent corruption.
    """
    if df is None or df.empty:
        return df

    # 1. Column Uniqueness
    if df.columns.duplicated().any():
        dupes = df.columns[df.columns.duplicated()].unique().tolist()
        raise ValueError(f"[{context}] Duplicate Column Labels: {dupes}")

    # 2. Index Uniqueness (The 'cannot reindex' killer)
    if df.index.duplicated().any():
        offending = df.index[df.index.duplicated()].unique().tolist()
        raise ValueError(f"[{context}] Duplicate Index Labels: {offending[:3]}...")

    # 3. Dtype Integrity
    # Ensure critical numeric fields aren't 'object' dtypes
    critical_numerics = [
        "close",
        "volume",
        "delivery_pct",
        "Close",
        "Volume",
        "DeliveryPct",
        "DELIV_QTY",
        "DELIV_PER",
    ]
    for col in [c for c in critical_numerics if c in df.columns]:
        if not pd.api.types.is_numeric_dtype(df[col]):
            raise TypeError(f"[{context}] Non-numeric dtype in {col}: {df[col].dtype}")

    return df
