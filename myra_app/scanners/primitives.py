import numpy as np
import pandas as pd
import pandas_ta as ta


def run_scanner(df: pd.DataFrame, scanner_id: str, funda: dict = {}) -> bool:
    """
    MYRA Technical Primitives (Low-Level Scanners)
    Designed for high-speed pre-filtering.
    """
    if len(df) < 50:
        return False

    try:
        c = df["Close"]

        # 101: RSI Oversold (< 30)
        if scanner_id == "101":
            rsi = ta.rsi(c, length=14).iloc[-1]
            return rsi < 30

        # 102: RSI Bullish (40-60)
        elif scanner_id == "102":
            rsi = ta.rsi(c, length=14).iloc[-1]
            return 40 <= rsi <= 60

        # 103: MACD Bullish Crossover
        elif scanner_id == "103":
            macd = ta.macd(c)
            return (
                macd["MACDh_12_26_9"].iloc[-1] > 0
                and macd["MACDh_12_26_9"].iloc[-2] <= 0
            )

        # 104: Golden Cross (50 SMA > 200 SMA)
        elif scanner_id == "104":
            ma50 = ta.sma(c, length=50).iloc[-1]
            ma200 = ta.sma(c, length=200).iloc[-1]
            return ma50 > ma200

        # 105: Volume Surge (2x Avg)
        elif scanner_id == "105":
            avg_vol = df["Volume"].rolling(window=20).mean().iloc[-1]
            return df["Volume"].iloc[-1] > (avg_vol * 2.0)

        # 106: Near 52-Week High (within 2%)
        elif scanner_id == "106":
            h52w = c.tail(252).max()
            return c.iloc[-1] >= (h52w * 0.98)

        # 107: Bollinger Band Squeeze (Width < 5%)
        elif scanner_id == "107":
            bb = ta.bbands(c, length=20)
            width = (bb["BBu_20_2.0_2.0"].iloc[-1] - bb["BBl_20_2.0_2.0"].iloc[-1]) / bb[
                "BBM_20_2.0_2.0"
            ].iloc[-1]
            return width < 0.05

        # 108: Above 200 SMA (Bullish Trend)
        elif scanner_id == "108":
            ma200 = ta.sma(c, length=200).iloc[-1]
            return c.iloc[-1] > ma200

        # 109: 1-Year (52W) Support Hunter (Legacy Logic)
        elif scanner_id == "109":
            l1y = funda.get("low_1y", 0)
            if l1y <= 0:
                return False
            df_w = (
                df.resample("W")
                .agg(
                    {
                        "Open": "first",
                        "High": "max",
                        "Low": "min",
                        "Close": "last",
                        "Volume": "sum",
                    }
                )
                .dropna()
            )
            w_atr = ta.atr(df_w["High"], df_w["Low"], df_w["Close"], length=14).iloc[-1]
            return c.iloc[-1] <= (l1y + (1.5 * w_atr))

            # 2-Year Low
            if not funda.get("low_2y"):
                funda["low_2y"] = (
                    float(df["Low"].iloc[-504:].min())
                    if len(df) >= 504
                    else float(df["Low"].min())
                )

        # 110: 2-Year Support Hunter (Legacy Logic)
        elif scanner_id == "110":
            l2y = funda.get("low_2y", 0)
            if l2y <= 0:
                return False
            df_w = (
                df.resample("W")
                .agg(
                    {
                        "Open": "first",
                        "High": "max",
                        "Low": "min",
                        "Close": "last",
                        "Volume": "sum",
                    }
                )
                .dropna()
            )
            w_atr = ta.atr(df_w["High"], df_w["Low"], df_w["Close"], length=14).iloc[-1]
            return c.iloc[-1] <= (l2y + (1.5 * w_atr))

        # 111: 3-Year Support Hunter (Legacy Logic)
        elif scanner_id == "111":
            l3y = funda.get("low_3y", 0)
            if l3y <= 0:
                return False
            df_w = (
                df.resample("W")
                .agg(
                    {
                        "Open": "first",
                        "High": "max",
                        "Low": "min",
                        "Close": "last",
                        "Volume": "sum",
                    }
                )
                .dropna()
            )
            w_atr = ta.atr(df_w["High"], df_w["Low"], df_w["Close"], length=14).iloc[-1]
            return c.iloc[-1] <= (l3y + (1.5 * w_atr))

        # 112: Weekly RSI Divergence (Bullish)
        elif scanner_id == "112":
            df_w = (
                df.resample("W")
                .agg(
                    {
                        "Open": "first",
                        "High": "max",
                        "Low": "min",
                        "Close": "last",
                        "Volume": "sum",
                    }
                )
                .dropna()
            )
            if len(df_w) < 30:
                return False
            rsi_w = ta.rsi(df_w["Close"], length=14)
            lookback = df_w.iloc[-26:-2]
            if lookback.empty:
                return False
            rsi_prev = rsi_w.loc[lookback["Close"].idxmin()]
            return rsi_w.iloc[-1] > rsi_prev

        # 113: Structural CHoCH (Change of Character)
        elif scanner_id == "113":
            lookback = df.iloc[-15:-1]
            return c.iloc[-1] > lookback["High"].max() if not lookback.empty else False

        # 114: Triple-Lock Delivery Confirmation
        elif scanner_id == "114":
            # 1. 1-Day (Shock)
            vol_avg = funda.get("vol_sma50", 1)
            shock = df["Volume"].iloc[-1] > (vol_avg * 2.0)
            # ... rest of legacy logic if any ...
            return shock  # Simplified for brevity in this specific primitive

        # 126: Institutional Accumulation (SMC-1 Phase 1)
        elif scanner_id == "126":
            return funda.get("smc_phase", 0) == 1

        # 127: Early Rebound (Within 10% of 52W Low)
        elif scanner_id == "127":
            l1y = funda.get("low_1y", 0)
            if l1y <= 0:
                return False
            return l1y <= c.iloc[-1] <= (l1y * 1.10)

        # 115: Phelps Base Detection (PKScreener Superpower)
        elif scanner_id == "115":
            h2y = funda.get("high_2y", 0)
            if h2y <= 0:
                return False
            # Breakout or Consolidating within 5% of 2-year high
            return c.iloc[-1] >= (h2y * 0.95)

        # 116: VWAP Breakout
        elif scanner_id == "116":
            vwap = funda.get("vwap_20d", 0)
            if vwap <= 0:
                return False
            return c.iloc[-1] > vwap and c.iloc[-2] <= vwap

        # 117: Pivot Breakout (Above R1)
        elif scanner_id == "117":
            r1 = funda.get("r1", 0)
            if r1 <= 0:
                return False
            return c.iloc[-1] > r1

        # 118: Multi-Timeframe (MTF) Alignment (Daily & Weekly Uptrend)
        elif scanner_id == "118":
            # Resample to weekly to check higher timeframe trend
            df_w = df.resample("W").agg({"Close": "last"}).dropna()
            if len(df_w) < 10:
                return False
            w_sma10 = ta.sma(df_w["Close"], length=10).iloc[-1]
            d_sma50 = ta.sma(c, length=50).iloc[-1]
            return c.iloc[-1] > d_sma50 and df_w["Close"].iloc[-1] > w_sma10

        # 119: NR7 (Narrow Range 7)
        elif scanner_id == "119":
            ranges = (df["High"] - df["Low"]).tail(7)
            return ranges.iloc[-1] == ranges.min()

        # 120: Inside Bar (2-Day)
        elif scanner_id == "120":
            return (df["High"].iloc[-1] < df["High"].iloc[-2]) and (
                df["Low"].iloc[-1] > df["Low"].iloc[-2]
            )

        # 121: TTM Squeeze (Bollinger inside Keltner)
        elif scanner_id == "121":
            upper_k = funda.get("keltner_upper", 0)
            lower_k = funda.get("keltner_lower", 0)
            if upper_k == 0:
                return False
            bb = ta.bbands(c, length=20)
            upper_b = bb["BBU_20_2.0"].iloc[-1]
            lower_b = bb["BBL_20_2.0"].iloc[-1]
            return (upper_b < upper_k) and (lower_b > lower_k)

        # 122: CPR Breakout (Above TC)
        elif scanner_id == "122":
            tc = funda.get("cpr_tc", 0)
            if tc == 0:
                return False
            return c.iloc[-1] > tc and c.iloc[-2] <= tc

        # 123: Graham Deep Value (PKScreener Superpower)
        elif scanner_id == "123":
            eps = funda.get("EPS_Latest", 0)
            bv = funda.get("BVPS_Latest", 0)
            if eps <= 0 or bv <= 0:
                return False
            graham = (22.5 * eps * bv) ** 0.5
            return c.iloc[-1] < graham

        # 125: Smart Money Accumulation (ATR Squeeze + High Delivery)
        elif scanner_id == "125":
            return funda.get("Squeeze") == True and funda.get("RDV", 0) > 1.0

    except Exception:
        pass
    return False
