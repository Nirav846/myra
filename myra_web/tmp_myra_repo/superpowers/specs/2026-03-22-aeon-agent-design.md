# ML Specification: MYRA-AEON Evolutionary Agent (ML-1)

**Date**: 2026-03-22  
**Status**: DRAFT  
**Focus**: Optimized Entry/Scale-In/Exit via Reinforcement Learning  
**Inspiration**: Evolution Strategy Agent (huseinzol05/Stock-Prediction-Models)

## 1. Objective
To automate the decision-making process for capital allocation by training an agent to recognize the precise "Institutional Footprint" that precedes a multi-bagger breakout and the "Distribution Signature" that signals an optimal exit.

## 2. The Agent Environment

### 2.1 State Space (Inputs)
The agent observes a 10-day sliding window of:
- **SMC Metrics**: `POC_Dist`, `Absorption`, `Tightness`, `Deliv_Pct`.
- **Trend**: `Multi-Dilation Confluence`, `SMA_Alignment`.
- **Volatility**: `ATR_Pct`, `BB_Width`.
- **Regime**: `Nifty_50_Trend`, `VIX_Stable`.

### 2.2 Action Space (Outputs)
The agent outputs a conviction level ($Q$):
- **Action 0**: EXIT (Sell all shares).
- **Action 1**: TACTICAL (Buy/Hold 25% position).
- **Action 2**: CORE (Buy/Hold 50% position).
- **Action 3**: CONVICTION (Buy/Hold 100% position).

## 3. Training: Evolution Strategies (ES)
Unlike standard gradient descent, we use population-based optimization:
1.  **Generate** 50 agents with randomized neural weights.
2.  **Simulate** each agent's performance on the 5-year DuckDB price history (2021-2026).
3.  **Reward** based on Total Profit and Sharpe Ratio (Risk-adjusted return).
4.  **Penalize** for Max Drawdown $>15\%$.
5.  **Evolve**: Select top 5 agents, mutate their "genes" (weights), and start the next generation.

## 4. Institutional Guardrails
The ML agent is NOT allowed to ignore the core MYRA v2.5 mandates:
- **Minimum Liquidity**: Agent only trades stocks with Price > 50 and Volume > 500k.
- **Risk Override**: If a stock hits the Hard Trailing Stop-Loss (TSL), the agent's position is forcefully liquidated regardless of its prediction.

## 5. Integration Plan
- **Module**: `myra_app/ml_engine.py` (Class `AEONEngine`).
- **Data Source**: `calculated_indicators` table via `librarian.py`.
- **User Interface**: `Strategy 31` in `myra.py` will display the agent's current "Conviction Level" for any stock.

## 6. Verification
- **Backtest**: Run the trained model on 2024-2025 "winners" (RVNL, MAZDOCK) to confirm it scales-in during Phase 1 and exits near peak distribution.
