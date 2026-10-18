# Phase 26 — Fundamentals Source, Date-Fallback and Log-Noise Audit

**Verdict: `LOG NOISE FIX VALIDATED — SOURCE CAUSE STILL OPEN`**

Read-only diagnostic plus two narrowly scoped fixes. No fundamentals refresh,
ingestion, EOD2 sync, backfill, scheduler job, or lake build was run. No live
request was made to any data source. No production database was written.

---

## 1. Executive verdict

The Phase 25 verdict (`INCONCLUSIVE`) was correct, and this phase can prove *why*
it was inconclusive rather than merely repeating it:

> **The refresh that produced 119 failures finished at 22:01 IST. The
> per-source diagnostics were committed at 22:02 IST — one minute later.**

The run could not possibly have recorded per-source reasons, because the code that
records them did not exist while it was running. No log, database, or cache
artifact from the run can supply the missing evidence either. The Screener.in
failure cause is therefore **not recoverable from this run by any means**, and
this audit does not claim one.

What this phase did establish:

| Area | Result |
|---|---|
| SQLite read-only error | **Mechanism proven.** It is not a read-only *connection* — the file has never used one. |
| Google fallback dates | **No datable period exists in the current extraction.** Cannot be fixed without new evidence. |
| Readiness log noise | **Cause proven and fixed.** 466-char line → 94 chars, no behaviour change. |
| Source diagnostics | **Two real misclassification bugs found and fixed** in the Phase 25 change. |
| Data preservation | **Verified intact**, and the run was net-positive (9,100 → 10,798 quarterly rows). |

The single-symbol probe in §9 remains the only route to a Screener.in root
cause. It is deliberately **not** executed here.

---

## 2. Confirmed vs unconfirmed Screener.in failure causes

### Confirmed

1. **The run predates its own diagnostics.** Run start `2026-10-18T16:01:21Z`
   (21:31 IST); commit `e8de12a` at `2026-10-18 22:02:24 +0530`. The process had
   the pre-fix module loaded in memory. *Certainty: high — two independent
   timestamps.*
2. **No forensic trace exists.** `_fetch_screener` issues a bare
   `self.session.get(url)` and **never uses the network cache**, so page
   responses were never retained. The cache DB holds only 2 Screener-related
   keys, both enrich-style (`fundamentals:RELIANCE|screener_in|roe`), not company
   pages. *Certainty: high — inspected.*
3. **Per-symbol reasons are still not persisted.** `fundamentals_sync` prints
   `mgr.last_error` to the console and stores only a failure *counter* in
   `fundamentals_park_state`. *Certainty: high.*
4. **The inert cooldown remains inert**, exactly as documented — `SourceManager`
   is never consulted. *Certainty: high.*

### Unconfirmed — and why

Whether Screener.in returned **403 (blocked)**, **429 (rate-limited)**, **404
(invalid symbol)**, a **200 whose HTML no longer matches**, or a **transient
network error** is **unknown**. These five are indistinguishable in the surviving
evidence, and guessing between them would be fabrication.

### Two real defects found in the Phase 25 diagnostics

Reading the new code against the mission's "are failures classified correctly"
question turned up two genuine bugs — a diagnostic that names the wrong cause is
worse than none:

1. **Cross-symbol status leakage.** `last_http_status` was initialised only in
   `__init__` (fetcher.py:417) and never reset per call. A 403 from symbol A was
   reported against symbol B.
2. **Cross-source misattribution.** `_fetch_google` never set a status of its
   own, so when Screener returned 403 and Google simply returned nothing,
   Google was logged as `HTTP 403` — *for a source that was never refused*.

Both are fixed, along with a third gap: a 200 response carrying no datable table
now reports `HTTP 200 but no quarterly table in page` rather than a generic
`no data returned`, separating "no financials for this symbol" from "the page
structure changed under us".

---

## 3. Google fallback reporting-period findings

`_fetch_google` (fetcher.py:1315) creates `{"report_date": f"Q-{i}"}` for
`i in 1..4` and fills values from **positional** columns `cells[1:5]`. The table's
header row is iterated but **never read** — there is no attempt to associate a
period label with a value.

