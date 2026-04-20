import pandas as pd
import numpy as np

from myra_app.strategies.fusion_engine import run

df = pd.DataFrame({
    "open": [100]*100,
    "high": [100]*100,
    "low": [100]*100,
    "close": [100]*100,
    "volume": [1000]*100,
    "htf_bullish": [1]*100,
    "mtf_bullish": [1]*100,
    "fvg_top": [102]*100,
    "fvg_bottom": [100]*100,
    "fvg_boundary": [101]*100,
    "swing_high": [110]*100,
    "swing_low": [90]*100,
    "fvg_freshness": [0.5]*100,
    "liquidity_distance": [0.5]*100,
    "trend_alignment": [0.5]*100,
    "delivery_qty": [1000]*100,
    "delivery_ma_60": [500]*100,
})

res = run(df)
print(res)
