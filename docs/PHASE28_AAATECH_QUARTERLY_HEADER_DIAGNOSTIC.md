# Phase 28 — AAATECH Quarterly Section and Period-Header Diagnostic

**Verdict: `SECTION PRESENT BUT PERIOD LABEL MISSING — DATE CONTRACT FOLLOW-UP NEEDED`**

Exactly one HTTP request was made, for one symbol. No refresh, retry, bulk
operation, fallback chain, scheduler job, or production write occurred.

---

## 1. Baseline and preflight

| Gate | Result |
|---|---|
| HEAD | `12827a7` — matches the expected baseline |
| Branch / tree | `main`, working tree clean at start |
| Synchronization | `HEAD == origin/main` |
| Phase 27 report + tool + tests reviewed | done |
| Fundamentals refresh / scheduler / ingestion writer | **none running** |
| Listeners on 8000 / 3000 | none |
| SQLite WAL files | 5 present, **all 0 bytes** (checkpointed, no active writer) |
| One request, no retry, no fallback, no DB access | verified by test (§3) |

A process scan initially appeared to show one MYRA writer. That was my own
PowerShell command line matching its own search pattern; re-checking with shell
processes excluded returned **nothing**, and no port was listening. Recorded
because it was observed, not because it was real.

`AAATECH` was reused as the subject, with its prior failure evidence already
established in Phase 27 (four recorded park-state failures, zero quarterly rows).

---

## 2. What was added, and why it required no production change

Phase 27 could report only `quarterly_table_matched`. That single boolean
conflated two situations with completely different follow-ups: no quarterly
section at all, versus a section whose header carries no period label.

Two observations were added **inside the diagnostic tool only**:

- `quarterly_section_present`
- `quarterly_header_has_period_label`

plus `quarterly_section_detection` (`present` / `absent` / `indeterminate`) as the
documented channel for an unclassifiable structure, which the brief explicitly
permits in place of forcing a false result.

Design decisions worth stating:

- **Period recognition reuses the parser's own `report_date_sort_key`** rather
  than introducing a second notion of "a date". Recognition additionally
  requires a month of 1–12 and a non-zero year, so a malformed header such as
  `2026-0` is rejected instead of counted.
- **Nothing is inferred.** Not from values, not from column position, not from
  the current date, and explicitly **not** from the `Q-1`…`Q-4` convention —
  which is a placeholder, and is asserted non-recognised by test.
- **Header inspection mirrors `parse_quarterly` exactly** (row 0, skipping the
  row-label column). Diagnosing any other row would not answer whether the
  parser's own contract is satisfied.
- **Section detection is independent of table presence**, because a section can
  exist while the table the parser wants does not — and reporting one as the
  other would aim the follow-up at the wrong target.
- The production parser was **not modified**. `myra_app/data_sources/screener_parse.py`
  is untouched, so ingestion behaviour is unaffected. The change set is purely
  additive: one extended tool, one new test file.

---

## 3. Mocked tests, run before the request

`tests/test_screener_probe_structure.py` — **37 new tests**, all mocked, no
network. Combined with the 26 Phase 27 tests: **63 passed**, all green before any
request was issued.

Coverage, against the brief's required list:

| Required case | Test |
|---|---|
| Section absent | `test_section_absent` |
| Section present, period label present | `test_section_present_with_period_label` |
| Section present, no period label | `test_section_present_without_period_label` |
| Ambiguous / malformed markup | `test_ambiguous_markup_is_indeterminate_not_false`, `test_malformed_markup_does_not_raise` |
| HTTP non-200 | `test_non_200_omits_the_structure_fields` |
| Transport exception with fake URL + token | `test_transport_failure_omits_the_structure_fields`, `test_exception_message_never_reaches_the_result` |
| Secret in body **and** cookies | `test_header_text_and_secrets_cannot_reach_the_result` |
| No financial values / raw header text in output | `test_no_raw_header_text_in_any_new_field`, `test_header_text_and_secrets_cannot_reach_the_result` |
| No database access | `test_no_database_access` (monkeypatched `sqlite3.connect` raises) |
| Exactly one request, no retries | `test_exactly_one_request_and_no_retry`, `test_exactly_one_request_even_when_detection_raises` |

The privacy boundary remains **structural**: `_result()` builds the output from a
fixed allowlist, so page content, header text, cookies and exception messages
have no route out. A test plants `SUPER_SECRET_COOKIE_VALUE` in both the body and
`Set-Cookie`, and asserts that the reporting label `Sep 2025`, a value
`123456.78`, and a row label `Revenue` are all equally absent from the output.

---

## 4. The single diagnostic request

| Field | Value |
|---|---|
| Symbol | `AAATECH` |
| UTC timestamp | `2026-10-19T16:59:39.321138+00:00` |
| HTTP status | **200** |
| Response byte length | **81,852** |
| Elapsed | 3.738 s |
| Completed without retry | true |
| `quarterly_section_present` | **true** |
| `quarterly_section_detection` | **present** |
| `quarterly_header_has_period_label` | **false** |
| `quarterly_table_matched` | **false** |

The response body was inspected in memory only. Nothing was saved, printed,
persisted, or used to update application data.

**Reproducibility note:** the byte length is **identical** to Phase 27
(81,852), from an independent request a day apart. The page is deterministic, so
this is a stable observation rather than a transient artifact.

---

## 5. What the evidence establishes — and what it does not

### Establishes

