import pandas as pd
import numpy as np
from myra_app.ml_engine import DilatedCNNForecaster


class Strategy:
    """
    AEON-CNN: Dilated CNN Seq2seq Forecaster (ML-2)
    Provides next-step price movement forecasts with 95.86% theoretical accuracy.
    Reference: huseinzol05/Stock-Prediction-Models Agent #18
    """

    def __init__(self, librarian=None):
        self.name = "Dilated CNN Forecast"
        self.engine = None
        self.librarian = librarian

    def run(self, df: pd.DataFrame, funda: dict) -> dict:
        if df.empty or len(df) < 60:
            return {"signal": False}

        if not self.engine:
            self.engine = DilatedCNNForecaster()

        # Get Forecast
        try:
            forecast_move = self.engine.predict_next(df)
            if forecast_move is None:
                forecast_move = 0
        except Exception:
            forecast_move = 0

        # Signal if forecast is bullish (> 0.5% move expected)
        has_signal = forecast_move > 0.005

        if has_signal or True:  # Return for all during discovery
            return {
                "signal": has_signal,
                "metrics": {
                    "Strategy": "CNN-Seq2seq",
                    "Forecast_Move%": round(forecast_move * 100, 2),
                    "Type": "DeepLearning",
                },
            }

        return {"signal": False}
