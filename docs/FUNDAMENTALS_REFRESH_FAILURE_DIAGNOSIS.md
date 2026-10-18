# Fundamentals Refresh Failure Diagnosis

**Read-only, bounded diagnostic. No refresh was triggered, no network request was
made to any data source, and no production data was written.**

## 1. Executive verdict

**`INCONCLUSIVE — SPECIFIC EVIDENCE REQUIRED`** (with one narrow, validated fix).

Two things are established and one is not:

1. **The run did not damage existing data.** Verified from code and database:
   the fundamentals UPSERT `COALESCE`s every column, so a failed or partial
   refresh cannot null out a metric already held; the sync marker is written only
   after a successful persist; and the undated-row guard refuses to write
   placeholder dates. Zero placeholder and zero NULL `report_date` rows exist in
   `quarterly_results` (9,100 rows).
2. **The reported "10-minute Screener.in cooldown" is inert.** `SourceManager`
   — the only thing that implements it — is never consulted by the fundamentals
   path. The message is cosmetic. It caused no data loss and no symbol skipping.
3. **The true per-symbol cause cannot be established from the run**, because
   `DataFetcher.fetch_fundamentals` caught every per-source exception at
   `debug` level. That is a proven diagnosability defect and is fixed here, but
   fixing it does not retroactively recover *this* run's upstream errors.

A fundamentals refresh was **still running** throughout this diagnosis
(PID 12388, started 2026-10-18 21:30). Nothing here was interrupted.

---

## 2. Confirmed root causes

### 2.1 The Screener.in failure reason was destroyed before it could be logged

`myra_app/fetcher.py:1175` (before this fix):

```python
except Exception:
    logger.debug("Non-critical fetch error, skipping item", exc_info=True)
    continue
```

Every per-source exception — connection error, HTTP failure, parse error — was
caught at `debug` and discarded. `fetch_fundamentals` returned `(None, None)`, so
`FundamentalManager` could only report the generic
`"multi-source fallback exhausted"` (`fundamental_manager.py:162`). That is
exactly why the run log could not say **why** Screener.in failed, and why the
generic message could not be improved without guessing.

### 2.2 The logged cooldown does not exist

`SourceManager.mark_failure` (`myra_app/data_sources/base.py:40-50`) sets a
10-minute `cooldown_until` and prints *"Cooling off for 10 mins"*. That manager's
only consumer would be `get_available_sources()` — which is **never called
anywhere** in the codebase (`data_sources/registry.py:3` documents it as
"inert ... never consulted", and `fundamental_manager.py:144-149` says the same).

The fundamentals path (`fetcher.py:1153-1178`) iterates the configured source
list itself and consults only `source_config.is_enabled`. It never reads
`cooldown_until`. A cooldown therefore **cannot** skip or reroute any symbol.

Two further points from the code:

- `mark_failure` decrements `score` on **every** failure with no floor, and
  `mark_success` only ever adds back `0.5`. Irrelevant while the manager is
  inert, but it would be a slow-burn mis-scoring if it were ever wired up.
- The **real** throttling in the fundamentals sync is entirely different
  (`myra_app/utils/fundamentals_sync.py:394-411`): HTTP `429/403/503` is
  classified as rate-limiting, does **not** count toward parking a symbol, sleeps
  120 s, and aborts the run after 3 consecutive occurrences. That mechanism is
  sound and is what should govern Screener.in.

The `screener_in` cooldown visible in `metadata.enrichment_source_health` is
**unrelated** — that state is loaded and persisted only by
`tasks/fundamentals_enrich.py` (lines 87, 119), a *different* task. Its stored
`cooldown_until` is `2026-10-11`, seven days stale.

### 2.3 AAATECH's undated Google rows — by design, correctly rejected

`fetcher._fetch_google` (`fetcher.py:1296-1325`) builds rows labelled
`"Q-1".."Q-4"` because Google Finance exposes no datable period headers. That is
a **structural limitation of the Google fallback, not a parsing loss** — there is
no real date in the page to lose, and inventing one would be fabrication.

`FundamentalManager.fetch_fundamentals` (`:167-178`) filters those out via
`_PLACEHOLDER_REPORT_DATE` and refuses to write when nothing dated remains.
This protects the `report_date DESC` ordering every consumer relies on: an
undated `"Q-1"` would sort above a real `"Sep 2025"` row and be read as the
newest quarter.

