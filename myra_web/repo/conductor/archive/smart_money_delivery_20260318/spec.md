# Specification: Enhance Institutional Delivery Data Analysis with Smart Money Indicators

## Objective
Enhance the MYRA system's ability to track and analyze institutional accumulation through NSE Bhavcopy Delivery Data (MTO), focusing on Delivery Percentage and Relative Delivery Volume.

## Requirements
- Integrate Delivery Percentage and Relative Delivery Volume into Smart Money accumulation detection.
- Implement delta-computed "Smart Money Score" stored in DuckDB.
- Enhance `fundamental_manager.py` to orchestrate delivery-based factors.
- Update `engine.py` to support delivery-weighted quantitative math.
- Ensure 100% adherence to MYRA v2.5 architectural isolation.

## Technical Details
- **Data Source:** NSE Bhavcopy MTO (via `fetcher.py`).
- **Storage:** `calculated_indicators` table in DuckDB (via `librarian.py`).
- **Math:** Volume-weighted delivery percentage relative to 20-day moving average.
