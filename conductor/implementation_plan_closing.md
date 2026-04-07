# CLOSING IMPLEMENTATION PLAN: Institutional Polish

## Objective
Finalize the 100% completion of `DATASOURCESIDEA.txt` by implementing Portfolio Optimization, Governance Red-Flags, and Factor Analytics.

## Key Files & Context
- `myra_app/ias_manager.py`: To house the `GovernanceAudit` logic.
- `myra_app/strategies/alpha/position_sizer.py` (New): Portfolio allocation logic.
- `tools/analyze_factors.py` (New): Factor importance and backtest analysis.

## Implementation Steps

### Phase 1: Portfolio Optimizer (Inspired by FinRL)
1.  **Create `myra_app/strategies/alpha/position_sizer.py`**:
    - Implement `VolatilityAdjustedSizer`: Uses ATR-based risk parity to suggest share quantity.
    - Implement `KellySizer`: Suggests allocation % based on the strategy's win rate (from Trust Loop Audit).

### Phase 2: Governance Red-Flag Audit
1.  **Update `IASManager` in `ias_manager.py`**:
    - Implement `run_governance_audit(symbol)`:
        - Flag: Pledge increase > 2% QoQ.
        - Flag: FII exit > 1% in latest quarter.
        - Flag: SAST Sell clusters (3+ insider sells in 30 days).
2.  **Integrate into Deep-Dive**: Update `ResultsManager.run_institutional_deep_dive` to display these flags.

### Phase 3: Factor Importance Analyzer (Inspired by AlphaPy)
1.  **Create `tools/analyze_factors.py`**:
    - Load data from `data/lake/*.parquet`.
    - Calculate correlation between IAS components (SAST, Delivery, Price) and 3-month forward returns.
    - Output a "Factor Alpha Report" to help tune weights.

## Verification & Testing
1.  **Sizing Test**: Verify that a high-volatility stock gets a smaller suggested quantity than a low-volatility stock for the same risk.
2.  **Audit Test**: Confirm that a symbol with a known pledge increase (e.g., manually inject data) triggers the "RED FLAG" warning.
3.  **Analytics Test**: Run the factor analyzer and verify it produces a ranking of factor effectiveness.
