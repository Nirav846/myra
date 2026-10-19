# Phase 27 — Single-Symbol Screener.in Diagnostic

**Verdict: `HTTP 200 TABLE MISSING — PARSING CONTRACT NEEDS FOLLOW-UP`**

One authorized HTTP request was made, for one symbol. No refresh, retry, bulk
operation, scheduler job, or production write occurred.

---

## 1. Baseline and selected symbol

| Item | Value |
|---|---|
| Baseline commit | `4e847ec` (matches the specified baseline) |
| Branch / sync | `main`, in sync with `origin/main` |
| Working tree at preflight | clean |
| Symbol probed | **`AAATECH`** |

`AAATECH` was selected from the previous failure set and its prior failure
independently confirmed from local records rather than assumed:

- `fundamentals_park_state["AAATECH"]` = `{"fails": 4, "last_attempt":
  "2026-10-18T16:01:21Z"}` — four recorded failures, the last at the start of the
  2026-10-18 refresh.
- `quarterly_results` rows for `AAATECH`: **0**.
- A `fundamentals` summary row exists (`pe` 65.79, `market_cap` present,
  `last_updated` 2026-10-17), so the symbol is real and was previously populated
  by the enrichment path — not an invalid ticker.

---

## 2. Preflight findings

All five conditions were satisfied before any request:

1. **Repository** — HEAD `4e847ec` = baseline, `main`, clean, synchronized.
2. **Fetch path reviewed** — `_fetch_screener` issues a single
   `self.session.get(...)` and **never consults the network cache**, so a page
   response is never retained; `_fetch_google` is guarded and cannot be reached
   by a Screener-only call; the Phase 26 status-reset and status-isolation fixes
   are in place and their tests pass (§5).
3. **Symbol confirmed** — `AAATECH`, with four recorded failures and zero
   quarterly rows (§1).
4. **No competing process** — zero Python processes; the metadata WAL was
   byte-identical across samples, so no writer was active.
5. **Single-request isolation verified** — `GhostSession.get()` resolves to
   `request()`, which performs **exactly one** `self.session.get(...)`. There is
   no retry loop, no fallback chain, and no persistence: the probe calls the
   session directly and never invokes `DataFetcher._fetch_screener`,
   `FundamentalManager`, or any DB writer.

The probe travels by the application's own approved client (`cloudscraper` with
the production headers and pacing) — normal source access behaviour, no identity
rotation, no circumvention.

---

## 3. Sanitized diagnostic result

One request, no retry:

| Field | Value |
|---|---|
| Symbol | `AAATECH` |
| UTC timestamp | `2026-10-18T17:49:53.823241+00:00` |
| Outcome category | `HTTP_RESPONSE` |
| HTTP status | **200** |
| Response byte length (200 only) | **81,852** |
| Quarterly table matched (200 only) | **false** |
| Elapsed | 3.277 s |
| Completed without retry | true |

No response body, header, cookie, credential, or full request URL was printed,
logged, or persisted. The body was inspected in memory only, to measure its
length and test for the expected table.

Corroborating offline observation: the same diagnostics established that
`parse_company_page` requires either `<section id="quarters">` with a `<table>`,
or a `<table>` inside a section whose text contains "Quarterly", **and** a
header row carrying at least one non-empty period label.

---

## 4. What the evidence proves — and what it does not

### Proves

1. **This request was not refused at the HTTP level.** The server returned
   `200` to the approved client. For *this* request, and this symbol, there was
   no 403, 429, or 404.
2. **The response was a substantial document** (81,852 bytes), not a truncated
   or empty error page.
3. **The expected quarterly-table structure was not found.** The parser
   contract was not satisfied by the delivered page.
4. **The transport worked.** The request completed in 3.277 s with no retry.

### Does not prove

1. **It does not prove a site-wide redesign.** One symbol cannot show that. The
   correct reading is the mission's own wording: the response did not match the
   expected structure, which does not by itself establish a redesign.
2. **It does not establish the failure distribution across the other 118
   failures.** They may share this cause or not; this probe cannot say.
3. **It does not distinguish "section absent" from "section present but
   undated."** The probe recorded the single permitted boolean, and
   `parse_quarterly` returns `[]` in both cases — whether `_quarterly_table()`
   found nothing or found a table whose header carried no period labels. This is
   a real limitation of a one-boolean probe.
4. **It does not prove the parser is broken.** Offline evidence shows the
   contract produces valid output: **884 symbols** hold properly dated quarters
   ("Mar 2023", "Sep 2025", "Dec 2025", …) and **0** placeholder rows exist,
   which only the Screener parser can produce. However, `quarterly_results` has
   **no write-timestamp column**, so those rows cannot be shown to have been
   written *recently*. The parser is not obviously globally broken; that is all
   the evidence supports.

### Interpretation, stated conservatively

`AAATECH`'s page was served successfully but did not contain the quarterly
structure the parser expects. That is equally consistent with (a) this symbol's
page genuinely lacking a quarterly results section, and (b) a structural change
affecting pages generally. **One probe cannot separate these**, and this report
does not choose between them.

