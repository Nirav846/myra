# Phase 11 — Readiness Transport Reliability & Fundamental Data Integrity

**Date:** 2026-10-15
**Base commit:** `88f3966` ("Fix timezone-dependent cooldown test", Phase 10)
**Branch:** `main`

---

## 1. Initial repository state (verified, not assumed)

The Phase 10 state was re-derived from the repository rather than taken on
trust. All of it matched:

| Check | Result |
|---|---|
| Branch | `main` |
| HEAD | `88f3966` |
| `origin/main` | `88f3966` |
| Ahead / behind | `0 / 0` |
| Working tree | clean |
| Stashes | 3 preserved (untouched) |
| Phase 10 files present | all 7 (`nse_calendar.py`, `eod_recovery.py`, `calendar_reconcile.py`, 3 test modules, PHASE10 doc) |

No material discrepancy, so work proceeded. No user changes were present or
overwritten.

---

## 2. Priority 1 — the readiness-panel `Failed to fetch`

### 2.1 What the symptom actually is

The footer showed:

```
Assessed 9:13:33 AM · updates only when the verdict changes · transport error: Failed to fetch
```

`Failed to fetch` is **not** a server message. It is the browser's string for a
`fetch()` that never completed. It is returned identically for connection
refusal, DNS failure, TLS failure **and** a CORS rejection — so the message
itself carried no information about which remedy applied.

### 2.2 Reproduction

An HTTP client cannot observe this, so the investigation was driven through a
real browser (Playwright, already installed — no new dependency) against the
real Vite dev server and the real FastAPI backend.

An early attempt using a Playwright route shim was **misleading** (it produced a
"loopback address space" denial that did not reproduce against the real page).
The shim was discarded and the matrix re-run against the actual dev server, which
is what produced the trustworthy result below.

Backend verified healthy throughout (`GET /api/pipeline/readiness` → `200`):

| Page origin | absolute `http://localhost:8000/api` | relative `/api` (Vite proxy) |
|---|---|---|
| `http://localhost:3000` (was allowlisted) | **200** | 200 |
| `http://127.0.0.1:3000` | **Failed to fetch** | 200 |
| `http://192.168.29.162:3000` (LAN) | **Failed to fetch** | 200 |

Browser console for the failing rows:

```
Access to fetch at 'http://localhost:8000/api/pipeline/readiness?changed_only=1'
from origin 'http://192.168.29.162:3000' has been blocked by CORS policy:
No 'Access-Control-Allow-Origin' header is present on the requested resource.
```

### 2.3 Root cause

Two independent defects that produce the identical symptom:

1. **`myra_web/src/config.ts` hardcoded an absolute cross-origin API base.**
   `API_BASE = 'http://localhost:8000/api'`, so *every* panel request was a
   cross-origin call into the browser's loopback address space. The Vite `/api`
   proxy was configured but unused by these callers.

2. **The backend CORS allow-list named only two origins** —
   `http://localhost:3000` and `http://localhost:5173`. The dev server runs
   `vite --host=0.0.0.0`, so it serves *and advertises* `127.0.0.1` and a LAN
   address. Opening the app on either of those origins produced a perfectly
   healthy API response that the browser refused to hand to the page.

A third, environmental contributor observed during Phase 10 and again here: with
no MYRA process running there is nothing to answer, which also surfaces as
`Failed to fetch`.

### 2.4 Fix

**Same-origin by default.** `config.ts` now resolves the base to the relative
`/api`, so requests stay on the page's own origin and the already-configured Vite
proxy (or a production reverse proxy) forwards them. `VITE_API_BASE` remains as
an explicit override for split deployments — a pattern two views already used
independently.

> **Note on a bug this introduced and caught.** The first attempt used an *empty*
> base. Because callers concatenate `${API_BASE}/pipeline/readiness`, that
> produced `/pipeline/readiness` — missing the router's `/api` mount — which Vite
> answered with the SPA fallback `index.html`. The new payload validator caught
> this immediately as `malformed` rather than rendering a false verdict. The base
> is therefore `/api`, not `''`.

