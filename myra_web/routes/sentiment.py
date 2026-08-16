from fastapi import APIRouter

router = APIRouter(prefix="/api/sentiment", tags=["sentiment"])


@router.get("/{ticker}")
async def get_news_sentiment(ticker: str, refresh: bool = False):
    """Get news headlines with FinBERT sentiment for a given NSE ticker.
    Results are cached for 6 hours. Use ?refresh=true to force fresh fetch."""
    try:
        from myra_app.news_sentiment import get_ticker_news

        news = get_ticker_news(ticker, refresh=refresh)
        return {
            "ticker": ticker.upper(),
            "count": len(news),
            "news": news,
            "cached": not refresh,
            "status": "success",
        }
    except Exception as e:
        return {
            "ticker": ticker.upper(),
            "error": str(e),
            "news": [],
            "status": "error",
        }
