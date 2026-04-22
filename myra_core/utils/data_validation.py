import pandas as pd
import logging

logger = logging.getLogger(__name__)

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
    critical_numerics = ['close', 'volume', 'delivery_pct', 'Close', 'Volume', 'DeliveryPct', 'DELIV_QTY', 'DELIV_PER']
    for col in [c for c in critical_numerics if c in df.columns]:
        if not pd.api.types.is_numeric_dtype(df[col]):
            raise TypeError(f"[{context}] Non-numeric dtype in {col}: {df[col].dtype}")

    return df