**CORS policy extracted and widened.** `myra_web/cors_config.py` now owns the
allow-list, adding `127.0.0.1` and `0.0.0.0` variants, overridable via
`MYRA_ALLOWED_ORIGINS`. It lives in its own module precisely so it is testable
without constructing the app (whose routers touch the database).

**Truthful failure reporting.** `Failed to fetch` is no longer echoed. Failures
are classified as `unreachable` / `http` / `malformed` and rendered as actionable
text, e.g.:

- `API not responding (HTTP 502 via proxy) — start it with: python run_fastapi.py`
- `backend unreachable (…) — start it with: python run_fastapi.py`

A 2xx response that breaks the response contract is now rejected as `malformed`
rather than rendered.

**Stale verdict disclaimed.** When the transport fails but a previous verdict is
still on screen, the footer adds *"shown verdict is not currently confirmed"* so a
stale `ready` cannot read as current.

**Redundant renders removed.** `setLastUpdated` fired on every *unchanged* poll,
re-rendering the panel every 30 s for a value that never moved — contradicting the
hook's own documented behaviour. State is now updated only for a changed verdict.

### 2.5 Verification (real browser, real app)

| Check | Result |
|---|---|
| `localhost:3000/mission-control` | panel renders, **0** `Failed to fetch`, **0** CORS |
| `127.0.0.1:3000` origin | request succeeds (CORS fixed) |
| `192.168.29.162:3000/mission-control` (LAN) | panel renders, **0** `Failed to fetch`, **0** CORS |
| Backend stopped | `Readiness unavailable — API not responding (HTTP 502 via proxy) — start it with: python run_fastapi.py` |
| Backend restarted | recovers to `READY` without a page reload |
| Live panel (LAN origin) | `READY`, latest `2026-10-14` = expected `2026-10-14`, 3203/3220 symbols |
| `tools/verify_readiness_transport.py` | all three origins `HTTP 200`, **0** failures |

**Residual instances found and closed during verification.** With the readiness
path fixed, the browser console still showed `http://localhost:8000/api/health`
being blocked from the LAN origin. Three more files hardcoded the same absolute
base and were the identical bug class, not a new defect:

- `myra_web/src/lib/Librarian.ts` — `apiUrl` default
- `myra_web/src/lib/marketCapCache.ts` — `getApiUrl()` fallback
- `myra_web/src/lib/SettingsContext.tsx` — default `apiEndpoint`

All three now default to `API_BASE`. After the change the console is clean from
the LAN origin: **0 CORS errors, 0 `Failed to fetch`**. A locally persisted
`apiEndpoint` in `localStorage` still overrides, which is intentional.

### 2.6 Regression tests

`tests/test_readiness_transport.py` — **19 tests**: CORS grant for allowlisted
origins (incl. preflight), refusal of unrelated origins, allow-list
configuration/override/normalisation, readiness response contract, readiness
opening no writable connection, frontend relative-base contract, no absolute URL
assignment, override still supported, no re-render on unchanged verdict, failure
classification, and payload-contract rejection.

`tools/verify_readiness_transport.py` — an operator-facing browser smoke check
that discovers the LAN origin and reports per-origin reachability. It is a
manual tool, not a CI gate (CI has no browsers).

---

## 3. Priority 2 — fundamental-data provenance and freshness

### 3.1 Audit tool

`tools/fundamentals_audit.py` — **strictly read-only** (every connection
`mode=ro`), bounded, one-off CLI, never called from a UI refresh. It reports
coverage against **explicit, non-conflated denominators**: rows in
`fundamentals` (3,948) and the tracked technical universe (3,424) are reported
separately.

Note: there is no `myra_fundamentals.db`. `fundamentals` lives in
`myra_valuation.db`.

### 3.2 Coverage, measured 2026-10-15