**Yes, this affects every symbol that falls through to Google** — any symbol
where Screener.in yields nothing gets only undated rows and is skipped. Confirmed
in the data: `AAATECH`, `AAKAAR_SME` and `AARADHYA_SME` all have **0**
`quarterly_results` rows, and `AAATECH` has NULL across all core metrics. The
guard did its job.

### 2.4 Why a symbol fails is not persisted

`fundamentals_sync` prints `mgr.last_error` to the console (`:417`) but stores
only a per-symbol fail counter in `fundamentals_park_state`. The reason string
is not retained, so per-symbol causes cannot be reconstructed after the run.
This is the missing telemetry noted in §4.

---

## 3. Run outcome counts

Recovered from `metadata.fundamentals_sync_status` and
`metadata.fundamentals_park_state` while the run was still in progress. Counters
are **cumulative across resumed runs**, and the run was live throughout, so these
are a snapshot, not a final tally.

| Metric | Value |
|---|---|
| Status at snapshot | `in_progress` (last symbol `CHETANA_SME`) |
| `total_symbols` (this pass) | 72 |
| `updated_count` (cumulative) | 221 |
| `failed_count` (cumulative) | 115 |
| Parked symbols (cumulative) | 115 |
| Parked by fail count | 1→57, 2→50, 3→2, 4→6 |
| Park threshold / window | 5 failures, 30 days (`PARK_AFTER_FAILURES`, `PARK_DAYS`) |
| Parked, attempt date | all 115 on 2026-10-18 |
| Index/benchmark rows skipped | 35 (as reported; `partition_index_rows`) |
| Symbols selected (as reported) | 3,465, of which 2,686 had recent prices |
| `fundamentals` rows | 3,951 (unchanged) |
| Rows updated today | 221 |
| Placeholder `Q-n` rows persisted | **0** |
| NULL/empty `report_date` | **0** |
| `sync_log.fundamentals_sync` | last run 2026-10-17, **cancelled** (previous run) |

**Explicit unknowns.** The 3,465 selection figure cannot be reconciled with the
72-symbol queue: selection happens before the freshness filter, and the counters
are cumulative across resumes. Per-symbol failure **reasons** were not persisted
(§2.4) and were not recoverable from the console transcript. The number of
symbols that hit a Screener.in cooldown is **zero by construction** (§2.2) — the
inert manager never skipped anyone; the count of symbols that reached the Google
fallback is not separately persisted, though 115 failures imply at least that many
reached it.

---

## 4. Exact Screener.in cooldown behaviour

| Question | Answer | Evidence |
|---|---|---|
| What marked it failing? | *Any* symbol where the whole chain returned `(None, None)` — not Screener.in specifically | `fundamental_manager.py:143-151` |
| Triggered by one symbol? | **Yes** — a single failure calls `mark_failure` | `base.py:40` |
| HTTP status / rate limit? | **Not** — always the generic 10-minute branch; `is_rate_limit` is never passed as `True` | `base.py:45`, single call site |
| Source-wide or narrow? | Source-wide in intent | `base.py:49` |
| Does it skip later symbols? | **No** — `get_available_sources()` is never called | grep: 2 hits, both comments |
| Does it reroute to fallbacks? | **No** — routing is the static config list | `fetcher.py:1161-1172` |
| Distinguishes rate limits / network / missing page / bad symbol / parse error / absent data? | **No** — all six collapse to one message | §2.1 |

The distinction that *does* exist is in `fundamentals_sync.py:394`: HTTP
`429/403/503` is separated out as rate-limiting, is excluded from parking, and
triggers backoff/abort. That is the correct mechanism and it worked.

---

## 5. Google fallback date-handling analysis

| Question | Answer |
|---|---|
| Fields for period / publication | `report_date` is the ordering key; `period_end` is the true financial period |
| Missing at source, lost in parsing, or mis-normalized? | **Absent at source.** `_fetch_google` hardcodes `"Q-1".."Q-4"`; Google Finance's tables carry no period headers |
| Web-page timestamp confused with period? | **No** — no timestamp is read at all, which is why a real date is unavailable |
| Could it affect other symbols? | **Yes — every symbol that reaches the Google fallback** |
| Correctly rejected before persistence? | **Yes** — filtered by `_PLACEHOLDER_REPORT_DATE`, and the write is skipped when nothing dated survives (`fundamental_manager.py:167-178`) |

No date was invented or substituted. The undated path cannot reach
`quarterly_results`.

---

## 6. Evidence on database writes and preservation

Read-only inspection of `myra_valuation.db` and `myra_metadata.db`, plus the
persistence code:

