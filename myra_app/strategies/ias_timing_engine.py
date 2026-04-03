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
    if df is None or len(df) < 40:
        return {"signal": False}

    try:
        symbol = funda.get('symbol', funda.get('Symbol', ''))
        
        if not symbol:
            return {"signal": False}

        # Stage 1: Setup Filter
        ias_score, _ = _ias_manager.calculate_ias(symbol, df)
        sast_score, _ = _ias_manager._get_sast_score(symbol)
        delivery_score = _ias_manager._get_delivery_score(df)
        
        if ias_score < 7.0 or sast_score < 7.0 or delivery_score < 7.0:
            return {"signal": False}
        
        latest = df.iloc[-1]
        close = latest['Close']
        high = latest['High']
        low = latest['Low']
        open_price = latest['Open']
        vol = latest['Volume']
        
        # Helper metrics
        avg_vol_20d = df['Volume'].iloc[-20:].mean()
        avg_vol_10d = df['Volume'].iloc[-10:].mean()
        base_high = df['High'].iloc[-21:-1].max()
        base_low = df['Low'].iloc[-21:-1].min()
        high_20d = df['High'].iloc[-20:].max()
        
        # Delivery pct from df or funda
        if 'delivery_pct' in df.columns:
            delivery_pct = df['delivery_pct'].iloc[-1]
        elif 'Delivery_Qty' in df.columns and vol > 0:
            delivery_pct = (df['Delivery_Qty'].iloc[-1] / vol) * 100
        else:
            delivery_pct = funda.get('delivery_percent', 0)

        # ATR calculation
        ranges = df['High'] - df['Low']
        atr_5 = ranges.iloc[-5:].mean()
        atr_20 = ranges.iloc[-20:].mean()
        
        # EMA and VWAP
        if 'sma20' in df.columns:
            ema_20 = df['sma20'].iloc[-1] # Fallback to SMA
        else:
            ema_20 = df['Close'].ewm(span=20, adjust=False).mean().iloc[-1]
            
        if 'VWAP' in df.columns:
            vwap = latest['VWAP']
        else:
            tp = (df['High'] + df['Low'] + df['Close']) / 3
            vwap = (tp * df['Volume']).rolling(window=20).sum().iloc[-1] / df['Volume'].rolling(window=20).sum().iloc[-1]
        
        # Stage 2: Trigger Engine
        
        # A. Breakout entry
        breakout_entry = (
            close > base_high * 1.003 and
            close > high_20d and
            vol >= 1.5 * avg_vol_20d and
            delivery_pct >= 45 and
            atr_5 < atr_20
        )
        
        # B. Pullback entry
        pullback_entry = (
            close > ema_20 and
            low <= ema_20 * 1.01 and
            close >= open_price and
            vol <= avg_vol_10d * 0.9
        )
        
        # C. Bear trap / failed breakdown entry
        support_level = df['Low'].iloc[-21:-1].min()
        body_size = abs(close - open_price)
        wick_lower = min(open_price, close) - low
        
        trap_entry = (
            low < support_level and
            close > support_level and
            delivery_pct >= 50 and
            vol >= 1.3 * avg_vol_20d and
            wick_lower > body_size
        )
        
        # Stage 3: Confirmation
        relative_strength = funda.get('RS_Raw', 1)  # Fallback to positive if missing
        confirmation = (
            close >= vwap and
            relative_strength > 0
        )
        
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
            0.35 * ias_score +
            0.20 * min(breakout_strength, 10) +
            0.15 * delivery_quality +
            0.15 * min(compression_quality, 10) +
            0.15 * min(relative_strength, 10)
        )

        return {
            "signal": True,
            "metrics": {
                "Type": setup_type,
                "Entry": f"₹{round(entry_price, 2)}",
                "SL": f"₹{round(stop_loss, 2)}",
                "Score": round(entry_quality, 2),
                "IAS": round(ias_score, 1)
            }
        }
        
    except Exception as e:
        return {"signal": False}
