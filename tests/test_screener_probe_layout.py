"""
Quarterly table layout and period-shaped markup (Phase 30).

Phase 29 established that AAATECH's quarterly table exists and that the parser's
header row has zero cells after the label column. It could not say whether that
first row is a one-cell title row or a row with no cells at all.

Phase 30 answers that from *layout* alone -- element presence and counts -- and
keeps the reporting-period question separate:

1. **Layout is measured, not interpreted.** No cell text is read to decide the
   classification, so the same markup shape always yields the same answer.
2. **A shape is not a date.** ``period_shaped_markup_outside_tr`` reports whether
   period-shaped markup sits outside any row; the token itself never leaves the
   helper.

No test here performs a network request: every response is a local mock.
"""

import pytest

from tools.probe_screener_once import (
    ALLOWED_FIELDS,
    _direct_child,
    _text_has_period_shape,
    classify_response,
    detect_quarterly_structure,
    probe,
)

SECRET = "SUPER_SECRET_COOKIE_VALUE_DO_NOT_LEAK"

LAYOUT_CLASSIFICATIONS = {
    "NO_QUARTERLY_SECTION",
    "NO_TABLE",
    "TABLE_WITHOUT_ROWS",
    "TABLE_WITH_LABEL_ONLY_FIRST_ROW",
    "TABLE_WITH_STRUCTURAL_HEADER",
    "PERIOD_SHAPED_MARKUP_OUTSIDE_ROWS",
    "AMBIGUOUS_LAYOUT",
}


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


def _section(inner: str) -> str:
    return f"<section id='quarters'>{inner}</section>"


def _table(inner: str) -> str:
    return f"<table>{inner}</table>"


# --------------------------------------------------------------------------- #
# Period-shape token helper (returns a bool, never the token)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("text", ["Sep 2025", "2026-09", "see Sep 2025 above"])
def test_period_shape_is_detected(text):
    assert _text_has_period_shape(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "",
        "TTM",
        "Q-1",
        "2026-00",
        "2026-13",
        "Quarterly 2025",  # a word, not a month
        "Results 2025",
        "12345",
        "no dates here",
    ],
)
def test_period_shape_is_not_detected(text):
    assert _text_has_period_shape(text) is False


def test_direct_child_finds_only_immediate_children():
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(
        "<table><thead><tr><th>x</th></tr></thead></table>", "html.parser"
    )
    table = soup.find("table")
    assert _direct_child(table, "thead") is not None
    assert _direct_child(table, "tbody") is None


# --------------------------------------------------------------------------- #
# Layout classification -- the documented set
# --------------------------------------------------------------------------- #


def test_section_absent():
    result = detect_quarterly_structure("<html><body><p>nothing</p></body></html>")
    assert result["layout_classification"] == "NO_QUARTERLY_SECTION"
    assert result["table_present"] is False
    assert result["row0_total_cell_count"] is None
    assert result["table_row_count"] is None
    assert result["thead_present"] is None
    assert result["tbody_present"] is None
    assert result["period_shaped_markup_outside_tr"] is None


def test_section_present_but_table_absent():
    result = detect_quarterly_structure(_section("<p>no table here</p>"))
    assert result["layout_classification"] == "NO_TABLE"
    assert result["table_present"] is False
    assert result["table_row_count"] is None
    assert result["thead_present"] is None
    assert result["period_shaped_markup_outside_tr"] is None


def test_table_present_with_zero_rows():
    result = detect_quarterly_structure(_section(_table("")))
    assert result["layout_classification"] == "TABLE_WITHOUT_ROWS"
    assert result["table_row_count"] == 0
    assert result["row0_total_cell_count"] is None  # no row to read


def test_first_row_with_one_label_only_cell():
    result = detect_quarterly_structure(
        _section(_table("<tr><th>Metric</th></tr><tr><th>Revenue</th><td>1</td></tr>"))
    )
    assert result["row0_total_cell_count"] == 1
    assert result["layout_classification"] == "TABLE_WITH_LABEL_ONLY_FIRST_ROW"


