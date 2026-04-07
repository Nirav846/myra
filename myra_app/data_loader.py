import os
import pandas as pd
import numpy as np
from datetime import datetime


class IndicatorManager:
    """
    MYRA Indicator Lake Manager (v3.0 ATOMIC)
    Isolates indicator storage by strategy to prevent schema-coupling errors.
    Stored in: data/indicators/{strategy_id}/{symbol}.parquet
    """

    def __init__(self):
        self.base_dir = os.path.join(os.getcwd(), "data", "indicators")
        if not os.path.exists(self.base_dir):
            os.makedirs(self.base_dir)

    def _get_path(self, strategy_id: str, symbol: str) -> str:
        strat_dir = os.path.join(self.base_dir, strategy_id.lower())
        if not os.path.exists(strat_dir):
            os.makedirs(strat_dir)
        return os.path.join(strat_dir, f"{symbol.upper()}.parquet")

    def save_indicators(self, strategy_id: str, symbol: str, df: pd.DataFrame):
        """Saves calculated indicators for a strategy/symbol pair."""
        if df.empty:
            return
        path = self._get_path(strategy_id, symbol)
        # Ensure date index is preserved and saved
        df.to_parquet(path, compression="snappy", index=True)

    def load_indicators(self, strategy_id: str, symbol: str) -> pd.DataFrame:
        """Loads indicators for a specific strategy and symbol."""
        path = self._get_path(strategy_id, symbol)
        if os.path.exists(path):
            return pd.read_parquet(path)
        return pd.DataFrame()

    def merge_indicators(self, symbol: str, strategy_ids: list) -> pd.DataFrame:
        """Composes a multi-strategy view for a symbol (Virtual Join)."""
        combined = pd.DataFrame()
        for sid in strategy_ids:
            df = self.load_indicators(sid, symbol)
            if not df.empty:
                if combined.empty:
                    combined = df
                else:
                    # Join on date index
                    combined = combined.join(df, how="outer", rsuffix=f"_{sid}")
        return combined


class StockDataLoader:
    """
    MYRA Smart DataLoader (v3.0 ATOMIC)
    Handles data normalization, local Parquet caching, and validation.
    """

    def __init__(self):
        self.parquet_dir = os.path.join(os.getcwd(), "data", "ohlcv_cache")
        if not os.path.exists(self.parquet_dir):
            os.makedirs(self.parquet_dir)
        self.indicators = IndicatorManager()

    def normalize_ohlcv(self, df: pd.DataFrame) -> pd.DataFrame:
        """Ensures consistent OHLCV casing and column order."""
        if df.empty:
            return df

        # Mapping for common casing variations
        mapping = {
            "open": "Open",
            "high": "High",
            "low": "Low",
            "close": "Close",
            "volume": "Volume",
            "OPEN": "Open",
            "HIGH": "High",
            "LOW": "Low",
            "CLOSE": "Close",
            "VOLUME": "Volume",
        }
        df.rename(columns=mapping, inplace=True)

        required = ["Open", "High", "Low", "Close", "Volume"]
        for col in required:
            if col not in df.columns:
                df[col] = np.nan

        return df[required + [c for c in df.columns if c not in required]]

    def save_to_parquet(self, symbol: str, df: pd.DataFrame):
        """Saves a symbol's history to high-speed Parquet format."""
        if df.empty:
            return
        path = os.path.join(self.parquet_dir, f"{symbol.upper()}.parquet")
        df.to_parquet(path, compression="snappy")

    def append_to_parquet(self, symbol: str, new_df: pd.DataFrame):
        """Efficiently merges new candles with existing Parquet data."""
        if new_df.empty:
            return
        existing = self.load_from_parquet(symbol)
        if not existing.empty:
            df = pd.concat([existing, new_df])
            df = df[~df.index.duplicated(keep="last")].sort_index()
        else:
            df = new_df
        self.save_to_parquet(symbol, df)

    def load_from_parquet(self, symbol: str) -> pd.DataFrame:
        """Loads a symbol's history from local Parquet store."""
        path = os.path.join(self.parquet_dir, f"{symbol.upper()}.parquet")
        if os.path.exists(path):
            return pd.read_parquet(path)
        return pd.DataFrame()

    def validate_integrity(self, df: pd.DataFrame) -> bool:
        """Checks for common data issues like gaps or zeros in price."""
        if df.empty or len(df) < 20:
            return False
        if (df["Close"] == 0).any():
            return False
        if df.index.duplicated().any():
            return False
        return True
