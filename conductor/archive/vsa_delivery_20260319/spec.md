# Specification: Enhance VSA Momentum with Delivery-Weighted Analysis

## Objective
Refine the "Volume Spread Analysis (VSA)" strategy by weighting volume spreads with delivery percentages to identify high-conviction institutional accumulation breakouts.

## Requirements
- **Delivery-Weighted Spread**: Calculate a new "VSA Intensity" factor: `(Spread / Volume) * Delivery_Percent`.
- **Strategy Logic**: Trigger signals when "Effort vs Result" (VSA principle) is backed by 60%+ delivery.
- **Factor Integration**: Use `rel_spread` and `rdv` from DuckDB to detect "Stopping Volume" and "No Supply" tests.
- **UI Integration**: Add "VSA_Intensity" and "Effort_Vibe" to the hero columns.

## Technical Details
- **Indicators**: `rel_spread`, `rdv`, `delivery_percent`, `closing_pos`.
- **Strategy File**: `myra_app/strategies/vsa_momentum.py`.
- **Core Signal**: High relative volume + narrowing spread + high closing position + spike in delivery.
