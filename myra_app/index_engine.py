#!/usr/bin/env python
"""
MYRA Index Engine - Direct NSE Quote Acquisition
Replaces Yahoo Finance for Benchmarks (^NSEI, ^INDIAVIX)
Enhanced with Local-First Breadth & YFinance Fallback.
"""
import time
import requests
import pandas as pd
from datetime import datetime, timedelta

try:
    import yfinance as yf
except ImportError:
    yf = None

try:
    from nsepython import nse_get_index_quote
except ImportError:
    nse_get_index_quote = None

class IndexEngine:
    def __init__(self):
        self.cache = {}
        self.cache_expiry = 3600  # 1 hour sweet spot

    def _is_cache_valid(self, key):
        if key not in self.cache:
            return False
        # Fix 30: Avoid chained indexing
        entry = self.cache[key]
        ts = entry["timestamp"]
        return (datetime.now() - ts).total_seconds() < self.cache_expiry

    def _store_cache(self, key, data):
        self.cache[key] = {
            "data": data,
            "timestamp": datetime.now()
        }

    def get_nifty(self):
        key = "NIFTY"
        if self._is_cache_valid(key):
            # Fix 42: Avoid chained indexing
            entry = self.cache[key]
            return entry["data"]

        # 1. Primary: NSE Direct (if nsepython works)
        if nse_get_index_quote:
            try:
                data = nse_get_index_quote("NIFTY 50")
                if data:
                    result = {
                        "last_price": float(data.get("lastPrice", 0)),
                        "change": float(data.get("change", 0)),
                        "pchange": float(data.get("pChange", 0)),
                        "timestamp": datetime.now()
                    }
                    self._store_cache(key, result)
                    return result
            except Exception: pass

        # 2. Fallback: YFinance
        if yf:
            try:
                data = yf.download("^NSEI", period="2d", interval="1d", progress=False)
                if not data.empty:
                    latest = data['Close'].iloc[-1]
                    prev = data['Close'].iloc[-2]
                    change = latest - prev
                    pchange = (change / prev) * 100
                    result = {
                        "last_price": round(float(latest), 2),
                        "change": round(float(change), 2),
                        "pchange": round(float(pchange), 2),
                        "timestamp": datetime.now()
                    }
                    self._store_cache(key, result)
                    return result
            except Exception: pass

        return self.cache.get(key, {}).get("data")

    def get_vix(self):
        key = "VIX"
        if self._is_cache_valid(key):
            # Fix 83: Avoid chained indexing
            entry = self.cache[key]
            return entry["data"]

        # 1. Primary: NSE Direct
        if nse_get_index_quote:
            try:
                data = nse_get_index_quote("INDIA VIX")
                if data:
                    result = {
                        "last_price": float(data.get("lastPrice", 0)),
                        "change": float(data.get("change", 0)),
                        "pchange": float(data.get("pChange", 0)),
                        "timestamp": datetime.now()
                    }
                    self._store_cache(key, result)
                    return result
            except Exception: pass

        # 2. Fallback: YFinance
        if yf:
            try:
                data = yf.download("^INDIAVIX", period="2d", interval="1d", progress=False)
                if not data.empty:
                    latest = data['Close'].iloc[-1]
                    prev = data['Close'].iloc[-2]
                    change = latest - prev
                    pchange = (change / prev) * 100
                    result = {
                        "last_price": round(float(latest), 2),
                        "change": round(float(change), 2),
                        "pchange": round(float(pchange), 2),
                        "timestamp": datetime.now()
                    }
                    self._store_cache(key, result)
                    return result
            except Exception: pass

        return self.cache.get(key, {}).get("data")

    def get_global_vibe(self):
        """Fetches performance of major world indices for global context"""
        key = "GLOBAL_VIBE"
        if self._is_cache_valid(key):
            # Fix 125: Avoid chained indexing
            entry = self.cache[key]
            return entry["data"]
            
        indices = {
            "S&P 500": "^GSPC",
            "NASDAQ": "^IXIC",
            "Nikkei 225": "^N225",
            "FTSE 100": "^FTSE"
        }
        
        if not yf: return None
        
        # Optimized with list comprehension (Fix 142: Avoid .append in loop)
        def _get_change(sym):
            try:
                data = yf.download(sym, period="2d", interval="1d", progress=False)
                if len(data) >= 2:
                    return ((data['Close'].iloc[-1] / data['Close'].iloc[-2]) - 1) * 100
            except: pass
            return None

        results = [float(c) for s in indices.values() if (c := _get_change(s)) is not None]
            
        if results:
            avg_change = sum(results) / len(results)
            vibe = "BULLISH" if avg_change > 0.5 else "BEARISH" if avg_change < -0.5 else "NEUTRAL"
            res = {"avg": round(avg_change, 2), "vibe": vibe}
            self._store_cache(key, res)
            return res
        return None

    def get_market_barometer(self):
        """Calculates 1M and 3M index performance for trend context"""
        key = "BAROMETER"
        if self._is_cache_valid(key):
            # Fix 157: Avoid chained indexing
            entry = self.cache[key]
            return entry["data"]
            
        if not yf: return None
        try:
            data = yf.download("^NSEI", period="4mo", interval="1d", progress=False)
            if not data.empty:
                latest = data['Close'].iloc[-1]
                m1 = data['Close'].iloc[-22] if len(data) >= 22 else data['Close'].iloc[0]
                m3 = data['Close'].iloc[-63] if len(data) >= 63 else data['Close'].iloc[0]
                
                perf_1m = ((latest / m1) - 1) * 100
                perf_3m = ((latest / m3) - 1) * 100
                
                res = {"1M": round(float(perf_1m), 1), "3M": round(float(perf_3m), 1)}
                self._store_cache(key, res)
                return res
        except Exception: pass
        return None

    def get_market_breadth(self, librarian=None):
        """Returns advance/decline counts for the full market (Local-First)"""
        key = "BREADTH"
        if self._is_cache_valid(key):
            # Fix 180: Avoid chained indexing
            entry = self.cache[key]
            return entry["data"]

        if not librarian or not librarian.conn: 
            return {"advances": 0, "declines": 0, "unchanged": 0}

        try:
            # PKScreener Superpower: DB-driven Breadth
            # Only count active universe stocks for accuracy
            sql = """
                WITH latest_prices AS (
                    SELECT p.symbol, p.close, 
                           LAG(p.close) OVER (PARTITION BY p.symbol ORDER BY p.date) as prev_close
                    FROM prices p
                    WHERE p.date >= CURRENT_DATE - INTERVAL 5 DAY
                    AND p.symbol IN (SELECT symbol FROM symbols_master WHERE in_active_universe = TRUE)
                ),
                current_state AS (
                    SELECT symbol, close, prev_close,
                           ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY symbol) as rn
                    FROM latest_prices
                )
                SELECT 
                    SUM(CASE WHEN close > prev_close THEN 1 ELSE 0 END) as advances,
                    SUM(CASE WHEN close < prev_close THEN 1 ELSE 0 END) as declines,
                    SUM(CASE WHEN close = prev_close THEN 1 ELSE 0 END) as unchanged
                FROM (
                    SELECT symbol, close, prev_close 
                    FROM (
                        SELECT *, ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY symbol) as r 
                        FROM latest_prices
                    ) -- This part needs careful DuckDB SQL for 'latest' date per symbol
                )
            """
            
            # Refined SQL for DuckDB to get breadth of the MOST RECENT day in DB
            sql_refined = """
                WITH latest_date AS (SELECT MAX(date) as d FROM prices),
                data AS (
                    SELECT p.symbol, p.close, p.open,
                           (SELECT close FROM prices p2 WHERE p2.symbol = p.symbol AND p2.date < p.date ORDER BY date DESC LIMIT 1) as prev_close
                    FROM prices p, latest_date ld
                    WHERE p.date = ld.d
                    AND p.symbol IN (SELECT symbol FROM symbols_master WHERE in_active_universe = TRUE)
                )
                SELECT 
                    SUM(CASE WHEN close > prev_close THEN 1 ELSE 0 END) as advances,
                    SUM(CASE WHEN close < prev_close THEN 1 ELSE 0 END) as declines,
                    SUM(CASE WHEN close = prev_close THEN 1 ELSE 0 END) as unchanged
                FROM data
            """
            
            res = librarian.conn.execute(sql_refined).fetchone()
            if res:
                result = {
                    "advances": int(res[0] or 0),
                    "declines": int(res[1] or 0),
                    "unchanged": int(res[2] or 0)
                }
                self._store_cache(key, result)
                return result
        except Exception:
            pass
            
        return {"advances": 0, "declines": 0, "unchanged": 0}
