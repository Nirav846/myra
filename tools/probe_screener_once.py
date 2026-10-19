"""Single-symbol, single-request Screener.in diagnostic (Phases 27-29).

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

On a 200, ``structure_classification`` states which of these was observed:

* ``EXPECTED_TABLE_MATCHED`` -- row 0 carries period labels and production
  parsing matched.
* ``TABLE_PERIOD_LABELS_IN_UNEXPECTED_STRUCTURE`` -- period-like labels exist,
  but not where the parser reads them.
* ``TABLE_NO_PERIOD_LABELS`` -- a table exists, but no row carries period labels.
* ``NO_TABLE`` -- the section exists but holds no table.
* ``SECTION_ABSENT`` -- no quarterly section was found.
* ``AMBIGUOUS`` -- the evidence does not identify one authoritative structure.

A matching label shape is a structural observation only. It is never a period
assigned to a financial row.

``layout_classification`` is a second, separate axis: it describes the table's
layout from element and cell counts alone -- ``NO_QUARTERLY_SECTION``,
``NO_TABLE``, ``TABLE_WITHOUT_ROWS``, ``TABLE_WITH_LABEL_ONLY_FIRST_ROW``,
``TABLE_WITH_STRUCTURAL_HEADER``, ``PERIOD_SHAPED_MARKUP_OUTSIDE_ROWS`` or
``AMBIGUOUS_LAYOUT``. ``structure_classification`` answers "are period labels
where the parser reads them?"; ``layout_classification`` answers "what shape is
the first row, and does the table use a wrapper structure?".

A single symbol cannot establish the failure distribution across a whole run.
"""

from __future__ import annotations

import json
import re
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
        # Phase 28: the section/header observations are booleans and an
        # enumerated detection state -- never header text.
        "quarterly_section_present",
        "quarterly_header_has_period_label",
        "quarterly_section_detection",
        # Phase 29: structural counts (small integers or null) and one
        # enumerated classification. No cell text, dates or values.
        "quarterly_table_present",
        "quarterly_table_count",
        "header_cell_count",
        "recognized_period_header_count",
        "mon_year_header_count",
        "iso_year_month_header_count",
        "structure_classification",
        # Phase 30: layout-only observations -- element presence and row/cell
        # counts, one enumerated layout classification, and one boolean. Still
        # no cell text, no dates, no values.
        "row0_total_cell_count",
        "thead_present",
        "thead_row_count",
        "tbody_present",
        "tbody_row_count",
        "table_row_count",
        "period_shaped_markup_outside_tr",
        "layout_classification",
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


def _is_period_label(text: str) -> bool:
    """Whether ``text`` is a real reporting-period label.

    Recognition uses the parser's *own* ``report_date_sort_key`` rather than
    anything introduced here, so "a period label" means exactly what production
    parsing means by one. That helper returns ``(0, 0)`` for anything it cannot
    read, and real labels always carry a month of 1-12, so a malformed header
    such as "2026-0" is correctly rejected rather than counted.

    Nothing is inferred from values, column position, the current date, or a
    ``Q-1``-style convention: only an explicitly recognised label qualifies.
    """
    from myra_app.data_sources.screener_parse import report_date_sort_key

    year, month = report_date_sort_key(text)
    return year > 0 and 1 <= month <= 12


#: Strict ``Mon YYYY``: a month name followed by a four-digit year.
_MONTH_YEAR_RE = re.compile(r"^[A-Za-z]{3,9}\s+\d{4}$")

#: Strict ``YYYY-MM``. The month is validated separately, so a syntactically
#: plausible but impossible ``2026-00`` or ``2026-13`` is refused.
_ISO_YEAR_MONTH_RE = re.compile(r"^(\d{4})-(\d{2})$")


def _is_mon_year_label(text: str) -> bool:
    """Whether ``text`` is a strict month-name + four-digit-year label.

    An *independent* shape test, not the parser's recogniser: it reports what
    the header looks like, so it must not borrow the recogniser's leniency (the
    recogniser also accepts ISO). The month name is resolved through the
    parser's own ``report_date_sort_key`` so a non-month such as ``Foo 2025``
    cannot pass.
    """
    from myra_app.data_sources.screener_parse import report_date_sort_key

    candidate = text.strip()
    if not _MONTH_YEAR_RE.match(candidate):
        return False
    year, month = report_date_sort_key(candidate)
    return year > 0 and 1 <= month <= 12


def _is_iso_year_month_label(text: str) -> bool:
    """Whether ``text`` is a strict ``YYYY-MM`` label with a real month.

    ``2026-00`` and ``2026-13`` are rejected: the shape is plausible, the month
    is not.
    """
    match = _ISO_YEAR_MONTH_RE.match(text.strip())
    if match is None:
        return False
    return 1 <= int(match.group(2)) <= 12


