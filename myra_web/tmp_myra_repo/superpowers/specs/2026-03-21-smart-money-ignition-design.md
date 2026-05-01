# Institutional Specification: Smart Money Ignition (SMC-1) - Revised

**Date**: 2026-03-21  
**Status**: APPROVED (Post-Technical Review)  
**Focus**: Institutional Accumulation (Phase 1) & Momentum Ignition (Phase 2)  
**Architecture**: MYRA v2.5

## 1. Objective
To identify institutional "bases" where Smart Money is absorbing shares (Phase 1) and detect the high-velocity breakout (Phase 2) using price-delivery profiles. This logic is inspired by the "Dilated CNN" concept of capturing long-range temporal patterns through delivery-at-price analysis.

## 2. Technical Logic

### 2.1 Phase 1: Delivery Point of Control (D-POC) Base
The D-POC is the most critical institutional metric, representing the "average cost" of the Smart Money over a specific period.
*   **Window**: 60 trading days.
*   **Implementation**: `Librarian` fetches the 60-day price/delivery window; `Engine` uses NumPy `histogram` to find the price bucket with the highest cumulative delivery.
*   **Aggregation**: Segment the 60-day price range into 50 equal buckets.
*   **Definition**: The bucket center with the `MAX(delivery_qty)` is the **D-POC**.
*   **Base Criteria (Phase 1)**: 
    *   Current Price is within ±2% of D-POC.
    *   Volatility (10-day Std Dev of Log Returns) is < 0.02 (Price Tightness).
    *   **Absorption Ratio**: `(delivery_qty / avg_delivery_20d) / Volatility` is > 50.

### 2.2 Phase 2: Ignition Trigger (Breakout)
The transition from absorption to expansion.
*   **Condition 1**: Price closes > 3% above the D-POC **AND** > 60-day High (to ensure base clearance).
*   **Condition 2**: Volume is > 1.5x the 20-day Moving Average (Relative Volume).
*   **Condition 3**: The **Multi-Dilation Confluence** (trends at dilations 2, 4, 8) must be positive, confirming trend synchronization across scales.

## 3. Modular Integration (MYRA v2.5 Layers)

### 3.1 `myra_app/librarian.py` (The Data Gatekeeper)
*   Implement `get_delivery_data(symbol, days=60)` to return a structured DataFrame of price and delivery history.
*   **Schema Update**: Add `d_poc` and `smc_phase` to the `calculated_indicators` table. Librarian's `update_indicator_history` calls `SMCManager` to compute these for the latest sync date.

### 3.2 `myra_app/engine.py` (The Math Layer)
*   **Class `SMCManager`**:
    *   `calculate_d_poc(df)`: Uses NumPy histograms for high-performance profiling.
    *   `get_confluence_score(df)`: Calculates average returns at dilations 2, 4, and 8.
*   **Mandate**: Use vectorized operations; no direct SQL.

### 3.3 `myra_app/screener.py` (The Orchestrator)
*   **Scanner 105**: "Institutional Accumulation" - Flags stocks meeting Phase 1 criteria.
*   **Strategy 5**: "Smart Money Ignition" - Flags stocks meeting Phase 2 criteria.

## 4. Verification & Testing (Sandbox Workflow)
*   **Reproduction**: Test against historical winners (**RVNL**, **IRFC**, **TRENT**, **TATAPOWER**).
*   **Success Metric**: D-POC must identify the "flat base" preceding the >20% move.
*   **Edge Cases**: Handle low-liquidity stocks (Minimum price > 50, Volume > 500k).

## 5. Security & Style
*   No direct SQL outside `librarian.py`.
*   All comments and logs strictly in English.
*   No external dependencies beyond `authorized_libraries` in `myra_sources.json`.
