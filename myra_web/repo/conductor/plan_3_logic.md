# BRAINSTORM PLAN 3: High-Alpha Strategic Scanners
# Inspired by: Extracted GitHub Strategies (Minervini, SMC, Microstructure)

## 1. Objective
Implement 10 high-conviction alpha strategies focused on Positional/Swing setups (3-month+ horizon) using structural accumulation patterns.

## 2. The Investing Strategy List
1.  **VCP Base Breakout:** Detect 3-6 month volatility contraction bases (Minervini style).
2.  **Bear Trap Reversal (Weekly):** Failed breakdown on weekly charts with massive institutional absorption.
3.  **RS Leadership (Relative Strength):** Symbols outperforming Nifty 500 for 6 months with shallow pullbacks.
4.  **Supply Absorption (Quiet Buying):** Decreasing volume on pullbacks to key long-term averages (150/200 DMA).
5.  **Institutional Gap-and-Hold:** Structural gaps after earnings that do not fill, signaling re-rating.
6.  **Liquidity Vacuum Move:** Identifying zones where large sellers are exhausted.
7.  **Post-Earnings Alpha Drift:** Tracking the 60-day momentum following a "positive surprise" in results.
8.  **Delivery Cluster Accumulation:** Sustained delivery > 55% for 2 weeks while price stays in a 5% range.
9.  **Stage 2 Trend Continuation:** Higher-highs/Higher-lows on monthly timeframes with volume confirmation.
10. **Governance + Alpha Confluence:** Final rank combining technical strength with LOW Pledged % and HIGH Insider buying.

## 3. Implementation Workflow
1.  Add 'ATR Contraction' and 'Volume Profile' primitives to the technical layer.
2.  Create a 'Corporate Actions' fetcher to trigger Earnings Drift logic.
3.  Implement 'Cluster Detection' for delivery data.

## 4. Success Criteria
- Generation of high-conviction signals even in flat markets.
- Reduced noise (fewer but higher quality results).
- Automation of 'Sequence Identification' (e.g., Trap -> Bounce -> Breakout).
