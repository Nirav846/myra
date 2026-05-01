import traceback

import numpy as np
import pandas as pd

from myra_app.librarian_intelligence import LibrarianIntelligenceMixin


class DummyLoader:
    class Indicators:
        def save_indicators(self, category, sym, df):
            print(
                f"Saved indicators for {sym}: rows={len(df)} columns={list(df.columns)[:10]}"
            )

    def __init__(self):
        self.indicators = DummyLoader.Indicators()


class Dummy(LibrarianIntelligenceMixin):
    def __init__(self):
        self.loader = DummyLoader()
        self.read_only = False

    def get_active_universe(self):
        return ["DUMMY"]

    def get_ohlcv(self, sym):
        # produce 60 business days of synthetic data
        idx = pd.date_range(end=pd.Timestamp.today(), periods=60, freq="B")
        close = np.linspace(100.0, 105.0, len(idx)) + np.random.normal(0, 0.2, len(idx))
        high = close + np.random.uniform(0.1, 0.5, len(idx))
        low = close - np.random.uniform(0.1, 0.5, len(idx))
        open_ = close + np.random.normal(0, 0.1, len(idx))
        volume = np.random.randint(1000, 5000, len(idx)).astype(float)
        # make last 5 days high delivery to trigger heavy absorption
        delivery_qty = (volume * 0.2).copy()
        delivery_pct = (delivery_qty / volume) * 100.0
        delivery_qty[-5:] = volume[-5:] * 0.7
        delivery_pct = (delivery_qty / volume) * 100.0
        df = pd.DataFrame(
            {
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
                "volume": volume,
                "delivery_qty": delivery_qty,
                "delivery_pct": delivery_pct,
            },
            index=idx,
        )
        return df


if __name__ == "__main__":
    d = Dummy()
    try:
        d.update_indicator_history()
        print("Smoke run completed without exceptions")
    except Exception:
        print("Smoke run FAILED:")
        traceback.print_exc()
