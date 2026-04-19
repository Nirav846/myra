import os
import yaml
import logging
import pandas as pd
import numpy as np
from myra_app.strategies.base_strategy import BaseStrategy


class FusionEngine(BaseStrategy):
    """
    Fusion Engine (v3.2) - Institutional Fusion Tracker
    Implements Multi-Timeframe Alignment, Proximity Alerting, and Delivery Conviction.
    """

    def __init__(self):
        super().__init__("Institutional Fusion Tracker", "fusion_tracker")
        self.config = self._load_config()

    def _load_config(self) -> dict:
        """Loads configuration from the YAML file."""
        config_path = os.path.join(
            os.path.dirname(__file__), "..", "..", "config", "fusion_config.yaml"
        )
        try:
            with open(config_path, "r") as f:
                return yaml.safe_load(f) or {}
        except Exception as e:
            logging.error(f"[FusionEngine] Error loading config: {e}")
            return {}

    def run(self, df: pd.DataFrame, funda: dict) -> dict:
        """Core vectorized execution logic."""
        # Load params
        params = self.config.get("parameters", {})
        lookback = params.get("lookback_trading_days", 60)

        if df.empty or len(df) < lookback:
            return {"signal": False}

        prox_radius = params.get("proximity_radius_pct", 0.03)
        inval_thresh = params.get("invalidation_threshold_pct", 0.015)
        spike_thresh = params.get("delivery_spike_threshold", 1.5)
        conv_mult = params.get("conviction_score_multiplier", 2.0)

        weights = params.get("weights", {})
        w_fvg = weights.get("fvg_freshness", 0.3)
        w_liq = weights.get("liquidity_distance", 0.2)
        w_trend = weights.get("trend_alignment", 0.5)

        # Standardize column names (case-insensitive mapping for safety)
        cols = {c.lower(): c for c in df.columns}
        c_close = cols.get("close")
        if not c_close:
            return {"signal": False}

        close = df[c_close]

        # Use 0 as default if indicator is missing from DataFrame
        htf_bullish = df["htf_bullish"] if "htf_bullish" in df.columns else pd.Series(0, index=df.index)
        mtf_bullish = df["mtf_bullish"] if "mtf_bullish" in df.columns else pd.Series(0, index=df.index)
        htf_bearish = df["htf_bearish"] if "htf_bearish" in df.columns else pd.Series(0, index=df.index)
        mtf_bearish = df["mtf_bearish"] if "mtf_bearish" in df.columns else pd.Series(0, index=df.index)

        # Multi-Timeframe Alignment
        is_long_aligned = (htf_bullish > 0) & (mtf_bullish > 0)
        is_short_aligned = (htf_bearish > 0) & (mtf_bearish > 0)

        # Base Score Components
        fvg_freshness = df["fvg_freshness"] if "fvg_freshness" in df.columns else pd.Series(0.0, index=df.index)
        liquidity_dist = df["liquidity_distance"] if "liquidity_distance" in df.columns else pd.Series(0.0, index=df.index)
        trend_align = df["trend_alignment"] if "trend_alignment" in df.columns else pd.Series(0.0, index=df.index)

        # Calculate base score, clip between -1.0 and 1.0
        base_score = (fvg_freshness * w_fvg) + (liquidity_dist * w_liq) + (trend_align * w_trend)
        # Flip the sign for short setups
        base_score = np.where(is_short_aligned, -base_score, base_score)
        base_score = np.clip(base_score, -1.0, 1.0)

        # Proximity Alerting
        fvg_boundary = df["fvg_boundary"] if "fvg_boundary" in df.columns else pd.Series(0.0, index=df.index)

        dist = np.abs(close - fvg_boundary) / close

        is_in_proximity = (dist <= prox_radius) & (dist > inval_thresh)
        is_active = dist <= inval_thresh

        signal_state = pd.Series("NONE", index=df.index)

        # If it's active (within invalidation threshold)
        signal_state = np.where(is_long_aligned & is_active, "LONG", signal_state)
        signal_state = np.where(is_short_aligned & is_active, "SHORT", signal_state)

        # If it's in proximity (within proximity radius but not yet active)
        signal_state = np.where(is_long_aligned & is_in_proximity & (fvg_boundary > 0), "PENDING_LONG", signal_state)
        signal_state = np.where(is_short_aligned & is_in_proximity & (fvg_boundary > 0), "PENDING_SHORT", signal_state)

        # Delivery Conviction Multiplier
        d_qty = df["delivery_qty"] if "delivery_qty" in df.columns else pd.Series(0.0, index=df.index)
        d_ma = df["delivery_ma_60"] if "delivery_ma_60" in df.columns else pd.Series(0.0, index=df.index)

        is_conviction_spike = (d_ma > 0) & (d_qty >= (d_ma * spike_thresh))

        # Apply multiplier and re-clip
        final_score = np.where(is_conviction_spike, base_score * conv_mult, base_score)
        final_score = np.clip(final_score, -1.0, 1.0)

        # Execution Logic
        fvg_top = df["fvg_top"] if "fvg_top" in df.columns else pd.Series(0.0, index=df.index)
        fvg_bottom = df["fvg_bottom"] if "fvg_bottom" in df.columns else pd.Series(0.0, index=df.index)
        swing_high = df["swing_high"] if "swing_high" in df.columns else pd.Series(0.0, index=df.index)
        swing_low = df["swing_low"] if "swing_low" in df.columns else pd.Series(0.0, index=df.index)

        entry_price = (fvg_top + fvg_bottom) / 2.0

        stop_loss = np.where(
            (signal_state == "LONG") | (signal_state == "PENDING_LONG"),
            fvg_bottom * 0.995,
            np.where(
                (signal_state == "SHORT") | (signal_state == "PENDING_SHORT"),
                fvg_top * 1.005,
                0.0
            )
        )

        take_profit = np.where(
            (signal_state == "LONG") | (signal_state == "PENDING_LONG"),
            swing_high,
            np.where(
                (signal_state == "SHORT") | (signal_state == "PENDING_SHORT"),
                swing_low,
                0.0
            )
        )

        # Risk:Reward Filter
        rr_ratio_min = params.get("execution", {}).get("rr_ratio_min", 2.0)

        # Calculate RR handling potential division by zero
        risk = np.abs(entry_price - stop_loss)
        reward = np.abs(take_profit - entry_price)

        # Avoid division by zero
        safe_risk = np.where(risk > 0, risk, np.inf)
        rr_ratio = reward / safe_risk

        # Enforce vectorized Risk:Reward check: Invalidate signals where RR is too low
        signal_state = np.where(rr_ratio < rr_ratio_min, "NONE", signal_state)

        # Priority Ranking
        mtf_aligned = is_long_aligned | is_short_aligned
        priority_score = np.abs(final_score)
        priority_score = np.where(mtf_aligned, priority_score + 10.0, priority_score)

        final_state_val = signal_state[-1] if isinstance(signal_state, np.ndarray) else signal_state.iloc[-1]

        if final_state_val == "NONE":
            return {"signal": False}

        # Strategy evaluation only uses the final row
        mtf_aligned_bool = mtf_aligned.iloc[-1] if not isinstance(mtf_aligned, np.ndarray) else mtf_aligned[-1]
        final_score_val = float(priority_score[-1] if isinstance(priority_score, np.ndarray) else priority_score.iloc[-1])
        final_entry = float(entry_price[-1] if isinstance(entry_price, np.ndarray) else entry_price.iloc[-1])
        final_sl = float(stop_loss[-1] if isinstance(stop_loss, np.ndarray) else stop_loss.iloc[-1])
        final_tp = float(take_profit[-1] if isinstance(take_profit, np.ndarray) else take_profit.iloc[-1])

        return {
            "signal": True,
            "metrics": {
                "Signal_Type": str(final_state_val),
                "Score": round(final_score_val, 2),
                "MTF_Aligned": "YES" if mtf_aligned_bool else "NO",
                "Entry": round(final_entry, 2),
                "SL": round(final_sl, 2),
                # Explicitly adding TP and T1 for position sizing calculations
                "TP": round(final_tp, 2),
                "T1": round(final_tp, 2)
            }
        }
