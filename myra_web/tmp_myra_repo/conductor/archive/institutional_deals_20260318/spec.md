# Specification: Implement Bulk & Block Deal Momentum Scanner

## Objective
Create a momentum-based scanner that identifies stocks experiencing significant institutional accumulation through Bulk and Block deals, cross-referenced with technical trend confirmation.

## Requirements
- **Data Integration**: Utilize the existing `large_deals` table in DuckDB.
- **Factor Computation**: Calculate "Institutional Intensity" (Total Buy Value / Market Cap or Avg Volume).
- **Strategy Logic**: Trigger signals when a Large Deal coincides with a breakout or a Stage 2 trend.
- **UI Integration**: Add a new option to MYRA for "Large Deal Momentum".
- **Validation**: Ensure deal data is correctly unified by `fetcher.py` and stored by `librarian.py`.

## Technical Details
- **Tables**: `large_deals`, `calculated_indicators`, `fundamentals`.
- **Strategy Name**: `large_deal_momentum.py`.
- **Primary Field**: `qty * price` (Deal Value).