def _row_has_period_label(row) -> bool:
    """Whether any cell after the row-label column is a period label."""
    cells = row.find_all(["th", "td"])
    return any(_is_period_label(cell.get_text().strip()) for cell in cells[1:])


#: Period-shaped tokens for the layout scan. The month-name form is validated
#: afterwards, so a phrase such as "Quarterly 2025" cannot pass as a period.
_PERIOD_TOKEN_RE = re.compile(r"\b\d{4}-(?:0[1-9]|1[0-2])\b|\b[A-Za-z]{3,9}\s+\d{4}\b")


def _direct_child(table, name: str):
    """The first direct child element of ``table`` named ``name``, else ``None``.

    Immediate children only, on purpose: ``<thead>``/``<tbody>`` are meaningful
    only as direct children of the table, and a descendant search would quietly
    accept stray nesting as though it were a structural wrapper.
    """
    for child in table.children:
        if getattr(child, "name", None) == name:
            return child
    return None


def _text_has_period_shape(text: str) -> bool:
    """Whether ``text`` holds a valid period-shaped token -- returning only a bool.

    Nothing but a boolean leaves this function: the token, and the text around
    it, are never stored or reported. The month is validated through the parser's
    own ``report_date_sort_key``, so ``2026-00`` and the phrase "Quarterly 2025"
    are both rejected rather than counted.
    """
    from myra_app.data_sources.screener_parse import report_date_sort_key

    for token in _PERIOD_TOKEN_RE.findall(text):
        year, month = report_date_sort_key(token)
        if year > 0 and 1 <= month <= 12:
            return True
    return False


def _period_shaped_markup_outside_rows(section) -> bool:
    """Whether period-shaped markup sits outside any table-row element.

    Scoped to the detected section, so an unrelated table elsewhere on the page
    cannot influence the answer. Text inside a ``<tr>`` is skipped; everything
    else in the section -- a ``<caption>``, a stray heading, text between rows --
    is checked. A boolean is all that escapes; no fragment is retained.
    """
    for node in section.find_all(string=True):
        parent = node.parent
        if parent is not None and (
            getattr(parent, "name", None) == "tr" or parent.find_parent("tr")
        ):
            continue
        if _text_has_period_shape(str(node)):
            return True
    return False


def _locate_section(soup):
    """Return ``(section, state)``, state in present/absent/indeterminate.

    ``#quarters`` wins, matching ``_quarterly_table``. Otherwise a section whose
    text mentions quarterly results is used; if more than one does, the answer
    is ``indeterminate`` rather than a guess.
    """
    section = soup.find(id="quarters")
    if section is not None:
        return section, "present"
    candidates = [
        node
        for node in soup.find_all("section")
        if "quarterly" in node.get_text().lower()
    ]
    if not candidates:
        return None, "absent"
    if len(candidates) > 1:
        return None, "indeterminate"
    return candidates[0], "present"


def _expected_table_matched(html: str) -> bool:
    """Whether production parsing found quarterly columns in this response.

    Unchanged in meaning from the original diagnostic: it calls the real
    ``parse_company_page``, so the answer is what the parser did rather than a
    re-implementation of it.
    """
    from myra_app.data_sources.screener_parse import parse_company_page

    try:
        return bool(parse_company_page(html)["quarterly"])
    except Exception:  # noqa: BLE001 - a parse failure is itself an observation
        return False


#: Returned when structure inspection itself fails. Every count is ``None`` --
#: "not available" -- never a zero that would read as "found none".
_SAFE_DEFAULT = {
    "section": "indeterminate",
    "header_has_period_label": False,
    "table_present": False,
    "table_count": 0,
    "header_cell_count": None,
    "recognized_period_header_count": None,
    "mon_year_header_count": None,
    "iso_year_month_header_count": None,
    "expected_table_matched": False,
    "classification": "AMBIGUOUS",
    # Phase 30 layout observations.
    "row0_total_cell_count": None,
    "table_row_count": None,
    "thead_present": None,
    "thead_row_count": None,
    "tbody_present": None,
    "tbody_row_count": None,
    "period_shaped_markup_outside_tr": None,
    "layout_classification": "AMBIGUOUS_LAYOUT",
}


