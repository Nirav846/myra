# AEON 3-Month Performance Audit

## Objective
Quantify the predictive accuracy of the AEON Agent (Strategy 31) by running an iterative backtest over the last 90 days (Dec 2025 - March 2026).

## Scope
- Create `backtest_aeon_3m.py` to run Strategy 31 daily.
- Measure **Precision**: % of "Ignition" signals that yielded >3% return within 10 days.
- Measure **Risk**: Maximum drawdown experienced by signals.
- Compare vs **Nifty 50** benchmark for the same period.

## Implementation Steps
1.  **Backtest Loop:** Iterate through all trading days from 2025-12-22 to 2026-03-22.
2.  **Signal Capture:** For each day, store every stock that received an "Ignition" or "Basing" signal.
3.  **Forward-Walk Validation:** For every signal, look at the price performance 10 days into the future.
4.  **Aggregation:** Calculate the Global Hit Rate and Average Profit per trade.
5.  **Reporting:** Generate a summary table of the performance.

## Success Criteria
- **Hit Rate > 60%** for Ignition signals.
- **Profit Factor > 1.5** (Total Wins / Total Losses).
