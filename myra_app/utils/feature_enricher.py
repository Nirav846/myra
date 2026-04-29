import os

import polars as pl

# Optimization for AMD A8-7410 (4 cores)
os.environ["POLARS_MAX_THREADS"] = "2"


class FeatureEnricher:
    def __init__(self, library):
        self.lib = library

    def enrich(self, candidates):
        if not candidates:
            return []

        # 1. Convert only the top 20 candidates to a Polars LazyFrame
        lf = pl.DataFrame(candidates).lazy()

        # 2. Native Polars Math (Using 'LTP' as the price column)
        enriched_lf = (
            lf.with_columns(
                [
                    # Volatility (Rolling Standard Deviation of LTP over 14 periods)
                    pl.col("LTP").rolling_std(window_size=14).alias("Volatility"),
                    # RSI Logic (Using 'LTP' for price differences)
                    (pl.col("LTP").diff().alias("diff")),
                ]
            )
            .with_columns(
                [
                    pl.when(pl.col("diff") > 0)
                    .then(pl.col("diff"))
                    .otherwise(0)
                    .alias("gain"),
                    pl.when(pl.col("diff") < 0)
                    .then(pl.col("diff").abs())
                    .otherwise(0)
                    .alias("loss"),
                ]
            )
            .with_columns(
                [
                    pl.col("gain").ewm_mean(span=14, adjust=False).alias("avg_gain"),
                    pl.col("loss").ewm_mean(span=14, adjust=False).alias("avg_loss"),
                ]
            )
            .with_columns(
                [
                    (
                        100
                        - (
                            100
                            / (1 + (pl.col("avg_gain") / (pl.col("avg_loss") + 1e-10)))
                        )
                    ).alias("RSI")
                ]
            )
            .drop(["diff", "gain", "loss", "avg_gain", "avg_loss"])
        )  # Cleanup temporary math columns

        # 3. Collect the results
        results = enriched_lf.collect().to_dicts()
        return results
