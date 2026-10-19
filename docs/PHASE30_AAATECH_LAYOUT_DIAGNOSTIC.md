# Phase 30 — AAATECH Quarterly Table Layout Diagnostic

**Verdict: `LABEL-ONLY FIRST ROW OBSERVED — PARSER CHANGE NOT JUSTIFIED`**

*One authorized HTTP request, one symbol. No refresh, retry, bulk run, scheduler
job or production write.*

---

## 1. Baseline and preflight

| Check | Result |
|---|---|
| HEAD | `7a6a924` (= `origin/main`) |
| Branch | `main` |
| Working tree | clean and synchronized |
| MYRA writer processes | 0 |
| Listeners on 8000 / 3000 | none |
| `*.db-wal` total size | 0 bytes (checkpointed) |
| Phase 29 report | read |
| Diagnostic tool + three test suites | read |
| Table selection in `screener_parse.py` | read (`_quarterly_table`, `parse_quarterly`) |

All gates passed before the request. Reading `_quarterly_table`/`parse_quarterly`
first was essential: it establishes that the parser selects the section's first
table and then reads `table.find_all("tr")[0]` — a recursive search, so a
`<thead>` row *is* the row it reads. The layout question had to be framed against
that exact contract.

## 2. What was added, and why it required no production change

Phase 29 reported `header_cell_count = 0`, which conflated "one label-only cell"
with "a row that has no cells at all". Phase 30 resolves that from layout alone.

Added to `tools/probe_screener_once.py` (the diagnostic only):

| Field | Meaning |
|---|---|
| `row0_total_cell_count` | first-row cells **including** the label column, or `null` |
| `table_row_count` | total rows of the selected table, or `null` |
| `thead_present` / `thead_row_count` | wrapper presence, and its row count when present |
| `tbody_present` / `tbody_row_count` | wrapper presence, and its row count when present |
| `period_shaped_markup_outside_tr` | whether period-shaped markup sits outside any row |
| `layout_classification` | one of a fixed seven-value set |

`layout_classification` is drawn from: `NO_QUARTERLY_SECTION`, `NO_TABLE`,
`TABLE_WITHOUT_ROWS`, `TABLE_WITH_LABEL_ONLY_FIRST_ROW`,
`TABLE_WITH_STRUCTURAL_HEADER`, `PERIOD_SHAPED_MARKUP_OUTSIDE_ROWS`,
`AMBIGUOUS_LAYOUT`.

### A deliberate contract decision

`structure_classification` was **not** repurposed. Phase 28/29 published it as a
documented, tested allowlist about *period labels*; redefining it in Phase 30
would silently invalidate those reports. The layout question is therefore a
**second axis** in a new field, and both are reported side by side. Phase 29's
tests are unchanged and still pass.

### Measurement rules honoured

- Only the detected section and its selected table are inspected; an unrelated
  table elsewhere cannot influence any count.
- Rows are counted as `<tr>` elements whose nearest ancestor table is the
  selected one, so a nested table's rows are not attributed to it.
- Presence and row counts are **separate** facts: `thead_present=True` with
  `thead_row_count=0` (present but empty) differs from `thead_present=False` with
  `thead_row_count=None` (absent).
- `thead`/`tbody` are read as **direct children** only, so stray nesting is not
  mistaken for a wrapper.
- `period_shaped_markup_outside_tr` is narrow: text inside a `<tr>` is skipped,
  the token is validated through the parser's own `report_date_sort_key`, and
  only a boolean leaves the helper. It is scoped to the section.
- `None` means "not available", never a zero that would read as "measured none".

`myra_app/` was not modified. `screener_parse.py` and the fetcher are byte-for-byte
unchanged, so ingestion behaviour cannot have shifted.

## 3. Mocked tests, run before the request

51 new tests in `tests/test_screener_probe_layout.py`, all mocked, all run before
the live request. Coverage against the brief:

| Required case | Test |
|---|---|
| Section absent | `test_section_absent` |
| Section present, table absent | `test_section_present_but_table_absent` |
| Table with zero rows | `test_table_present_with_zero_rows` |
| First row with one label-only cell | `test_first_row_with_one_label_only_cell` |
| First row with multiple cells | `test_first_row_with_multiple_cells` |
| `thead` absent / present / present-but-empty | `test_thead_absent`, `test_thead_present_counts_its_rows`, `test_thead_present_but_empty_is_not_absent` |
| `tbody` absent / present / present-but-empty | `test_tbody_absent`, `test_tbody_present_counts_its_rows`, `test_empty_tbody_present_with_zero_rows` |
| Empty and non-empty sections | `test_empty_tbody_present_with_zero_rows`, `test_non_empty_table_section_counts_rows_and_cells` |
| Period markup outside rows | `test_period_markup_outside_rows_is_detected`, `…_classifies_when_the_header_lacks_labels` |
| Unrelated table must not count | `test_date_shaped_markup_in_an_unrelated_table_does_not_count` |
| Ambiguous markup | `test_multiple_tables_in_the_section_is_ambiguous`, `test_several_quarterly_sections_is_ambiguous` |
| Non-200 | `test_non_200_omits_the_layout_fields` |
| Transport exception with fake URL and token | `test_exception_message_cannot_reach_the_result` |
| Secrets in content and cookies | `test_no_cell_text_value_or_url_reaches_the_result` |
| No raw text / dates / values / URLs | same, plus `test_layout_counts_are_small_or_null` |
| No database access | `test_no_database_access` |
| Exactly one request, no retry | `test_exactly_one_request_and_no_retry`, `…_even_when_detection_raises` |

