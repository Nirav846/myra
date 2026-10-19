"""Single-symbol, single-request Screener.in diagnostic (Phase 27).

Why this exists
---------------
A fundamentals refresh reported many symbols as
``multi-source fallback exhausted`` without saying *which* source failed or
*how*. This tool answers that for exactly one symbol with exactly one HTTP
request, and reports only what the request actually proved.

What it deliberately is not
---------------------------
* Not a fetcher. It never runs the source-fallback chain, so it cannot reach
  Google Finance and cannot mask a Screener.in failure.
* Not a writer. It never opens a database for writing and never populates the
  network cache, so it cannot change stored fundamentals, reporting dates, or
  freshness markers.
* Not a retry loop. One call, or an exception recorded by class. No retrying, no
  identity rotation, and no attempt to work around a refusal.

Privacy boundary
----------------
Response bodies, headers, cookies and full URLs never leave this process. The
result is assembled from a fixed allowlist of fields and nothing else, so a
secret echoed inside a page body has no path into the output. Tests pin that
down (``tests/test_screener_probe.py``).

Usage -- ``<SYMBOL>`` is required and exactly one request is made::

    python tools/probe_screener_once.py AAATECH

Interpreting the outcome
------------------------
======================================  ====================================
Outcome                                  What it supports
======================================  ====================================
``HTTP_RESPONSE`` + 403/429              This request was refused / limited.
``HTTP_RESPONSE`` + 404                  The resource was not found.
``HTTP_RESPONSE`` + 200, table absent    The page did not match the expected
                                         quarterly-table structure. This does
                                         **not** by itself prove a redesign.
``HTTP_RESPONSE`` + 200, table present   The table was found; a historical
                                         failure would then lie downstream in
                                         parsing or extraction.
``NETWORK_ERROR`` / ``TIMEOUT``          The request failed in transport. The
                                         site's HTTP status is unknown.
======================================  ====================================

A single symbol cannot establish the failure distribution across a whole run.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone

#: The only keys that may ever appear in a result. Anything not listed here is
#: dropped by construction, so page content has no route into the output.
ALLOWED_FIELDS = frozenset(
    {
        "symbol",
        "utc_attempt",
        "outcome",
        "http_status",
        "response_bytes",
        "quarterly_table_matched",
        "elapsed_seconds",
        "completed_without_retry",
        "transport_exception_class",
    }
)

#: Exception classes that mean "the request did not complete in time" as
#: opposed to a connection-level fault. Classified from the type alone -- the
#: message is never read, because it can embed the full URL.
TIMEOUT_CLASSES = frozenset(
    {
        "Timeout",
        "ReadTimeout",
        "ConnectTimeout",
        "ConnectionTimeout",
        "TimeoutException",
    }
)


def build_url(symbol: str) -> str:
    """The same URL ``DataFetcher._fetch_screener`` requests."""
    return f"https://www.screener.in/company/{symbol}/consolidated/"


def _result(**fields) -> dict:
    """Assemble a result, dropping anything outside the allowlist."""
    return {k: v for k, v in fields.items() if k in ALLOWED_FIELDS}


def classify_response(response, elapsed_seconds: float, symbol: str) -> dict:
    """Turn a 200-or-other HTTP response into a sanitised result.

    The response is inspected in memory only: its byte length is recorded, and
    the quarterly table is detected to decide the boolean. No part of the body
    is returned, and for a non-200 no body is read at all.
    """
    status = getattr(response, "status_code", None)
    out = _result(
        symbol=symbol,
        http_status=status,
        elapsed_seconds=elapsed_seconds,
        completed_without_retry=True,
    )
    if status == 200:
        from myra_app.data_sources.screener_parse import parse_company_page

        try:
            matched = bool(parse_company_page(response.text)["quarterly"])
        except Exception:  # noqa: BLE001 - detection failure is itself the answer
            matched = False
        out["outcome"] = "HTTP_RESPONSE"
        out["response_bytes"] = len(response.content)
        out["quarterly_table_matched"] = matched
    else:
        # A refusal is still an HTTP response. Its body is never touched.
        out["outcome"] = "HTTP_RESPONSE"
    return out


def probe(symbol: str, session=None, timeout: int = 15) -> dict:
    """Issue exactly one request for ``symbol`` and classify the outcome.

    ``session`` defaults to the application's own :class:`GhostSession`, so the
    probe travels by the same approved client, headers and pacing as production
    traffic. It is injectable purely so tests can supply a mock.
    """
    if session is None:
        from myra_app.fetcher import DataFetcher

        session = DataFetcher().session

    started = datetime.now(timezone.utc).isoformat()
    begin = time.perf_counter()
    try:
        response = session.get(build_url(symbol), timeout=timeout)
    except Exception as exc:  # noqa: BLE001 - classification, not handling
        elapsed = round(time.perf_counter() - begin, 3)
        cls = type(exc).__name__
        return _result(
            symbol=symbol,
            utc_attempt=started,
            outcome="TIMEOUT" if cls in TIMEOUT_CLASSES else "NETWORK_ERROR",
            # Present but null: the key is always reported so the result
            # schema does not change shape with the outcome.
            http_status=None,
            # Class only. The message can contain the full URL.
            transport_exception_class=cls,
            elapsed_seconds=elapsed,
            completed_without_retry=True,
        )

    elapsed = round(time.perf_counter() - begin, 3)
    result = classify_response(response, elapsed, symbol)
    result["utc_attempt"] = started
    return result


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: python tools/probe_screener_once.py <SYMBOL>")
        return 2
    # Exactly one request. No retry loop, no fallback, no second symbol.
    print(json.dumps(probe(argv[1].strip().upper()), indent=2))
    return 0


if __name__ == "__main__":
    import sys

    raise SystemExit(main(sys.argv))
