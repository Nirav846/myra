# Data Flow & State Verification
Before generating or modifying any pipeline code, ALWAYS verify the complete data lifecycle:
1. **Fetch:** Does `fetcher.py` acquire the data robustly (handling rate limits and fallbacks)?
2. **Transform:** Is data processed in memory using pandas or Polars before hitting the database?
3. **Save:** Is data handed off EXCLUSIVELY to `librarian.py` for DuckDB storage?
4. **Use:** Does the app query stored data correctly rather than triggering redundant API calls?

# Expanded Research Mandate
ALWAYS consult the following sources before writing complex logic:
- **GitHub:** Prioritize existing resilient logic (`nsepython`, `screener-scraper`).
- **Official Broker APIs:** Verify against Upstox/Shoonya docs.
- **Trading Forums:** Search TradingQnA or Reddit (`r/algotrading`) for workarounds.
- **Quant Forums:** Validate math models on Quantitative Finance Stack Exchange.
