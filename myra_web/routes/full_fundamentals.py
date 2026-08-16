"""Deep Fundamentals API router.

GET /api/full-fundamentals/{symbol}  -> full fundamentals + rule-based insights.
Accepts ?refresh=true to bypass the 1-hour cache.
"""

import json
import logging
from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException

from myra_app.fetchers.full_fundamentals import (
    fetch_full_fundamentals,
    load_cache,
    save_cache,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/full-fundamentals", tags=["full-fundamentals"])

CACHE_TTL_HOURS = 1


# --------------------------------------------------------------------------- #
# Small numeric helpers
# --------------------------------------------------------------------------- #

def _series_values(series):
    if not series:
        return []
    return [p.get("value") for p in series if isinstance(p, dict) and p.get("value") is not None]


def _series_latest(series):
    vals = _series_values(series)
    return vals[-1] if vals else None


def _series_avg(series):
    vals = _series_values(series)
    return sum(vals) / len(vals) if vals else None


def _normalize_debt_to_equity(v):
    """yfinance returns D/E as a percentage for Indian tickers (e.g. 36.65)."""
    if v is None:
        return None
    if v > 5:
        return round(v / 100, 2)
    return round(v, 2)


def _normalize_dividend_yield(v):
    """Snapshot values are already percents; yfinance may return a fraction."""
    if v is None:
        return None
    if v > 1:
        return round(v, 2)
    return round(v * 100, 2)


# --------------------------------------------------------------------------- #
# Insights
# --------------------------------------------------------------------------- #

def generate_insights(data: dict) -> list:
    """Rule-based qualitative read of the fundamentals payload."""
    insights = []
    snapshot = data.get("snapshot", {}) or {}
    timeseries = data.get("timeseries", {}) or {}
    ydata = data.get("yfinance", {}) or {}
    shareholding = snapshot.get("shareholding", {}) or {}

    def add(key, title, detail, severity):
        insights.append(
            {"key": key, "title": title, "detail": detail, "severity": severity}
        )

    # 1. PE vs 5-year average
    pe_series = timeseries.get("pe")
    if pe_series:
        avg = _series_avg(pe_series)
        cur = snapshot.get("pe") or _series_latest(pe_series)
        if avg and cur:
            ratio = cur / avg
            if ratio > 2:
                add(
                    "pe",
                    "PE well above historical",
                    f"Current PE {cur:.1f} is more than 2x the 5-year average of {avg:.1f}.",
                    "red",
                )
            elif ratio > 1.2:
                add(
                    "pe",
                    "PE above 5-year average",
                    f"Current PE {cur:.1f} vs 5-year average {avg:.1f}.",
                    "yellow",
                )
            elif ratio < 0.8:
                add(
                    "pe",
                    "PE below 5-year average",
                    f"Current PE {cur:.1f} is trading below the 5-year average of {avg:.1f}.",
                    "green",
                )
            else:
                add(
                    "pe",
                    "PE in line with history",
                    f"Current PE {cur:.1f} vs 5-year average {avg:.1f}.",
                    "green",
                )

    # 2. ROE trend / level
    roe_series = timeseries.get("roe")
    if roe_series and len(roe_series) >= 2:
        earlier = roe_series[0].get("value")
        recent = roe_series[-1].get("value")
        if earlier is not None and recent is not None:
            if recent < earlier:
                add(
                    "roe",
                    "ROE declining",
                    f"ROE fell from {earlier:.1f}% to {recent:.1f}% over the window.",
                    "yellow",
                )
            else:
                add(
                    "roe",
                    "ROE improving",
                    f"ROE rose from {earlier:.1f}% to {recent:.1f}% over the window.",
                    "green",
                )
    else:
        roe = snapshot.get("roe") or ydata.get("roe")
        if roe is not None:
            if roe >= 12:
                add("roe", "Healthy ROE", f"ROE of {roe:.1f}% indicates strong capital efficiency.", "green")
            elif roe >= 8:
                add("roe", "Moderate ROE", f"ROE of {roe:.1f}% is acceptable but not exceptional.", "yellow")
            else:
                add("roe", "Weak ROE", f"ROE of {roe:.1f}% is below healthy thresholds.", "red")

    # 3. ROCE
    roce = snapshot.get("roce") or _series_latest(timeseries.get("roce")) or ydata.get("roce")
    if roce is not None:
        if roce >= 15:
            add("roce", "Strong ROCE", f"ROCE of {roce:.1f}% clears the 15% efficiency bar.", "green")
        elif roce >= 10:
            add("roce", "Decent ROCE", f"ROCE of {roce:.1f}% is reasonable but below 15%.", "yellow")
        else:
            add("roce", "Low ROCE", f"ROCE of {roce:.1f}% signals weak capital returns.", "red")

    # 4. Leverage (Debt/Equity)
    de = _normalize_debt_to_equity(ydata.get("debt_to_equity"))
    if de is not None:
        if de >= 2:
            add("leverage", "Highly leveraged", f"Debt/Equity of {de:.2f} is dangerously high.", "red")
        elif de < 0.5:
            add("leverage", "Low leverage", f"Debt/Equity of {de:.2f} is conservative.", "green")
        else:
            add("leverage", "Moderate leverage", f"Debt/Equity of {de:.2f} is within normal bounds.", "yellow")

    # 5. Promoter holding
    prom = shareholding.get("promoters")
    if prom is not None:
        if prom >= 50:
            add("promoter", "Strong promoter stake", f"Promoters hold {prom:.1f}% — high alignment.", "green")
        elif prom < 30:
            add("promoter", "Low promoter stake", f"Promoters hold only {prom:.1f}%.", "red")
        else:
            add("promoter", "Moderate promoter stake", f"Promoters hold {prom:.1f}%.", "yellow")

    # 6. Analyst recommendation
    rec = str(ydata.get("recommendation_key") or "").lower()
    if rec:
        if rec in ("strong_buy", "buy"):
            add("analyst", "Analysts bullish", "Consensus analyst rating is BUY.", "green")
        elif rec in ("strong_sell", "sell"):
            add("analyst", "Analysts bearish", "Consensus analyst rating is SELL.", "red")
        else:
            add("analyst", "Analysts neutral", "Consensus analyst rating is HOLD.", "yellow")

    # 7. Dividend yield
    dy = _normalize_dividend_yield(snapshot.get("dividend_yield") or ydata.get("dividend_yield"))
    if dy is not None and dy > 0:
        if dy >= 2:
            add("dividend", "Attractive dividend", f"Dividend yield of {dy:.2f}% beats the 2% bar.", "green")
        else:
            add("dividend", "Low dividend yield", f"Dividend yield of {dy:.2f}% is below 2%.", "yellow")

    # 8. Earnings growth
    eg = ydata.get("earnings_growth")
    if eg is not None:
        if eg < 0:
            add("growth", "Earnings contracting", f"Earnings growth is {eg * 100:.1f}% YoY.", "yellow")
        else:
            add("growth", "Earnings growing", f"Earnings growth is {eg * 100:.1f}% YoY.", "green")

    return insights


# --------------------------------------------------------------------------- #
# Endpoint
# --------------------------------------------------------------------------- #

def _cache_is_fresh(cache_hit) -> bool:
    try:
        updated = datetime.fromisoformat(cache_hit["last_updated"])
        return datetime.now() - updated < timedelta(hours=CACHE_TTL_HOURS)
    except Exception:
        return False


@router.get("/{symbol}")
def get_full_fundamentals(symbol: str, refresh: bool = False):
    sym = symbol.strip().upper()
    if not sym:
        raise HTTPException(status_code=400, detail="Symbol is required")

    if not refresh:
        cache_hit = load_cache(sym)
        if cache_hit and _cache_is_fresh(cache_hit):
            return {
                "symbol": sym,
                "data": cache_hit["data"],
                "insights": generate_insights(cache_hit["data"]),
                "cached": True,
                "last_updated": cache_hit["last_updated"],
            }

    try:
        data = fetch_full_fundamentals(sym)
    except Exception as e:
        logger.exception("full fundamentals fetch failed for %s", sym)
        raise HTTPException(status_code=500, detail=f"Failed to fetch fundamentals for {sym}: {e}")

    has_data = bool(
        data.get("snapshot")
        or data.get("timeseries")
        or data.get("ratios")
        or data.get("yfinance")
    )
    if not has_data:
        raise HTTPException(status_code=404, detail=f"No data found for symbol {sym}")

    insights = generate_insights(data)
    source = ", ".join(data.get("sources", [])) or "mixed"
    save_cache(sym, json.dumps(data, default=str), source=source)
    return {
        "symbol": sym,
        "data": data,
        "insights": insights,
        "cached": False,
        "last_updated": data.get("timestamp"),
    }
