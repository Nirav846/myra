# Phase 2 — Security, Dataflow & Correctness Audit

**Date:** 2026-10-13
**Scope:** dependency advisories, end-to-end dataflow, confirmed correctness defects + regression tests, scanner performance baseline.
**Starting commit:** `d47ad4c` (Phase 1, already on `origin/main`).
**Type:** mixed — security fixes, defect fixes, tests, and a performance baseline. No scoring/screening semantics were changed without a proven defect.

---

## 1. Dependency advisories (Workstream 1)

Three open Dependabot alerts were open at the start; all three are now resolved and GitHub reports **0 open / 26 fixed**.

| Alert | Package | Sev | Advisory | Fix |
|---|---|---|---|---|
| #29 | `black` (`requirements-dev.txt`) | medium | `GHSA-fj7x-q9j7-g6q6` / CVE-2024-21503 (ReDoS) | bumped to `26.10.1` |
| #28 | `source-map-js` (`myra_web/pnpm-lock.yaml`) | high | `GHSA-68fv-2mgg-jv7q` / CVE-2026-93749 | lockfile bump `1.2.1 → 1.2.2` |
| #27 | `proxy-addr` (`myra_web/pnpm-lock.yaml`) | critical | `GHSA-jqcg-44mw-7w3h` / CVE-2026-90711 | lockfile bump `2.0.7 → 2.0.8` |

Notes:

- The `black` alert was **introduced by the Phase 1 test/CICD work** (it added `requirements-dev.txt` pinning `23.3.0`). Fixing only CVE-2024-21503 would not have been enough: `pip-audit` flags `24.x` via `PYSEC-2026-2120` (fixed in `26.3.0`) and `PYSEC-2026-2121` (fixed in `26.3.1`). The pin was therefore taken to **`26.10.1`** (latest stable, `pip-audit`-clean) in both `requirements-dev.txt` and the `black` pre-commit hook rev.
- The npm fixes were **lockfile-only** (`pnpm --dir myra_web update source-map-js proxy-addr`); `package.json` was not modified. `express` is declared in `package.json` but never imported by the app, so the `proxy-addr` code path is not actually reachable — removing the unused dependency is a follow-up, not a security requirement.
- `myra_web/package-lock.json` appears in older Dependabot history but is **not tracked and does not exist on disk**; the tracked lockfile is `myra_web/pnpm-lock.yaml`.

Evidence:

```
gh api repos/Nirav846/myra/dependabot/alerts -> all state=fixed, 0 open
pnpm --dir myra_web audit                    -> 0 info/low/moderate/high/critical
python -m pip_audit -r requirements-dev.txt  -> No known vulnerabilities found
```

---

## 2. Dataflow map (Workstream 2)

Verified by reading the modules below (not inferred from names). The pipeline is
**daily/EOD only** — there is no intraday source, so nothing here assumes sub-minute refresh.

```
  INGEST            myra_app/fetcher.py  →  myra_app/utils/bhavcopy_parser.py
                    myra_app/daily_ingestor.py  (is_trading_day, calculate_missing_dates)
                          │  writes technical_data
                          ▼
  STORE             myra_technical.db + 7 sidecars (myra_app/librarian_core.py DB_MAP)
                    resolved only via myra_app/constants.py DB_DIR · WAL mode
                          │
  ENRICH            myra_app/feature_enrichment.py::process_enrichment_pipeline (daily)
                    feature_enrichment.enrich_from_dataframe  (batched, tools/enrich_history.py)
                    myra_app/utils/smc_calculator.py
                          │  writes back enrichment columns (myra_app/safe_write.py only)
                          ▼
  SOURCE FALLBACK   myra_app/data_sources/{cache,registry,resolver}.py
                    myra_app/enrichment_sources.py (yfinance → Morningstar → Screener.in)
                    myra_app/fundamental_sync.py (camelCase→snake_case in _merge_and_insert)
                          │
  SCAN              myra_app/engine.py::run_scan(symbols, strategy, as_of_date)
                    → myra_app/strategies/*.scan()   (7 registry + DCB endpoint-only)
                    → myra_app/db/bulk_loader.py::load_ohlcv_for_universe()
                      + rows_for_symbol() / get_df_for_symbol()
                    multi-scanner batch: myra_web/confluence_batch.py::run_confluence_batch()
                          │
  RANK              myra_app/results_manager.py::apply_global_ranking / sanitize_results
                          │
  SURFACE           myra_web/routes/*   (/api/data-health, /api/scanners, /api/pipeline)
                    myra_web/src/*      (HealthStatusBar.tsx, scanners/portfolio views)
```

