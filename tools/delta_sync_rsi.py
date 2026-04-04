import os
import sys

# Add current dir to path
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.append(PROJECT_ROOT)

from myra_app.screener import MYRAScreener
from rich.console import Console

console = Console()
screener = MYRAScreener(console)

try:
    console.print("[bold cyan][*] MYRA Repair: Adding RSI Support & Performing Delta Sync (2026-03-30)...[/bold cyan]")
    
    # 1. Update Schema First (Ensure column exists)
    try:
        screener.lib.conn.execute("ALTER TABLE calculated_indicators ADD COLUMN rsi DOUBLE")
        console.print("[dim][*] Added 'rsi' column to calculated_indicators.[/dim]")
    except Exception:
        # If exists, it will fail, which is fine
        pass

    target_date = "2026-03-30"
    
    # 2. Re-run the heavy logic for the latest date (Surgical Delta)
    # Using the exact same logic from librarian_intelligence.py (which I just updated)
    
    # We'll call update_indicator_history but we want it to be fast.
    # Actually, the best way is to run the query I just fixed but with a filter for the target date.
    
    sql = f"""
        INSERT OR REPLACE INTO calculated_indicators
        WITH base_data AS (
            SELECT p.*, 
                LAG(p.close) OVER (PARTITION BY p.symbol ORDER BY p.date) as prev_close,
                LAG(p.high) OVER (PARTITION BY p.symbol ORDER BY p.date) as prev_high,
                LAG(p.low) OVER (PARTITION BY p.symbol ORDER BY p.date) as prev_low,
                LAG(p.high, 2) OVER (PARTITION BY p.symbol ORDER BY p.date) as high_minus_2,
                LAG(p.low, 2) OVER (PARTITION BY p.symbol ORDER BY p.date) as low_minus_2,
                ABS(p.close - LAG(p.close, 1) OVER (PARTITION BY p.symbol ORDER BY p.date)) as abs_diff_1,
                ABS(p.close - LAG(p.close, 10) OVER (PARTITION BY p.symbol ORDER BY p.date)) as abs_diff_10,
                GREATEST(p.close - LAG(p.close) OVER (PARTITION BY p.symbol ORDER BY p.date), 0) as gain,
                GREATEST(LAG(p.close) OVER (PARTITION BY p.symbol ORDER BY p.date) - p.close, 0) as loss
            FROM prices p
            WHERE p.date >= CAST('{target_date}' AS DATE) - INTERVAL 1000 DAY
        ),
        tr_data AS (
            SELECT *,
                GREATEST(high - low, ABS(high - prev_close), ABS(low - prev_close)) as tr,
                CASE WHEN low > high_minus_2 THEN 1 WHEN high < low_minus_2 THEN -1 ELSE 0 END as fvg_val
            FROM base_data
        ),
        funda_latest AS (
            SELECT symbol, eps, book_value FROM (
                SELECT symbol, eps, book_value, ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY last_updated DESC) as rn
                FROM fundamentals_quarterly WHERE eps IS NOT NULL AND NOT isnan(eps)
            ) WHERE rn = 1
        ),
        computed AS (
            SELECT rd.*, fl.eps as eps_latest, fl.book_value as bvps_latest,
                AVG(rd.close) OVER (PARTITION BY rd.symbol ORDER BY rd.date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) as sma20,
                AVG(rd.close) OVER (PARTITION BY rd.symbol ORDER BY rd.date ROWS BETWEEN 49 PRECEDING AND CURRENT ROW) as sma50,
                AVG(rd.close) OVER (PARTITION BY rd.symbol ORDER BY rd.date ROWS BETWEEN 149 PRECEDING AND CURRENT ROW) as sma150,
                AVG(rd.close) OVER (PARTITION BY rd.symbol ORDER BY rd.date ROWS BETWEEN 199 PRECEDING AND CURRENT ROW) as sma200,
                AVG(rd.tr) OVER (PARTITION BY rd.symbol ORDER BY rd.date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) as atr20,
                AVG(rd.tr) OVER (PARTITION BY rd.symbol ORDER BY rd.date ROWS BETWEEN 4 PRECEDING AND CURRENT ROW) as atr5,
                AVG(rd.tr) OVER (PARTITION BY rd.symbol ORDER BY rd.date ROWS BETWEEN 13 PRECEDING AND CURRENT ROW) as atr14,
                rd.delivery_qty / NULLIF(AVG(rd.delivery_qty) OVER (PARTITION BY rd.symbol ORDER BY rd.date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW), 0) as rdv,
                STDDEV(rd.close) OVER (PARTITION BY rd.symbol ORDER BY rd.date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) as std20,
                MIN(rd.close) OVER (PARTITION BY rd.symbol ORDER BY rd.date ROWS BETWEEN 251 PRECEDING AND CURRENT ROW) as low_1y,
                MAX(rd.high) OVER (PARTITION BY rd.symbol ORDER BY rd.date ROWS BETWEEN 251 PRECEDING AND CURRENT ROW) as high_1y,
                AVG(rd.volume) OVER (PARTITION BY rd.symbol ORDER BY rd.date ROWS BETWEEN 49 PRECEDING AND CURRENT ROW) as vol_sma50,
                AVG(rd.delivery_qty) OVER (PARTITION BY rd.symbol ORDER BY rd.date ROWS BETWEEN 49 PRECEDING AND CURRENT ROW) as deliv_sma50,
                AVG(rd.volume) OVER (PARTITION BY rd.symbol ORDER BY rd.date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) as avg_volume_20d,
                AVG(rd.delivery_qty) OVER (PARTITION BY rd.symbol ORDER BY rd.date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) as avg_delivery_20d,
                SUM(rd.close * rd.volume) OVER (PARTITION BY rd.symbol ORDER BY rd.date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) / NULLIF(SUM(rd.volume) OVER (PARTITION BY rd.symbol ORDER BY rd.date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW), 0) as vwap_20d,
                (rd.prev_high + rd.prev_low + rd.prev_close) / 3 as pivot_pt,
                (rd.prev_high + rd.prev_low) / 2 as cpr_bc,
                ((rd.prev_high + rd.prev_low + rd.prev_close) / 3 - ((rd.prev_high + rd.prev_low) / 2)) + (rd.prev_high + rd.prev_low + rd.prev_close) / 3 as cpr_tc,
                ((rd.prev_high + rd.prev_low + rd.prev_close) / 3 * 2) - rd.prev_low as r1,
                ((rd.prev_high + rd.prev_low + rd.prev_close) / 3 * 2) - rd.prev_high as s1,
                AVG(rd.close) OVER (PARTITION BY rd.symbol ORDER BY rd.date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) + (1.5 * AVG(rd.tr) OVER (PARTITION BY rd.symbol ORDER BY rd.date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW)) as keltner_upper,
                AVG(rd.close) OVER (PARTITION BY rd.symbol ORDER BY rd.date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) - (1.5 * AVG(rd.tr) OVER (PARTITION BY rd.symbol ORDER BY rd.date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW)) as keltner_lower,
                (rd.high - rd.low) / NULLIF(AVG(rd.high - rd.low) OVER (PARTITION BY rd.symbol ORDER BY rd.date ROWS BETWEEN 49 PRECEDING AND CURRENT ROW), 0) as rel_spread,
                rd.volume / NULLIF(AVG(rd.volume) OVER (PARTITION BY rd.symbol ORDER BY rd.date ROWS BETWEEN 49 PRECEDING AND CURRENT ROW), 0) as rel_vol,
                (rd.close - rd.low) / NULLIF(rd.high - rd.low, 0) as closing_pos,
                (rd.delivery_qty * rd.close) / 10000000.0 as money_flow_cr,
                ((rd.close - rd.low) - (rd.high - rd.low)) / NULLIF(rd.high - rd.low, 0) as ad_multiplier,
                rd.abs_diff_10 / NULLIF(SUM(rd.abs_diff_1) OVER (PARTITION BY rd.symbol ORDER BY rd.date ROWS BETWEEN 9 PRECEDING AND CURRENT ROW), 0) as efficiency_ratio,
                100 - (100 / (1 + (AVG(rd.gain) OVER (PARTITION BY rd.symbol ORDER BY rd.date ROWS BETWEEN 13 PRECEDING AND CURRENT ROW) / NULLIF(AVG(rd.loss) OVER (PARTITION BY rd.symbol ORDER BY rd.date ROWS BETWEEN 13 PRECEDING AND CURRENT ROW), 0)))) as rsi
            FROM tr_data rd
            LEFT JOIN funda_latest fl ON rd.symbol = fl.symbol
        ),
        statistical AS (
            SELECT *,
                (sma20 + 2*std20 - (sma20 - 2*std20)) / NULLIF(sma20, 0) as bb_width,
                LOG(NULLIF(STDDEV(close) OVER (PARTITION BY symbol ORDER BY date ROWS BETWEEN 29 PRECEDING AND CURRENT ROW), 0)) / NULLIF(LOG(30), 0) as hurst_exponent,
                ((MAX(high) OVER (PARTITION BY symbol ORDER BY date ROWS BETWEEN 4 PRECEDING AND CURRENT ROW) - MIN(low) OVER (PARTITION BY symbol ORDER BY date ROWS BETWEEN 4 PRECEDING AND CURRENT ROW)) < (1.5 * atr14)) as squeeze_flag
            FROM computed
        ),
        stability AS (
            SELECT *,
                (CAST(SUM(CASE WHEN close > sma50 THEN 1 ELSE 0 END) OVER (PARTITION BY symbol ORDER BY date ROWS BETWEEN 59 PRECEDING AND CURRENT ROW) AS DOUBLE) / 60.0) as pct_above_ma50_60d,
                ((MAX(high) OVER (PARTITION BY symbol ORDER BY date ROWS BETWEEN 251 PRECEDING AND CURRENT ROW) - close) / NULLIF(MAX(high) OVER (PARTITION BY symbol ORDER BY date ROWS BETWEEN 251 PRECEDING AND CURRENT ROW), 0)) as drawdown,
                CASE WHEN close > MAX(high) OVER (PARTITION BY symbol ORDER BY date ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING) THEN 1 WHEN close < MIN(low) OVER (PARTITION BY symbol ORDER BY date ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING) THEN -1 ELSE 0 END as structure_break
            FROM statistical
        ),
        footprints AS (
            SELECT *,
                SUM(ad_multiplier * volume) OVER (PARTITION BY symbol ORDER BY date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) as ad_flow,
                (deliv_sma50 / NULLIF(AVG(delivery_qty) OVER (PARTITION BY symbol ORDER BY date ROWS BETWEEN 250 PRECEDING AND 50 PRECEDING), 0)) as absorp_ratio,
                (COALESCE(rdv, 0) * 0.4) + (COALESCE(delivery_percent, 0) / 100.0 * 0.3) + (CASE WHEN squeeze_flag = TRUE THEN 0.3 ELSE 0.0 END) as smart_money_score,
                AVG(close) OVER (PARTITION BY symbol ORDER BY date ROWS BETWEEN 59 PRECEDING AND CURRENT ROW) as d_poc
            FROM stability
        ),
        smc_final AS (
            SELECT *,
                CASE WHEN close > (d_poc * 1.03) AND close >= high_1y AND volume > (avg_volume_20d * 1.5) THEN 2 WHEN ABS(close - d_poc) / d_poc <= 0.015 AND (std20 / NULLIF(close, 0)) < 0.015 AND volume < (avg_volume_20d * 0.6) AND (absorp_ratio * 100) > 60 THEN 1 ELSE 0 END as smc_phase
            FROM footprints
        )
        SELECT symbol, date, open, high, low, close, volume,
               sma20, sma50, sma150, sma200, atr20, atr5, 
               0 as low_1y, 0 as low_2y, 0 as low_3y, high_1y, 0 as high_2y,
               vol_sma50, deliv_sma50, avg_volume_20d, avg_delivery_20d, ad_flow, absorp_ratio, 
               pct_above_ma50_60d, drawdown, vwap_20d, pivot_pt, r1, s1, rel_spread, rel_vol, closing_pos, money_flow_cr,
               eps_latest, bvps_latest, pivot_pt as cpr_pivot, cpr_bc, cpr_tc, keltner_upper, keltner_lower,
               hurst_exponent, efficiency_ratio, bb_width,
               atr14, rdv, squeeze_flag,
               delivery_qty, delivery_percent,
               smart_money_score,
               std20, d_poc, smc_phase,
               fvg_val as fvg, structure_break as bos, structure_break as choch,
               rsi
        FROM smc_final
        WHERE date = CAST('{target_date}' AS DATE)
    """
    
    with console.status(f"[bold magenta][*] Computing technicals with RSI for {target_date}...[/bold magenta]"):
        screener.lib.conn.execute(sql)
        
    # 3. Verify
    count = screener.lib.conn.execute("SELECT COUNT(*) FROM calculated_indicators WHERE date = ? AND rsi IS NOT NULL", (target_date,)).fetchone()[0]
    if count > 0:
        console.print(f"[success][✔] RSI Support Added: {count} symbols updated for {target_date}.[/success]")
    else:
        console.print(f"[warning][!] RSI calculation failed for {target_date}.[/warning]")

finally:
    screener.close()
