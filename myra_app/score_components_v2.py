import numpy as np
import pandas as pd

def precompute_ranks(df):
    """
    Precomputes percentile ranks for the entire dataframe in a single vectorized pass.
    This eliminates the O(N^2) performance bottleneck, dropping scan time to milliseconds.
    """
    # .rank(pct=True) automatically ignores NaNs and scales values between 0.0 and 1.0
    # na_option='bottom' ensures that missing data gets the lowest rank
    if "roe" in df.columns:
        df["_rank_roe"] = df["roe"].rank(pct=True, na_option='bottom')
    if "ProfitGrowth" in df.columns:
        df["_rank_growth"] = df["ProfitGrowth"].rank(pct=True, na_option='bottom')
    if "avg_delivery_20d" in df.columns:
        df["_rank_delivery"] = df["avg_delivery_20d"].rank(pct=True, na_option='bottom')
    if "avg_volume_20d" in df.columns:
        df["_rank_volume"] = df["avg_volume_20d"].rank(pct=True, na_option='bottom')
    if "smart_money_score" in df.columns:
        df["_rank_sm_score"] = df["smart_money_score"].rank(pct=True, na_option='bottom')
    return df

# 1. TREND STRUCTURE (LONG TERM)
def trend_score(row):
    score = 0
    sma50 = row.get("sma50", 0)
    sma150 = row.get("sma150", 0)
    sma200 = row.get("sma200", 0)
    close = row.get("close", 0)
    
    if pd.isna(sma50) or pd.isna(sma150) or pd.isna(sma200) or pd.isna(close):
        return 0.3 # Neutral fallback

    if sma50 > sma150: score += 0.4
    if sma150 > sma200: score += 0.4
    if close > sma200: score += 0.2
    return score

# 2. STABILITY (CONSISTENCY)
def stability_score(row):
    val = row.get("pct_above_ma50_60d", 0)
    return val if not pd.isna(val) else 0.3

# 3. DELIVERY ACCUMULATION
def delivery_score(row):
    # PKScreener Superpower: Smart Money Weighted Delivery
    d_rank = row.get("_rank_delivery", 0.3)
    sm_rank = row.get("_rank_sm_score", 0.3)
    
    if pd.isna(d_rank) or d_rank is None: d_rank = 0.3
    if pd.isna(sm_rank) or sm_rank is None: sm_rank = 0.3
    
    # High weight to Composite Smart Money Score
    return (sm_rank * 0.6) + (d_rank * 0.4)

# 4. LIQUIDITY
def liquidity_score(row):
    val = row.get("_rank_volume")
    if pd.isna(val) or val is None: return 0.3
    return val

# 5. BASE FORMATION (VCP / SQUEEZE)
def base_score(row):
    # PKScreener Superpower: TTM Squeeze & Tightness
    score = 0
    atr = row.get("atr_pct", 0.05)
    if pd.isna(atr): atr = 0.05
    
    if atr < 0.03: score += 0.5
    elif atr < 0.05: score += 0.3
    
    # TTM Squeeze Detection
    upper_k = row.get("keltner_upper", 0)
    lower_k = row.get("keltner_lower", 0)
    
    if upper_k and not pd.isna(upper_k) and upper_k > 0:
        close = row.get("close", 0)
        atr5 = row.get("atr5", 0)
        if close and atr5 and not pd.isna(close) and not pd.isna(atr5):
            # Check if 1.5 ATR is completely inside Keltner Channels
            if (close + atr5 < upper_k) and (close - atr5 > lower_k):
                score += 0.5
            
    return min(1.0, score if score > 0 else 0.3)

# 6. FUNDAMENTALS (DYNAMIC)
def fundamental_score(row):
    # Fetch precomputed ranks (falling back to 0.4 if missing)
    roe_score = row.get("_rank_roe", 0.4)
    growth_score = row.get("_rank_growth", 0.4)
    
    if pd.isna(roe_score): roe_score = 0.4
    if pd.isna(growth_score): growth_score = 0.4
    
    f_score = row.get("F_Score", 0)
    quality_score = f_score / 5.0 # Institutional Quality Weighting (0 to 1)
    
    return (roe_score * 0.4) + (growth_score * 0.3) + (quality_score * 0.3)

# 7. VALUATION (BENJAMIN GRAHAM)
def valuation_score(row):
    # PKScreener Superpower: Graham Number & Margin of Safety
    close = row.get("close", 1)
    graham = row.get("graham_number", 0)
    
    if graham <= 0: return 0.5 # Neutral if no data
    
    # Margin of Safety: Discount to Graham Number
    mos = (graham / close) - 1
    
    if mos > 0.3: return 1.0 # Deep Value
    if mos > 0: return 0.8 # Fairly Valued
    if mos > -0.2: return 0.5 # Slightly Overvalued
    return 0.2 # Expensive

def regime_adjustment(score, regime):
    if regime == "EXTREME FEAR":
        return score * 1.15
    elif regime == "EXTREME GREED":
        return score * 0.9
    return score