| Safeguard | Verified | Evidence |
|---|---|---|
| Undated results cannot overwrite valid data | **Yes** | Write skipped entirely; 0 placeholder / 0 NULL `report_date` rows |
| Failed refresh preserves existing values | **Yes** | Every UPSERT column is `COALESCE(EXCLUDED.x, fundamentals.x)` (`fundamental_manager.py:266-282`) |
| Sync marker only on success | **Yes** | Marker update (`:306-312`) is reached only after `v_conn.commit()`; failures return `False` at `:318` before it |
| Nothing deleted | **Yes** | No DELETE path in the write sequence |
| Source failure vs cooldown reported distinctly | **No** (fixed) | Both collapsed to one message; now per-source reasons are surfaced |
| Readiness not fooled by HTTP 200 / job start | **Yes** | `fundamental_facts()` derives dates from stored data (`MAX(last_updated)`, `MAX(period_end)`), never from job state |
| Partial success represented honestly | **Partly** | Status stayed `in_progress` and was not stamped complete on cancel (`fundamentals_sync.py:442-448`); but per-symbol causes were not persisted |

For the three reported symbols, nothing was corrupted — `AAATECH` holds NULL
metrics and 0 quarterly rows, which is the honest representation of "no dated
source data", not a destroyed row.

---

## 7. Smallest justified correction

**One narrow fix, applied:** make the per-source failure reason survive to the
run log.

- `myra_app/fetcher.py` — record a per-source reason in
  `self.last_source_errors` for each source that raised, was denied
  (`HTTP <status>`), or returned no data. Only the exception **type** is stored,
  never its message, so a URL, cookie or token cannot reach the log.
- `myra_app/fundamental_manager.py` — surface those reasons in `last_error`
  instead of overwriting them with the generic message.

**Deliberately not changed:** the cooldown itself, date validation, source
priority, scoring, growth defaults, freshness thresholds, and the COALESCE /
undated-row safeguards. The inert `SourceManager` is documented rather than
rewired — rewiring it would change throttling behaviour, which the brief
forbids without understanding intent, and it is provably not a cause of this
run's failures.

---

## 8. Focused regression tests

`tests/test_fundamentals_diagnostics.py` — **9 tests**, no network, temporary
databases only:

- per-source reasons surface through the manager;
- the generic message is the fallback when a fetcher records nothing;
- placeholder `Q-n` detection, and that real periods are *not* filtered;
- undated-only rows are refused; mixed rows keep only the dated one;
- recorded reasons cannot carry message text (no URL/cookie leakage);
- `DataFetcher` distinguishes denied page from no-data and never formats the
  exception message;
- the production UPSERT still `COALESCE`s, and a partial payload cannot null a
  held value.

**Results:** 9 passed. Related suites (fundamental / fetcher / data_source /
enrich / screener / source_priority / traction): **326 passed**.
**Full suite: 1858 passed, 1 error** — the error is
`live_db_guard` detecting the **live fundamentals refresh** writing
`myra_valuation.db` mid-run, not a regression. Both affected modules
(`test_wyckoff_mcap.py`, `test_wyckoff_spring_score.py`) pass in isolation
(16 and 44 passed). The guard is doing its job; the environment has a concurrent
writer. `black` / `flake8` / Performance Guard / schema-drift: clean.

---

## 9. Safe next action (no full-universe refresh)

1. **Let the in-flight run finish** — it is resumable and already has 221 updated.
2. **Targeted retry of the parked symbols only** once upstream recovers, via the
   park-state mechanism — not a new full-universe refresh.
3. **Probe one failing symbol directly** (e.g. `AAATECH`) against Screener.in
   from an operator machine to establish whether the failure is HTTP-level
   (blocked/rate-limited) or parse-level. This is the specific evidence that is
   missing, and it needs no pipeline run.
4. With this fix deployed, the **next** run's log will name the cause per
   symbol, so no separate probe is needed for future failures.

---

## 10. Outstanding

- The upstream cause of the Screener.in failures on this run is unrecoverable
  from existing evidence (§2.1).
- `SourceManager` remains inert; its `score` decay would mis-bias sources if it
  were ever wired up.
- `profit_growth`, `sales_growth`, `debt_to_equity` remain `unavailable` with
  **no producer** (dead Morningstar mappings) — independent of this incident.
- `roe_ttm` has a documented two-part defect (request list + merge key mismatch).

**Verdict: `INCONCLUSIVE — SPECIFIC EVIDENCE REQUIRED`**