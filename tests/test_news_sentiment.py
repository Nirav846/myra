"""
Tests for news_sentiment module — Groww feed fetcher + GDELT failover + cache.
All network calls are mocked; no real HTTP or model inference in unit tests.
"""

import sqlite3
import tempfile
import os
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

import myra_app.news_sentiment as ns


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FAKE_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0.0.0"


def _groww_item(
    nse: str = "RELIANCE",
    bse: str = "500325",
    title: str = "Reliance hits new high",
    body: str = "Source: Moneycontrol\nReliance Industries surged.",
    published_at: str = "2025-12-11T10:57:26",
    company: str = "Reliance Industries",
    cta_url: str = "https://groww.in/stocks/reliance-industries",
    include_cta: bool = True,
    include_meta: bool = True,
) -> dict:
    """Build a single Groww feed item dict mimicking the real API shape."""
    meta = {}
    if include_meta:
        meta = {"nseScriptCode": nse, "bseScriptCode": bse}
    cta_entry = {}
    if include_cta:
        cta_entry = {"ctaText": company, "ctaUrl": cta_url, "meta": meta}
    return {"data": {"title": title, "body": body, "publishedAt": published_at, "cta": [cta_entry]}}


def _fake_response(status_code=200, json_data=None):
    """Create a fake requests.Response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    return resp


# ---------------------------------------------------------------------------
# fetch_groww_news — pagination + parsing
# ---------------------------------------------------------------------------


class TestFetchGrowwNews:
    """Unit tests for the Groww feed fetcher."""

    @patch("myra_app.news_sentiment.time.sleep")
    @patch("myra_app.news_sentiment.requests.get")
    def test_pagination_and_filtering(self, mock_get, mock_sleep):
        """Page 1 has matching + non-matching items; page 2 empty → only matching returned."""
        item_match = _groww_item(nse="RELIANCE", title="Reliance up 2%")
        item_no_match = _groww_item(nse="TCS", title="TCS quarterly results")
        page1 = _fake_response(200, {"feed": [item_match, item_no_match]})
        page2 = _fake_response(200, {"feed": []})

        mock_get.side_effect = [page1, page2]
        result = ns.fetch_groww_news("RELIANCE", max_pages=2)

        assert len(result) == 1
        assert result[0]["headline"] == "Reliance up 2%"
        assert result[0]["nse_script"] == "RELIANCE"
        assert result[0]["company_name"] == "Reliance Industries"
        assert result[0]["source"] == "Moneycontrol"
        assert result[0]["date"] == "2025-12-11"
        assert result[0]["domain"] == "groww.in"
        assert result[0]["language"] == "English"
        assert result[0]["tone"] is None
        # Sleep called once between pages
        mock_sleep.assert_called_once_with(0.6)

    @patch("myra_app.news_sentiment.time.sleep")
    @patch("myra_app.news_sentiment.requests.get")
    def test_empty_feed_returns_empty(self, mock_get, mock_sleep):
        """Empty feed on page 1 → returns []."""
        mock_get.return_value = _fake_response(200, {"feed": []})
        result = ns.fetch_groww_news("RELIANCE")
        assert result == []

    @patch("myra_app.news_sentiment.time.sleep")
    @patch("myra_app.news_sentiment.requests.get")
    def test_missing_cta_skips_item(self, mock_get, mock_sleep):
        """Item with no cta array → skipped without crash."""
        item_no_cta = {"data": {"title": "Some news", "body": "", "publishedAt": "2025-01-01T00:00:00", "cta": []}}
        mock_get.return_value = _fake_response(200, {"feed": [item_no_cta]})
        result = ns.fetch_groww_news("RELIANCE")
        assert result == []

    @patch("myra_app.news_sentiment.time.sleep")
    @patch("myra_app.news_sentiment.requests.get")
    def test_missing_meta_skips_item(self, mock_get, mock_sleep):
        """Item with cta but no meta → ticker won't match → item skipped."""
        item_no_meta = _groww_item(include_meta=False)
        mock_get.return_value = _fake_response(200, {"feed": [item_no_meta]})
        result = ns.fetch_groww_news("RELIANCE")
        assert result == []

    @patch("myra_app.news_sentiment.time.sleep")
    @patch("myra_app.news_sentiment.requests.get")
    def test_malformed_published_at_handled(self, mock_get, mock_sleep):
        """Short or empty publishedAt → date field is the raw value (no crash)."""
        item = _groww_item(published_at="???", title="Weird date article")
        # Page 1 has item, page 2 empty → only 1 copy returned
        mock_get.side_effect = [
            _fake_response(200, {"feed": [item]}),
            _fake_response(200, {"feed": []}),
        ]
        result = ns.fetch_groww_news("RELIANCE", max_pages=2)
        assert len(result) == 1
        assert result[0]["date"] == "???"

    @patch("myra_app.news_sentiment.time.sleep")
    @patch("myra_app.news_sentiment.requests.get")
    def test_http_error_returns_empty(self, mock_get, mock_sleep):
        """Non-200 response → returns []."""
        mock_get.return_value = _fake_response(429)
        result = ns.fetch_groww_news("RELIANCE")
        assert result == []

    @patch("myra_app.news_sentiment.time.sleep")
    @patch("myra_app.news_sentiment.requests.get")
    def test_network_exception_returns_empty(self, mock_get, mock_sleep):
        """Network timeout/exception → returns []."""
        mock_get.side_effect = Exception("connection refused")
        result = ns.fetch_groww_news("RELIANCE")
        assert result == []

    @patch("myra_app.news_sentiment.time.sleep")
    @patch("myra_app.news_sentiment.requests.get")
    def test_source_extraction_from_body(self, mock_get, mock_sleep):
        """'Source: Reuters' in body → source is 'Reuters'."""
        item = _groww_item(body="Some text.\nSource: Reuters\nMore text.")
        mock_get.return_value = _fake_response(200, {"feed": [item]})
        result = ns.fetch_groww_news("RELIANCE")
        assert result[0]["source"] == "Reuters"

    @patch("myra_app.news_sentiment.time.sleep")
    @patch("myra_app.news_sentiment.requests.get")
    def test_source_fallback_when_no_source_tag(self, mock_get, mock_sleep):
        """No 'Source:' tag in body → source defaults to 'GROWW'."""
        item = _groww_item(body="Just some news text, no source tag.")
        mock_get.return_value = _fake_response(200, {"feed": [item]})
        result = ns.fetch_groww_news("RELIANCE")
        assert result[0]["source"] == "GROWW"

    @patch("myra_app.news_sentiment.time.sleep")
    @patch("myra_app.news_sentiment.requests.get")
    def test_bse_code_match(self, mock_get, mock_sleep):
        """Ticker matches BSE code when NSE code doesn't."""
        item = _groww_item(nse="POWERGRID", bse="532898")
        # Page 1 has item, page 2 empty
        mock_get.side_effect = [
            _fake_response(200, {"feed": [item]}),
            _fake_response(200, {"feed": []}),
        ]
        result = ns.fetch_groww_news("532898", max_pages=2)
        assert len(result) == 1

    @patch("myra_app.news_sentiment.time.sleep")
    @patch("myra_app.news_sentiment.requests.get")
    def test_empty_title_skipped(self, mock_get, mock_sleep):
        """Item with empty title → skipped."""
        item = _groww_item(title="")
        mock_get.return_value = _fake_response(200, {"feed": [item]})
        result = ns.fetch_groww_news("RELIANCE")
        assert result == []

    @patch("myra_app.news_sentiment.time.sleep")
    @patch("myra_app.news_sentiment.requests.get")
    def test_malformed_data_skipped(self, mock_get, mock_sleep):
        """Item with no 'data' key → skipped without crash."""
        item = {"no_data_here": True}
        mock_get.return_value = _fake_response(200, {"feed": [item]})
        result = ns.fetch_groww_news("RELIANCE")
        assert result == []


