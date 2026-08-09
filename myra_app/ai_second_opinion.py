"""Gemini LLM second opinion for stock analysis.

Uses the Gemini REST API (no google.generativeai dependency) to provide
a BUY/SELL/HOLD signal with rationale and confidence score. Results are
cached in a SQLite sidecar DB (myra_ai_cache.db) to avoid redundant
API calls within the same day.

Public API:
    get_ai_second_opinion(symbol, technical_summary) -> dict
    build_technical_summary(symbol) -> str
"""

import json
import logging
import os
import sqlite3
from datetime import datetime
from pathlib import Path

import requests
from dotenv import load_dotenv

from myra_app.constants import DB_DIR

# ---------------------------------------------------------------------------
# .env loading (same pattern as fundamental_sync.py)
# ---------------------------------------------------------------------------
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

logger = logging.getLogger("myra.ai_second_opinion")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
_GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
_GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")
_CACHE_DB_PATH = os.path.join(DB_DIR, "myra_ai_cache.db")
_CACHE_TTL_SECONDS = 86400  # 24 hours (one trading day)


# ---------------------------------------------------------------------------
# Cache helpers (throwaway — errors never propagate)
# ---------------------------------------------------------------------------

def _init_cache_db() -> None:
    """Create the cache table if it doesn't exist."""
    try:
        conn = sqlite3.connect(_CACHE_DB_PATH)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS ai_opinion_cache (
                cache_key TEXT PRIMARY KEY,
                result_json TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now', 'localtime'))
            )
            """
        )
        conn.commit()
        conn.close()
    except Exception:
        logger.debug("Cache DB init failed", exc_info=True)


def _cache_get(cache_key: str) -> dict | None:
    """Return cached result if fresh enough, else None."""
    try:
        conn = sqlite3.connect(_CACHE_DB_PATH)
        row = conn.execute(
            "SELECT result_json, created_at FROM ai_opinion_cache WHERE cache_key = ?",
            (cache_key,),
        ).fetchone()
        conn.close()
        if row is None:
            return None
        result_json, created_at = row
        # Check freshness
        try:
            created = datetime.fromisoformat(created_at)
            age = (datetime.now() - created).total_seconds()
            if age > _CACHE_TTL_SECONDS:
                return None
        except (TypeError, ValueError):
            return None
        return json.loads(result_json)
    except Exception:
        logger.debug("Cache read failed", exc_info=True)
        return None


def _cache_put(cache_key: str, result: dict) -> None:
    """Store result in cache. Failures are silently ignored."""
    try:
        _init_cache_db()
        conn = sqlite3.connect(_CACHE_DB_PATH)
        conn.execute(
            "INSERT OR REPLACE INTO ai_opinion_cache (cache_key, result_json) VALUES (?, ?)",
            (cache_key, json.dumps(result, default=str)),
        )
        conn.commit()
        conn.close()
    except Exception:
        logger.debug("Cache write failed", exc_info=True)


# ---------------------------------------------------------------------------
# Gemini REST call (isolated for monkeypatching)
# ---------------------------------------------------------------------------

def _post_generate_content(prompt: str, api_key: str) -> dict | None:
    """POST to Gemini generateContent endpoint. Returns parsed JSON or None."""
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/"
        f"models/{_GEMINI_MODEL}:generateContent?key={api_key}"
    )
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": {
                "type": "OBJECT",
                "properties": {
                    "signal": {"type": "STRING", "enum": ["BUY", "SELL", "HOLD"]},
                    "reason": {"type": "STRING"},
                    "confidence": {"type": "NUMBER", "format": "float"},
                },
                "required": ["signal", "reason", "confidence"],
            },
        },
    }
    try:
        resp = requests.post(url, json=body, timeout=30)
        if resp.status_code in (429, 403):
            logger.warning("Gemini API rate-limited / forbidden: %s", resp.status_code)
            return None
        resp.raise_for_status()
        data = resp.json()
        text = data["candidates"][0]["content"]["parts"][0]["text"]
        return json.loads(text)
    except (requests.Timeout, requests.ConnectionError, requests.RequestException):
        logger.warning("Gemini API network error", exc_info=True)
        return None
    except (KeyError, IndexError, json.JSONDecodeError):
        logger.warning("Gemini API unexpected response structure", exc_info=True)
        return None


# ---------------------------------------------------------------------------
# Response normalisation
# ---------------------------------------------------------------------------

_VALID_SIGNALS = {"BUY", "SELL", "HOLD"}


def _normalise(raw: dict | None, source: str = "gemini", cached: bool = False) -> dict:
    """Normalise a raw Gemini response into a canonical dict."""
    if raw is None:
        return _degraded("LLM degraded — no response", cached=cached)
    signal = str(raw.get("signal", "HOLD")).upper()
    if signal not in _VALID_SIGNALS:
        signal = "HOLD"
    try:
        confidence = float(raw.get("confidence", 0.5))
    except (TypeError, ValueError):
        confidence = 0.5
    confidence = max(0.0, min(1.0, confidence))
    reason = str(raw.get("reason", "No rationale provided."))
    return {
        "signal": signal,
        "reason": reason,
        "confidence": round(confidence, 3),
        "source": source,
        "cached": cached,
    }


def _degraded(reason: str = "LLM degraded", cached: bool = False) -> dict:
    return {
        "signal": "HOLD",
        "reason": reason,
        "confidence": 0.5,
        "source": "degraded",
        "cached": cached,
    }


# ---------------------------------------------------------------------------
# Public API: get_ai_second_opinion
# ---------------------------------------------------------------------------

def get_ai_second_opinion(symbol: str, technical_summary: str) -> dict:
    """Fetch a second opinion from Gemini LLM.

    Returns a dict with keys: signal, reason, confidence, source, cached.
    Never raises — any error returns a degraded HOLD signal.
    """
    today_str = datetime.now().strftime("%Y-%m-%d")
    cache_key = f"{symbol}:{today_str}"

    # Check cache (return a copy so caller can't mutate the cache entry)
    cached = _cache_get(cache_key)
    if cached is not None:
        return {**cached, "cached": True}

    # Check API key
    api_key = _GEMINI_API_KEY
    if not api_key:
        return _degraded("LLM degraded — GEMINI_API_KEY not configured")

    prompt = (
        "You are an expert NSE (India) equities analyst. "
        "Evaluate the following technical and fundamental data for a stock "
        "and provide a trading signal.\n\n"
        f"{technical_summary}\n\n"
        "Respond with ONLY a JSON object: "
        '{"signal": "BUY"|"SELL"|"HOLD", "reason": "<brief rationale citing the figures>", '
        '"confidence": <float 0.0 to 1.0>}. '
        "Keep the reason concise (1-3 sentences). "
        "Do NOT include any text outside the JSON object."
    )

    raw = _post_generate_content(prompt, api_key)
    result = _normalise(raw, source="gemini", cached=False)

    # Cache the result (throwaway — errors silently ignored)
    _cache_put(cache_key, {**result, "cached": False})
    result["cached"] = False
    return result


# ---------------------------------------------------------------------------
# build_technical_summary — local data only
# ---------------------------------------------------------------------------

def build_technical_summary(symbol: str) -> str:
    """Build a compact text summary from local SQLite data.

    Never raises — returns a minimal string on any failure.
    """
    try:
        return _build_summary_inner(symbol)
    except Exception:
        logger.debug("build_technical_summary failed for %s", symbol, exc_info=True)
        return f"No local data available for {symbol}."


def _build_summary_inner(symbol: str) -> str:
    lines = [f"Ticker: {symbol}"]

    # --- technical_data (myra_technical.db) ---
    try:
        tech_db = os.path.join(DB_DIR, "myra_technical.db")
        conn = sqlite3.connect(tech_db)
        row = conn.execute(
            """
            SELECT close, delivery_pct, sma_50, high_52w, low_52w,
                   trend_alignment, relative_volume_score
            FROM technical_data
            WHERE symbol = ?
            ORDER BY date DESC
            LIMIT 1
            """,
            (symbol,),
        ).fetchone()
        conn.close()
        if row:
            close, del_pct, sma50, h52, l52, trend, rel_vol = row
            lines.append(f"Latest close: {close}")
            lines.append(f"Delivery %: {del_pct}")
            lines.append(f"SMA 50: {sma50}")
            lines.append(f"52w high: {h52}, 52w low: {l52}")
            lines.append(f"Trend alignment: {trend}")
            lines.append(f"Relative volume score: {rel_vol}")
    except Exception:
        logger.debug("Failed to read technical_data for %s", symbol, exc_info=True)

    # --- fundamentals (myra_valuation.db) ---
    try:
        val_db = os.path.join(DB_DIR, "myra_valuation.db")
        conn = sqlite3.connect(val_db)
        row = conn.execute(
            """
            SELECT COALESCE(pe, peRatio),
                   COALESCE(roe, returnOnEquity),
                   sector,
                   COALESCE(promoter_holding_pct, 0)
            FROM fundamentals
            WHERE symbol = ?
            ORDER BY date DESC
            LIMIT 1
            """,
            (symbol,),
        ).fetchone()
        conn.close()
        if row:
            pe, roe, sector, promoter = row
            lines.append(f"PE: {pe}, ROE: {roe}")
            lines.append(f"Sector: {sector}")
            lines.append(f"Promoter holding: {promoter}%")
    except Exception:
        logger.debug("Failed to read fundamentals for %s", symbol, exc_info=True)

    # --- market mood ---
    try:
        from myra_app.strategies.base_strategy import MarketMoodHelper

        mood_helper = MarketMoodHelper()
        mood = mood_helper.get_market_mood(lib=None)
        lines.append(f"Market mood: {mood}")
    except Exception:
        lines.append("Market mood: NEUTRAL")

    return "\n".join(lines)
