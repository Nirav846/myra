import logging
import time

from fastapi import APIRouter, HTTPException, Query

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/finstack", tags=["finstack"])

_finstack_cache = {}
CACHE_TTL = 300  # 5 minutes


def _validate_finstack(result: dict) -> dict:
    if "error" in result:
        raise HTTPException(status_code=500, detail=result["error"])
    if "_raw" in result:
        raise HTTPException(
            status_code=502, detail="FinStack MCP returned non-JSON response"
        )
    return result


@router.get("/nifty-outlook")
async def finstack_nifty_outlook():
    cache_key = "nifty_outlook"
    now = time.time()
    if (
        cache_key in _finstack_cache
        and (now - _finstack_cache[cache_key]["ts"]) < CACHE_TTL
    ):
        return _finstack_cache[cache_key]["data"]
    from myra_app.utils.finstack_bridge import get_nifty_outlook

    try:
        data = await get_nifty_outlook()
        _finstack_cache[cache_key] = {"ts": now, "data": data}
        return data
    except Exception as e:
        logger.exception("finstack_nifty_outlook failed")
        raise HTTPException(status_code=500, detail="Internal server error")
async def finstack_fii_retail_divergence(symbol: str = "RELIANCE"):
    cache_key = f"fii_divergence:{symbol}"
    now = time.time()
    if (
        cache_key in _finstack_cache
        and (now - _finstack_cache[cache_key]["ts"]) < CACHE_TTL
    ):
        return _finstack_cache[cache_key]["data"]
    from myra_app.utils.finstack_bridge import get_fii_retail_divergence

    try:
        data = await get_fii_retail_divergence(symbol)
        _finstack_cache[cache_key] = {"ts": now, "data": data}
        return data
    except Exception as e:
        logger.exception("finstack_fii_retail_divergence failed")
        raise HTTPException(status_code=500, detail="Internal server error")
# async def finstack_sebi_alerts():
#     from myra_app.utils.finstack_bridge import get_sebi_alerts
#     result = await get_sebi_alerts()
#     return _validate_finstack(result)


@router.get("/morning-brief")
async def finstack_morning_brief():
    cache_key = "morning_brief"
    now = time.time()
    if (
        cache_key in _finstack_cache
        and (now - _finstack_cache[cache_key]["ts"]) < CACHE_TTL
    ):
        return _finstack_cache[cache_key]["data"]
    from myra_app.utils.finstack_bridge import get_morning_brief

    try:
        data = await get_morning_brief()
        _finstack_cache[cache_key] = {"ts": now, "data": data}
        return data
    except Exception as e:
        logger.exception("finstack_morning_brief failed")
        raise HTTPException(status_code=500, detail="Internal server error")
# async def finstack_scan_pledge_risks():
#     from myra_app.utils.finstack_bridge import scan_pledge_risks
#     result = await scan_pledge_risks()
#     return _validate_finstack(result)


@router.get("/stock-brief/{symbol}")
async def finstack_stock_brief(symbol: str):
    from myra_app.utils.finstack_bridge import get_stock_brief

    result = await get_stock_brief(symbol)
    return _validate_finstack(result)


@router.get("/stock-brief")
async def stock_brief(
    symbol: str = Query(..., description="Stock symbol, e.g., RELIANCE")
):
    cache_key = f"stock_brief:{symbol}"
    now = time.time()
    if (
        cache_key in _finstack_cache
        and (now - _finstack_cache[cache_key]["ts"]) < CACHE_TTL
    ):
        return _finstack_cache[cache_key]["data"]
    from myra_app.utils.finstack_bridge import get_stock_brief

    try:
        data = await get_stock_brief(symbol=symbol.upper())
        _finstack_cache[cache_key] = {"ts": now, "data": data}
        return data
    except Exception as e:
        logger.exception("stock_brief failed")
        raise HTTPException(status_code=500, detail="Internal server error")
async def finstack_social_sentiment(symbol: str):
    from myra_app.utils.finstack_bridge import get_social_sentiment

    result = await get_social_sentiment(symbol)
    return _validate_finstack(result)


@router.get("/pledge-alert/{symbol}")
async def finstack_pledge_alert(symbol: str):
    from myra_app.utils.finstack_bridge import get_pledge_alert

    result = await get_pledge_alert(symbol)
    return _validate_finstack(result)


@router.get("/unusual-activity")
async def unusual_activity(
    symbol: str = Query(..., description="Stock symbol, e.g., RELIANCE")
):
    cache_key = f"unusual_activity:{symbol}"
    now = time.time()
    if (
        cache_key in _finstack_cache
        and (now - _finstack_cache[cache_key]["ts"]) < CACHE_TTL
    ):
        return _finstack_cache[cache_key]["data"]
    from myra_app.utils.finstack_bridge import detect_unusual_activity

    try:
        data = await detect_unusual_activity(symbol=symbol.upper())
        _finstack_cache[cache_key] = {"ts": now, "data": data}
        return data
    except Exception as e:
        logger.exception("unusual_activity failed")
        raise HTTPException(status_code=500, detail="Internal server error")
async def finstack_stock_timeline(symbol: str = ""):
    if not symbol:
        raise HTTPException(
            status_code=400, detail="query parameter 'symbol' is required"
        )
    cache_key = f"stock_timeline:{symbol}"
    now = time.time()
    if (
        cache_key in _finstack_cache
        and (now - _finstack_cache[cache_key]["ts"]) < CACHE_TTL
    ):
        return _finstack_cache[cache_key]["data"]
    from myra_app.utils.finstack_bridge import get_stock_timeline

    try:
        data = await get_stock_timeline(symbol)
        _finstack_cache[cache_key] = {"ts": now, "data": data}
        return data
    except Exception as e:
        logger.exception("finstack_stock_timeline failed")
        raise HTTPException(status_code=500, detail="Internal server error")
