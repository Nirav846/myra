"""News sentiment via GDELT + Groww fallback + FinBERT with 6-hour SQLite caching."""
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
            tone = art.get("tone", None)
            try:
                tone = float(tone) if tone is not None else None
            except (TypeError, ValueError):
                tone = None
            articles.append(
                {
                    "headline": art.get("title", ""),
                    "url": art.get("url", ""),
                    "source": art.get("source", art.get("domain", "")),
                    "date": art.get("date", art.get("seendate", "")),
                    "language": art.get("language", ""),
                    "tone": tone,  # GDELT's built-in sentiment score (-100 to +100)
                    "domain": art.get("domain", ""),
                }
            )
        return articles
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Groww news feed fetcher (keyless, public endpoint — GDELT failover)
# ---------------------------------------------------------------------------

_GROWW_FEED_URL = "https://groww.in/v2/api/feed/public"
_GROWW_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def _extract_source_from_body(body: str) -> str:
    """Pull the 'Source: X' tag from article body text. Falls back to 'GROWW'."""
    if not body:
        return "GROWW"
    for line in body.split("\n"):
        stripped = line.strip()
        if stripped.lower().startswith("source:"):
            src = stripped.split(":", 1)[1].strip()
            if src:
                return src
    return "GROWW"


def _extract_slug_from_url(url: str) -> str:
    """Best-effort extraction of a stock slug from a Groww ctaUrl."""
    if not url:
        return ""
    # Typical: https://groww.in/stocks/reliance-industries or .../stocks/RELIANCE
    parts = url.rstrip("/").split("/")
    try:
        idx = parts.index("stocks")
        if idx + 1 < len(parts):
            return parts[idx + 1]
    except ValueError:
        pass
    return ""


def fetch_groww_news(ticker: str, max_pages: int = 3) -> list[dict]:
    """Paginate the Groww public news feed and return articles matching *ticker*.

    Returns a list of normalised article dicts:
        headline, company_name, source, date, url, domain, language, tone, nse_script
    Returns [] on any error or when no matching items are found.
    """
    ticker_upper = ticker.upper().strip()
    matched: list[dict] = []
    headers = {"User-Agent": _GROWW_UA}

    for page in range(1, max_pages + 1):
        try:
            resp = requests.get(
                _GROWW_FEED_URL,
                params={
                    "page": page,
                    "publisherId": "stocknewssummary",
                    "size": 50,
                },
                headers=headers,
                timeout=15,
            )
            if resp.status_code != 200:
                break
            body = resp.json()
            feed_items = body.get("feed", [])
            if not feed_items:
                break  # no more pages

            for item in feed_items:
                try:
                    data = item.get("data", {})
                    if not data:
                        continue

                    # --- ticker matching ---
                    nse_code = ""
                    bse_code = ""
                    cta_list = data.get("cta", [])
                    if cta_list and isinstance(cta_list, list):
                        first_cta = cta_list[0]
                        meta = first_cta.get("meta", {}) if isinstance(first_cta, dict) else {}
                        nse_code = (meta.get("nseScriptCode") or "").strip().upper()
                        bse_code = (meta.get("bseScriptCode") or "").strip().upper()

                    # Match if NSE code or BSE code equals the ticker
                    if nse_code != ticker_upper and bse_code != ticker_upper:
                        continue

                    # --- extract fields ---
                    headline = (data.get("title") or "").strip()
                    if not headline:
                        continue

                    body_text = data.get("body") or ""
                    source = _extract_source_from_body(body_text)

                    # publishedAt: "2025-12-11T10:57:26" → date part
                    published_at = data.get("publishedAt") or ""
                    date_part = published_at[:10] if len(published_at) >= 10 else published_at

                    # Company name from CTA display text
                    company_name = ""
                    if cta_list and isinstance(cta_list, list) and cta_list:
                        first_cta = cta_list[0]
                        if isinstance(first_cta, dict):
                            company_name = (first_cta.get("ctaText") or "").strip()

                    # URL from ctaUrl
                    cta_url = ""
                    if cta_list and isinstance(cta_list, list) and cta_list:
                        first_cta = cta_list[0]
                        if isinstance(first_cta, dict):
                            cta_url = (first_cta.get("ctaUrl") or "").strip()
                    if not cta_url:
                        slug = _extract_slug_from_url(cta_url)
                        if slug:
                            cta_url = f"https://groww.in/stocks/{slug}"

                    matched.append(
                        {
                            "headline": headline,
                            "company_name": company_name,
                            "source": source,
                            "date": date_part,
                            "url": cta_url,
                            "domain": "groww.in",
                            "language": "English",
                            "tone": None,
                            "nse_script": nse_code or ticker_upper,
                        }
                    )
                except Exception:
                    continue  # skip malformed items

            # Polite delay between pages
            if page < max_pages:
                time.sleep(0.6)

        except Exception:
            break  # network / timeout → stop paging

    return matched


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


def _get_company_name(ticker: str) -> str | None:
    """Try to find a company name for the ticker from the fundamentals table.
    Returns None if no meaningful name is found."""
    try:
        import sqlite3, os
        from myra_app.constants import DB_DIR

        conn = sqlite3.connect(os.path.join(DB_DIR, "myra_valuation.db"))
        # Try to find a sector or any identifying info
        row = conn.execute(
            "SELECT sector, industry FROM fundamentals WHERE symbol=?", (ticker,)
        ).fetchone()
        conn.close()
        if row and row[0]:
            return (
                f"{ticker}"  # Use ticker itself — GDELT handles NSE tickers reasonably
            )
        return None
    except Exception:
        return None