def test_first_row_with_multiple_cells():
    result = detect_quarterly_structure(
        _section(_table("<tr><th>Metric</th><th>Column A</th></tr>"))
    )
    assert result["row0_total_cell_count"] == 2
    assert result["layout_classification"] == "TABLE_WITH_STRUCTURAL_HEADER"


def test_row0_total_minus_one_equals_the_phase29_header_count():
    """The two counts must agree: total = label column + header cells."""
    result = detect_quarterly_structure(
        _section(_table("<tr><th>Metric</th><th>Sep 2025</th><th>Jun 2025</th></tr>"))
    )
    assert result["row0_total_cell_count"] == 3
    assert result["header_cell_count"] == 2
    assert result["row0_total_cell_count"] == result["header_cell_count"] + 1


def test_first_row_with_no_cells_at_all_is_ambiguous():
    result = detect_quarterly_structure(_section(_table("<tr></tr>")))
    assert result["row0_total_cell_count"] == 0
    assert result["layout_classification"] == "AMBIGUOUS_LAYOUT"


# --------------------------------------------------------------------------- #
# thead / tbody presence and counts (reported separately)
# --------------------------------------------------------------------------- #


def test_thead_absent():
    result = detect_quarterly_structure(
        _section(_table("<tr><th>Metric</th><th>Sep 2025</th></tr>"))
    )
    assert result["thead_present"] is False
    assert result["thead_row_count"] is None


def test_thead_present_counts_its_rows():
    html = _section(
        _table(
            "<thead><tr><th>Metric</th><th>Sep 2025</th></tr></thead>"
            "<tbody><tr><th>Revenue</th><td>1</td></tr></tbody>"
        )
    )
    result = detect_quarterly_structure(html)
    assert result["thead_present"] is True
    assert result["thead_row_count"] == 1


def test_thead_present_but_empty_is_not_absent():
    result = detect_quarterly_structure(
        _section(_table("<thead></thead><tr><th>Metric</th></tr>"))
    )
    assert result["thead_present"] is True
    assert result["thead_row_count"] == 0  # present, but measured empty


def test_tbody_absent():
    result = detect_quarterly_structure(
        _section(_table("<tr><th>Metric</th><th>Sep 2025</th></tr>"))
    )
    assert result["tbody_present"] is False
    assert result["tbody_row_count"] is None


def test_tbody_present_counts_its_rows():
    html = _section(_table("<tbody><tr><th>Metric</th><th>Sep 2025</th></tr></tbody>"))
    result = detect_quarterly_structure(html)
    assert result["tbody_present"] is True
    assert result["tbody_row_count"] == 1


def test_empty_tbody_present_with_zero_rows():
    result = detect_quarterly_structure(_section(_table("<tbody></tbody>")))
    assert result["tbody_present"] is True
    assert result["tbody_row_count"] == 0
    assert result["table_row_count"] == 0
    assert result["layout_classification"] == "TABLE_WITHOUT_ROWS"


def test_non_empty_table_section_counts_rows_and_cells():
    html = _section(
        _table(
            "<tr><th>Metric</th><th>Sep 2025</th></tr><tr><th>Revenue</th><td>1</td></tr>"
        )
    )
    result = detect_quarterly_structure(html)
    assert result["table_row_count"] == 2
    assert result["row0_total_cell_count"] == 2


# --------------------------------------------------------------------------- #
# Period-shaped markup outside rows
# --------------------------------------------------------------------------- #


def test_period_markup_outside_rows_is_detected():
    result = detect_quarterly_structure(
        _section(_table("<tr><th>Metric</th></tr>") + "<caption>Sep 2025</caption>")
    )
    assert result["period_shaped_markup_outside_tr"] is True


def test_period_markup_inside_rows_is_not_outside():
    result = detect_quarterly_structure(
        _section(_table("<tr><th>Metric</th><th>Sep 2025</th></tr>"))
    )
    assert result["period_shaped_markup_outside_tr"] is False


