import sqlite3, time
conn = sqlite3.connect('myra_app/db/myra_technical.db')
query = """
SELECT t.symbol, t.date, t.close as anomaly_close, latest_price.latest_close,
       t.delivery_pct, t.delivery_divergence_score,
       t.volatility_compression_score, t.relative_volume_score,
       t.nifty_outperformance_score, t.volume
FROM technical_data t
LEFT JOIN (
    SELECT symbol, close as latest_close
    FROM technical_data
    WHERE (symbol, date) IN (SELECT symbol, MAX(date) FROM technical_data GROUP BY symbol)
) latest_price ON t.symbol = latest_price.symbol
WHERE t.date >= date('now', '-30 days')
  AND t.delivery_pct IS NOT NULL
  AND t.delivery_divergence_score IS NOT NULL
ORDER BY t.delivery_divergence_score DESC
LIMIT 500
"""
start = time.time()
result = conn.execute(query).fetchall()
elapsed = time.time() - start
print(f'Rows: {len(result)}, Time: {elapsed:.2f}s')
conn.close()