def _get_db_path() -> str:
    return os.path.join(DB_DIR, "myra_news.db")


def _init_db():
    """Create the news_sentiment table if it doesn't exist."""
    conn = sqlite3.connect(_get_db_path())
    conn.execute(
        """
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
    """
    )
    # Add new metadata columns if they don't exist (idempotent migration)
    for col, col_type in [
        ("url", "TEXT"),
        ("language", "TEXT"),
        ("tone", "REAL"),
        ("domain", "TEXT"),
        ("company_name", "TEXT"),
    ]:
        try:
            conn.execute(f"ALTER TABLE news_sentiment ADD COLUMN {col} {col_type}")
        except sqlite3.OperationalError:
            pass  # column already exists
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_ticker_date
        ON news_sentiment(ticker, published_at DESC)
    """
    )
    conn.commit()
    conn.close()


def _get_cached_news(ticker: str, max_age_hours: int = 6) -> list[dict] | None:
    """Return cached news if fresh enough, otherwise None."""
    _init_db()
    conn = sqlite3.connect(_get_db_path())
    row = conn.execute(
        "SELECT COUNT(*) FROM news_sentiment WHERE ticker=? AND created_at > datetime('now', 'localtime', ?)",
        (ticker, f"-{max_age_hours} hours"),
    ).fetchone()
    if row and row[0] > 0:
        rows = conn.execute(
            "SELECT headline, source, published_at, sentiment_label, sentiment_score, "
            "url, language, tone, domain, company_name "
            "FROM news_sentiment WHERE ticker=? "
            "ORDER BY published_at DESC LIMIT 30",
            (ticker,),
        ).fetchall()
        conn.close()
        return [
            {
                "headline": r[0],
                "source": r[1],
                "date": r[2],
                "sentiment": r[3],
                "confidence": r[4],
                "url": r[5] or "",
                "language": r[6] or "",
                "tone": r[7],
                "domain": r[8] or "",
                "company_name": r[9] or "",
            }
            for r in rows
        ]
    conn.close()
    return None


def _store_news(ticker: str, articles: list[dict]):
    """Store classified articles in the cache."""
    _init_db()
    conn = sqlite3.connect(_get_db_path())
    for art in articles:
        conn.execute(
            "INSERT OR IGNORE INTO news_sentiment "
            "(ticker, headline, source, published_at, sentiment_label, sentiment_score, "
            "url, language, tone, domain, company_name) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                ticker,
                art["headline"],
                art.get("source", "GDELT"),
                art.get("date", ""),
                art["sentiment"],
                art["confidence"],
                art.get("url", ""),
                art.get("language", ""),
                art.get("tone"),
                art.get("domain", ""),
                art.get("company_name", ""),
            ),
        )
    conn.commit()
    conn.close()


def get_ticker_news(ticker: str, refresh: bool = False) -> list[dict]:
    """Get news for a ticker. Uses cache unless refresh=True or cache is stale.
    Uses quoted company name to avoid false positives for common-word tickers.
    Falls back to Groww public feed when GDELT returns few/no articles."""
    ticker = ticker.upper().strip()

    # Try cache first
    if not refresh:
        cached = _get_cached_news(ticker)
        if cached:
            return cached

    # First attempt: search with quoted ticker (works for most NSE symbols)
    articles_raw = fetch_gdelt_news(f'"{ticker}"')

    # If ticker is a common word (like RELIANCE, AMBER, FOCUS), GDELT may return
    # irrelevant global news. Try with company context.
    if not articles_raw or len(articles_raw) < 3:
        # GDELT enforces ~1 request/5s, so wait before fallback calls
        time.sleep(5.5)
        # Try ticker + "Ltd" or ticker + "Limited"
        articles_raw = fetch_gdelt_news(f'"{ticker} Ltd"')
        if not articles_raw:
            time.sleep(5.5)
            articles_raw = fetch_gdelt_news(f'"{ticker} Limited"')

    # If STILL nothing, try sector-qualified search using the DB
    if not articles_raw:
        time.sleep(5.5)
        company = _get_company_name(ticker)
        if company:
            articles_raw = fetch_gdelt_news(f'"{company}" (stock OR shares OR NSE)')

    # ---------------------------------------------------------------
    # Groww fallback: if GDELT yielded fewer than 3 articles, try Groww
    # ---------------------------------------------------------------
    groww_articles: list[dict] = []
    if len(articles_raw) < 3:
        groww_articles = fetch_groww_news(ticker)

    if not articles_raw and not groww_articles:
        return []

    # Combine GDELT + Groww, dedupe by headline (prefer GDELT on collision)
    seen_headlines: set[str] = set()
    combined: list[dict] = []

    for art in articles_raw:
        headline = art.get("headline", "")
        if headline and headline not in seen_headlines:
            seen_headlines.add(headline)
            combined.append(art)

    for art in groww_articles:
        headline = art.get("headline", "")
        if headline and headline not in seen_headlines:
            seen_headlines.add(headline)
            combined.append(art)

    # Classify sentiment and build final result dicts
    results = []
    for art in combined:
        label, score = _classify_sentiment(art["headline"])
        results.append(
            {
                "headline": art["headline"],
                "url": art.get("url", ""),
                "source": art.get("source", ""),
                "date": art.get("date", ""),
                "language": art.get("language", ""),
                "tone": art.get("tone", None),
                "domain": art.get("domain", ""),
                "sentiment": label,
                "confidence": round(score, 4),
                "company_name": art.get("company_name", ""),
            }
        )

    # Store in cache
    _store_news(ticker, results)

    return results
