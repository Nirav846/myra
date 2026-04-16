import pandas as pd
import numpy as np
from myra_app.ias_manager import IASManager

# Instantiate IAS Manager once to avoid overhead
_ias_manager = IASManager()


def run(df: pd.DataFrame, funda: dict) -> dict:
    """
    IAS + Entry Timing Engine
    Detects high-conviction setups using IAS and pinpoints entry using specific technical triggers.
    """
    if df is None or df.empty or len(df) < 40:
        return {"signal": False}

    try:
        symbol = funda.get("symbol", funda.get("Symbol", ""))

        if not symbol:
            return {"signal": False}

        # Handle TitleCase legacy dependencies
        df_legacy = df.rename(columns={
            "open": "Open",
            "high": "High",
            "low": "Low",
            "close": "Close",
            "volume": "Volume"
        })

        # Stage 1: Setup Filter
        ias_score, _ = _ias_manager.calculate_ias(symbol, df_legacy)
        sast_score, _ = _ias_manager._get_sast_score(symbol)
        delivery_score = _ias_manager._get_delivery_score(df_legacy)

        high_conviction = (
            ias_score >= 7.0 and sast_score >= 7.0 and delivery_score >= 7.0
        )

        if funda.get("require_high_conviction", False) and not high_conviction:
            return {"signal": False}

        latest = df.iloc[-1]

        # Check if columns are lowercase or TitleCase (to handle tests or missing renames)
        col_c = "close" if "close" in df.columns else "Close"
        col_h = "high" if "high" in df.columns else "High"
        col_l = "low" if "low" in df.columns else "Low"
        col_o = "open" if "open" in df.columns else "Open"
        col_v = "volume" if "volume" in df.columns else "Volume"

        close = latest[col_c]
        high = latest[col_h]
        low = latest[col_l]
        open_price = latest[col_o]
        vol = latest[col_v]

        # Helper metrics
        avg_vol_20d = df[col_v].iloc[-20:].mean()
        avg_vol_10d = df[col_v].iloc[-10:].mean()
        base_high = df[col_h].iloc[-21:-1].max()
        base_low = df[col_l].iloc[-21:-1].min()
        high_20d = df[col_h].iloc[-20:].max()

        # Delivery pct from df or funda
        if "delivery_pct" in df.columns:
            delivery_pct = df["delivery_pct"].iloc[-1]
        elif "Delivery_Qty" in df.columns and vol > 0:
            delivery_pct = (df["Delivery_Qty"].iloc[-1] / vol) * 100
        else:
            delivery_pct = funda.get("delivery_percent", 0)

        # ATR calculation
        ranges = df[col_h] - df[col_l]
        atr_5 = ranges.iloc[-5:].mean()
        atr_20 = ranges.iloc[-20:].mean()

        # EMA and VWAP
        if "sma20" in df.columns:
            ema_20 = df["sma20"].iloc[-1]  # Fallback to SMA
        else:
            ema_20 = df[col_c].ewm(span=20, adjust=False).mean().iloc[-1]

        if "VWAP" in df.columns:
            vwap = latest["VWAP"]
        else:
            tp = (df[col_h] + df[col_l] + df[col_c]) / 3
            vwap = (tp * df[col_v]).rolling(window=20).sum().iloc[-1] / df[
                col_v
            ].rolling(window=20).sum().iloc[-1]

        # Stage 2: Trigger Engine

        # A. Breakout entry
        breakout_entry = (
            close > base_high * 1.003
            and close > high_20d
            and vol >= 1.5 * avg_vol_20d
            and delivery_pct >= 45
            and atr_5 < atr_20
        )

        # B. Pullback entry
        pullback_entry = (
            close > ema_20
            and low <= ema_20 * 1.01
            and close >= open_price
            and vol <= avg_vol_10d * 0.9
        )

        # C. Bear trap / failed breakdown entry
        support_level = df[col_l].iloc[-21:-1].min()
        body_size = abs(close - open_price)
        wick_lower = min(open_price, close) - low

        trap_entry = (
            low < support_level
            and close > support_level
            and delivery_pct >= 50
            and vol >= 1.3 * avg_vol_20d
            and wick_lower > body_size
        )

        # Stage 3: Confirmation
        relative_strength = funda.get("RS_Raw", 1)  # Fallback to positive if missing
        confirmation = close >= vwap and relative_strength > 0

        should_buy = confirmation and (breakout_entry or pullback_entry or trap_entry)

        if not should_buy:
            return {"signal": False}

        # Entry, Stop, Quality calculation
        setup_type = ""
        entry_price = 0.0
        stop_loss = 0.0

        if breakout_entry:
            setup_type = "Breakout"
            entry_price = max(base_high * 1.003, high)
            stop_loss = min(base_low, low) * 0.995
        elif pullback_entry:
            setup_type = "Pullback"
            entry_price = max(high, ema_20)
            stop_loss = low * 0.995
        elif trap_entry:
            setup_type = "Bear Trap"
            entry_price = max(close, high)
            stop_loss = low * 0.995

        # Composite Quality Score
        breakout_strength = ((close / base_high) - 1) * 100 if breakout_entry else 0
        delivery_quality = min(delivery_pct / 50, 1) * 10
        compression_quality = (atr_20 / atr_5) * 5 if atr_5 > 0 else 0

        entry_quality = (
            0.35 * ias_score
            + 0.20 * min(breakout_strength, 10)
            + 0.15 * delivery_quality
            + 0.15 * min(compression_quality, 10)
            + 0.15 * min(relative_strength, 10)
        )

        conviction_level = "High" if high_conviction else "Technical"

        return {
            "signal": True,
            "metrics": {
                "Type": setup_type,
                "Conviction": conviction_level,
                "Entry": f"₹{round(entry_price, 2)}",
                "SL": f"₹{round(stop_loss, 2)}",
                "Score": round(entry_quality, 2),
                "IAS": round(ias_score, 1),
            },
        }

    except Exception:
        return {"signal": False}
