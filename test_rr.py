import pandas as pd
from myra_app.strategies.fusion_engine import FusionEngine

engine = FusionEngine()
df = pd.DataFrame({
    "close": [101.0]*60,
    "htf_bullish": [1]*60,
    "mtf_bullish": [1]*60,
    "fvg_top": [102]*60,
    "fvg_bottom": [100]*60, # entry = 101, sl = 99.5, risk = 1.5
    "fvg_boundary": [100]*60,
    "swing_high": [102]*60, # tp = 102, reward = 1.0 (RR < 2.0)
    "swing_low": [90]*60,
    "fvg_freshness": [0.5]*60,
    "liquidity_distance": [0.5]*60,
    "trend_alignment": [0.5]*60,
    "delivery_qty": [1000]*60,
    "delivery_ma_60": [500]*60,
})

res = engine.run(df, {})
print("Bad RR test:", res)
