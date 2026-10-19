"""
Quarterly table structure and period-label shapes (Phase 29).

Phase 27 reported a single boolean. Phase 28 split it into "section present" and
"header has a period label", but two very different situations still read the
same: a section with no table, and a table whose header carries no period-like
labels. Phase 29 separates them and reports the header's *shape* as counts.

Two properties are pinned here:

1. **Structure is reported, never guessed.** Ambiguity -- several tables, or
   several period-labelled rows -- is admitted as ``AMBIGUOUS`` rather than
   resolved by silently picking one.
2. **A label shape is not a date.** ``mon_year_header_count`` and
   ``iso_year_month_header_count`` count *shapes*; nothing is assigned to a
   financial row, and no cell text, date or value ever reaches the result.

No test here performs a network request: every response is a local mock.
"""

import pytest

from tools.probe_screener_once import (
    ALLOWED_FIELDS,
    _is_iso_year_month_label,
    _is_mon_year_label,
    _is_period_label,
    classify_response,
    detect_quarterly_structure,
    probe,
)

SECRET = "SUPER_SECRET_COOKIE_VALUE_DO_NOT_LEAK"

CLASSIFICATIONS = {
    "SECTION_ABSENT",
    "NO_TABLE",
    "TABLE_NO_PERIOD_LABELS",
    "TABLE_PERIOD_LABELS_IN_UNEXPECTED_STRUCTURE",
    "EXPECTED_TABLE_MATCHED",
    "AMBIGUOUS",
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


def _section(rows: str) -> str:
    return f"<section id='quarters'><table>{rows}</table></section>"


def _header(labels) -> str:
    body = "".join(f"<th>{label}</th>" for label in labels)
    return f"<tr><th>Metric</th>{body}</tr>"


def _data(label: str, values) -> str:
    body = "".join(f"<td>{value}</td>" for value in values)
    return f"<tr><th>{label}</th>{body}</tr>"


# --------------------------------------------------------------------------- #
# Period-label *shape* helpers (independent of the parser's recogniser)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("label", ["Sep 2025", "Mar 2026", "December 2024"])
def test_mon_year_shape_accepts_real_month_labels(label):
    assert _is_mon_year_label(label) is True


@pytest.mark.parametrize(
    "label",
    [
        "",
        "TTM",
        "Q-1",
        "Foo 2025",  # a word that is not a month
        "Sep 25",  # two-digit year
        "Sep",  # no year
        "2025",  # no month
        "2026-09",  # ISO, not Mon YYYY
        "Sep-2025",  # hyphen, not the strict shape
    ],
)
def test_mon_year_shape_rejects_everything_else(label):
    assert _is_mon_year_label(label) is False


@pytest.mark.parametrize("label", ["2025-09", "2026-01", "2026-12"])
def test_iso_shape_accepts_valid_year_months(label):
    assert _is_iso_year_month_label(label) is True


@pytest.mark.parametrize(
    "label",
    [
        "2026-00",  # month zero
        "2026-13",  # month thirteen
        "2026-9",  # not two digits
        "26-09",  # two-digit year
        "2026/09",  # wrong separator
        "Sep 2025",  # Mon YYYY, not ISO
        "2026-",
    ],
)
def test_iso_shape_rejects_invalid_year_months(label):
    assert _is_iso_year_month_label(label) is False


def test_shape_helpers_are_independent_of_the_parser_recogniser():
    """The recogniser accepts "2026-9"; the strict shape test must not."""
    assert _is_period_label("2026-9") is True
    assert _is_iso_year_month_label("2026-9") is False


# --------------------------------------------------------------------------- #
# Structure classification -- the documented set
# --------------------------------------------------------------------------- #


def test_section_absent():
    result = detect_quarterly_structure("<html><body><p>nothing</p></body></html>")
    assert result["section"] == "absent"
    assert result["table_present"] is False
    assert result["table_count"] == 0
    assert result["classification"] == "SECTION_ABSENT"


def test_section_present_with_no_table():
    result = detect_quarterly_structure("<section id='quarters'></section>")
    assert result["section"] == "present"
    assert result["table_present"] is False
    assert result["table_count"] == 0
    assert result["header_cell_count"] is None
    assert result["classification"] == "NO_TABLE"


def test_table_present_with_no_rows():
    result = detect_quarterly_structure(
        "<section id='quarters'><table></table></section>"
    )
    assert result["table_present"] is True
    assert result["table_count"] == 1
    # No header row to read: unavailable, not zero.
    assert result["header_cell_count"] is None
    assert result["classification"] == "TABLE_NO_PERIOD_LABELS"


def test_table_header_with_no_cells_after_the_label_column():
    result = detect_quarterly_structure(_section("<tr><th>Metric</th></tr>"))
    assert result["header_cell_count"] == 0  # measured: the row exists
    assert result["recognized_period_header_count"] == 0
    assert result["classification"] == "TABLE_NO_PERIOD_LABELS"


def test_table_header_without_period_like_labels():
    result = detect_quarterly_structure(
        _section(_header(["Column A", "Column B"]) + _data("Revenue", [1, 2]))
    )
    assert result["header_cell_count"] == 2
    assert result["recognized_period_header_count"] == 0
    assert result["mon_year_header_count"] == 0
    assert result["iso_year_month_header_count"] == 0
    assert result["classification"] == "TABLE_NO_PERIOD_LABELS"


def test_table_with_mon_year_labels():
    result = detect_quarterly_structure(
        _section(_header(["Sep 2025", "Jun 2025"]) + _data("Revenue", [1, 2]))
    )
    assert result["recognized_period_header_count"] == 2
    assert result["mon_year_header_count"] == 2
    assert result["iso_year_month_header_count"] == 0
    assert result["classification"] == "EXPECTED_TABLE_MATCHED"


def test_table_with_iso_year_month_labels():
    result = detect_quarterly_structure(
        _section(_header(["2026-09", "2026-06"]) + _data("Revenue", [1, 2]))
    )
    assert result["recognized_period_header_count"] == 2
    assert result["mon_year_header_count"] == 0
    assert result["iso_year_month_header_count"] == 2
    assert result["classification"] == "EXPECTED_TABLE_MATCHED"


def test_table_with_mixed_label_shapes():
    result = detect_quarterly_structure(
        _section(_header(["Sep 2025", "2026-06"]) + _data("Revenue", [1, 2]))
    )
    assert result["recognized_period_header_count"] == 2
    assert result["mon_year_header_count"] == 1
    assert result["iso_year_month_header_count"] == 1


@pytest.mark.parametrize("label", ["2026-00", "2026-13"])
def test_invalid_iso_months_are_not_counted(label):
    result = detect_quarterly_structure(
        _section(_header([label]) + _data("Revenue", [1]))
    )
    assert result["recognized_period_header_count"] == 0
    assert result["iso_year_month_header_count"] == 0
    assert result["classification"] == "TABLE_NO_PERIOD_LABELS"


def test_period_labels_in_a_different_row_are_unexpected_structure():
    rows = (
        _header(["Column A"])
        + "<tr><th>Period</th><th>Sep 2025</th></tr>"
        + _data("Revenue", [1])
    )
    result = detect_quarterly_structure(_section(rows))
    # Not where the parser reads: row 0 has no period label.
    assert result["header_has_period_label"] is False
    assert result["recognized_period_header_count"] == 0
    assert result["classification"] == "TABLE_PERIOD_LABELS_IN_UNEXPECTED_STRUCTURE"


def test_multiple_period_label_rows_are_ambiguous():
    rows = (
        _header(["Amount", "Other"])
        + "<tr><th>Period</th><th>Sep 2025</th><th>Jun 2025</th></tr>"
        + "<tr><th>Period</th><th>Mar 2025</th><th>Dec 2024</th></tr>"
    )
    result = detect_quarterly_structure(_section(rows))
    assert result["classification"] == "AMBIGUOUS"


def test_multiple_tables_in_the_section_are_ambiguous():
    html = (
        "<section id='quarters'>"
        "<table><tr><th>Period</th><th>Sep 2025</th></tr></table>"
        "<table><tr><th>Period</th><th>Jun 2025</th></tr></table>"
        "</section>"
    )
    result = detect_quarterly_structure(html)
    assert result["table_count"] == 2
    assert result["table_present"] is True
    # No single authoritative header row: counts stay unavailable.
    assert result["header_cell_count"] is None
    assert result["recognized_period_header_count"] is None
    assert result["classification"] == "AMBIGUOUS"


def test_unrelated_page_tables_do_not_affect_the_counts():
    html = (
        _section(_header(["Column A"]) + _data("Revenue", [1]))
        + "<div><table><tr><th>Elsewhere</th><th>Sep 2025</th></tr></table></div>"
    )
    result = detect_quarterly_structure(html)
    assert result["header_cell_count"] == 1  # only the section's header
    assert result["recognized_period_header_count"] == 0
    assert result["mon_year_header_count"] == 0
    assert result["iso_year_month_header_count"] == 0
    assert result["classification"] == "TABLE_NO_PERIOD_LABELS"


def test_several_quarterly_sections_are_ambiguous():
    html = (
        "<section><table><tr><th>P</th><th>Sep 2025</th></tr></table>"
        "Quarterly Results</section>"
        "<section><table><tr><th>P</th><th>Jun 2025</th></tr></table>"
        "Quarterly Results</section>"
    )
    result = detect_quarterly_structure(html)
    assert result["section"] == "indeterminate"
    assert result["classification"] == "AMBIGUOUS"
    assert result["header_cell_count"] is None


@pytest.mark.parametrize("bad", ["", "<html", "<<<>>>", "not html", "<section"])
def test_malformed_markup_never_raises(bad):
    result = detect_quarterly_structure(bad)
    assert result["classification"] in CLASSIFICATIONS


def test_classification_is_never_driven_by_the_weak_match_flag():
    """A non-empty header matches production parsing but holds no period.

    ``parse_quarterly`` accepts any non-empty header cell as a column, so
    ``quarterly_table_matched`` cannot stand in for "period labels present".
    """
    result = detect_quarterly_structure(
        _section(_header(["Column A", "Column B"]) + _data("Revenue", [1, 2]))
    )
    assert result["expected_table_matched"] is True
    assert result["classification"] == "TABLE_NO_PERIOD_LABELS"


# --------------------------------------------------------------------------- #
# Wiring into the result
# --------------------------------------------------------------------------- #


def test_probe_reports_every_structural_field_on_200():
    session = _Session(_Resp(200, text=_section(_header(["Sep 2025"]))))
    result = probe("AAATECH", session=session)

    assert result["outcome"] == "HTTP_RESPONSE"
    assert result["quarterly_table_present"] is True
    assert result["quarterly_table_count"] == 1
    assert result["header_cell_count"] == 1
    assert result["recognized_period_header_count"] == 1
    assert result["mon_year_header_count"] == 1
    assert result["iso_year_month_header_count"] == 0
    assert result["structure_classification"] == "EXPECTED_TABLE_MATCHED"
    assert set(result) <= ALLOWED_FIELDS


def test_structure_classification_is_from_the_documented_set():
    session = _Session(_Resp(200, text=_section(_header(["Sep 2025"]))))
    result = probe("AAATECH", session=session)
    assert result["structure_classification"] in CLASSIFICATIONS


def test_counts_are_small_or_null_never_a_value():
    session = _Session(_Resp(200, text=_section(_header(["Sep 2025"]))))
    result = probe("AAATECH", session=session)
    for key in (
        "quarterly_table_count",
        "header_cell_count",
        "recognized_period_header_count",
        "mon_year_header_count",
        "iso_year_month_header_count",
    ):
        value = result[key]
        assert value is None or (isinstance(value, int) and 0 <= value <= 100)


def test_non_200_omits_the_structural_fields():
    session = _Session(_Resp(status_code=429, text="<html>x</html>"))
    result = probe("AAATECH", session=session)
    assert result["http_status"] == 429
    for key in (
        "quarterly_table_present",
        "quarterly_table_count",
        "header_cell_count",
        "recognized_period_header_count",
        "mon_year_header_count",
        "iso_year_month_header_count",
        "structure_classification",
    ):
        assert key not in result


def test_transport_failure_omits_the_structural_fields():
    session = _Session(error=type("ConnectionError", (Exception,), {})("boom"))
    result = probe("AAATECH", session=session)
    assert result["outcome"] == "NETWORK_ERROR"
    assert "structure_classification" not in result


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


def test_exception_message_cannot_reach_the_result():
    leaky = "failed for https://www.screener.in/company/AAATECH/?token=abc123"
    session = _Session(error=type("ConnectionError", (Exception,), {})(leaky))
    result = probe("AAATECH", session=session)
    rendered = repr(result)
    assert "screener.in" not in rendered
    assert "abc123" not in rendered


def test_result_contains_only_allowlisted_fields():
    session = _Session(_Resp(200, text=_section(_header(["Sep 2025"]))))
    result = probe("AAATECH", session=session)
    assert set(result) <= ALLOWED_FIELDS


def test_classify_response_accepts_a_response_directly():
    result = classify_response(
        _Resp(200, text=_section(_header(["2026-09"]))), 0.5, "A"
    )
    assert result["structure_classification"] == "EXPECTED_TABLE_MATCHED"
    assert result["iso_year_month_header_count"] == 1


# --------------------------------------------------------------------------- #
# Request discipline and no database access
# --------------------------------------------------------------------------- #


def test_exactly_one_request_and_no_retry():
    session = _Session(_Resp(200, text=_section(_header(["Sep 2025"]))))
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
    assert result["structure_classification"] == "AMBIGUOUS"
    assert result["header_cell_count"] is None
    assert SECRET not in repr(result)


def test_no_database_access(monkeypatch):
    import sqlite3

    monkeypatch.setattr(
        sqlite3,
        "connect",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("db touched")),
    )
    session = _Session(_Resp(200, text=_section(_header(["Sep 2025"]))))
    result = probe("AAATECH", session=session)
    assert result["structure_classification"] == "EXPECTED_TABLE_MATCHED"
