# Wyckoff Scanner Discrepancies and Improvement Notes

## Backtest Results Summary (50 symbols, 12 scan dates)
- Total events: 1401 (SC: 704, Spring: 24, SOS: 673)
- All event types showed negative mean returns across horizons
- However, Q5-Q1 spreads were positive for all types, indicating quality stratification works

## Observed Discrepancies
1. SC events consistently negative (-1.34% to -5.15%) - may indicate overly loose detection criteria
2. Spring events show best Q5-Q1 at 60d (+14.09%) - suggests optimal horizon for Spring signals
3. SOS events show best Q5-Q1 at 180d (+13.74%) - suggests longer-term effectiveness

## Hardcoded Thresholds Identified for Dynamic Conversion
### SC Detection:
- volume_p > row_avg_vol * 1.8
- close_p > (low_p + (high_p - low_p) * 0.35)  
- del_pct > 40
- close_p <= row_range_low * 1.15

### Spring Detection:
- low_p < prior_low * 0.99 (1% undercut)
- del_pct > max(25.0, avg_del_50d * 1.2)

### SOS Detection:
- close_p > (row_range_low + (row_range_high - row_range_low) * 0.45)
- volume_p > row_avg_vol * 1.2
- del_pct >= row_avg_del * 1.0

## Recommended Dynamic Approaches
1. Volume thresholds: Based on historical volume percentiles (e.g., 80th-90th percentile)
2. Price thresholds: Based on ATR or historical price ranges
3. Delivery thresholds: Based on historical delivery % distributions
4. Thresholds that adapt to market regime (volatility, trend strength)

## Next Steps
1. Implement dynamic threshold calculation based on lookback period statistics
2. Test with walk-forward validation to avoid look-ahead bias
3. Compare performance against static thresholds
4. Document all changes in this file for review