**Finding: the current extraction contains no period handling whatsoever.**
`Q-1`…`Q-4` are literal labels, not derived dates.

Three requirements from the brief are therefore unsatisfiable *today*:

- Extract the actual reporting period from source evidence — no extraction exists.
- Preserve correct year/quarter association across year boundaries — impossible
  without year information, which the current code never captures.
- Distinguish genuine quarter 1 from a relative "most recent" column — not
  attemptable from positional indexing.

**No implementation was written, deliberately.** Adding period parsing now would
require *inventing* the expected label format (`Jun 2025`? `TTM`? `Q2 FY26`?)
without a single sample of the page. A wrong guess would assign figures to the
wrong quarter — precisely the failure the brief forbids. The existing guard
already makes this harmless: such rows are rejected before persistence, and
**0 placeholder rows exist** in the database.

This is a **source limitation**, not a bug. Per the brief's guidance, the
fallback is **left enabled and unchanged** — it cannot damage data, and disabling
it would change source priority without evidence.

---

## 4. SQLite read-only error findings

Reported error: `sqlite3.OperationalError: attempt to write a readonly database`
at ~line 211 of `fundamental_manager.py`.

**Line 211 (pre-fix) is `v_conn.executemany(query, records)`** — the first real
INSERT into `quarterly_results`. Confirmed against `git show HEAD~1`.

### Hypothesis eliminated: a read-only connection
`fundamental_manager.py` has **never** used a read-only connection — verified
across its last 8 revisions (all `mode=ro: none`). `_get_val_conn` uses a plain
`sqlite3.connect(...)`.

### Mechanism proven by isolated experiment

Because every MYRA sidecar is already in WAL mode, `PRAGMA journal_mode=WAL`
becomes a **no-op that succeeds even on a read-only file**. The failure is
therefore deferred to the first actual write — reproducing the error at exactly
the reported line:

```
line 85   PRAGMA journal_mode=WAL -> OK, mode=wal   <-- PASSES
line 211  executemany -> OperationalError: attempt to write a readonly database
```

### Directory permissions eliminated
A read-only *directory* (WAL/-shm uncreatable) produced **no error at all** —
SQLite silently kept the existing journal mode. Ruled out.

### Conclusion
The error means the **valuation DB file was in a read-only state at that
instant** (read-only attribute, ACL, or a backup/AV/VSS lock), while the
connection itself was normal and writable. `myra_valuation.db` is writable now.

**No fix applied, and none needed:** the persist path already reports
`persist failed (OperationalError: ...)` with a full traceback
(fundamental_manager.py:340-349). The error was **already observable** when it
happened. Production configuration is untouched — no permission changes, no WAL
or journal changes, no database replacement.

**Residual risk (documented, not changed):** such a failure books the symbol as
a fetch failure and increments its park counter, so a transient filesystem state
can wrongly park a healthy symbol. Changing that is a behavioural decision
beyond this phase's scope.

---

## 5. Readiness access-log noise

**Cause confirmed end to end.** `useDataReadiness` polls every 30 s
(`useDataReadiness.ts:139-142`) sending `since=<URL-encoded JSON fingerprint>`.
`fingerprint()` (`data_readiness.py:962`) returns `json.dumps(payload)` —
**238 raw chars, 390 percent-encoded**, producing a **466-character** access-log
line. Measured, not estimated.

**Why filtering rather than shrinking the cursor:** the fingerprint is a
cross-system contract — the frontend returns it verbatim, the backend compares it
for equality (`pipeline.py:91`), and `test_readiness_transport.py` plus
`tools/verify_readiness_transport.py` pin the behaviour. Rewriting its wire format
trades a cosmetic gain for real compatibility risk. Eliding at the log boundary
leaves the contract untouched.

### Implementation
New `myra_web/access_log_filter.py`; a filter on the `uvicorn.access` logger
rewrites only the request-target argument that uvicorn emits:

```
BEFORE: len=182
  127.0.0.1 - "GET /api/pipeline/readiness?changed_only=1&since=%7B%22coverage%22%3A%202686... HTTP/1.1" 200
AFTER : len=94
  127.0.0.1 - "GET /api/pipeline/readiness?changed_only=1&since=<elided:238 chars> HTTP/1.1" 200
```