1. **A quarterly-results section exists on the page.** This was the open question
   from Phase 27, and it is answered: the missing table is **not** because the
   symbol's page lacks a quarterly section.
2. **The header the parser reads carries no recognisable reporting-period
   label.** Combined with `quarterly_table_matched = false`, the parser's
   date/header contract is not satisfied by the page as served.
3. **Therefore the next investigation belongs on the parser's side, not on
   per-symbol data availability.** Phase 27 could not tell these apart; this
   does.

### Does not establish

1. **It does not prove a site-wide change.** One symbol cannot. The 118 other
   failures are unexamined and this probe says nothing about them.
2. **It does not distinguish two structures inside "section present, label
   absent"** — the honest limitation of this probe. The detection returns
   `header_has_period_label = false` both when the section contains **no
   `<table>` at all**, and when a table exists whose row 0 has no period labels.
   Both lead to the same verdict and the same follow-up, so nothing is blocked —
   but the follow-up will need that distinction, and this output cannot supply
   it.
3. **It does not establish *why* the labels are missing** — whether Screener
   renders period labels in a different element, a different row, or a different
   format than `Mon YYYY`. No page content was retained, by design, so this is
   genuinely unknown rather than merely unstated.
4. **It does not contradict Phase 26's finding** that 884 symbols hold properly
   dated quarters. That the parser has worked historically and does not work for
   this page are compatible, and the absence of a write-timestamp on
   `quarterly_results` still prevents dating those rows.

### Cautious interpretation

The parser expects a header row whose cells read like `Sep 2025`. For this page,
whatever the parser reads there is not that. The conservative reading is
precisely the brief's: the reporting-period contract needs a bounded follow-up
using the observed structure. **No date was invented and no period was inferred.**

---

## 6. No refresh, no retry, no fallback chain, no production write

- **No refresh, ingestion, backfill, enrichment, EOD2 sync, lake build, or
  scheduler job ran.** No service was started or stopped.
- **Exactly one request**, for one symbol, with no retry and no second probe.
- **The multi-source fallback chain was not invoked**, so Google Finance was
  never reached and could not mask a Screener.in result.
- **No production database was written.** Every value is identical before and
  after:

| Check | Before | After |
|---|---|---|
| `fundamentals_sync_status` updated / failed | 229 / 119 | 229 / 119 |
| Parked symbols | 119 | 119 |
| `AAATECH` park fails | 4 | 4 |
| `quarterly_results` rows | 10,798 | 10,798 |
| `fundamentals` rows | 3,951 | 3,951 |
| `AAATECH` quarterly rows | 0 | 0 |
| Placeholder `Q-n` rows | 0 | 0 |
| `AAATECH.last_updated` | 2026-10-17T01:46:29 | 2026-10-17T01:46:29 |

- No reporting-date validation, source priority, scoring, freshness threshold, or
  safeguard was changed; the live DB guard was not modified or weakened.
- No response body, header, cookie, credential, query string, or full request URL
  was printed, saved, or logged.

---

## 7. Tests, guards, and validation

| Scope | Result |
|---|---|
| `tests/test_screener_probe_structure.py` (new) | **37 passed** |
| `tests/test_screener_probe.py` (Phase 27) | **26 passed** — unchanged by the extension |
| Focused + related suites (probe, structure, source classification, diagnostics, `test_fetch_screener`, `test_screener_parse`, access-log filter) | **113 passed** |
| **Full suite** | **1954 passed, 0 failed** (6 min 52 s) |
| black / flake8 / Performance Guard / schema-drift | clean |

The full suite total is internally consistent: 1,891 (last green baseline) + 26
(Phase 27) + 37 (Phase 28) = 1,954. It was run because nothing was contending for
the databases — no writer, no listener, all WALs zero-length — and unlike
Phase 27 it **completed**.

---

## 8. Recommended next step — narrowly scoped

One bounded follow-up, **not authorized here**:

**Establish what the parser actually reads in that header.** For the same symbol,
in a single request, capture only structural facts — never content:

1. Whether the detected quarterly section contains a `<table>` at all (the
   distinction §5.2 could not make).
2. If so, the **count of header cells** and the **count that a period recogniser
   accepts**, plus a boolean for whether any single cell *shape* matches a
   month-name-then-year pattern versus ISO `YYYY-MM`.

That distinguishes, without retaining any page text, whether the page exposes
period labels at all, or exposes them in a form or position the parser does not
read. The outcome then selects exactly one of two actions:

- **Labels absent from the page** → per-symbol or per-page reality; the symbol
  legitimately has no dated quarters and remaining parked is correct behaviour.
  No parser change is warranted, and the rule that never invents a date stands.
- **Labels present but not where `parse_quarterly` looks** → the parser's
  section/header contract has drifted and warrants its own bounded fix, which
  *would* potentially affect other symbols and must then be validated against
  symbols that currently parse correctly.

Until that runs, **no conclusion about the other 118 failures is warranted**, and
no parser change, refresh, retry, or further probe should be initiated.

---

## 9. Final state

| Item | Value |
|---|---|
| Final HEAD | recorded after commit (see handoff) |
| Change set | additive: extended `tools/probe_screener_once.py`, new `tests/test_screener_probe_structure.py`, this report |
| Production source modified | none — `screener_parse.py` and the fetcher are untouched |
| Working tree | clean after commit |
| CI / CodeQL | reported only when verified |
| **Verdict** | **`SECTION PRESENT BUT PERIOD LABEL MISSING — DATE CONTRACT FOLLOW-UP NEEDED`** |