# ---------------------------------------------------------------------------
# GDELT-empty → Groww fallback integration (get_ticker_news)
# ---------------------------------------------------------------------------


class TestGrowwFallback:
    """Integration tests for get_ticker_news with mocked GDELT + Groww."""

    @patch("myra_app.news_sentiment._classify_sentiment", return_value=("neutral", 0.5))
    @patch("myra_app.news_sentiment._store_news")
    @patch("myra_app.news_sentiment._get_cached_news", return_value=None)
    @patch("myra_app.news_sentiment.fetch_groww_news")
    @patch("myra_app.news_sentiment.fetch_gdelt_news", return_value=[])
    def test_gdelt_empty_groww_used(
        self, mock_gdelt, mock_groww, mock_cached, mock_store, mock_classify
    ):
        """GDELT returns [] → Groww articles should be returned."""
        mock_groww.return_value = [
            {
                "headline": "Groww: Reliance news",
                "company_name": "Reliance Industries",
                "source": "Moneycontrol",
                "date": "2025-12-11",
                "url": "https://groww.in/stocks/reliance",
                "domain": "groww.in",
                "language": "English",
                "tone": None,
                "nse_script": "RELIANCE",
            }
        ]
        result = ns.get_ticker_news("RELIANCE", refresh=True)
        assert len(result) == 1
        assert result[0]["headline"] == "Groww: Reliance news"
        assert result[0]["company_name"] == "Reliance Industries"
        mock_groww.assert_called_once_with("RELIANCE")

    @patch("myra_app.news_sentiment._classify_sentiment", return_value=("positive", 0.8))
    @patch("myra_app.news_sentiment._store_news")
    @patch("myra_app.news_sentiment._get_cached_news", return_value=None)
    @patch("myra_app.news_sentiment.fetch_groww_news")
    @patch("myra_app.news_sentiment.fetch_gdelt_news", return_value=[])
    def test_both_empty_returns_empty(
        self, mock_gdelt, mock_groww, mock_cached, mock_store, mock_classify
    ):
        """Both GDELT and Groww return [] → final result is []."""
        mock_groww.return_value = []
        result = ns.get_ticker_news("ILLUSIVE", refresh=True)
        assert result == []

    @patch("myra_app.news_sentiment.time.sleep")
    @patch("myra_app.news_sentiment._classify_sentiment", return_value=("neutral", 0.5))
    @patch("myra_app.news_sentiment._store_news")
    @patch("myra_app.news_sentiment._get_cached_news", return_value=None)
    @patch("myra_app.news_sentiment.fetch_groww_news")
    @patch("myra_app.news_sentiment.fetch_gdelt_news")
    def test_gdelt_few_then_groww_supplements(
        self, mock_gdelt, mock_groww, mock_cached, mock_store, mock_classify, mock_sleep
    ):
        """GDELT returns 1 article, Groww returns 2 → combined = 3."""
        mock_gdelt.side_effect = [
            [{"headline": "GDELT art", "url": "", "source": "GDELT", "date": "2025-12-10", "language": "", "tone": None, "domain": ""}],
            [{"headline": "GDELT art", "url": "", "source": "GDELT", "date": "2025-12-10", "language": "", "tone": None, "domain": ""}],
            [{"headline": "GDELT art", "url": "", "source": "GDELT", "date": "2025-12-10", "language": "", "tone": None, "domain": ""}],
        ]
        mock_groww.return_value = [
            {"headline": "Groww art 1", "company_name": "Co", "source": "GROWW", "date": "2025-12-11", "url": "", "domain": "groww.in", "language": "English", "tone": None, "nse_script": "X"},
            {"headline": "Groww art 2", "company_name": "Co", "source": "GROWW", "date": "2025-12-11", "url": "", "domain": "groww.in", "language": "English", "tone": None, "nse_script": "X"},
        ]
        result = ns.get_ticker_news("X", refresh=True)
        headlines = [r["headline"] for r in result]
        assert "GDELT art" in headlines
        assert "Groww art 1" in headlines
        assert "Groww art 2" in headlines


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------