### Freshness sub-flow

`/api/data-health` (in `myra_web/routes/health.py`) reports staleness. It needs the
*last trading session* from `myra_app/daily_ingestor.py::is_trading_day`, which is
backed by the calendar sidecar (`myra_calendar.db`). `Librarian.get_expected_trading_day`
was **not** reusable from this endpoint: it derives the date from `get_max_price_date`,
which is the very value being validated, so using it would make the check circular.
Phase 2 therefore added a self-contained helper (see §3.1).

---

## 3. Confirmed correctness defects (Workstream 3)

### 3.1 Freshness reported as calendar days (fixed)

**Defect.** `/api/data-health` computed `days_behind` as *calendar* days since the
latest OHLCV row. `HealthStatusBar.tsx` turns red at `>= 3`, so every Monday and every
day after a market holiday the dashboard raised a false "data is behind" alarm even when
data was perfectly current (e.g. Friday's close is only 1 session behind on Monday, but
3 calendar days).

**Fix.** New pure helper `trading_day_freshness()` in `myra_web/utils.py` walks
`is_trading_day` backwards from the latest row (18:30 IST session-ready cut-off, 14-day
cap) and returns `(expected_trading_date, trading_days_behind)`. `health.py` now emits
`trading_days_behind` + `expected_trading_date` alongside the legacy `days_behind`
(kept for compatibility); the UI prefers `trading_days_behind` with thresholds
0 (green) / 1 (warning) / >=2 (error) and a tooltip showing the expected session.

**Regression tests.** `tests/test_health_freshness.py` (8 tests) cover: current data on a
Monday → 0 sessions behind but 3 calendar days; post-holiday; weekend; session-not-ready
before 18:30; unknown/non-trading rows; cap behaviour.

### 3.2 Final ranking silently returned unsorted rows (fixed)

**Defect.** `myra_app/results_manager.py::apply_global_ranking` read `df["Stage"]` and
`df["Stars"]` directly. Any scan whose result lacked those optional columns raised
`KeyError`; the surrounding broad `except Exception: return df` swallowed it, so the
ranking step **silently no-opped** and rows were surfaced in arbitrary order with no
error and no log.

**Fix.** Missing `Stage`/`Stars` now default to lowest priority (unknown stage / no
stars) via guarded column access, and the residual `except` now logs a warning instead
of failing silently. Ranking semantics are otherwise unchanged.

**Regression tests.** `tests/test_results_manager.py` (10 tests) lock the ordering
contract (stage dominance; anomaly outranks stage; stars and money-flow tie-breaks;
Indian-format money-flow parsing; ranking stability for equal input), the two
missing-column regressions, temp-column cleanup, and `sanitize_results` price filtering.
This path previously had **no tests at all**.

---

## 4. Scanner performance baseline (Workstream 4)

There is no dedicated benchmark harness in `tools/`, so the baseline uses the
**existing scanner-parity suite** (`tests/test_bulk_loader.py`), which exercises real
scanner `scan()` code against the live ~2 GB `myra_technical.db` (read-only, skipped
when the DB is absent).

Reproducible command:

```powershell
$env:PYTHONPATH="D:\01screener\Myra"
python -m pytest tests/test_bulk_loader.py -q --durations=20
```

Measured (2026-10-13, local machine, module-scoped bulk fixture loaded once):

| Phase | Elapsed |
|---|---|
| `TestScannerBulkParity::test_wyckoff_parity` | 46.96 s (110.64 s in the full-suite run) |
| `TestScannerBulkParity::test_accumulation_base_parity` | 29.74 s |
| bulk universe load (module fixture, one-off) | 29.16 s (13.52 s warm) |
| `TestScannerBulkParity::test_trigger_parity` | 8.77 s |
| `TestScannerBulkParity::test_dcb_parity` | 6.85 s |
| **whole module** | **124.75 s** (wall 128.93 s) |

