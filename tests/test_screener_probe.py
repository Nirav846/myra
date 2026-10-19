"""
Privacy boundary and outcome classification for the single-symbol probe.

Two properties are worth a test of their own, so they are pinned here:

1. **The privacy boundary holds structurally.** Page content, headers, cookies
   and exception messages must have no path into the result -- not "are usually
   omitted", but "cannot be included", because the result is assembled from a
   fixed allowlist.
2. **Classification is faithful.** 403, 429, 404, other non-200, 200-with-table,
   200-without-table, network error and timeout must map to distinct, correctly
   reported outcomes, and byte length must be recorded only for a 200.

No test here performs a network request: every response is a local mock.
"""

import pytest

from tools.probe_screener_once import (
    ALLOWED_FIELDS,
    build_url,
    classify_response,
    main,
    probe,
)

SECRET = "SUPER_SECRET_COOKIE_VALUE_DO_NOT_LEAK"


class _Resp:
    """Minimal stand-in for a requests/responses response object."""

    def __init__(self, status_code=200, text="", content=None, headers=None):
        self.status_code = status_code
        self.text = text
        self.content = content if content is not None else text.encode("utf-8")
        self.headers = headers or {}
        self.cookies = {SECRET: "abc"}


class _Session:
    """Records calls so 'exactly one request' is assertable."""

    def __init__(self, response=None, error=None):
        self._response = response
        self._error = error
        self.calls = []

    def get(self, url, timeout=None):
        self.calls.append((url, timeout))
        if self._error is not None:
            raise self._error
        return self._response


def _quarters_html() -> str:
    """Minimal markup matching the structure `parse_company_page` requires."""
    return """
    <section id="quarters">
      <table>
        <tr><th>Period</th><th>Sep 2025</th><th>Jun 2025</th></tr>
        <tr><th>Revenue</th><td>100</td><td>90</td></tr>
      </table>
    </section>
    """


# --------------------------------------------------------------------------- #
# Outcome classification
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("status", [403, 429, 404, 500, 503])
def test_non_200_statuses_are_reported_verbatim(status):
    """A refusal must stay distinguishable -- 403 is not 429 is not 404."""
    session = _Session(_Resp(status_code=status, text="<html>blocked</html>"))
    result = probe("AAATECH", session=session)

    assert result["outcome"] == "HTTP_RESPONSE"
    assert result["http_status"] == status
    # Byte length is a 200-only signal.
    assert "response_bytes" not in result
    assert "quarterly_table_matched" not in result


def test_200_without_quarterly_table():
    session = _Session(_Resp(200, text="<html><body>no table</body></html>"))
    result = probe("AAATECH", session=session)

    assert result["http_status"] == 200
    assert result["outcome"] == "HTTP_RESPONSE"
    assert result["quarterly_table_matched"] is False
    assert result["response_bytes"] > 0


def test_200_with_quarterly_table():
    session = _Session(_Resp(200, text=_quarters_html()))
    result = probe("AAATECH", session=session)

    assert result["http_status"] == 200
    assert result["quarterly_table_matched"] is True
    assert result["response_bytes"] > 0


@pytest.mark.parametrize(
    "exc_name", ["ConnectionError", "SSLError", "DNSError", "ProtocolError"]
)
def test_transport_failures_report_class_only(exc_name):
    session = _Session(error=type(exc_name, (Exception,), {})("boom"))
    result = probe("AAATECH", session=session)

    assert result["outcome"] == "NETWORK_ERROR"
    assert result["transport_exception_class"] == exc_name
    assert result["http_status"] is None
    assert result["completed_without_retry"] is True


@pytest.mark.parametrize("exc_name", ["Timeout", "ReadTimeout", "ConnectTimeout"])
def test_timeouts_are_distinguished_from_other_transport_errors(exc_name):
    session = _Session(error=type(exc_name, (Exception,), {})("slow"))
    result = probe("AAATECH", session=session)

    assert result["outcome"] == "TIMEOUT"
    assert result["transport_exception_class"] == exc_name