Two tests pin the reasoning itself: `test_row0_total_minus_one_equals_the_phase29_header_count`
(consistency between the two axes) and
`test_first_row_with_no_cells_at_all_is_ambiguous` (a zero-cell row is *not*
reported as label-only).

## 4. The single diagnostic request

| | |
|---|---|
| Symbol | `AAATECH` |
| UTC timestamp | `2026-10-19T18:07:16.108328+00:00` |
| HTTP status | `200` |
| Response bytes | `81,852` |
| Elapsed | `2.947 s` |
| Retry | none (`completed_without_retry: true`) |

```json
{
  "quarterly_section_detection": "present",
  "quarterly_table_present": true,
  "quarterly_table_count": 1,
  "row0_total_cell_count": 1,
  "table_row_count": 13,
  "thead_present": true,
  "thead_row_count": 1,
  "tbody_present": true,
  "tbody_row_count": 12,
  "period_shaped_markup_outside_tr": false,
  "header_cell_count": 0,
  "quarterly_table_matched": false,
  "structure_classification": "TABLE_NO_PERIOD_LABELS",
  "layout_classification": "TABLE_WITH_LABEL_ONLY_FIRST_ROW"
}
```

The response body was inspected in memory only and not retained. Exactly one
request was made; it was issued as `python -m tools.probe_screener_once AAATECH`
because invoking the file by path leaves the repository root off `sys.path` (as
found in Phase 29).

## 5. What the evidence establishes — and what it does not

### Establishes

- **The first row has exactly one cell.** `row0_total_cell_count = 1`. This is the
  fact Phase 29 could not produce: the row is a **label-only** row, not a row with
  no cells. That single cell means the parser has nothing after the label column,
  so no date can be read from row 0 whatever that cell contains — this explains
  the persistent parse miss by structure alone.
- **The table's wrapper is standard and complete.** `thead` (1 row) + `tbody`
  (12 rows) = `table_row_count = 13`. Since the parser's `find_all("tr")` is
  recursive, it traverses `thead` and reads the same first row. **The wrapper is
  therefore benign, not the cause**: it is exactly the structure the parser
  already handles.
- **No period-shaped markup exists outside rows** (`period_shaped_markup_outside_tr
  = false`), scoped to the section; and **no row carries a period-like label in
  its non-first columns** (`structure_classification = TABLE_NO_PERIOD_LABELS`).
- **The page is deterministic.** 81,852 bytes for a fourth time, from a fourth
  independent request days apart.

### Does not establish

- **What the single first-row cell contains.** A title, a period, or anything else
  are all consistent with the observation; the tool does not read cell text by
  design.
- **Where the page's reporting periods live, if anywhere.** Given no period
  markup outside rows and none in any row's non-first columns, the only remaining
  place is the **label column** (`<th>`/`<td>` at index 0 of each row) — which the
  diagnostic does not read, because it deliberately mirrors the parser's contract
  rather than exceeding it. This is a scope limit, stated as such, not a finding.
- **Anything site-wide.** One symbol cannot show that the 118 other parked symbols
  share this shape, and the phase forbids inferring why they failed.
- **Whether other symbols even have this layout.** Not tested.

### Cautious interpretation

For this symbol, on this day, the quarterly table does not present period labels
where the parser reads them, and the first row cannot. The evidence is fully
consistent with periods living in the label column, but that is **not proven** and
is deliberately outside both the parser's and this diagnostic's field of view.
Nothing here indicates that the table wrapper is at fault.

## 6. Production parser, fetcher and databases untouched

- `myra_app/` diff is empty — `screener_parse.py`, the fetcher and the ingestion
  path are byte-for-byte unchanged.
- No refresh, ingestion, EOD sync, backfill, enrichment or lake build ran; the
  network cache was not populated. No source priority, ranking, scoring, freshness
  threshold, date validation or safeguard was changed;
  `MYRA_V25_RANKING_INPUTS` was not enabled.
- Production data re-read (read-only `mode=ro`) is identical to the Phase 29
  baseline:

| Metric | Value |
|---|---|
| `quarterly_results` rows | 10,798 |
| `fundamentals` rows | 3,951 |
| Placeholder `Q-n` report dates | 0 |
| NULL/blank `report_date` | 0 |
| AAATECH quarterly rows | 0 |
| AAATECH `last_updated` | `2026-10-17T01:46:29.474666` (unchanged) |
| Parked symbols | 119 (AAATECH present) |

## 7. Tests, guards, and validation

| Suite | Result |
|---|---|
| Phase 30 (new) | 51 passed |
| Phase 27 + 28 + 29 (unchanged) | 119 passed |
| Four probe suites together | 170 passed |
| **Full suite** | **2061 passed, 0 failed** (5:44) |

Internally consistent: 2010 + 51. `black` and `flake8` clean; the schema-drift and
Performance Guard pre-commit hooks passed during validation.

## 8. Recommendation — no separate parser-contract investigation is justified

**Do not change the parser. Keep AAATECH parked.**

The evidence points away from a parser bug. The wrapper is standard, and the
parser already traverses it correctly. What this page does not do — for this
symbol — is present reporting periods where the parser reads them, and the first
row structurally cannot. Optionally reading the label column would not fix that:
assigning periods from row position or cell order is exactly the inference this
task forbids and the repository treats as unsafe.

If the operator wants to pursue this further, it is a **source-evidence** question
rather than a parser question — but that would need its own authorization, and
answering it would require reading cell text, which the current privacy boundary
deliberately prevents. On the evidence available, the correct outcome is:
AAATECH stays parked, and no parser change is made.

**Hard stop.** No further request, no second symbol, no parser or fetcher change,
no reporting date accepted or fabricated, no refresh, no Phase 31 without new
authorization.