def test_period_markup_outside_rows_classifies_when_the_header_lacks_labels():
    html = _section(
        _table("<tr><th>Metric</th><th>Column A</th></tr>") + "<p>Sep 2025</p>"
    )
    result = detect_quarterly_structure(html)
    assert result["period_shaped_markup_outside_tr"] is True
    assert result["layout_classification"] == "PERIOD_SHAPED_MARKUP_OUTSIDE_ROWS"


def test_date_shaped_markup_in_an_unrelated_table_does_not_count():
    html = (
        _section(_table("<tr><th>Metric</th><th>Column A</th></tr>"))
        + "<div><table><tr><th>Elsewhere</th><th>Sep 2025</th></tr></table></div>"
    )
    result = detect_quarterly_structure(html)
    assert result["period_shaped_markup_outside_tr"] is False
    assert result["layout_classification"] == "TABLE_WITH_STRUCTURAL_HEADER"


# --------------------------------------------------------------------------- #
# Ambiguity
# --------------------------------------------------------------------------- #


def test_multiple_tables_in_the_section_is_ambiguous():
    html = _section(
        _table("<tr><th>Metric</th><th>Sep 2025</th></tr>")
        + _table("<tr><th>Metric</th><th>Jun 2025</th></tr>")
    )
    result = detect_quarterly_structure(html)
    assert result["layout_classification"] == "AMBIGUOUS_LAYOUT"
    assert result["thead_present"] is None
    assert result["row0_total_cell_count"] is None


def test_several_quarterly_sections_is_ambiguous():
    html = (
        "<section><table><tr><th>M</th><th>Sep 2025</th></tr></table>"
        "Quarterly Results</section>"
        "<section><table><tr><th>M</th><th>Jun 2025</th></tr></table>"
        "Quarterly Results</section>"
    )
    result = detect_quarterly_structure(html)
    assert result["layout_classification"] == "AMBIGUOUS_LAYOUT"
    assert result["thead_present"] is None


@pytest.mark.parametrize("bad", ["", "<html", "<<<>>>", "not html", "<section"])
def test_malformed_markup_never_raises(bad):
    result = detect_quarterly_structure(bad)
    assert result["layout_classification"] in LAYOUT_CLASSIFICATIONS


# --------------------------------------------------------------------------- #
# Wiring into the result
# --------------------------------------------------------------------------- #


def test_probe_reports_every_layout_field_on_200():
    html = _section(
        _table(
            "<thead><tr><th>Metric</th><th>Sep 2025</th></tr></thead>"
            "<tbody><tr><th>Revenue</th><td>1</td></tr></tbody>"
        )
    )
    session = _Session(_Resp(200, text=html))
    result = probe("AAATECH", session=session)

    assert result["layout_classification"] == "TABLE_WITH_STRUCTURAL_HEADER"
    assert result["row0_total_cell_count"] == 2
    assert result["table_row_count"] == 2
    assert result["thead_present"] is True
    assert result["thead_row_count"] == 1
    assert result["tbody_present"] is True
    assert result["tbody_row_count"] == 1
    assert result["period_shaped_markup_outside_tr"] is False
    assert set(result) <= ALLOWED_FIELDS


def test_label_only_first_row_reaches_the_result():
    session = _Session(_Resp(200, text=_section(_table("<tr><th>Metric</th></tr>"))))
    result = probe("AAATECH", session=session)
    assert result["row0_total_cell_count"] == 1
    assert result["layout_classification"] == "TABLE_WITH_LABEL_ONLY_FIRST_ROW"


def test_layout_classification_is_from_the_documented_set():
    session = _Session(_Resp(200, text=_section(_table("<tr><th>M</th></tr>"))))
    result = probe("AAATECH", session=session)
    assert result["layout_classification"] in LAYOUT_CLASSIFICATIONS