class TestDeduplication:
    """Verify headline-based dedup between GDELT and Groww."""

    @patch("myra_app.news_sentiment._classify_sentiment", return_value=("neutral", 0.5))
    @patch("myra_app.news_sentiment._store_news")
    @patch("myra_app.news_sentiment._get_cached_news", return_value=None)
    @patch("myra_app.news_sentiment.fetch_groww_news")
    @patch("myra_app.news_sentiment.fetch_gdelt_news", return_value=[])
    def test_same_headline_deduped(
        self, mock_gdelt, mock_groww, mock_cached, mock_store, mock_classify
    ):
        """Same headline in GDELT and Groww → only one copy in results."""
        # GDELT returns article X
        # We mock it so GDELT returns empty first, then Groww returns article X
        # Actually to test dedup we need both sources to return the same headline
        # Let's use a scenario where GDELT has 1 article and Groww has the same
        mock_gdelt.side_effect = [
            [{"headline": "Same headline", "url": "gdelt-url", "source": "GDELT", "date": "2025-12-10", "language": "", "tone": None, "domain": ""}],
            [{"headline": "Same headline", "url": "gdelt-url", "source": "GDELT", "date": "2025-12-10", "language": "", "tone": None, "domain": ""}],
            [{"headline": "Same headline", "url": "gdelt-url", "source": "GDELT", "date": "2025-12-10", "language": "", "tone": None, "domain": ""}],
        ]
        mock_groww.return_value = [
            {"headline": "Same headline", "company_name": "Co", "source": "GROWW", "date": "2025-12-11", "url": "groww-url", "domain": "groww.in", "language": "English", "tone": None, "nse_script": "X"},
        ]
        result = ns.get_ticker_news("X", refresh=True)
        matching = [r for r in result if r["headline"] == "Same headline"]
        assert len(matching) == 1
        # GDELT preferred on collision → source should be GDELT
        assert matching[0]["source"] == "GDELT"

    @patch("myra_app.news_sentiment._classify_sentiment", return_value=("neutral", 0.5))
    @patch("myra_app.news_sentiment._store_news")
    @patch("myra_app.news_sentiment._get_cached_news", return_value=None)
    @patch("myra_app.news_sentiment.fetch_groww_news")
    @patch("myra_app.news_sentiment.fetch_gdelt_news", return_value=[])
    def test_different_headlines_both_kept(
        self, mock_gdelt, mock_groww, mock_cached, mock_store, mock_classify
    ):
        """Different headlines → both appear in results."""
        mock_groww.return_value = [
            {"headline": "Headline A", "company_name": "", "source": "GROWW", "date": "2025-12-11", "url": "", "domain": "groww.in", "language": "English", "tone": None, "nse_script": "X"},
            {"headline": "Headline B", "company_name": "", "source": "GROWW", "date": "2025-12-11", "url": "", "domain": "groww.in", "language": "English", "tone": None, "nse_script": "X"},
        ]
        result = ns.get_ticker_news("X", refresh=True)
        headlines = [r["headline"] for r in result]
        assert "Headline A" in headlines
        assert "Headline B" in headlines


