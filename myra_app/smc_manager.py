"""MYRA SMC Manager - Institutional Accumulation Engine (SMC-1)"""
import pandas as pd
import numpy as np


class SMCManager:
    """
    MYRA SMC Manager - Institutional Accumulation Engine (SMC-1)
    Focuses on Delivery Point of Control (D-POC) and Multi-Scale Trend Confluence.
    """

    @staticmethod
    def calculate_fvg(df):
        if len(df) < 3:
            return pd.Series(0, index=df.index)

        fvg = np.zeros(len(df))
        highs = df["high"].values
        lows = df["low"].values

        gap_threshold = 0.002

        for i in range(2, len(df)):
            if lows[i] > highs[i - 2] * (1 - gap_threshold):
                fvg[i] = 1
            elif highs[i] < lows[i - 2] * (1 + gap_threshold):
                fvg[i] = -1
        return pd.Series(fvg, index=df.index)

    @staticmethod
    def get_fvg_buy_zone(df):
        if len(df) < 3:
            return None

        try:
            cols = {c.lower(): c for c in df.columns}
            h_col = cols.get("high")
            l_col = cols.get("low")
            c_col = cols.get("close")
            if not h_col or not l_col or not c_col:
                return None

            highs = df[h_col].values
            lows = df[l_col].values
            closes = df[c_col].values
            ltp = closes[-1]

            for i in range(len(df) - 1, max(2, len(df) - 756), -1):
                if lows[i] > highs[i - 2]:
                    bottom = highs[i - 2]
                    top = lows[i]
                    mid = (top + bottom) / 2

                    if ltp < bottom:
                        continue

                    is_dead = False
                    for j in range(i + 1, len(df)):
                        if closes[j] < (bottom * 0.985):
                            is_dead = True
                            break

                    if not is_dead:
                        return {"top": top, "bottom": bottom, "mid": mid}
            return None
        except Exception:
            return None

    @staticmethod
    def calculate_market_structure(df, window=3):
        if len(df) < window * 2 + 2:
            return pd.Series(0, index=df.index), pd.Series(0, index=df.index)

        bos = np.zeros(len(df))
        choch = np.zeros(len(df))

        last_high = np.nan
        last_low = np.nan
        last_swing_index = 0
        trend = 0

        c_prices = df["close"].values
        h_prices = df["high"].values
        l_prices = df["low"].values

        for i in range(len(df)):
            conf_idx = i - window
            if conf_idx >= window:
                window_slice_h = h_prices[conf_idx - window : conf_idx + window + 1]
                if h_prices[conf_idx] == np.max(window_slice_h):
                    last_high = h_prices[conf_idx]
                    last_swing_index = i

                window_slice_l = l_prices[conf_idx - window : conf_idx + window + 1]
                if l_prices[conf_idx] == np.min(window_slice_l):
                    last_low = l_prices[conf_idx]
                    last_swing_index = i

            if i - last_swing_index > 60:
                last_high = np.nan
                last_low = np.nan

            if trend == 1:
                if not np.isnan(last_high) and c_prices[i] > last_high:
                    bos[i] = 1
                elif not np.isnan(last_low) and c_prices[i] < last_low:
                    choch[i] = -1
                    trend = -1
            elif trend == -1:
                if not np.isnan(last_low) and c_prices[i] < last_low:
                    bos[i] = -1
                elif not np.isnan(last_high) and c_prices[i] > last_high:
                    choch[i] = 1
                    trend = 1
            else:
                if not np.isnan(last_high) and c_prices[i] > last_high:
                    trend = 1
                if not np.isnan(last_low) and c_prices[i] < last_low:
                    trend = -1

        return pd.Series(bos, index=df.index), pd.Series(choch, index=df.index)

    @staticmethod
    def calculate_d_poc(df, buckets=50):
        if df.empty or "close" not in df.columns or "delivery_qty" not in df.columns:
            return 0.0

        try:
            valid_df = df.dropna(subset=["close", "delivery_qty"])
            if valid_df.empty:
                return 0.0

            prices = valid_df["close"].values
            delivery = valid_df["delivery_qty"].values
            delivery = delivery.astype(float)
            p_min, p_max = prices.min(), prices.max()
            if p_max == p_min:
                return float(p_min)

            hist, bin_edges = np.histogram(
                prices, bins=buckets, range=(p_min, p_max), weights=delivery
            )

            max_idx = np.argmax(hist)
            d_poc = (bin_edges[max_idx] + bin_edges[max_idx + 1]) / 2

            if d_poc == 0 and p_min > 0:
                return float(prices[-1])

            return float(d_poc)
        except Exception:
            return 0.0

    @staticmethod
    def get_confluence_score(df):
        if len(df) < 40:
            return 0.0

        try:
            returns = np.log(df["close"] / df["close"].shift(1)).dropna()

            def dilated_mean(series, dilation, window=5):
                subset = series.iloc[-window * dilation :: dilation]
                return subset.mean() if not subset.empty else 0.0

            c2 = dilated_mean(returns, 2)
            c4 = dilated_mean(returns, 4)
            c8 = dilated_mean(returns, 8)

            return float(c2 + c4 + c8)
        except Exception:
            return 0.0

    @staticmethod
    def get_smc_phase(df, d_poc, confluence, funda={}):
        if df.empty or d_poc == 0:
            return 0

        try:
            ltp = df["close"].iloc[-1]
            avg_vol_20 = df["volume"].rolling(20).mean().iloc[-1]
            vol_last = df["volume"].iloc[-1]
            high_60 = df["close"].rolling(60).max().iloc[-1]

            if (ltp > (d_poc * 1.03) and ltp >= high_60 and vol_last > (avg_vol_20 * 1.5) and confluence > 0):
                return 2

            price_near_poc = abs(ltp - d_poc) / d_poc <= 0.02
            std20 = funda.get("std20", 0)
            tightness = (std20 / ltp * 100) if ltp > 0 else 100
            is_tight = tightness < 1.5
            volume_dryup = vol_last < (avg_vol_20 * 0.6)

            if price_near_poc and is_tight and volume_dryup:
                return 1

            return 0
        except Exception:
            return 0
