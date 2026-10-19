# Phase 29 — AAATECH Quarterly Table Structure and Period-Label Diagnostic

**Verdict: `TABLE PRESENT WITHOUT PERIOD-LIKE HEADERS — DATE CONTRACT STILL OPEN`**

*One authorized HTTP request, one symbol. No refresh, retry, bulk run, scheduler
job or production write.*

---

## 1. Baseline and preflight

| Check | Result |
|---|---|
| HEAD | `12085e0` (= `origin/main`) |
| Branch | `main` |
| Working tree | clean and synchronized |
| MYRA writer processes | 0 |
| Listeners on 8000 / 3000 | none |
| `*.db-wal` total size | 0 bytes (checkpointed) |
| Phase 28 report | read |
| Diagnostic tool + both test suites | read |
| Production parser + date helper | read (`screener_parse.py`) |

The preflight gates were all satisfied before any request. In particular the
production parser and the existing period-label recogniser were read first, so
the new observations mirror the contract they describe rather than inventing a
second one.

## 2. What was added, and why it required no production change

Phase 28 reported `quarterly_header_has_period_label = false` for two opposite
situations — "the section has no `<table>`" and "a table exists but its header
row has no period labels". Phase 29 separates them.

Added to `tools/probe_screener_once.py` (the diagnostic only):

| Field | Meaning |
|---|---|
| `quarterly_table_present` | a `<table>` exists inside the detected section |
| `quarterly_table_count` | how many tables are in that section |
| `header_cell_count` | cells in row 0 **after** the row-label column, or `null` |
| `recognized_period_header_count` | row-0 labels the existing recogniser accepts |
| `mon_year_header_count` | row-0 labels matching strict *Month YYYY* |
| `iso_year_month_header_count` | row-0 labels matching strict `YYYY-MM` (month 01–12) |
| `structure_classification` | one of a fixed six-value set |

`structure_classification` is drawn from: `SECTION_ABSENT`, `NO_TABLE`,
`TABLE_NO_PERIOD_LABELS`, `TABLE_PERIOD_LABELS_IN_UNEXPECTED_STRUCTURE`,
`EXPECTED_TABLE_MATCHED`, `AMBIGUOUS`.

Design points required by the brief:

- **Table presence is determined independently of header matching.** A table with
  no usable header is still a table.
- **Header row definition is fixed and mirrored from `parse_quarterly`** (row 0,
  cells after the first). Nothing silently selects a different row: if more than
  one row carries period labels, or more than one table exists in the section,
  the result is `AMBIGUOUS`.
- **Two independent strict shape tests** (`Mon YYYY`, `YYYY-MM`) run alongside the
  parser's own recogniser. Months are validated, so `2026-00` and `2026-13` are
  rejected.
- **Unavailable counts are `null`, never `0`.** A measured zero (a header row that
  exists but has no period cells) and an unavailable one (no table at all) are
  distinguishable.
- **No cell text, date, value, row label or HTML has any route into the output.**
  Results are assembled from a fixed allowlist.

`myra_app/` was not modified. `screener_parse.py` and the fetcher are byte-for-byte
unchanged, so ingestion behaviour cannot have shifted.

### A documented discovery about the existing flag

`parse_quarterly` accepts **any** non-empty header cell as a column — it never
checks that a cell is a date (`screener_parse.py:228-231`). So
`quarterly_table_matched` is `true` for a header of `"Column A"`, `"Column B"` just
as for `"Sep 2025"`. The new `structure_classification` is therefore driven by the
**period-label structure**, not by that flag, and a test pins the two apart. Both
are reported side by side; neither implies the other.

## 3. Mocked tests, run before the request

56 new tests in `tests/test_screener_probe_table_structure.py`, all mocked, all run
before the live request. Coverage against the brief:

| Required case | Test |
|---|---|
| Quarterly section absent | `test_section_absent` |
| Section present, no table | `test_section_present_with_no_table` |
| Table present, no header cells | `test_table_present_with_no_rows`, `test_table_header_with_no_cells_after_the_label_column` |
| Header without period labels | `test_table_header_without_period_like_labels` |
| `Mon YYYY` labels | `test_table_with_mon_year_labels` |
| Valid ISO labels | `test_table_with_iso_year_month_labels` |
| Invalid ISO `00` / `13` | `test_invalid_iso_months_are_not_counted` |
| Labels in a different row | `test_period_labels_in_a_different_row_are_unexpected_structure` |
| Multiple header rows / ambiguous markup | `test_multiple_period_label_rows_are_ambiguous`, `test_multiple_tables_in_the_section_are_ambiguous`, `test_several_quarterly_sections_are_ambiguous`, `test_malformed_markup_never_raises` |
| Unrelated page tables must not count | `test_unrelated_page_tables_do_not_affect_the_counts` |
| Non-200 and transport failure | `test_non_200_omits_the_structural_fields`, `test_transport_failure_omits_the_structural_fields` |
| Secrets in body, cookies, exception | `test_no_cell_text_value_or_url_reaches_the_result`, `test_exception_message_cannot_reach_the_result` |
| No raw text / values / URLs | same, plus `test_counts_are_small_or_null_never_a_value` |
| No database access | `test_no_database_access` |
| Exactly one request, no retry | `test_exactly_one_request_and_no_retry`, `test_exactly_one_request_even_when_detection_raises` |

The privacy boundary remains structural rather than incidental: a test plants a
secret in the body and in `Set-Cookie` and asserts the label `Sep 2025`, the value
`123456.78` and the row label `Revenue` are all equally absent from the result.