Requirements honoured: endpoint, response shape, `changed_only` semantics and
readiness-change detection are **untouched**; method, route, status, client and
HTTP version all still logged; **failed requests stay fully visible**; access
logging is never globally disabled (a test asserts handlers survive, since
uvicorn gates access logging on `hasHandlers()`); no credentials are logged.
Elision applies only above 64 chars, so short cursors stay readable, and the
marker reports the cursor size so operators can still see it varied.

Installed in both `myra_web/myra_fastapi_server.py` (imported by uvicorn) and
`run_fastapi.py`; the installer is idempotent.

**A test caught a real flaw during development:** the first implementation
re-encoded the query through `urlencode`, producing
`since=%3Celided%3A238+chars%3E` — unreadable, defeating the entire purpose. The
final version splices raw segments and leaves all other parameters' encoding
byte-identical.

---

## 6. Data-preservation evidence

Read-only verification against the live sidecars:

| Safeguard | Evidence |
|---|---|
| Undated rows rejected | 0 placeholder `Q-n` rows, 0 NULL/blank `report_date` |
| Failed refresh cannot erase data | 13 `COALESCE(EXCLUDED.x, fundamentals.x)` columns |
| Marker only after commit | 200 markers dated 2026-10-18; set at `:306-312`, after `commit()` |
| Readiness uses stored periods | reporting `2026-09-30`, retrieval `2026-10-18` — correctly distinct |
| Run was net-positive | quarterly rows **9,100 → 10,798** (+1,698) |

Minor discrepancy noted honestly: `updated_count` reached 229 while only 200
freshness markers were written. The counter includes resumed/retried passes and
symbols absent from `symbols_master`; it is not a data-loss signal, since stored
rows demonstrably grew.

---

## 7. Tests and validation

| Scope | Result |
|---|---|
| `test_access_log_filter.py` (new) | **21 passed** |
| `test_fundamentals_source_classification.py` (new) | **21 passed** |
| `test_fundamentals_diagnostics.py` (Phase 25) | **21 passed** |
| Related suites (fundamental/fetcher/readiness/pipeline/data_source/enrich/…) | **473 passed** |
| **Full suite** | **1891 passed, 0 failed** |
| black / flake8 / Performance Guard / schema-drift | clean |

The full suite is green for the first time this session: the Phase 25 run had
`live_db_guard` errors caused by the concurrent refresh writing
`myra_valuation.db`. No refresh is active now (zero Python processes; WAL frozen
at 22:29:45), and **the live DB guard was not modified or weakened**.

---

## 8. Remaining unknowns

1. **Why Screener.in failed** — unrecoverable from this run (§2).
2. **What triggered the transient read-only file state** — mechanism proven,
   trigger unknown (§4).
3. **Google's true page structure** — whether datable period labels exist in the
   source at all is unverified, since no sample was captured (§3).
4. **Per-symbol reasons still unpersisted** — console-only, lost at run end.

### The precise next evidence needed

A **single-symbol, separately authorized** probe — no pipeline run:

1. Fetch one known-failing symbol (e.g. `AAATECH`) from `screener_in` directly.
2. Record only: HTTP status code, and for a 200, the response byte length plus
   whether a quarterly table selector matched. **No page body, no headers, no
   cookies, no URL query strings.**
3. That single data point discriminates all five candidate causes in §2 and
   settles whether this is access denial, rate limiting, an invalid symbol, an
   HTML structure change, or a transient network fault.

After that, the shipped diagnostics make this a per-run observation rather than a
one-off experiment.

---

## 9. Operational confirmation

- **No full-universe fundamentals refresh was triggered.**
- No ingestion, EOD2 sync, historical backfill, scheduler job or lake build ran.
- No live request was made to Screener.in, Google Finance, or any other source.
- No production database was written; all inspection used `mode=ro` connections.
- No cookies, tokens or credentials appear in this report or in any diagnostic
  (asserted by test).
- No scoring, ranking, growth default, freshness threshold, source priority,
  date validation, or `MYRA_V25_RANKING_INPUTS` setting was changed.
- The live DB guard was preserved.
- The scheduler was not started, and no service was started or stopped.