> Caveat: this baseline depends on the live dataset, which grows over time, so absolute
> numbers are not stable across days. Treat it as a regression canary (same command,
> roughly comparable numbers), not a fixed SLA. The bulk-load cost dominates and is paid
> once per module — the suite does **not** reload the universe per test.

### Finding: the multi-scanner batch reloads the universe once per scanner

`myra_web/confluence_batch.py::run_confluence_batch` runs ~12 confluence scanners
sequentially in one process. Every scanner's `scan()` calls
`bulk_loader.load_ohlcv_for_universe(...)` itself (e.g. `accumulation_base_scanner.py:231`,
`trigger_scanner.py:206`, `wyckoff_automaton.py:871`, `operator_fingerprint_scanner.py:140`),
and there is no shared cache, so the full-universe query is repeated **N times** for the
same `as_on_date`.

**Not fixed in this phase.** Sharing one loaded frame across scanners is a real win but
carries a correctness hazard (scanners receive the same mutable dict of DataFrames; a
process-level cache must also invalidate on ingest or it would serve stale OHLCV, which
would violate the project's no-stale-data invariant). That trade-off needs a designed
invalidation key (DB path + mtime/size + window + symbols) and live-DB verification,
which is beyond a safe one-shot change. Documented here as the top deferred perf item.

---

## 5. Verification

All commands run locally on Windows / Python 3.12.10.

| Check | Command | Result |
|---|---|---|
| Full suite + coverage | `pytest tests/ --cov=myra_app` | **1129 passed**, 16 warnings, 0 failed/errored, 418.89 s, 41% coverage (20360 stmts) |
| Formatting | `black --check` (26.10.1) on changed files | 5 files unchanged |
| Lint | `flake8` on changed files | exit 0 (clean) |
| Frontend typecheck | `pnpm --dir myra_web lint` (`tsc --noEmit`) | clean |
| Frontend build | `pnpm --dir myra_web build` | built in 22.83 s (pre-existing warnings only) |
| npm audit | `pnpm --dir myra_web audit` | 0 vulnerabilities |
| Python audit | `pip-audit -r requirements-dev.txt` | No known vulnerabilities found |
| Dependabot | `gh api .../dependabot/alerts` | 26 fixed, **0 open** |

Test count grew from 1111 (end of Phase 1) to **1129** (+8 freshness, +10 ranking).

### Pre-existing issues observed (not introduced here, still reported)

- `myra_app/feature_enrichment.py:138,143,148` — Polars `DeprecationWarning`:
  `rolling_mean(min_periods=...)` renamed to `min_samples` (Polars >= 1.21).
- `xgboost` emits `Unknown file format: 'xgb'. Using UBJSON ('ubj')` when loading the
  serialized booster — cosmetic, but the model file format should be standardised.
- `test_wyckoff_parity` wall time swung 47 s → 111 s between runs on the live DB (I/O /
  OS cache variance), which is why the perf numbers above are labelled a canary.
- `requirements.in` lists local module names rather than PyPI packages (left untouched).

---

## 6. Commits

| Commit | Summary |
|---|---|
| `7be28e4` | `fix(security)`: bump vulnerable dependencies past known advisories |
| `5a53b79` | `style(tests)`: reformat test suite for black 26.10.1 |
| `ec2c40f` | `fix(freshness)`: measure staleness in trading sessions, not calendar days |
| `94ce6a1` | `fix(ranking)`: keep final ranking working when optional columns are absent |

---

## 7. Deferred / next phase

1. **Share one bulk load across the confluence batch** (§4) — biggest measured perf win;
   needs an mtime-invalidated cache and live verification.
2. Remove the unused `express` dependency in `myra_web/package.json`.
3. Add regression tests for `score_components_v2.py` and `positional_engine.py` (identified
   as untested risk surfaces; not touched here).
4. Standardise the XGBoost model serialization format; clear the Polars `min_periods`
   deprecation in `feature_enrichment.py`.
