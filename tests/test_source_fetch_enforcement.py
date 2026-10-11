"""DataFetcher.fetch_fundamentals honours the operator's source switches.

When a stream is disabled in the Data Sources config it must be skipped even
though the fetch method behind it would succeed. The enabled/disabled state
itself is tested in tests/test_source_config.py; here we pin the skip wiring.
"""

import pytest

from myra_app.data_sources import source_config
from myra_app.fetcher import DataFetcher


class _FakeResponse:
    def __init__(self, text, status_code=200):
        self.text = text
        self.status_code = status_code


class _FakeSession:
    def get(self, url, timeout=None):  # noqa: ARG002 - signature mirror
        return _FakeResponse("<html></html>")


def _fetcher():
    f = DataFetcher.__new__(DataFetcher)
    f.session = _FakeSession()
    f.last_http_status = None
    f.registry = {
        "data_streams": {
            "fundamentals": [
                {"name": "screener_in", "tier": "primary"},
                {"name": "google_finance", "tier": "fallback_1"},
                {"name": "finology", "tier": "fallback_2"},
            ]
        }
    }
    return f


@pytest.fixture(autouse=True)
def _reset_config_cache():
    source_config.invalidate_cache()
    yield
    source_config.invalidate_cache()


def test_screener_serves_when_all_enabled(monkeypatch):
    monkeypatch.setattr(source_config, "is_enabled", lambda _name: True)
    f = _fetcher()
    f._fetch_screener = lambda symbol: [{"report_date": "Mar 2026", "revenue": 1.0}]

    def _google(symbol):
        raise AssertionError("google must not be tried when screener succeeds")

    f._fetch_google = _google
    data, source = f.fetch_fundamentals("RELIANCE")
    assert source == "screener_in"
    assert data[0]["revenue"] == 1.0


def test_disabled_screener_falls_through_to_google(monkeypatch):
    def _screener(symbol):
        raise AssertionError("disabled screener_in must not be fetched")

    monkeypatch.setattr(source_config, "is_enabled", lambda name: name != "screener_in")
    f = _fetcher()
    f._fetch_screener = _screener
    f._fetch_google = lambda symbol: [
        {"report_date": "Sep 2025", "revenue": 5.0, "market_cap": 1e9}
    ]
    data, source = f.fetch_fundamentals("RELIANCE")
    assert source == "google_finance"
    assert data[0]["market_cap"] == 1e9


def test_all_disabled_returns_none(monkeypatch):
    def _screener(symbol):
        raise AssertionError("disabled screener_in must not be fetched")

    def _google(symbol):
        raise AssertionError("disabled google_finance must not be fetched")

    monkeypatch.setattr(source_config, "is_enabled", lambda _name: False)
    f = _fetcher()
    f._fetch_screener = _screener
    f._fetch_google = _google
    assert f.fetch_fundamentals("RELIANCE") == (None, None)
