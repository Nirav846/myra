# Specification: NSE Surpriver v2

## Goal
To detect "silent accumulation" by measuring how many lookback windows (5, 10, 15, 20, 30 days) consistently show anomalous delivery absorption relative to price movement.

## Logic
1.  **Daily Accumulation Score**: 
    - `0.5 * Z(Delivery) + 0.3 * Z(Volume) + 0.2 * (1 - |Return|)`
2.  **Consistency Engine**: 
    - Check if the `Accumulation Score` is > 0.5 for at least 60% of the days in each window.
3.  **Absorption Check**: 
    - Count "Buying Wicks" (Close > Open and Low < Previous Low) to ensure supply is being bought on dips.
4.  **Anomaly Score**: 
    - Weighted average of Consistency (60%) and Absorption (40%).

## Constraints
- Must be within 40% of the 52-week low (avoiding top-chasers).
- Volume must be > 1.1x of the window average.
