# BRAINSTORM PLAN 2: MYRA v4 Modular Quant Core
# Inspired by: zvt, pybroker, hftbacktest, barter-rs

## 1. Objective
Transition MYRA from a sequential rule-based scanner to a fully modular, high-performance quant framework with separate layers for Data, Factors, Signals, and Execution.

## 2. Key Components
- **Factor Scoring Engine (zvt Style):**
    - Instead of binary (True/False) scans, each scanner becomes a 'Factor'.
    - Factors output scores (e.g., RS Score = 0.8, Delivery Score = 0.9).
    - Confluence Logic: Final rank = Weighted Average of multiple factors.
- **ML Ranking Layer (pybroker / AlphaPy Style):**
    - Use MYRA scan results as features for XGBoost/RandomForest.
    - Automate 'Feature Selection' to find which patterns actually lead to price moves.
- **Execution Realism (hftbacktest Style):**
    - Add 'Slippage' and 'Commission' models to all backtests.
    - Simulated Order Book matching for realistic fill probability.
- **Event-Driven Bus (barter-rs Style):**
    - Move toward an async architecture where 'DataEvent' triggers 'SignalEvent' which triggers 'ExecutionEvent'.

## 3. Implementation Workflow
1.  Refactor 'scanners.py' into 'factors/' directory.
2.  Implement a 'Ranker' class that aggregates factor scores.
3.  Design a 'BacktestParity' check to ensure local results match real-world execution.

## 4. Success Criteria
- 10x faster execution using vector-based factor computation.
- Ability to 'Plug-and-Play' new factors without modifying the core engine.
- Realistic backtest reports showing Net Profit AFTER slippage.
