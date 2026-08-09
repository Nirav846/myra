from abc import ABC, abstractmethod

import numpy as np
import pandas as pd


class BaseStrategy(ABC):
    """
    MYRA Base Strategy (v1.0)
    All elite strategies inherit from this class.
    Provides built-in Sentiment and AI Assist hooks.
    """

    def __init__(self, name: str, strategy_id: str):
        self.name = name
        self.id = strategy_id

    @abstractmethod
    def run(self, df: pd.DataFrame, funda: dict) -> dict:
        """Core logic to be implemented by child strategies."""
        pass

    def get_market_mood(self, lib) -> str:
        """
        Determines Market Sentiment — PCR-first, VIX fallback.

        Priority:
          1. PCR snapshot from options_chain.db (standalone, no VIX needed).
             BULLISH → GREED,  BEARISH → FEAR,  NEUTRAL → NEUTRAL.
          2. Fallback: India VIX fear proxy (currently always NEUTRAL
             because ^INDIAVIX has zero rows, but kept for future use).
             Fear (VIX > 20) | Neutral (15-20) | Greed (VIX < 15).

        The function never raises — any failure in the PCR path falls
        through to the VIX path, which itself catches all exceptions.
        """
        # --- Path 1: PCR snapshot (standalone, no VIX data required) ---
        try:
            from myra_app.options_chain import get_latest_pcr_snapshot

            snapshot = get_latest_pcr_snapshot()
            if snapshot is not None:
                regime = snapshot.get("regime", "NEUTRAL")
                _PCR_MOOD_MAP = {
                    "BULLISH": "GREED",
                    "BEARISH": "FEAR",
                    "NEUTRAL": "NEUTRAL",
                }
                return _PCR_MOOD_MAP.get(regime, "NEUTRAL")
        except Exception:
            pass  # fall through to VIX path

        # --- Path 2: VIX fallback (legacy) ---
        try:
            # We fetch India VIX as a fear proxy
            df_vix = lib.safe_execute(
                "SELECT close FROM benchmark WHERE symbol = '^INDIAVIX' ORDER BY date DESC LIMIT 1",
                conn=lib._tech_conn,
            ).fetchone()
            vix = df_vix[0] if df_vix else 18.0

            if vix > 22:
                return "EXTREME FEAR"
            if vix > 18:
                return "FEAR"
            if vix < 14:
                return "GREED"
            return "NEUTRAL"
        except Exception:
            return "NEUTRAL"

    def calculate_kelly_size(self, win_rate: float, reward_to_risk: float) -> float:
        """
        Built-in: Kelly Criterion for optimal position sizing.
        Formula: K% = W - [(1-W) / R]
        """
        if reward_to_risk <= 0:
            return 0.05
        k = win_rate - ((1 - win_rate) / reward_to_risk)
        return round(max(0.02, min(0.25, k)), 2)  # Clamp between 2% and 25%

    def get_ai_second_opinion(self, symbol: str, technical_summary: str) -> dict:
        """LLM second opinion via Gemini (or degraded fallback).

        Delegates to ``myra_app.ai_second_opinion.get_ai_second_opinion``.

        Returns a dict with keys:
            signal (str): "BUY" | "SELL" | "HOLD"
            reason (str): Analyst rationale
            confidence (float): 0.0 – 1.0
            source (str): "gemini" | "degraded"
            cached (bool): True if served from the local TTL cache
        Never raises — any failure returns a degraded HOLD signal.
        """
        from myra_app.ai_second_opinion import get_ai_second_opinion

        return get_ai_second_opinion(symbol, technical_summary)


class MarketMoodHelper(BaseStrategy):
    """Concrete implementation for global mood calculation only."""

    def __init__(self):
        super().__init__("MoodHelper", "0")

    def run(self, df, funda):
        return {"signal": False}
