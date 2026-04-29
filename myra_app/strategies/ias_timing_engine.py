import logging

import numpy as np
import pandas as pd

from myra_app.ias_manager import IASManager

# Instantiate IAS Manager once to avoid overhead
_ias_manager = IASManager()


def run(df: pd.DataFrame, funda: dict) -> dict:
    """
    IAS + Entry Timing Engine (v3.2)
    Compliance: Expects CamelCase columns from DataAdapter (Open, High, Low, Close, Volume).
    Detects Breakouts, Pullbacks, and Bear Traps.
    """
    # 1. Basic Validation
    if df is None or df.empty or len(df) < 40:
        return {"signal": False}

    try:
        symbol = funda.get("symbol", funda.get("Symbol", ""))
        if not symbol:
            return {"signal": False}

        # 2. Setup Stage: IAS & Institutional Quality
        # We pass the DF directly as DataAdapter has already handled CamelCase and Sorting
        ias_score, _ = _ias_manager.calculate_ias(symbol, df)
        sast_score, _ = _ias_manager._get_sast_score(symbol)
        delivery_score = _ias_manager._get_delivery_score(df)

        high_conviction = (
            ias_score >= 7.0 and sast_score >= 7.0 and delivery_score >= 7.0
        )

        # Skip if mandatory high conviction is required but not met
        if funda.get("require_high_conviction", False) and not high_conviction:
            return {"signal": False}

        # 3. Technical Extract
        latest = df.iloc[-1]
        close = latest["Close"]
        high = latest["High"]
        low = latest["Low"]
        open_price = latest["Open"]
        vol = latest["Volume"]

        # 4. Metric Calculation
        avg_vol_20d = df["Volume"].iloc[-20:].mean()
        avg_vol_10d = df["Volume"].iloc[-10:].mean()
        base_high = df["High"].iloc[-21:-1].max()
        base_low = df["Low"].iloc[-21:-1].min()
        high_20d = df["High"].iloc[-20:].max()

        # Delivery % check (Uses DataAdapter's mapped column)
        delivery_pct = (
            df["DeliveryPct"].iloc[-1]
            if "DeliveryPct" in df.columns
            else funda.get("delivery_percent", 0)
        )

        # Volatility / ATR
        ranges = df["High"] - df["Low"]
        atr_5 = ranges.iloc[-5:].mean()
        atr_20 = ranges.iloc[-20:].mean()

        # Trend and Momentum (Lazy Fallback handling)
        ema_20 = (
            df["sma20"].iloc[-1]
            if "sma20" in df.columns
            else df["Close"].ewm(span=20).mean().iloc[-1]
        )

        # VWAP Approximation for daily timeframe
        if "VWAP" in df.columns:
            vwap = latest["VWAP"]
        else:
            tp = (df["High"] + df["Low"] + df["Close"]) / 3
            vwap = (tp * df["Volume"]).rolling(window=20).sum().iloc[-1] / df[
                "Volume"
            ].rolling(window=20).sum().iloc[-1]

        # 5. TRIGGER LOGIC

        # A. Breakout (VCP-style)
        breakout_entry = (
            close > base_high * 1.003
            and close > high_20d
            and vol >= 1.5 * avg_vol_20d
            and delivery_pct >= 45
            and atr_5 < atr_20  # Volatility Contraction check
        )

        # B. Pullback (Mean Reversion)
        pullback_entry = (
            close > ema_20
            and low <= ema_20 * 1.01
            and close >= open_price
            and vol <= avg_vol_10d * 0.9
        )

        # C. Bear Trap (Failed Breakdown)
        support_level = df["Low"].iloc[-21:-1].min()
        body_size = abs(close - open_price)
        wick_lower = min(open_price, close) - low
        trap_entry = (
            low < support_level
            and close > support_level
            and delivery_pct >= 50
            and vol >= 1.3 * avg_vol_20d
            and wick_lower > body_size
        )

        # 6. Confirmation & Signal Generation
        relative_strength = funda.get("RS_Raw", 1)
        confirmation = close >= vwap and relative_strength > 0
        should_buy = confirmation and (breakout_entry or pullback_entry or trap_entry)

        if not should_buy:
            return {"signal": False}

        # 7. Formatting Result
        setup_type = (
            "Breakout"
            if breakout_entry
            else "Pullback"
            if pullback_entry
            else "Bear Trap"
        )

        # Entry/SL Logic
        if breakout_entry:
            entry_price = max(base_high * 1.003, high)
            stop_loss = min(base_low, low) * 0.995
        elif pullback_entry:
            entry_price = max(high, ema_20)
            stop_loss = low * 0.995
        else:  # Trap
            entry_price = max(close, high)
            stop_loss = low * 0.995

        # Quality Scoring
        delivery_quality = min(delivery_pct / 50, 1) * 10
        compression_quality = (atr_20 / atr_5) * 5 if atr_5 > 0 else 0

        entry_quality = (
            0.35 * ias_score
            + 0.15 * delivery_quality
            + 0.15 * min(compression_quality, 10)
            + 0.15 * min(relative_strength, 10)
            + 0.20 * (10 if breakout_entry else 5)
        )

        return {
            "signal": True,
            "metrics": {
                "Type": setup_type,
                "Conviction": "High" if high_conviction else "Technical",
                "Entry": f"₹{round(entry_price, 2)}",
                "SL": f"₹{round(stop_loss, 2)}",
                "Score": round(entry_quality, 2),
                "IAS": round(ias_score, 1),
            },
        }

    except Exception as e:
        logging.error(f"Timing Engine error for {funda.get('symbol')}: {e}")
        return {"signal": False}