def detect_quarterly_structure(html: str) -> dict:
    """Classify the quarterly section, its table and its header, value-free.

    Reports three facts that the original single boolean collapsed together:
    whether the quarterly section exists, whether a table exists inside it, and
    whether that table's header carries labels the parser recognises. It adds
    structural *counts* describing that header and one ``classification`` from a
    small fixed set:

    ``SECTION_ABSENT``
        No quarterly section was found in this response.
    ``NO_TABLE``
        The section exists but holds no table.
    ``TABLE_NO_PERIOD_LABELS``
        A table exists, but no row carries a period-like label.
    ``TABLE_PERIOD_LABELS_IN_UNEXPECTED_STRUCTURE``
        Period-like labels exist, but not where the parser reads them (its
        header is row 0).
    ``EXPECTED_TABLE_MATCHED``
        Row 0 carries period labels *and* production parsing matched.
    ``AMBIGUOUS``
        The evidence does not identify one authoritative structure.

    Counts describe the parser's own header row (row 0, excluding the row-label
    column) and are ``None`` when that row is not reliably available. A matching
    shape is a structural observation only: it is never treated as a period that
    could be assigned to a financial row.

    ``expected_table_matched`` is reported alongside the classification but is a
    weaker signal: ``parse_quarterly`` accepts any non-empty header cell as a
    column, so a header of "Column A" matches while carrying no period at all.
    The classification is driven by the period-label structure, not by that flag.

    ``layout_classification`` is a separate axis describing layout rather than
    labels. ``row0_total_cell_count`` counts the first row's cells *including*
    the label column, which distinguishes a label-only first row (1) from a row
    with no cells at all (0) -- a distinction Phase 29's count could not make.
    ``thead_present``/``tbody_present`` report element presence, while their row
    counts stay ``None`` when the element is absent, so "present but empty"
    (``True``, 0) differs from "absent" (``False``, ``None``).
    ``period_shaped_markup_outside_tr`` reports whether period-shaped markup
    exists outside any row, without ever returning the token.
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    section, state = _locate_section(soup)

    result = {
        "section": state,
        "header_has_period_label": False,
        "table_present": False,
        "table_count": 0,
        "header_cell_count": None,
        "recognized_period_header_count": None,
        "mon_year_header_count": None,
        "iso_year_month_header_count": None,
        "expected_table_matched": False,
        "classification": "SECTION_ABSENT",
        # Phase 30 layout observations. ``None`` means "not available", never a
        # zero that would read as "measured none".
        "row0_total_cell_count": None,
        "table_row_count": None,
        "thead_present": None,
        "thead_row_count": None,
        "tbody_present": None,
        "tbody_row_count": None,
        "period_shaped_markup_outside_tr": None,
        "layout_classification": "NO_QUARTERLY_SECTION",
    }

    if state == "indeterminate":
        result["classification"] = "AMBIGUOUS"
        result["layout_classification"] = "AMBIGUOUS_LAYOUT"
        return result
    if state == "absent":
        return result

    result["expected_table_matched"] = _expected_table_matched(html)

    tables = section.find_all("table")
    result["table_count"] = len(tables)
    result["table_present"] = bool(tables)

    # Counts are only meaningful with exactly one table in play: with several
    # there is no single "relevant header row", so the counts stay None.
    rows = []
    if len(tables) == 1:
        table = tables[0]
        # Rows of *this* table only: a row inside a nested table is not ours.
        rows = [
            row for row in table.find_all("tr") if row.find_parent("table") is table
        ]
        result["table_row_count"] = len(rows)
        if rows:
            row0_cells = rows[0].find_all(["th", "td"])
            # Phase 30: the *total* first-row cell count including the label
            # column. This is what separates "one label-only cell" from a first
            # row that genuinely has no cells -- Phase 29's count conflated both.
            result["row0_total_cell_count"] = len(row0_cells)
            labels = [cell.get_text().strip() for cell in row0_cells[1:]]
            result["header_cell_count"] = len(labels)
            result["recognized_period_header_count"] = sum(
                _is_period_label(text) for text in labels
            )
            result["mon_year_header_count"] = sum(
                _is_mon_year_label(text) for text in labels
            )
            result["iso_year_month_header_count"] = sum(
                _is_iso_year_month_label(text) for text in labels
            )
            result["header_has_period_label"] = (
                result["recognized_period_header_count"] > 0
            )
        thead = _direct_child(table, "thead")
        tbody = _direct_child(table, "tbody")
        # Presence and row count are separate facts, so "present but empty"
        # (True, 0) stays distinguishable from "absent" (False, None).
        result["thead_present"] = thead is not None
        result["thead_row_count"] = (
            len(thead.find_all("tr")) if thead is not None else None
        )
        result["tbody_present"] = tbody is not None
        result["tbody_row_count"] = (
            len(tbody.find_all("tr")) if tbody is not None else None
        )
        result["period_shaped_markup_outside_tr"] = _period_shaped_markup_outside_rows(
            section
        )

    # Classification follows the *structure* the period labels reveal, not
    # ``expected_table_matched``: that flag is weak, because ``parse_quarterly``
    # accepts any non-empty header cell as a column (it never checks that a cell
    # is a date). So a header of "Column A" matches too. The two are reported
    # side by side on purpose; neither implies the other.
    if not tables:
        result["classification"] = "NO_TABLE"
        result["layout_classification"] = "NO_TABLE"
    elif len(tables) > 1:
        # More than one table in the section: which one is authoritative is not
        # determinable, so neither is the structure.
        result["classification"] = "AMBIGUOUS"
        result["layout_classification"] = "AMBIGUOUS_LAYOUT"
    else:
        label_rows = [
            index for index, row in enumerate(rows) if _row_has_period_label(row)
        ]
        if len(label_rows) > 1:
            # Period labels in more than one row: which one the parser would
            # read is not determinable, so neither is the structure.
            result["classification"] = "AMBIGUOUS"
        elif result["header_has_period_label"] and result["expected_table_matched"]:
            result["classification"] = "EXPECTED_TABLE_MATCHED"
        elif label_rows:
            result["classification"] = "TABLE_PERIOD_LABELS_IN_UNEXPECTED_STRUCTURE"
        else:
            result["classification"] = "TABLE_NO_PERIOD_LABELS"

        # Phase 30 layout axis, decided from cell and element counts alone.
        row0_total = result["row0_total_cell_count"]
        if not rows:
            result["layout_classification"] = "TABLE_WITHOUT_ROWS"
        elif row0_total == 0:
            # A row element with no cells is neither a header nor a label row,
            # and no documented classification describes it: keep the ambiguity.
            result["layout_classification"] = "AMBIGUOUS_LAYOUT"
        elif row0_total == 1:
            result["layout_classification"] = "TABLE_WITH_LABEL_ONLY_FIRST_ROW"
        elif (
            result["period_shaped_markup_outside_tr"]
            and not result["header_has_period_label"]
        ):
            result["layout_classification"] = "PERIOD_SHAPED_MARKUP_OUTSIDE_ROWS"
        else:
            result["layout_classification"] = "TABLE_WITH_STRUCTURAL_HEADER"
    return result


def _safe_detect(html: str) -> dict:
    """``detect_quarterly_structure`` that cannot raise and cannot leak.

    Any failure is reported as indeterminate with null counts. The exception
    message is deliberately discarded: it can echo page markup.
    """
    try:
        return detect_quarterly_structure(html)
    except Exception:  # noqa: BLE001 - an unreachable structure is an answer
        return dict(_SAFE_DEFAULT)


def classify_response(response, elapsed_seconds: float, symbol: str) -> dict:
    """Turn a 200-or-other HTTP response into a sanitised result.

    The response is inspected in memory only: its byte length is recorded, and
    the quarterly structure is classified to fill the structural fields. No part
    of the body is returned, and for a non-200 no body is read at all.
    """
    status = getattr(response, "status_code", None)
    out = _result(
        symbol=symbol,
        http_status=status,
        elapsed_seconds=elapsed_seconds,
        completed_without_retry=True,
    )
    if status == 200:
        structure = _safe_detect(response.text)
        out["outcome"] = (
            "INDETERMINATE_STRUCTURE"
            if structure["section"] == "indeterminate"
            or structure["classification"] == "AMBIGUOUS"
            or structure["layout_classification"] == "AMBIGUOUS_LAYOUT"
            else "HTTP_RESPONSE"
        )
        out["response_bytes"] = len(response.content)
        out["quarterly_table_matched"] = structure["expected_table_matched"]
        # The section/table/header observations. On an indeterminate or
        # ambiguous result the booleans stay False rather than asserting
        # absence; the detection and classification fields carry the ambiguity
        # so nothing is claimed that was not observed.
        out["quarterly_section_detection"] = structure["section"]
        out["quarterly_section_present"] = structure["section"] == "present"
        out["quarterly_header_has_period_label"] = structure["header_has_period_label"]
        out["quarterly_table_present"] = structure["table_present"]
        out["quarterly_table_count"] = structure["table_count"]
        out["header_cell_count"] = structure["header_cell_count"]
        out["recognized_period_header_count"] = structure[
            "recognized_period_header_count"
        ]
        out["mon_year_header_count"] = structure["mon_year_header_count"]
        out["iso_year_month_header_count"] = structure["iso_year_month_header_count"]
        out["structure_classification"] = structure["classification"]
        # Phase 30 layout observations: element presence and counts, one
        # enumerated classification, one boolean. Still value-free.
        out["row0_total_cell_count"] = structure["row0_total_cell_count"]
        out["table_row_count"] = structure["table_row_count"]
        out["thead_present"] = structure["thead_present"]
        out["thead_row_count"] = structure["thead_row_count"]
        out["tbody_present"] = structure["tbody_present"]
        out["tbody_row_count"] = structure["tbody_row_count"]
        out["period_shaped_markup_outside_tr"] = structure[
            "period_shaped_markup_outside_tr"
        ]
        out["layout_classification"] = structure["layout_classification"]
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
