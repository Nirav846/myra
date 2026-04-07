# Specification: XGBoost Trend Forecaster

## Objective
Implement an experimental ensemble learning component using XGBoost to predict short-term (1-3 day) trends for the NIFTY 50 index based on historical OHLCV data, technical indicators, and market breadth.

## Requirements
1. **Model Architecture**: Implement a multi-scale XGBoost ensemble using `xgboost`.
2. **Data Pipeline**:
    - Fetch historical NIFTY 50 OHLCV data from the `Librarian` (DuckDB).
    - Enrich with Technical Indicators (RSI, ATR, MACD) and Intraday Breadth snapshots.
3. **Forecasting Interface**:
    - Implement a `predict_trend()` method that returns a directional bias (BULLISH/BEARISH/NEUTRAL) and a confidence score.
4. **UI Integration**:
    - Add an "AI Trend Forecast" section to the `Market Intelligence Dashboard` in `myra.py`.
    - Display the predicted direction and confidence.
5. **Training Lifecycle**:
    - Automatic training on startup if no model exists, or periodic retraining.

## Technical Details
- **Module**: `myra_app/ml_engine.py`.
- **Framework**: `xgboost`, `scikit-learn`.
- **Lookback Window**: 60 trading days.
- **Features**: OHLCV, RSI, Volume Shocks, Breadth Delta.

