# IMPLEMENTATION PLAN: Modular Quant Core (Plan 2)

## Objective
Transition MYRA from a sequential rule-based scanner to a fully modular Factor-Based Scoring Engine.

## Key Files & Context
- `myra_app/factors/` (New): Directory for individual factor modules.
- `myra_app/factor_engine.py` (New): Orchestrator for factor computation.
- `myra_app/engine.py`: Core processing coordinator.
- `myra_app/strategies/scanners.py`: To be refactored into factors.

## Implementation Steps

### Phase 1: Factor Infrastructure
1.  **Create `myra_app/factors/base_factor.py`**: Define the abstract `BaseFactor` class.
2.  **Implement Initial Factors**:
    - `rs_factor.py`: Normalized Relative Strength score.
    - `delivery_factor.py`: High-conviction absorption score.
    - `ias_factor.py`: Bridging the Governance data into the scoring engine.
3.  **Create `myra_app/factors/__init__.py`**: Automated factor registration.

### Phase 2: Scoring Engine
1.  **Implement `FactorEngine` in `myra_app/factor_engine.py`**:
    - Load enabled factors from config.
    - Compute scores in parallel (reusing engine workers).
    - Handle normalization and min-max scaling across the universe.
2.  **Implement `Ranker`**:
    - Weighted aggregate of factor scores.
    - Percentile ranking across the entire universe.

### Phase 3: Engine Integration
1.  **Update `myra_app/engine.py`**:
    - Integrate `FactorEngine` into the `run_scan` workflow.
    - Support "Rank-Based Filtering" (e.g., only show top 5% by IAS + RS).
2.  **Execution Realism**:
    - Add `slippage` and `commission` parameters to backtesting logic.

## Verification & Testing
1.  **Parity Test**: Ensure the new factor-based RS matches the legacy RS scanner triggers at its 1.0 score point.
2.  **Performance Check**: Verify that computing 5 factors across 3700 stocks takes < 2 minutes.
3.  **Backtest Audit**: Run a backtest with and without slippage to verify realistic profit degradation.
