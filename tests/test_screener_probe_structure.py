"""
Section/header detection and its privacy boundary (Phase 28).

Phase 27 could report only a single boolean, which conflated two very different
situations: no quarterly section at all, versus a section whose header carries no
recognisable reporting period. These tests pin down that the two are now
reported separately, that a period label is recognised only from real evidence,
and that the privacy boundary from Phase 27 still holds.

No test here performs a network request: every response is a local mock.
"""

import pytest

from tools.probe_screener_once import (
    ALLOWED_FIELDS,
    _is_period_label,
    classify_response,
    detect_quarterly_structure,
    probe,
)

SECRET = "SUPER_SECRET_COOKIE_VALUE_DO_NOT_LEAK"


class _Resp:
    def __init__(self, status_code=200, text="", content=None, headers=None):
        self.status_code = status_code
        self.text = text
        self.content = content if content is not None else text.encode("utf-8")
        self.headers = headers or {}
        self.cookies = {SECRET: "abc"}


class _Session:
    def __init__(self, response=None, error=None):
        self._response = response
        self._error = error
        self.calls = []

    def get(self, url, timeout=None):
        self.calls.append((url, timeout))
        if self._error is not None:
            raise self._error
        return self._response


def _section_with_header(header_labels: str) -> str:
    return f"""
    <section id="quarters">
      <table>
        <tr><th>Period</th>{header_labels}</tr>
        <tr><th>Revenue</th><td>100</td><td>90</td></tr>
      </table>
    </section>
    """


# --------------------------------------------------------------------------- #
# Period-label recognition (must never be inferred)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("label", ["Sep 2025", "Mar 2026", "Dec 2024", "2026-09"])
def test_real_period_labels_are_recognised(label):
    assert _is_period_label(label) is True


@pytest.mark.parametrize(
    "label",
    [
        "",
        "TTM",
        "Q-1",
        "Q-4",
        "Period",
        "Revenue",
        "1234",  # a value, not a period
        "2026-0",  # malformed month must not count
        "0",  # no month at all
        "Sep",  # month without a year
        "2026",  # year without a month
    ],
)
def test_non_period_text_is_not_recognised(label):
    """Including the Q-1..Q-4 convention: it is a placeholder, not a date."""
    assert _is_period_label(label) is False


# --------------------------------------------------------------------------- #
# detect_quarterly_structure -- the four required outcomes
# --------------------------------------------------------------------------- #


def test_section_absent():
    result = detect_quarterly_structure(
        "<html><body><div>nothing relevant</div></body></html>"
    )
    assert result["section"] == "absent"
    assert result["header_has_period_label"] is False


def test_section_present_with_period_label():
    result = detect_quarterly_structure(
        _section_with_header("<th>Sep 2025</th><th>Jun 2025</th>")
    )
    assert result["section"] == "present"
    assert result["header_has_period_label"] is True


def test_section_present_without_period_label():
    result = detect_quarterly_structure(
        _section_with_header("<th>Column A</th><th>Column B</th>")
    )
    assert result["section"] == "present"
    assert result["header_has_period_label"] is False


def test_section_present_but_no_table():
    """A section with no table has no header -- present, but no label."""
    result = detect_quarterly_structure('<section id="quarters"></section>')
    assert result["section"] == "present"
    assert result["header_has_period_label"] is False


def test_ambiguous_markup_is_indeterminate_not_false():
    """Several plausible quarterly sections: refuse to guess."""
    html = """
    <section><table><tr><th>P</th><th>Sep 2025</th></tr></table>
      Quarterly Results</section>
    <section><table><tr><th>P</th><th>Jun 2025</th></tr></table>
      Quarterly Results</section>
    """
    result = detect_quarterly_structure(html)
    assert result["section"] == "indeterminate"
    assert result["header_has_period_label"] is False


def test_section_found_by_heading_when_id_is_absent():
    html = """
    <section>
      <h2>Quarterly Results</h2>
      <table><tr><th>Period</th><th>Sep 2025</th></tr></table>
    </section>
    """
    result = detect_quarterly_structure(html)
    assert result["section"] == "present"
    assert result["header_has_period_label"] is True


def test_id_quarters_wins_over_heading_search():
    """The parser prefers #quarters; detection must agree with it."""
    html = """
    <section id="quarters"><table><tr><th>Period</th><th>Sep 2025</th></tr></table>
    </section>
    <section>Quarterly commentary, no table here</section>
    """
    result = detect_quarterly_structure(html)
    assert result["section"] == "present"
    assert result["header_has_period_label"] is True


def test_malformed_markup_does_not_raise():
    for html in ["", "<html", "<<<>>>", "not html at all", "<section id='quarters'>"]:
        result = detect_quarterly_structure(html)
        assert result["section"] in {"present", "absent", "indeterminate"}


# --------------------------------------------------------------------------- #
# Wiring into the result
# --------------------------------------------------------------------------- #


def test_200_section_present_label_present():
    session = _Session(_Resp(200, text=_section_with_header("<th>Sep 2025</th>")))
    result = probe("AAATECH", session=session)

    assert result["outcome"] == "HTTP_RESPONSE"
    assert result["quarterly_section_present"] is True
    assert result["quarterly_header_has_period_label"] is True
    assert result["quarterly_table_matched"] is True


def test_200_section_present_label_absent():
    session = _Session(_Resp(200, text=_section_with_header("<th>Column A</th>")))
    result = probe("AAATECH", session=session)

    assert result["quarterly_section_present"] is True
    assert result["quarterly_header_has_period_label"] is False


