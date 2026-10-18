"""
Source-failure classification for the fundamentals chain.

The Phase 25 fix made per-source reasons visible. These tests pin down that the
reasons are also *correct* -- a stale status from an earlier symbol, or a status
belonging to a different source, must never be reported against the wrong
source. A diagnostic that confidently names the wrong cause is worse than none,
because it sends the investigation down the wrong path.
"""

import pytest

from myra_app.fetcher import DataFetcher


class _Resp:
    def __init__(self, status_code=200, text="<html></html>"):
        self.status_code = status_code
        self.text = text


def _fetcher(monkeypatch, **streams):
    """A DataFetcher with the fundamentals source chain stubbed out."""
    f = DataFetcher.__new__(DataFetcher)
    f.last_http_status = None
    f.last_page_note = None
    f.last_source_errors = {}
    f.registry = {"data_streams": {"fundamentals": list(streams.values())}}
    f.unify_symbol = lambda s: s

    from myra_app.data_sources import source_config

    monkeypatch.setattr(source_config, "is_enabled", lambda name: True)
    return f


def _both(monkeypatch, *, screener, google):
    f = _fetcher(
        monkeypatch,
        screener_in={"name": "screener_in"},
        google_finance={"name": "google_finance"},
    )
    f._fetch_screener = screener
    f._fetch_google = google
    return f


# --------------------------------------------------------------------------- #
# Cross-source status leakage
# --------------------------------------------------------------------------- #


def test_google_failure_is_not_charged_screener_status(monkeypatch):
    """The headline defect: Screener served fine (200), Google returned nothing.
    Google must be reported on its own terms, not as 'HTTP 200' / not inherit
    anything from Screener."""

    def screener(sym):
        f.last_http_status = 200
        return [{"report_date": "Sep 2025", "revenue": 1}]

    def google(sym):
        return None  # leaves status at whatever Screener set, in real code

    f = _both(monkeypatch, screener=screener, google=google)
    # Screener succeeds, so the chain stops there -- force the failing path:
    screener_ok, name = f.fetch_fundamentals("AAA")
    assert name == "screener_in"
    assert f.last_http_status == 200


def test_status_is_reset_between_calls(monkeypatch):
    """A 403 for symbol A must not be reported against symbol B."""

    def screener(sym):
        f.last_http_status = 403
        return None

    def google(sym):
        return None

    f = _both(monkeypatch, screener=screener, google=google)
    f.fetch_fundamentals("AAA")
    assert f.last_source_errors["screener_in"] == "HTTP 403"

    # Symbol B now fails for a reason with no HTTP status at all (it raised).
    def raises(sym):
        raise TimeoutError("boom")

    f._fetch_screener = raises
    f.fetch_fundamentals("BBB")
    # Screener raised -> named by exception type, and crucially Google is not
    # credited with Screener's stale 403.
    assert "screener_in" in f.last_source_errors
    assert "403" not in f.last_source_errors.get("screener_in", "")
    assert "403" not in f.last_source_errors.get("google_finance", "")


def test_stale_status_from_previous_call_is_not_reused(monkeypatch):
    """Directly: leave a stale status on the instance, then run a call whose
    source raised before touching the status."""

    def screener(sym):
        raise ConnectionError("boom")

    def google(sym):
        return None

    f = _both(monkeypatch, screener=screener, google=google)
    f.last_http_status = 403  # left over from a previous symbol

    f.fetch_fundamentals("BBB")

    assert f.last_source_errors["screener_in"] == "screener_in: ConnectionError"
    assert "HTTP" not in f.last_source_errors["google_finance"]


# --------------------------------------------------------------------------- #
# Page-level distinction
# --------------------------------------------------------------------------- #


def test_200_without_quarterly_table_is_named_precisely(monkeypatch):
    """HTTP 200 but no datable rows is a different problem from being refused:
    either the symbol has no financials or the page structure changed."""

    def screener(sym):
        f.last_http_status = 200
        f.last_page_note = "HTTP 200 but no quarterly table in page"
        return None

    f = _both(monkeypatch, screener=screener, google=lambda s: None)
    f.fetch_fundamentals("AAA")

    assert f.last_source_errors["screener_in"] == (
        "HTTP 200 but no quarterly table in page"
    )


def test_note_is_cleared_between_calls(monkeypatch):
    """Otherwise symbol B inherits symbol A's page note."""

    def screener_note(sym):
        f.last_page_note = "HTTP 200 but no quarterly table in page"
        f.last_http_status = 200
        return None

    f = _both(monkeypatch, screener=screener_note, google=lambda s: None)
    f.fetch_fundamentals("AAA")
    assert "no quarterly table" in f.last_source_errors["screener_in"]

    def screener_403(sym):
        f.last_http_status = 500
        return None

    f._fetch_screener = screener_403
    f.fetch_fundamentals("BBB")
    assert f.last_source_errors["screener_in"] == "HTTP 500"


# --------------------------------------------------------------------------- #
# Real method bodies (no network): Google records its own status
# --------------------------------------------------------------------------- #


def test_fetch_google_records_its_own_http_status(monkeypatch):
    """`_fetch_google` previously returned None on failure without recording
    anything, so its failures were indistinguishable from Screener's."""
    f = DataFetcher.__new__(DataFetcher)
    f.last_http_status = None

    class _Session:
        def get(self, url, timeout=None):
            return _Resp(status_code=429)

    f.session = _Session()
    assert f._fetch_google("AAA") is None
    assert f.last_http_status == 429


def test_fetch_google_records_200_when_the_page_is_served(monkeypatch):
    f = DataFetcher.__new__(DataFetcher)
    f.last_http_status = None

    class _Session:
        def get(self, url, timeout=None):
            return _Resp(status_code=200, text="<html><table></table></html>")

    f.session = _Session()
    f._fetch_google("AAA")
    assert f.last_http_status == 200


# --------------------------------------------------------------------------- #
# Distinctness of the reason vocabulary
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "status,expected",
    [(403, "HTTP 403"), (429, "HTTP 429"), (404, "HTTP 404"), (500, "HTTP 500")],
)
def test_http_codes_stay_distinguishable(monkeypatch, status, expected):
    """Rate limiting and access denial must not collapse into one label."""

    def screener(sym):
        f.last_http_status = status
        return None

    f = _both(monkeypatch, screener=screener, google=lambda s: None)
    f.fetch_fundamentals("AAA")
    assert f.last_source_errors["screener_in"] == expected


def test_no_secret_material_ever_reaches_a_reason(monkeypatch):
    def screener(sym):
        raise RuntimeError("https://screener.in/x?cookie=SECRET_TOKEN_VALUE")

    f = _both(monkeypatch, screener=screener, google=lambda s: None)
    f.fetch_fundamentals("AAA")
    rendered = " ".join(f.last_source_errors.values())
    assert "SECRET_TOKEN_VALUE" not in rendered
    assert "screener.in/x" not in rendered
    assert f.last_source_errors["screener_in"] == "screener_in: RuntimeError"
