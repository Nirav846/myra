# MYRA Semantic Patterns & Institutional Logic

## 1. Modular Piped Orchestration
- **Pattern**: Chain lightweight "Scanners" (Primitives) before heavy "Strategies".
- **Logic**: Primitives (101-118) filter the full universe in SQL. Strategies perform deep analysis only on survivors.
- **Vibe**: 104 (Golden Cross) -> 12 (Super-Scan) is the gold standard for trend leaders.

## 2. Dynamic Capital Allocation (RA% & Kelly)
- **Pattern**: Fund Manager position sizing.
- **Math**: `RA% = Base_Risk_Weight * (1 + Kelly_Fraction)`.
- **Logic**: Scales capital aggressively based on historical Win Rate (Accuracy) and Volatility (ATR).

## 3. Tiered Universe Intelligence
- **Pattern**: Signal-to-Noise optimization via `symbols_master`.
- **Logic**: 
  - **Institutional Core**: NIFTY 500 + Liquid Tier 3 (Avg Vol > 500k, Deliv% > 40%, Price > 50).
  - **Dynamic Rebalancing**: Counts update after every sync based on 20-day institutional norms.

## 4. Institutional Fundamental Factor Ranking
- **Pattern**: Tri-Source fundamental verification (Screener, Google, Finology).
- **Logic**: Unified financials in **Crores**. YoY Growth + Quality (ROE/ROCE) + Valuation (Relative P/E).

## 5. India-Native Data Resilience
- **Pattern**: Exclusive Gatekeeper and Requests Caching.
- **Integrity**: Validates CSV checksums before database import to prevent "Server Busy" corruption.

## 6. Volume Spread Analysis (VSA) & Money Flow
- **Pattern**: "Effort vs Result" anomaly detection.
- **Logic**: Identifies Institutional Absorption when high volume occurs on narrow spreads at market bottoms.
- **Money Flow**: Tracks the absolute Rupee-Volume of delivery (Cr) to identify where the "Heavy Wallets" are entering.

## 7. Sector Leadership Heatmap
- **Pattern**: Top-down market analysis.
- **Logic**: Groups the core universe by sector and ranks leadership by the % of stocks in an active uptrend.

## 8. Strategy Success Probability
- **Pattern**: Backtest-backed confidence metrics.
- **Metric**: **Accuracy** = % of times a trigger yielded >3% within the next 10 trading days.

## 9. Large Deal Intelligence
- **Pattern**: Whale Tracking via Bulk/Block deals.
- **Logic**: Incremental tracking of large institutional transactions to flag immediate interest from FIIs/DIIs.

## 10. The Watchdog Daemon
- **Pattern**: Continuous intraday monitoring.
- **Logic**: Continuous background scanning with Telegram alerts for *new* breakouts only.

## 11. Anti-Fragile Data Flow (Bug Prevention)
- **Case-Sensitivity Mismatch**: ALWAYS use case-insensitive column lookups or standard mapping (e.g., `close` vs `Close`) when transitioning between the SQL Layer (Librarian) and the AI Layer (ML-Engine).
- **Momentum Context**: When passing data to ML models, NEVER pass a single snapshot. ALWAYS pass a minimum 10-day window of indicators to allow the agent to see "Momentum" rather than just a static value.
## 12. Ghost Engine (Institutional Stealth)
- **Pattern**: TLS/JA3 Fingerprint Spoofing.
- **Logic**: Mimics Chrome 124/Firefox 115 at the handshake layer via `curl_cffi` and `scrapling`.
- **Bypass**: Evades 2026 NSE Firewall by ensuring network frame sequences are human-identical.

## 13. Logic Guardian (Mathematical Integrity)
- **Pattern**: Oracle-based database testing (SQLancer-inspired).
- **NoREC (Non-optimizing Reference Engine Check)**: Ensures DuckDB's optimized query results match a brute-force Python scan.
- **TLP (Ternary Logic Partitioning)**: Validates that data subsets (True/False/Null) sum perfectly to the total record count.
- **Goal**: Prevents silent data corruption and optimizer regressions in complex institutional joins.
