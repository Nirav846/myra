# Specification: Enhance Bottom Hunter Strategy with Dynamic Support Reversals

## Objective
Improve the "Bottom Hunter" strategy by integrating dynamic support detection and high-conviction reversal triggers (Bullish Divergence + Volume Absorption).

## Requirements
- **Dynamic Support**: Identify 1Y, 2Y, and 3Y price floors via SQL pre-computation.
- **Factor Integration**: Use "Relative Spread" and "Closing Position" to detect "Absorption" at bottoms.
- **Strategy Logic**: Trigger signals when price stabilizes at a multi-year floor with increasing "Smart Money Score".
- **Weinstein Context**: Ensure signals are clearly labeled as "Stage 1 (Basing)" vs "Stage 4 (Dangerous Fall)".
- **UI Integration**: Update the Bottom Hunter hero columns to include "Absorption" and "Floor Type".

## Technical Details
- **Indicators**: `low_1y`, `low_2y`, `low_3y`, `closing_pos`, `rel_spread`.
- **Strategy File**: Update `myra_app/strategies/bottom_hunter.py` (or create enhanced version).
- **Core Signal**: Confluence of multi-year support touch + 5-day narrowing spread + high delivery.
