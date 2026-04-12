import polars as pl
import pandas as pd

df = pd.DataFrame({
    "pct_above_ma50_60d": [0.5, None],
    "_rank_delivery": [0.8, None],
    "_rank_sm_score": [0.9, None],
    "_rank_volume": [0.7, None],
    "atr_pct": [0.02, 0.04],
    "keltner_upper": [105, 110],
    "keltner_lower": [95, 90],
    "close": [100, 100],
    "atr5": [2, 10],
    "_rank_roe": [0.6, None],
    "_rank_growth": [0.7, None],
    "F_Score": [4, 0],
    "graham_number": [120, 0],
    "drawdown": [0.1, 0.5]
})

df_pl = pl.from_pandas(df)

s = pl.col("pct_above_ma50_60d").fill_null(0.3)

d_rank = pl.col("_rank_delivery").fill_null(0.3)
sm_rank = pl.col("_rank_sm_score").fill_null(0.3)
d = sm_rank * 0.6 + d_rank * 0.4

l = pl.col("_rank_volume").fill_null(0.3)

atr = pl.col("atr_pct").fill_null(0.05)
b_score = pl.when(atr < 0.03).then(0.5).when(atr < 0.05).then(0.3).otherwise(0.0)

upper_k = pl.col("keltner_upper").fill_null(0.0)
lower_k = pl.col("keltner_lower").fill_null(0.0)
close_pl = pl.col("close").fill_null(0.0)
atr5 = pl.col("atr5").fill_null(0.0)

sqz = pl.when(
    (upper_k > 0) & (close_pl > 0) & (atr5 > 0) &
    ((close_pl + atr5) < upper_k) & ((close_pl - atr5) > lower_k)
).then(0.5).otherwise(0.0)

b_raw = b_score + sqz
b = pl.when(b_raw > 0).then(
    pl.when(b_raw > 1.0).then(1.0).otherwise(b_raw)
).otherwise(0.3)

roe_score = pl.col("_rank_roe").fill_null(0.4)
growth_score = pl.col("_rank_growth").fill_null(0.4)
f_score_val = pl.col("F_Score").fill_null(0.0)
quality_score = f_score_val / 5.0
f = roe_score * 0.4 + growth_score * 0.3 + quality_score * 0.3

# I don't need valuation_score in `compute_score` since it wasn't used in rank directly
print(df_pl.with_columns(s=s, d=d, l=l, b=b, f=f))