Denominators: `fundamentals` rows = **3,948**; tracked universe (distinct symbols
in `technical_data`) = **3,424**.

| Field | Usable | % of rows | % of universe | Latest `last_updated` |
|---|---|---|---|---|
| `market_cap` | 3,246 | 82.22% | 94.80% | 2026-10-13 |
| `net_margin` | 3,309 | 83.81% | 96.64% | 2026-10-13 |
| `dividend_yield` | 1,510 | 38.25% | 44.10% | 2026-10-13 |
| `inst_holding` | 728 | 18.44% | 21.26% | 2026-10-13 |
| `roe` | 691 | 17.50% | 20.18% | 2026-10-13 |
| `profit_growth` | **0** | **0.00%** | 0.00% | — |
| `sales_growth` | **0** | **0.00%** | 0.00% | — |
| `debt_to_equity` | **0** | **0.00%** | 0.00% | — |

The previously reported figures (**ROE ≈ 17.5%**, **market cap ≈ 82.2%**) were
**confirmed**. `profit_growth` was confirmed empty. `sales_growth` and
`debt_to_equity` were found to be **equally empty** — a new finding.

Additional camelCase columns measured directly: `marketCap` 8 rows (0.20%),
`returnOnEquity` 442 (11.20%), `operatingMargin` / `grossMargin` /
`currentRatio` 7 each (~0.18%), `revenueGrowth` / `earningsGrowth` /
`debtToEquity` / `netMargin` / `dividendYield` / `enterpriseValue` / `beta` **all
0**. The Morningstar camelCase path is effectively dead.

### 3.3 Provenance table

| Field | Status | Producer | Units |
|---|---|---|---|
| `market_cap` | verified | `fundamental_sync._compute_market_cap_from_prices` (derived) | INR |
| `shares_outstanding` | verified | `fundamental_sync` | shares |
| `pe` | verified | `fundamental_sync` (bulk) | ratio |
| `current_ratio` | verified | `fundamental_sync` (bulk) | ratio |
| `dividend_yield` | verified | `fundamental_sync` (bulk) | percent |
| `roe` | verified | Screener.in / bulk + `returnOnEquity` | percent (0–100) |
| `net_margin` | **unverified** | no confirmed live producer | **MIXED — see below** |
| `inst_holding` | verified | institutional sidecar, month-end | percent |
| `profit_growth` | **unavailable** | none — dead mapping `earningsGrowth → profit_growth` | unknown |
| `sales_growth` | **unavailable** | none — dead mapping `revenueGrowth → sales_growth` | unknown |
| `debt_to_equity` | **unavailable** | none — dead mapping `debtToEquity → debt_to_equity` | unknown |
| `roe_ttm` | **unavailable** | none | unknown |
| `smart_money_score` | **unavailable** | none — read with `0` default, never written | unknown |

**The percentage-vs-fraction defect is confirmed and quantified.** `net_margin`
sample 0.1366 (fraction-like, 13.66%) but max 699.9 and min −26,570 (percent-like,
or corrupt). The column mixes scales, so values must not be compared across rows
or aggregated until normalised.

**`inst_holding` is a stock, not a flow.** Month-end shareholding cannot show who
bought during a quarter. It must not be presented as "smart-money activity".

### 3.4 Freshness — implementation

`myra_app/fundamental_freshness.py`, exposed through the existing
`/api/pipeline/readiness` response as `fundamentals.freshness`. No new endpoint.

States: `fresh` / `aging` / `stale` / `unknown`.

Cadence is per source *kind*, not a universal TTL:

| Kind | Cadence | Window |
|---|---|---|
| market-derived | daily | 7 d |
| ownership (month-end) | monthly | 45 d |
| financial (quarterly) | quarterly | 120 d |

These are **policy choices**, labelled as such — not source-confirmed facts.

Rules enforced and tested:

- a **missing** timestamp yields `unknown`, never `fresh`;
- a **recent retrieval of an old reporting period is still old** — the period
  decides, not the fetch time;
