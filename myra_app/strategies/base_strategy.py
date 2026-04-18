import pandas as pd
import numpy as np
from abc import ABC, abstractmethod


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
        Built-in: Determines Market Sentiment via VIX.
        Fear (VIX > 20) | Neutral (15-20) | Greed (VIX < 15)
        """
        try:
            # We fetch India VIX as a fear proxy
            df_vix = lib._tech_conn.execute(
                "SELECT close FROM benchmark WHERE symbol = '^INDIAVIX' ORDER BY date DESC LIMIT 1"
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

    def get_ai_second_opinion(self, symbol: str, technical_summary: str):
        """
        Hook for Idea #3: LLM Integration.
        To be implemented once we discuss the specific AI provider.
        """
        return "AI ANALYSIS PENDING"


class MarketMoodHelper(BaseStrategy):
    """Concrete implementation for global mood calculation only."""

    def __init__(self):
        super().__init__("MoodHelper", "0")

    def run(self, df, funda):
        return {"signal": False}
