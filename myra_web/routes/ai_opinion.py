from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api/ai-opinion", tags=["ai_opinion"])


@router.get("/{ticker}")
async def get_ai_opinion(ticker: str):
    """On-demand Gemini LLM second opinion for a stock.

    Returns a BUY/SELL/HOLD signal with rationale, confidence, and the
    technical summary the model evaluated.  Results are cached for 24 h
    by the underlying module (rate-limit-safe, no per-candidate loops).
    """
    try:
        from myra_app.ai_second_opinion import (
            build_technical_summary,
            get_ai_second_opinion,
        )

        summary = build_technical_summary(ticker.upper())
        opinion = get_ai_second_opinion(ticker.upper(), summary)
        return {
            "ticker": ticker.upper(),
            "signal": opinion["signal"],
            "reason": opinion["reason"],
            "confidence": opinion["confidence"],
            "source": opinion["source"],
            "cached": opinion["cached"],
            "summary": summary,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