- a future reporting period is `unknown` (a projection is not evidence);
- **valid zero is preserved** and never conflated with missing;
- nothing converts missing to zero (`COALESCE`, `or 0` absent from the module);
- the module imports **no** database, threading, scheduling or HTTP code — it is
  pure reporting.

Current live state: reporting period **2026-09-30**, retrieval **2026-10-13** →
`fresh` (15 days old, quarterly cadence).

`tools/db_doctor.py` also flagged `screener_fundamentals` last updated
**2026-08-16** — roughly two months stale — while holding only **3 rows**.

### 3.5 Handling of untrustworthy fields

- `profit_growth`, `sales_growth`, `debt_to_equity`, `roe_ttm`,
  `smart_money_score`: labelled **unavailable** with the reason. **No producer
  was fabricated.**
- Phase 6's opt-in remains opt-in: `MYRA_V25_RANKING_INPUTS` is untouched, and
  the freshness module deliberately does not reference it (asserted by test).
- `smart_money_score` is **not** a `fundamentals` column at all — it is read from
  a candidate dict in `universe_loader` / `worker_pool` with a `0` default and is
  never written anywhere. This confirms and extends the Phase 6 finding.

### 3.6 Known problems found but **not** changed (out of scope, documented)

These are pre-existing and were deliberately left alone, since fixing them would
change screening behaviour:

- `myra_web/routes/fund_traction.py:254-255` uses
  `COALESCE(f.roe, 0) * 0.4 + COALESCE(f.net_margin, 0) * 0.3` — converts missing
  fundamentals to zero inside a score.
- `myra_web/confluence_batch.py:161` and `routes/scanners.py:1836` use
  `COALESCE(f.market_cap, 0)`.
- `myra_app/universe_loader.py:215-216` does `f.get("profit_growth", 0) or 0` on a
  field that is empty for every row.

Each is a null-as-zero defect in a scoring path and warrants its own phase with
explicit tests, rather than a silent change inside a reliability phase.

---

## 4. Validation

| Gate | Result |
|---|---|
| Full pytest suite | **1439 passed**, 0 failed |
| Live-database writes from tests | **none** |
| `black` 26.10.1 | clean |
| `flake8` | clean |
| `tsc --noEmit` | clean |
| `npm run build` | success |
| Performance guard | **273 violations / 0 CRITICAL** (was 271; +2 are the same `.append()`-in-loop false positive — a plain dict list, not a DataFrame) |

A test I added initially wrote rows into the session-shared fixture DB and broke
an unrelated module's row-count assertions later in the run. Fixed by restoring
the fixture in-test; verified by running the two modules together (53 passed).

---

## 5. Remaining issues and recommended follow-up

1. **`net_margin` unit normalisation** — the field is currently unusable for
   comparison. Needs a source-side decision, not an inference.
2. **Dead Morningstar camelCase path** — `fundamental_sync.MS_CANONICAL_MAP` maps
   `revenueGrowth`/`earningsGrowth`/`debtToEquity`, but none are ever populated.
   Either the upstream field list or the write path is broken.
3. **Null-as-zero in scoring paths** — three sites listed in §3.6.
4. **`screener_fundamentals` is stale and nearly empty** (3 rows, 2026-08-16).
5. **Official calendar coverage is 2026 only** (carried from Phase 10).
6. **No authoritative universe denominator** — the true NSE symbol count is not
   established, so "% of tracked universe" uses the tracked set.
7. **`smart_money_score`** remains without a defensible definition; no synthetic
   proxy was created.

---

## 6. Guardrails honoured

No scoring-weight changes · no speculative data sources · no invented values,
periods or freshness metadata · no fabricated smart-money signals · no blanket
suppression of `Failed to fetch` · no second scheduler or background loop · no
expensive scan on UI refresh · no destructive database cleanup · no unrelated
refactoring · no database files, secrets or generated audit output committed.