# ---------------------------------------------------------------------------
# Cache round-trip with company_name
# ---------------------------------------------------------------------------


class TestCacheRoundTrip:
    """Verify _store_news / _get_cached_news round-trips company_name."""

    def test_company_name_persisted(self, tmp_path, monkeypatch):
        """Store an article with company_name, retrieve it back."""
        db_path = str(tmp_path / "test_news.db")
        monkeypatch.setattr(ns, "_get_db_path", lambda: db_path)

        # Force fresh DB creation
        ns._init_db()

        ticker = "TESTCO"
        articles = [
            {
                "headline": "TestCo quarterly results",
                "source": "GROWW",
                "date": "2025-12-11",
                "sentiment": "positive",
                "confidence": 0.91,
                "url": "https://groww.in/stocks/testco",
                "language": "English",
                "tone": None,
                "domain": "groww.in",
                "company_name": "TestCo Ltd",
            }
        ]
        ns._store_news(ticker, articles)

        cached = ns._get_cached_news(ticker, max_age_hours=12)
        assert cached is not None
        assert len(cached) == 1
        assert cached[0]["company_name"] == "TestCo Ltd"
        assert cached[0]["headline"] == "TestCo quarterly results"

    def test_company_name_default_empty(self, tmp_path, monkeypatch):
        """Article without company_name → defaults to '' in cache."""
        db_path = str(tmp_path / "test_news2.db")
        monkeypatch.setattr(ns, "_get_db_path", lambda: db_path)
        ns._init_db()

        ticker = "NOCO"
        articles = [
            {
                "headline": "NoCo news",
                "source": "GDELT",
                "date": "2025-12-11",
                "sentiment": "neutral",
                "confidence": 0.5,
                "url": "",
                "language": "",
                "tone": None,
                "domain": "",
            }
        ]
        ns._store_news(ticker, articles)

        cached = ns._get_cached_news(ticker, max_age_hours=12)
        assert cached is not None
        assert cached[0]["company_name"] == ""

    def test_idempotent_alter_table(self, tmp_path, monkeypatch):
        """Calling _init_db twice does not raise."""
        db_path = str(tmp_path / "test_news3.db")
        monkeypatch.setattr(ns, "_get_db_path", lambda: db_path)
        ns._init_db()
        ns._init_db()  # should not raise

    def test_cache_hit_returns_results(self, tmp_path, monkeypatch):
        """Cache hit skips GDELT/Groww entirely."""
        db_path = str(tmp_path / "test_news4.db")
        monkeypatch.setattr(ns, "_get_db_path", lambda: db_path)
        ns._init_db()

        ticker = "CACHETEST"
        articles = [
            {
                "headline": "Cached headline",
                "source": "GROWW",
                "date": "2025-12-11",
                "sentiment": "positive",
                "confidence": 0.75,
                "url": "",
                "language": "",
                "tone": None,
                "domain": "",
                "company_name": "Cache Corp",
            }
        ]
        ns._store_news(ticker, articles)

        # get_ticker_news with refresh=False should return cache
        result = ns.get_ticker_news(ticker, refresh=False)
        assert len(result) == 1
        assert result[0]["headline"] == "Cached headline"
        assert result[0]["company_name"] == "Cache Corp"


# ---------------------------------------------------------------------------
# _extract_source_from_body edge cases
# ---------------------------------------------------------------------------


class TestExtractSource:
    """Unit tests for _extract_source_from_body helper."""

    def test_source_found(self):
        assert ns._extract_source_from_body("Body\nSource: Reuters\nMore") == "Reuters"

    def test_source_not_found(self):
        assert ns._extract_source_from_body("Just plain text") == "GROWW"

    def test_empty_body(self):
        assert ns._extract_source_from_body("") == "GROWW"

    def test_none_body(self):
        assert ns._extract_source_from_body(None) == "GROWW"

    def test_source_with_extra_colon(self):
        assert ns._extract_source_from_body("Source: Economic: Times") == "Economic: Times"