def test_200_section_absent():
    session = _Session(_Resp(200, text="<html><body>nope</body></html>"))
    result = probe("AAATECH", session=session)

    assert result["quarterly_section_detection"] == "absent"
    assert result["quarterly_section_present"] is False
    assert result["quarterly_header_has_period_label"] is False


def test_indeterminate_sets_a_distinct_outcome():
    html = """
    <section><table><tr><th>P</th><th>Sep 2025</th></tr></table>
      Quarterly Results</section>
    <section><table><tr><th>P</th><th>Jun 2025</th></tr></table>
      Quarterly Results</section>
    """
    session = _Session(_Resp(200, text=html))
    result = probe("AAATECH", session=session)

    assert result["outcome"] == "INDETERMINATE_STRUCTURE"
    assert result["quarterly_section_detection"] == "indeterminate"
    # Nothing is asserted as absent when the structure could not be classified.
    assert result["quarterly_section_present"] is False
    assert result["quarterly_header_has_period_label"] is False


def test_non_200_omits_the_structure_fields():
    session = _Session(_Resp(status_code=429, text="<html>x</html>"))
    result = probe("AAATECH", session=session)

    assert result["http_status"] == 429
    assert "quarterly_section_present" not in result
    assert "quarterly_header_has_period_label" not in result
    assert "quarterly_table_matched" not in result


def test_transport_failure_omits_the_structure_fields():
    session = _Session(error=type("ConnectionError", (Exception,), {})("boom"))
    result = probe("AAATECH", session=session)

    assert result["outcome"] == "NETWORK_ERROR"
    assert "quarterly_section_present" not in result


# --------------------------------------------------------------------------- #
# Privacy boundary (still structural)
# --------------------------------------------------------------------------- #


def test_header_text_and_secrets_cannot_reach_the_result():
    body = f"""
    <html><body>
      <div>{SECRET}</div>
      <section id="quarters">
        <table>
          <tr><th>Period</th><th>Sep 2025</th></tr>
          <tr><th>Revenue</th><td>123456.78</td></tr>
        </table>
      </section>
    </body></html>
    """
    session = _Session(_Resp(200, text=body, headers={"Set-Cookie": f"sid={SECRET}"}))
    result = probe("AAATECH", session=session)

    rendered = repr(result)
    assert SECRET not in rendered
    assert "Sep 2025" not in rendered, "a reporting label must not be returned"
    assert "123456.78" not in rendered, "a financial value must not be returned"
    assert "Revenue" not in rendered, "row labels must not be returned"
    assert "screener.in" not in rendered


def test_result_still_contains_only_allowlisted_fields():
    session = _Session(_Resp(200, text=_section_with_header("<th>Sep 2025</th>")))
    result = probe("AAATECH", session=session)
    assert set(result) <= ALLOWED_FIELDS


def test_new_fields_are_booleans_and_an_enumerated_state():
    session = _Session(_Resp(200, text=_section_with_header("<th>Sep 2025</th>")))
    result = probe("AAATECH", session=session)
    assert isinstance(result["quarterly_section_present"], bool)
    assert isinstance(result["quarterly_header_has_period_label"], bool)
    assert result["quarterly_section_detection"] in {
        "present",
        "absent",
        "indeterminate",
    }


def test_no_raw_header_text_in_any_new_field():
    """The enum and booleans carry classification only -- no text channels."""
    session = _Session(_Resp(200, text=_section_with_header("<th>Sep 2025</th>")))
    result = probe("AAATECH", session=session)
    for key in (
        "quarterly_section_present",
        "quarterly_header_has_period_label",
        "quarterly_section_detection",
    ):
        assert not isinstance(result[key], str) or result[key] in {
            "present",
            "absent",
            "indeterminate",
        }


# --------------------------------------------------------------------------- #
# Request discipline and no database access
# --------------------------------------------------------------------------- #


def test_exactly_one_request_and_no_retry():
    session = _Session(_Resp(200, text=_section_with_header("<th>Sep 2025</th>")))
    result = probe("AAATECH", session=session)
    assert len(session.calls) == 1
    assert result["completed_without_retry"] is True


def test_exactly_one_request_even_when_detection_raises():
    """A structure-detection failure must not trigger a second attempt."""
    import tools.probe_screener_once as mod

    session = _Session(_Resp(200, text="<html></html>"))
    original = mod.detect_quarterly_structure

    def boom(html):
        raise RuntimeError(f"leaky {SECRET}")

    mod.detect_quarterly_structure = boom
    try:
        result = probe("AAATECH", session=session)
    finally:
        mod.detect_quarterly_structure = original

    assert len(session.calls) == 1
    assert result["outcome"] == "INDETERMINATE_STRUCTURE"
    assert SECRET not in repr(result)


def test_no_database_access(monkeypatch):
    import sqlite3

    monkeypatch.setattr(
        sqlite3,
        "connect",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("db touched")),
    )
    session = _Session(_Resp(200, text=_section_with_header("<th>Sep 2025</th>")))
    result = probe("AAATECH", session=session)
    assert result["quarterly_section_present"] is True


def test_classify_response_accepts_a_response_directly():
    result = classify_response(
        _Resp(200, text=_section_with_header("<th>Sep 2025</th>")), 0.5, "AAATECH"
    )
    assert result["quarterly_header_has_period_label"] is True
    assert result["quarterly_section_present"] is True
