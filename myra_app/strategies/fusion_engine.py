import os
import yaml
import logging
import pandas as pd
from myra_core.utils.data_validation import enforce_index_contract
import numpy as np
from myra_app.strategies.base_strategy import BaseStrategy





class FusionEngine(BaseStrategy):
    """
    Fusion Engine (v3.2) - Institutional Fusion Tracker
    """

    def __init__(self):
        super().__init__("Institutional Fusion Tracker", "fusion_tracker")
        self.config = self._load_config()

    def _load_config(self) -> dict:
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
        """
        Entry point — THIS is where your crash was happening.
        Now fully protected.
        """

        if df is None or df.empty:
            return {"signal": False}

        try:
            # 🔥 CRITICAL FIX: enforce index contract
            df = enforce_index_contract(df)
        except Exception as e:
            logging.debug(f"[FusionEngine] Index cleanup failed: {e}")
            return {"signal": False}

        # Normalize column casing AFTER cleaning
        df = df.rename(columns=str.title)

        return self.compute_fusion_signal(df)

    def compute_fusion_signal(self, df: pd.DataFrame) -> dict:
        """
        Core vectorized execution logic
        """

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

        close = df["Close"] if "Close" in df.columns else None
        if close is None:
            return {"signal": False}

        # Safe Series generators
        def safe_series(col, default=0.0):
            return df[col] if col in df.columns else pd.Series(default, index=df.index)

        htf_bullish = safe_series("htf_bullish")
        mtf_bullish = safe_series("mtf_bullish")
        htf_bearish = safe_series("htf_bearish")
        mtf_bearish = safe_series("mtf_bearish")

        is_long_aligned = (htf_bullish > 0) & (mtf_bullish > 0)
        is_short_aligned = (htf_bearish > 0) & (mtf_bearish > 0)

        fvg_freshness = safe_series("fvg_freshness")
        liquidity_dist = safe_series("liquidity_distance")
        trend_align = safe_series("trend_alignment")

        base_score = (fvg_freshness * w_fvg) + (liquidity_dist * w_liq) + (trend_align * w_trend)
        base_score = np.where(is_short_aligned, -base_score, base_score)
        base_score = np.clip(base_score, -1.0, 1.0)

        fvg_boundary = safe_series("fvg_boundary")
        dist = np.abs(close - fvg_boundary) / close

        is_in_proximity = (dist <= prox_radius) & (dist > inval_thresh)
        is_active = dist <= inval_thresh

        # 🔥 SAFE INIT
        signal_state = pd.Series(np.full(len(df), "NONE"), index=df.index)

        signal_state = np.where(is_long_aligned & is_active, "LONG", signal_state)
        signal_state = np.where(is_short_aligned & is_active, "SHORT", signal_state)

        signal_state = np.where(is_long_aligned & is_in_proximity & (fvg_boundary > 0), "PENDING_LONG", signal_state)
        signal_state = np.where(is_short_aligned & is_in_proximity & (fvg_boundary > 0), "PENDING_SHORT", signal_state)

        d_qty = safe_series("delivery_qty")
        d_ma = safe_series("delivery_ma_60")

        is_conviction_spike = (d_ma > 0) & (d_qty >= (d_ma * spike_thresh))

        final_score = np.where(is_conviction_spike, base_score * conv_mult, base_score)
        final_score = np.clip(final_score, -1.0, 1.0)

        fvg_top = safe_series("fvg_top")
        fvg_bottom = safe_series("fvg_bottom")
        swing_high = safe_series("swing_high")
        swing_low = safe_series("swing_low")

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

        rr_ratio_min = params.get("execution", {}).get("rr_ratio_min", 2.0)

        risk = np.abs(entry_price - stop_loss)
        reward = np.abs(take_profit - entry_price)

        safe_risk = np.where(risk > 0, risk, np.inf)
        rr_ratio = reward / safe_risk

        signal_state = np.where(rr_ratio < rr_ratio_min, "NONE", signal_state)

        mtf_aligned = is_long_aligned | is_short_aligned

        priority_score = np.abs(final_score)
        priority_score = np.where(mtf_aligned, priority_score + 10.0, priority_score)

        final_state_val = signal_state[-1] if isinstance(signal_state, np.ndarray) else signal_state.iloc[-1]

        if final_state_val == "NONE":
            return {"signal": False}

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
                "TP": round(final_tp, 2),
                "T1": round(final_tp, 2)
            }
        }


_engine = FusionEngine()


def run(df: pd.DataFrame, funda: dict = None) -> dict:
    return _engine.run(df, funda)
