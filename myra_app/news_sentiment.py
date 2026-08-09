"""News sentiment via GDELT + FinBERT with 6-hour SQLite caching."""
import sqlite3, os, requests, time
from datetime import datetime, timedelta
from transformers import AutoTokenizer, AutoModelForSequenceClassification
import torch

from myra_app.constants import DB_DIR

MODEL_NAME = "ProsusAI/finbert"
_tokenizer = None
_model = None

def _load_model():
    """Load FinBERT once. HF cache persists across restarts — downloads only on first install."""
    global _tokenizer, _model
    if _tokenizer is None:
        _tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
        _model = AutoModelForSequenceClassification.from_pretrained(MODEL_NAME)

def fetch_gdelt_news(query: str, max_results: int = 20) -> list[dict]:
    """Fetch news articles from GDELT Document API (free, no key required)."""
    url = "https://api.gdeltproject.org/api/v2/doc/doc"
    params = {
        "query": f"{query} (stock OR shares OR market)",
        "mode": "artlist",
        "format": "json",
        "maxrecords": max_results,
        "timespan": "7d",
        "sort": "datedesc",
    }
    try:
        resp = requests.get(url, params=params, timeout=15)
        if resp.status_code != 200:
            return []
        data = resp.json()
        articles = []
        for art in data.get("articles", []):
            articles.append({
                "headline": art.get("title", ""),
                "source": art.get("source", ""),
                "date": art.get("date", ""),
            })
        return articles
    except Exception:
        return []

def _classify_sentiment(text: str) -> tuple[str, float]:
    """Run FinBERT on a single headline. Returns (label, confidence)."""
    if not text or len(text.strip()) < 5:
        return "neutral", 0.0
    _load_model()
    inputs = _tokenizer(text, return_tensors="pt", truncation=True, max_length=512)
    with torch.no_grad():
        outputs = _model(**inputs)
        probs = torch.nn.functional.softmax(outputs.logits, dim=-1)
    labels = ["positive", "negative", "neutral"]
    idx = torch.argmax(probs).item()
    return labels[idx], probs[0][idx].item()

def _get_db_path() -> str:
    return os.path.join(DB_DIR, "myra_news.db")

def _init_db():
    """Create the news_sentiment table if it doesn't exist."""
    conn = sqlite3.connect(_get_db_path())
    conn.execute("""
        CREATE TABLE IF NOT EXISTS news_sentiment (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            headline TEXT NOT NULL,
            source TEXT,
            published_at TEXT,
            sentiment_label TEXT,
            sentiment_score REAL,
            created_at TEXT DEFAULT (datetime('now', 'localtime')),
            UNIQUE(ticker, headline, published_at)
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_ticker_date
        ON news_sentiment(ticker, published_at DESC)
    """)
    conn.commit()
    conn.close()

def _get_cached_news(ticker: str, max_age_hours: int = 6) -> list[dict] | None:
    """Return cached news if fresh enough, otherwise None."""
    _init_db()
    conn = sqlite3.connect(_get_db_path())
    row = conn.execute(
        "SELECT COUNT(*) FROM news_sentiment WHERE ticker=? AND created_at > datetime('now', 'localtime', ?)",
        (ticker, f'-{max_age_hours} hours')
    ).fetchone()
    if row and row[0] > 0:
        rows = conn.execute(
            "SELECT headline, source, published_at, sentiment_label, sentiment_score "
            "FROM news_sentiment WHERE ticker=? "
            "ORDER BY published_at DESC LIMIT 30",
            (ticker,)
        ).fetchall()
        conn.close()
        return [{"headline": r[0], "source": r[1], "date": r[2], "sentiment": r[3], "confidence": r[4]} for r in rows]
    conn.close()
    return None

def _store_news(ticker: str, articles: list[dict]):
    """Store classified articles in the cache."""
    _init_db()
    conn = sqlite3.connect(_get_db_path())
    for art in articles:
        conn.execute(
            "INSERT OR IGNORE INTO news_sentiment (ticker, headline, source, published_at, sentiment_label, sentiment_score) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (ticker, art["headline"], art.get("source", "GDELT"), art.get("date", ""),
             art["sentiment"], art["confidence"])
        )
    conn.commit()
    conn.close()

def get_ticker_news(ticker: str, refresh: bool = False) -> list[dict]:
    """Get news for a ticker. Uses cache unless refresh=True or cache is stale."""
    ticker = ticker.upper().strip()

    # Try cache first
    if not refresh:
        cached = _get_cached_news(ticker)
        if cached:
            return cached

    # Fetch fresh
    articles_raw = fetch_gdelt_news(ticker)
    # If GDELT returns nothing, try ticker + "Ltd" or ticker + "Limited".
    # GDELT enforces ~1 request/5s, so wait before the fallback call.
    if not articles_raw:
        time.sleep(5.5)
        articles_raw = fetch_gdelt_news(f"{ticker} Ltd")

    if not articles_raw:
        return []

    # Classify sentiment
    results = []
    for art in articles_raw:
        label, score = _classify_sentiment(art["headline"])
        results.append({
            "headline": art["headline"],
            "source": art.get("source", "GDELT"),
            "date": art.get("date", ""),
            "sentiment": label,
            "confidence": round(score, 4),
        })

    # Store in cache
    _store_news(ticker, results)

    return results