def test_layout_counts_are_small_or_null():
    session = _Session(_Resp(200, text=_section(_table("<tr><th>M</th></tr>"))))
    result = probe("AAATECH", session=session)
    for key in (
        "row0_total_cell_count",
        "table_row_count",
        "thead_row_count",
        "tbody_row_count",
    ):
        value = result[key]
        assert value is None or (isinstance(value, int) and 0 <= value <= 100)


def test_non_200_omits_the_layout_fields():
    session = _Session(_Resp(status_code=403, text="<html>x</html>"))
    result = probe("AAATECH", session=session)
    assert result["http_status"] == 403
    for key in (
        "row0_total_cell_count",
        "thead_present",
        "thead_row_count",
        "tbody_present",
        "tbody_row_count",
        "table_row_count",
        "period_shaped_markup_outside_tr",
        "layout_classification",
    ):
        assert key not in result


def test_transport_failure_omits_the_layout_fields():
    session = _Session(error=type("ConnectionError", (Exception,), {})("boom"))
    result = probe("AAATECH", session=session)
    assert result["outcome"] == "NETWORK_ERROR"
    assert "layout_classification" not in result


def test_classify_response_accepts_a_response_directly():
    result = classify_response(
        _Resp(200, text=_section(_table("<tr><th>M</th></tr>"))), 0.5, "A"
    )
    assert result["layout_classification"] == "TABLE_WITH_LABEL_ONLY_FIRST_ROW"


# --------------------------------------------------------------------------- #
# Privacy boundary
# --------------------------------------------------------------------------- #


def test_no_cell_text_value_or_url_reaches_the_result():
    body = f"""
    <html><body>
      <div>{SECRET}</div>
      <section id='quarters'>
        <table>
          <tr><th>Metric</th><th>Sep 2025</th></tr>
          <tr><th>Revenue</th><td>123456.78</td></tr>
        </table>
        <p>Sep 2025</p>
      </section>
    </body></html>
    """
    session = _Session(_Resp(200, text=body, headers={"Set-Cookie": f"sid={SECRET}"}))
    result = probe("AAATECH", session=session)

    rendered = repr(result)
    assert SECRET not in rendered
    assert "Sep 2025" not in rendered, "a period label must not be returned"
    assert "123456.78" not in rendered, "a financial value must not be returned"
    assert "Revenue" not in rendered, "row labels must not be returned"
    assert "screener.in" not in rendered
    assert "cookie" not in rendered.lower()


def test_exception_message_cannot_reach_the_result():
    leaky = "failed for https://www.screener.in/company/AAATECH/?token=abc123"
    session = _Session(error=type("ConnectionError", (Exception,), {})(leaky))
    result = probe("AAATECH", session=session)
    rendered = repr(result)
    assert "screener.in" not in rendered
    assert "abc123" not in rendered


def test_result_contains_only_allowlisted_fields():
    session = _Session(_Resp(200, text=_section(_table("<tr><th>M</th></tr>"))))
    result = probe("AAATECH", session=session)
    assert set(result) <= ALLOWED_FIELDS


# --------------------------------------------------------------------------- #
# Request discipline and no database access
# --------------------------------------------------------------------------- #


def test_exactly_one_request_and_no_retry():
    session = _Session(_Resp(200, text=_section(_table("<tr><th>M</th></tr>"))))
    result = probe("AAATECH", session=session)
    assert len(session.calls) == 1
    assert result["completed_without_retry"] is True


def test_exactly_one_request_even_when_detection_raises():
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
    assert result["layout_classification"] == "AMBIGUOUS_LAYOUT"
    assert result["row0_total_cell_count"] is None
    assert SECRET not in repr(result)


def test_no_database_access(monkeypatch):
    import sqlite3

    monkeypatch.setattr(
        sqlite3,
        "connect",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("db touched")),
    )
    session = _Session(_Resp(200, text=_section(_table("<tr><th>M</th></tr>"))))
    result = probe("AAATECH", session=session)
    assert result["layout_classification"] == "TABLE_WITH_LABEL_ONLY_FIRST_ROW"