## 4. The single diagnostic request

| | |
|---|---|
| Symbol | `AAATECH` |
| UTC timestamp | `2026-10-19T17:37:05.217739+00:00` |
| HTTP status | `200` |
| Response bytes | `81,852` |
| Elapsed | `3.597 s` |
| Retry | none (`completed_without_retry: true`) |

```json
{
  "quarterly_section_detection": "present",
  "quarterly_section_present": true,
  "quarterly_table_present": true,
  "quarterly_table_count": 1,
  "header_cell_count": 0,
  "recognized_period_header_count": 0,
  "mon_year_header_count": 0,
  "iso_year_month_header_count": 0,
  "quarterly_table_matched": false,
  "structure_classification": "TABLE_NO_PERIOD_LABELS"
}
```

**Request discipline.** The first invocation attempt failed with
`ModuleNotFoundError: No module named 'myra_app'` at the import, *before* the
request was issued — so it spent no request. The command was corrected to
`python -m tools.probe_screener_once AAATECH` and exactly one request was then
made. The response body was inspected in memory only and not retained.

## 5. What the evidence establishes — and what it does not

### Establishes

- **A quarterly section exists and contains a table.** `quarterly_table_count = 1`.
  Phase 28's remaining ambiguity is resolved: this is *not* the "no table" case.
- **The table's header row has zero cells after the row-label column**
  (`header_cell_count = 0`). The parser reads exactly that row, so it has nothing
  to treat as a column — which is a sufficient explanation for
  `quarterly_table_matched = false`.
- **No row in the table carries a period-like label in its non-first columns.**
  Had one existed, the classification would have been
  `TABLE_PERIOD_LABELS_IN_UNEXPECTED_STRUCTURE`. It is `TABLE_NO_PERIOD_LABELS`.
  So this is not merely "the labels are in the wrong row": they are absent from
  every row's non-label cells.
- **The page is deterministic.** 81,852 bytes for a third time, from a third
  independent request days apart.

### Does not establish

- **Why the header row has no cells.** A single-cell title row, a row whose only
  cell is the label, labels placed in the *first* cell of rows, labels outside
  `<tr>` elements, and genuinely absent labels are all consistent with
  `header_cell_count = 0`. Distinguishing them requires reading cell text, which
  this tool refuses to do by design.
- **How many cells row 0 actually contains.** `header_cell_count` counts cells
  *after* the label column, so both "one label-only cell" and "no cells at all"
  report `0`.
- **Anything site-wide.** One symbol cannot establish that the other 118 parked
  symbols fail the same way. `AAATECH`'s own record is unchanged and it remains
  correctly parked.

### Cautious interpretation

The failure is not a missing page and not a missing table. It is that the table
the parser finds does not present a period-label header in row 0. Whether that is
a page-structure change or a per-symbol data peculiarity cannot be decided from
one response, and the honest reading is the narrower one: for this symbol, on
this day, the header the parser reads is empty.

## 6. No refresh, no retry, no fallback chain, no production write

Production data was re-read (read-only `mode=ro` connections) and is identical to
the Phase 28 baseline:

| Metric | Value |
|---|---|
| `quarterly_results` rows | 10,798 |
| `fundamentals` rows | 3,951 |
| Placeholder `Q-n` report dates | 0 |
| NULL/blank `report_date` | 0 |
| AAATECH quarterly rows | 0 |
| AAATECH `fundamentals` rows | 1 |
| AAATECH `last_updated` | `2026-10-17T01:46:29.474666` (unchanged) |
| Parked symbols (`fundamentals_park_state`) | 119 (AAATECH present) |

No refresh, ingestion, EOD sync, backfill, enrichment or lake build ran. The
network cache was not populated. No source priority, ranking, scoring, freshness
threshold or date-validation setting was touched, and `MYRA_V25_RANKING_INPUTS`
was not enabled.

## 7. Tests, guards, and validation

| Suite | Result |
|---|---|
| Phase 29 (new) | 56 passed |
| Phase 27 + Phase 28 (unchanged) | 63 passed |
| Three probe suites together | 119 passed |
| **Full suite** | **2010 passed, 0 failed** (5:56) |

The full-suite total is internally consistent: 1954 + 56. `black` and `flake8`
are clean on the changed files; the schema-drift and Performance Guard pre-commit
hooks passed during validation.

## 8. Recommended next step — narrowly scoped

Do **not** change the parser yet. The evidence justifies only the next observation,
and it must stay value-free.

One follow-up request for the same symbol, capturing **layout facts without text**:

- total cells in row 0 (including the label column) — separates "one label-only
  cell" from "no cells";
- whether the table uses `<thead>`/`<tbody>` and the row counts in each;
- total row count of the table;
- whether any element outside `<tr>` within the section contains period-shaped
  markup.

Only if that shows a systematic wrapper structure — for example, period labels
that live for every symbol in a separate element the parser never reads — would a
parser change be justified. Such a change alters ingestion, so it needs its own
explicit authorization and its own bounded validation (fixture-backed tests plus a
one-symbol dry run), not an inference from a single page.

If row 0 turns out to be a genuine single-cell title row that varies per symbol,
then AAATECH remaining parked is the correct outcome and no parser change is
warranted.

## 9. Final state

`tools/probe_screener_once.py` extended (additive), one new test file, this report,
and a session-history entry. `myra_app/` untouched. Committed and pushed with
CI/CodeQL verified.

**Hard stop.** No further request, no second symbol, no parser change, no refresh,
no Phase 30 without new authorization.
