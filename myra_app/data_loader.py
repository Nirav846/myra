import os
from datetime import datetime

import numpy as np
import pandas as pd

from myra_app import constants
from myra_app import indicator_lake
from myra_core.utils.data_validation import enforce_index_contract


class IndicatorManager:
    """
    MYRA Indicator Lake Manager (v3.0 ATOMIC)
    Isolates indicator storage by strategy to prevent schema-coupling errors.
    Stored in: data/indicators/{strategy_id}/{symbol}.parquet

    Paths resolve through ``myra_app.indicator_lake`` (anchored to
    ``constants.DATA_DIR``) rather than ``os.getcwd()``. The cwd-relative form
    meant that running MYRA from any other working directory silently created a
    *second*, empty lake — which is how ``myra_app/data/indicators`` came to
    exist alongside the real ``data/indicators``. Nothing here creates a
    directory until something is actually saved, so a read-only status probe
    cannot materialise an empty lake as a side effect.
    """

    def __init__(self):
        pass

    @property
    def base_dir(self) -> str:
        """Indicators root. Resolved on every access, never cached at import."""
        return indicator_lake.indicators_root()

    def _get_path(self, strategy_id: str, symbol: str) -> str:
        """Write path: creates the strategy directory, then returns the file."""
        strat_dir = indicator_lake.strategy_dir(strategy_id)
        os.makedirs(strat_dir, exist_ok=True)
        return os.path.join(strat_dir, f"{symbol.upper()}.parquet")

    def _read_path(self, strategy_id: str, symbol: str) -> str:
        """Read path: pure arithmetic, so reading never creates a directory."""
        return indicator_lake.symbol_path(strategy_id, symbol)

    def save_indicators(self, strategy_id: str, symbol: str, df: pd.DataFrame):
        """Save calculated indicators for a strategy/symbol pair, atomically.

        Written to a sibling temp file and moved into place with ``os.replace``
        so an interrupted or failed write can never leave a partial parquet file
        that later loads as if it were a complete artifact. ``os.replace`` is
        atomic on both POSIX and Windows.
        """
        if df.empty:
            return
        path = self._get_path(strategy_id, symbol)
        tmp_path = f"{path}.tmp"
        try:
            # Ensure date index is preserved and saved
            df.to_parquet(tmp_path, compression="snappy", index=True)
            os.replace(tmp_path, path)
        except BaseException:
            # Never leave the scratch file behind, and never let a half-written
            # artifact masquerade as a healthy one.
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except OSError:
                pass
            raise

    def load_indicators(self, strategy_id: str, symbol: str) -> pd.DataFrame:
        """Loads indicators for a specific strategy and symbol."""
        path = self._read_path(strategy_id, symbol)
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
        self.parquet_dir = os.path.join(constants.DATA_DIR, "ohlcv_cache")
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

        # Enforce Standardized Datetime Index for all Parquet files
        if not isinstance(df.index, pd.DatetimeIndex):
            try:
                df.index = pd.to_datetime(df.index, errors="coerce").dt.normalize()
                df = df[df.index.notna()]
            except Exception:
                pass

        path = os.path.join(self.parquet_dir, f"{symbol.upper()}.parquet")
        df.to_parquet(path, compression="snappy")

    def append_to_parquet(self, symbol: str, new_df: pd.DataFrame):
        """Efficiently merges new candles with existing Parquet data."""
        if new_df.empty:
            return
        existing = self.load_from_parquet(symbol)
        if not existing.empty:
            df = pd.concat([existing, new_df])
            df = enforce_index_contract(df)
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