---

## 5. Tests and results

The diagnostic was shipped as a reusable, reviewed tool rather than left as a
throwaway script, so its privacy boundary and classification are pinned by
tests.

| Scope | Result |
|---|---|
| `tests/test_screener_probe.py` (new) | **26 passed** |
| Related suites — probe + Phase 26 status classification + diagnostics + `test_fetch_screener.py` + `test_screener_parse.py` + `test_access_log_filter.py` | **50 passed** |
| Full suite | **not completed — aborted by the operator** |
| black / flake8 / Performance Guard / schema drift | clean |

The new tests assert, using **mocked responses and no network requests**:

- **Privacy boundary is structural**, not conventional: the result is built from
  a fixed allowlist, so a secret planted in a body, header, cookie, or exception
  message has no path into the output. A test plants `SUPER_SECRET_COOKIE_VALUE`
  in both body and `Set-Cookie` and asserts it cannot appear.
- **Exception messages never survive** — a message containing a full URL and
  token is asserted absent while its class is retained.
- **No financial values are returned** — table detection yields a boolean only;
  an injected `123456.78` is asserted absent.
- **Classification is faithful** — 403, 429, 404, 500, 503, 200-with-table,
  200-without-table, `NETWORK_ERROR` and `TIMEOUT` all map to distinct outcomes,
  with byte length recorded **only** for a 200.
- **Exactly one request** on success and on failure.
- **No database connection** is attempted (a monkeypatched `sqlite3.connect`
  that raises would fail the test).
- The Phase 26 **status-reset and Google status-isolation tests still pass**.

One defect was caught during development: on a transport failure the result
omitted `http_status` entirely, making the result schema vary with the outcome.
It now always reports the key (null when unknown).

The full suite was **aborted by the operator before completion**, so it is not
claimed. This is materially lower risk than usual because **the change set is
purely additive** — two new files, and **zero modifications to existing
source** — which is verified by `git diff --name-only` returning nothing. The
Phase 26 full suite was previously green at 1,891 passed, and the immediately
affected suites were re-run and pass.

---

## 6. No refresh, no production write

- **No fundamentals refresh, ingestion, EOD2 sync, backfill, enrichment, lake
  build, or scheduler job was run.** No service was started or stopped.
- **Exactly one HTTP request was made**, for one symbol, with no retry and no
  second probe.
- **No production database was written.** Every inspected value is byte-for-byte
  what it was before the probe:

| Check | Before | After |
|---|---|---|
| `fundamentals_sync_status` updated / failed | 229 / 119 | 229 / 119 |
| Parked symbols | 119 | 119 |
| `park_state[AAATECH].fails` | 4 | 4 |
| `quarterly_results` rows | 10,798 | 10,798 |
| `fundamentals` rows | 3,951 | 3,951 |
| `AAATECH` quarterly rows | 0 | 0 |
| `AAATECH.last_updated` | 2026-10-17T01:46:29 | 2026-10-17T01:46:29 |

One honest note: `myra_metadata.db`'s mtime advanced at 22:54, which is
**before** the probe (23:19) and consistent with SQLite performing its automatic
WAL checkpoint when a read-only inspection connection closed. The file size is
unchanged and every stored value is identical, so no application data was
written. This is recorded rather than omitted.
- No safeguard was disabled, no date validation loosened, no source priority
  changed, and the **live DB guard was not modified or weakened**.
- No scoring, ranking, growth default, freshness threshold, or
  `MYRA_V25_RANKING_INPUTS` setting was touched.

---

## 7. Recommended next step (limited to the evidence observed)

Only one thing is worth doing next, and it should not happen without explicit
authorization:

**Resolve the one ambiguity this probe could not.** The single boolean could not
separate "no quarterly section" from "section present but undated". Extending the
probe to report two booleans — whether `<section id="quarters">` (or a section
containing "Quarterly") exists, and whether its header carried a non-empty
period label — would distinguish them **in a single further request for the same
symbol**. This is the smallest change that converts the current ambiguity into a
decision, and it still reveals no page content.

If that distinguishes the cases:

- **Section genuinely absent** → this is a per-symbol data reality (limited
  reporting history), not a defect. `AAATECH` would be expected to remain without
  quarterly rows, and the parked-symbol behaviour is working as designed.
- **Section present but undated** → the parser's header/date contract needs
  follow-up against the current markup, which *would* affect other symbols too
  and deserves its own bounded investigation.

Until then, **no conclusion about the other 118 failures is warranted**, and no
further probe, retry, or refresh should be initiated.

---

## 8. Final state

| Item | Status |
|---|---|
| Final HEAD | recorded after commit (see handoff) |
| Change set | additive only: `tools/probe_screener_once.py` + `tests/test_screener_probe.py` + this report |
| Working tree | clean after commit |
| CI / CodeQL | reported only if verified |
| Verdict | **`HTTP 200 TABLE MISSING — PARSING CONTRACT NEEDS FOLLOW-UP`** |