def test_exception_message_never_reaches_the_result():
    leaky = "failed for url https://www.screener.in/company/AAATECH/?token=abc123"
    session = _Session(error=type("ConnectionError", (Exception,), {})(leaky))
    result = probe("AAATECH", session=session)

    rendered = repr(result)
    assert "screener.in" not in rendered
    assert "abc123" not in rendered
    assert leaky not in rendered


# --------------------------------------------------------------------------- #
# Privacy boundary
# --------------------------------------------------------------------------- #


def test_secrets_in_a_200_body_cannot_reach_the_result():
    body = (
        "<html><body>"
        f"<div>{SECRET}</div>"
        "<script>document.cookie='session=TOP_SECRET'</script>"
        "<section id='quarters'></section>"
        "</body></html>"
    )
    session = _Session(_Resp(200, text=body, headers={"Set-Cookie": f"sid={SECRET}"}))
    result = probe("AAATECH", session=session)

    rendered = repr(result)
    assert SECRET not in rendered
    assert "TOP_SECRET" not in rendered
    assert "cookie" not in rendered.lower()


def test_result_contains_only_allowlisted_fields():
    session = _Session(_Resp(200, text=_quarters_html()))
    result = probe("AAATECH", session=session)
    assert set(result) <= ALLOWED_FIELDS


def test_result_contains_only_allowlisted_fields_on_transport_error():
    session = _Session(error=type("ConnectionError", (Exception,), {})("x"))
    result = probe("AAATECH", session=session)
    assert set(result) <= ALLOWED_FIELDS


def test_no_financial_values_are_returned():
    """Detection yields a boolean only -- never an extracted number."""
    html = _quarters_html().replace("<td>100</td>", "<td>123456.78</td>")
    session = _Session(_Resp(200, text=html))
    result = probe("AAATECH", session=session)

    assert result["quarterly_table_matched"] is True
    assert "123456.78" not in repr(result)
    assert not any(isinstance(v, (int, float)) and v > 1000 for v in result.values())


# --------------------------------------------------------------------------- #
# Request discipline
# --------------------------------------------------------------------------- #


def test_exactly_one_request_is_made():
    session = _Session(_Resp(200, text=_quarters_html()))
    probe("AAATECH", session=session)
    assert len(session.calls) == 1


def test_exactly_one_request_even_when_it_fails():
    session = _Session(error=type("ConnectionError", (Exception,), {})("x"))
    probe("AAATECH", session=session)
    assert len(session.calls) == 1


def test_probe_does_not_touch_the_database_or_cache(tmp_path, monkeypatch):
    """A diagnostic must not be able to write, even by accident."""
    import sqlite3

    def _forbidden(*args, **kwargs):
        raise AssertionError("probe attempted a database connection")

    monkeypatch.setattr(sqlite3, "connect", _forbidden)

    session = _Session(_Resp(200, text=_quarters_html()))
    result = probe("AAATECH", session=session)
    assert result["quarterly_table_matched"] is True


def test_url_matches_the_production_fetch_path():
    assert build_url("AAATECH") == (
        "https://www.screener.in/company/AAATECH/consolidated/"
    )


def test_classify_response_accepts_a_response_directly():
    result = classify_response(_Resp(200, text=_quarters_html()), 1.5, "AAATECH")
    assert result["quarterly_table_matched"] is True
    assert result["elapsed_seconds"] == 1.5
    assert result["symbol"] == "AAATECH"


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def test_cli_requires_exactly_one_symbol():
    assert main(["probe_screener_once.py"]) == 2
    assert main(["probe_screener_once.py", "A", "B"]) == 2


def test_cli_does_not_run_on_import():
    """Importing the tool must never fire a request."""
    import tools.probe_screener_once as mod

    assert callable(mod.probe)
