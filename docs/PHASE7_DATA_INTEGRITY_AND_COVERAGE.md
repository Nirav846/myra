# Phase 7 - data integrity, technical coverage & fundamental freshness

Directive: (1) eliminate the unexplained full-suite write to the live
`myra_app/db/myra_metadata.db`, with a confirmed root cause and regression
protection; (2) diagnose and repair verified causes of missing or partial
`technical_data` history, with reproducible coverage statistics; (3) diagnose
fundamental coverage, establish trustworthy freshness semantics, and integrate
only improvements that are defensible from already-ingested data; (4) validate
the Phase 6 opt-in ranking without promoting it to the default.

Guardrails honoured throughout: no historical market or fundamental data was
deleted, rewritten, or synthesised; no metric definition, score weight, window,
threshold, or screening rule was changed; every database access was read-only
(`file:...?mode=ro`) or a plain `SELECT`; no broad refactor, new dependency, new
table, or new cache was introduced. Where a fix could not be justified from
evidence it was **not** made, and the limitation is recorded in sections 7/11.

## 1. Starting and final commit hashes

| Item | Value |
|------|-------|
| Branch | `main` |
| Phase 7 **starting** commit | `768852e` (`docs: correct the Phase 6 window-anchoring impact numbers`), `== origin/main`, clean tree |
| Phase 7 **final** commit | `9575853` (`fix(data-integrity): stop tests writing the live metadata DB; Phase 7 audit`) - recorded by the follow-up `docs:` commit |
| Working tree at start | clean (`git status --short` empty) |
| Python / pandas / numpy / polars / pytest | 3.12.10 / 2.2.3 / 1.26.4 / 1.40.1 / 9.0.3 |
| Live DBs present | yes - `myra_app/db/myra_technical.db` (2,053,451,776 B), `myra_valuation.db` (262,062,080 B), `myra_metadata.db` (2,228,224 B), + 12 more (`quick_check` all `ok`, section 4) |

## 2. Reproducing the full-suite metadata-DB write (and what actually happens)

The Phase 6 record claimed the test suite left `myra_metadata.db` modified, based
on a file **mtime** change after a suite run. Phase 7 confirmed the write, found
its exact source, and fixed it.

Reproduction steps:

1. Save this probe as `probe2.py` (it captures the **real** `sqlite3.connect`
   before `tests/conftest.py` patches it) and run the suite with `-p probe2`:

   ```python
   import hashlib, os, sqlite3
   _REAL = sqlite3.connect        # captured before conftest patches sqlite3.connect
   URI = "file:D:/01screener/Myra/myra_app/db/myra_metadata.db?mode=ro"
   LOG = open(os.path.join(os.environ["TEMP"], "opencode", "probe2.log"), "w")

   def snapshot():
       c = _REAL(URI, uri=True)
       out = {}
       for (t,) in c.execute("SELECT name FROM sqlite_master WHERE type='table'"):
           h = hashlib.sha1(); n = 0
           for row in c.execute('SELECT * FROM "%s"' % t):
               h.update(repr(row).encode()); n += 1
           out[t] = "%s(%d)" % (h.hexdigest()[:10], n)
       c.close(); return out

   _prev = snapshot()
   def pytest_runtest_teardown(item, nextitem):
       global _prev
       cur = snapshot()
       for t in [t for t in cur if cur[t] != _prev.get(t)]:
           LOG.write("CHANGED after %s: %s %s -> %s\n"
                     % (item.nodeid, t, _prev.get(t), cur[t])); LOG.flush()
       _prev = cur
   ```

2. Run the whole suite with the probe loaded.

Two traps must be avoided or the probe reads a false "no change" (both caught the
first attempt, see section 9): the probe must capture the real `sqlite3.connect`
**before** conftest installs its live-access guard, or every read raises
`_LiveDbAccessError` and returns identical error sentinels; and it must digest the
**rows**, not just their counts, or an `UPDATE` is invisible.

Observed result, reproducible on every run:

* The probe pinpoints exactly **three** writes, one per test, all in
  `tests/test_task_offload.py`:

  ```
  CHANGED teardown tests/test_task_offload.py::test_spawn_task_runs_function_in_background
     sqlite_sequence  e2b8c5d189(3) -> 1b0486dd9f(3)
     task_registry    b9fe052583(2529) -> f70d96ef49(2530)
  CHANGED teardown tests/test_task_offload.py::test_spawn_task_passes_kwargs
     task_registry    f70d96ef49(2530) -> 159ff91448(2531)
  CHANGED teardown tests/test_task_offload.py::test_spawn_task_error_handling
     task_registry    159ff91448(2531) -> bd71868312(2532)
  ```

* The live `task_registry` grows by **3 rows per full-suite run** (2529 -> 2532).
* The guard reports it as `wal_frames=12` on `myra_metadata.db` - exactly three
  INSERTs x ~4 WAL frames - with **identical size, mtime, and `sync_log` digest**,
  i.e. a committed-but-not-checkpointed WAL write.

## 3. Confirmed root cause, the fix, and remaining uncertainty

**Confirmed root cause: `tests/test_task_offload.py` INSERTs into the live
`myra_metadata.db`.** Its three tests (plus the module's own setup path) call
`myra_web.myra_fastapi_server._spawn_task`, which calls
`myra_app.task_tracker.register(...)`, which runs `INSERT INTO task_registry`
(plus an AUTOINCREMENT bump to `sqlite_sequence`).

Why every existing guard missed it:

* `fixture_db_dir` is deliberately **opt-in** (its docstring records why a
  session-scoped version leaked the redirect into unrelated tests). This module
  never requested it, so `DB_DIR` stayed live for its tests.
* `task_tracker._get_conn()` re-reads `DB_DIR` on each call but memoises the
  connection in a module-level `_conn`. Nothing resets that cache outside
  `fixture_db_dir`, so the module opened a connection to the live metadata DB and
  reused it.
* The write is **WAL-only**: at commit time the main `.db`'s size and mtime are
  unchanged, so the pre-existing size+mtime signature could not see it. The mtime
  moves only later, when the process exits and SQLite checkpoints the WAL - which
  is exactly the Phase 6 "file mtime changed after the suite" observation, now
  fully explained (no external process involved).
* The first Phase 7 probe reported "0 changes" because its reads were blocked by
  the live-access guard (equal error sentinels) **and** it compared row counts; a
  probe fault, corrected in section 2.

**Fix (shipped), three parts - no application code changed:**

1. `tests/test_task_offload.py` gains an autouse `_isolated_db(fixture_db_dir)`
   fixture, so every test in the module runs against the fixture DBs (`DB_DIR`
   redirected, `task_tracker._conn` reset). `task_tracker` itself is correct; the
   defect was an un-isolated test.
2. The guard can now see a committed-but-not-checkpointed WAL write.
   `tests/conftest.py` parses the SQLite WAL header and counts committed frames:
   `_WAL_MAGICS = (0x377F0682, 0x377F0683)`, `_WAL_HEADER_BYTES = 32`,
   `_WAL_FRAME_HEADER_BYTES = 24`, page size 1 -> 65,536;
   `_wal_committed_frames(path)` returns 0 for absent/empty/junk sidecars and
   `(size - 32) // (24 + page_size)` otherwise; `_db_signatures()` appends
   `:wal_frames=N` **only when `N > 0`**. This is what made the defect visible.
3. A structural regression test (`tests/test_db_isolation.py` section 8) fails if
   any test module calls `_spawn_task(` without requesting `fixture_db_dir`, so a
   future module cannot reintroduce it. (The other modules that touch the tracker
   - `test_mf_holdings_task.py` etc. - monkeypatch `register`/`unregister` and
   never reach a database; only `_spawn_task` is the write path.)

**Remaining uncertainty: none.** The Phase 6 mtime signal, the guard's
`wal_frames=12`, the three extra rows, and the checkpoint-on-exit mtime moves are
all accounted for by this single cause. Side note: the three rows the bug wrote
(ids 2530-2532 in the live `task_registry`) are left in place - they are expired
`Done` rows that `task_tracker.get_active_tasks()` purges on its own next call,
and deleting production rows is out of scope.

## 4. Database integrity verification

Every SQLite file under `myra_app/db/` was opened read-only and validated
(2026-10-14):

| DB | journal | tables | `quick_check` | time |
|----|---------|--------|---------------|------|
| `myra_archive.db` | wal | 4 | ok | 1.2 s |
| `myra_cache_network.db` | wal | 2 | ok | 0.0 s |
| `myra_calendar.db` | wal | 2 | ok | 0.0 s |
| `myra_governance.db` | wal | 5 | ok | 0.0 s |
| `myra_institutional.db` | wal | 13 | ok | 1.4 s |
| `myra_metadata.db` | wal | 18 | ok | 0.2 s |
| `myra_news.db` | delete | 2 | ok | 0.0 s |
| `myra_options.db` | wal | 3 | ok | 0.0 s |
| `myra_portfolio.db` | wal | 9 | ok | 0.0 s |
| `myra_scoring.db` | wal | 4 | ok | 0.2 s |
| `myra_technical.db` | wal | 5 | ok | 59.8 s |
| `myra_valuation.db` | wal | 21 | ok | 11.1 s |

No corruption, no orphaned `-wal` requiring recovery, and no data loss. The
isolation suite (`tests/test_db_isolation.py`, `tests/test_db_isolation_order.py`)
is green, so no test writes any live DB.

Three **pre-existing empty stray files** also live in that directory -
`myra_meta.db` (0 tables), `technical.db` (0 tables), `valuation.db` (0 tables).
They are **not** in `DB_MAP` and no repository code path creates them:
`technical.db`/`valuation.db` are the bare names from an already-fixed
"bare-name" bug (see the comment at `myra_app/fundamental_manager.py:69` and
`tests/test_fundamental_manager.py`), and `myra_meta.db` is referenced by no
source file at all. They are left in place on purpose - deleting files in the
live data directory is outside this phase's mandate (and the "never delete data
you did not create" invariant). Recommended follow-up: confirm with the owner
and remove.

## 5. Technical coverage: before / after

Universe definition used for all figures: the latest scan snapshot
`myra_reports/scan_rs_rating.csv` (938 symbols) crossed with `technical_data`
presence, plus `symbols_master` (3,610 rows) for the "why is a symbol missing
entirely" analysis. Window = the **20 most recent distinct `technical_data.date`
values** at measurement time (`2026-08-17 .. 2026-10-12`), i.e. trading sessions,
not calendar days. Measured read-only on 2026-10-14.

Baseline table (`before`), from the live DB:

| Metric | Value |
|--------|-------|
| `technical_data` rows | **8,009,058** |
| distinct symbols | 3,423 |
| distinct dates | 7,858 |
| date range | 1995-01-02 .. 2026-10-12 |
| rows on each of the last 25 dates | ~3,165 .. 3,231 |
| symbols with 20/20 sessions (last 20 dates) | 2,895 (of 3,353 trading in-window) |
| snapshot symbols with 20/20 sessions | **639** / 938 |
| snapshot symbols with a partial window | 458 |
| snapshot symbols with 0 sessions | 292 |
| per-symbol session histogram (snapshot) | `{0:292, 5:2, 9:1, 11:1, 17:1, 19:2, 20:639}` |
| field completeness (all history) | `open/high/low/close/volume/delivery/delivery_pct` = 100 % |
| always-NULL columns (all 8.0 M rows) | `vwap`, `delivery_qty`, `stock_return` |
| `delivery_ratio`, `trades` | only from 2026-08-17 (124,126 / 8,009,058 rows, 39 dates) |

There was no earlier reproducible baseline to diff against - the AGENTS.md claim
of "~2.25 M technical rows" is simply wrong (the table holds 8.0 M), so the
figures above are recorded as the Phase 7 baseline. `delivery` is exactly
`delivery_pct * volume / 100` on every row where both are present, i.e.
`delivery` **is** the delivery quantity and `delivery_qty` is an orphaned legacy
duplicate.

**After / repaired in this phase:** the only genuine data bug found in this area
was in the *reader*, not the data - `myra_app/portfolio_db.get_delivery_alerts`
selected the never-populated `delivery_qty` and divided it by volume, raising
`TypeError` that its `except sqlite3.Error` did not catch. Fixed to
`COALESCE(delivery_qty, delivery) AS del_qty`; the `close` column the caller also
needs was added. **No market row was altered.** Coverage figures are therefore
identical before/after by design.

## 6. Root causes of the technical gaps (confirmed vs unexplained)

All confirmed, none unexplained:

1. **New listings** - a real IPO has no history before listing: `ACEVECTOR`
   (first row 2026-10-05), `ADROITIND` (2026-09-30), `AGASTYAEN` (2026-09-25).
   Correct behaviour; a fabricated back-fill would be wrong.
2. **Illiquid / rarely-traded symbols** - the last *trading session* for a symbol
   may be months old (`ADL` first row 2020-10-01, many `_SME` tickers), so it has
   far fewer than 20 sessions in a 20-session window. Correct behaviour.
3. **Universe inflation** - **1,191 of 3,610** `symbols_master` symbols have
   **zero** `technical_data` rows: ETFs (`ABSLBANETF`, ...), rights-entitlement
   placeholders (`5PAISA-RE`, `ABIN-RE`), and SME-base tickers (`AAKAAR`, where
   only `AAKAAR_SME` trades). These are not missing prices; they are symbols that
   do not have EOD equity rows, so 292 of the snapshot's 938 symbols having no
   data is largely this effect.
4. **Dead columns** - `vwap`, `delivery_qty`, `stock_return` are NULL across all
   8.0 M rows; readers (like the portfolio bug above) must not depend on them.
5. **Recently-added columns** - `delivery_ratio` and `trades` only exist from
   2026-08-17, so any historical average of them is meaningless.

At the date level the ingestion is complete: 39 distinct session dates from
2026-08-17 to 2026-10-12 with **no missing trading date** and no dropout.

## 7. Fundamental coverage: before / after

`fundamentals` = **3,948 rows / 3,948 distinct symbols** (measured 2026-10-14).
This phase is **diagnosis only** - no fundamental value was written, so the
coverage table is both "before" and "after":

| Column | non-null | % of 3,948 | zeros |
|--------|----------|-----------|-------|
| `pe` | 3,275 | 83.0 % | 39 |
| `market_cap` | 3,246 | 82.2 % | - |
| `sector` | 3,905 | 98.9 % | - |
| `net_margin` | 3,309 | 83.8 % | - |
| `shares_outstanding` | 3,320 | 84.1 % | - |
| `promoter_holding_pct` | 3,198 | 81.0 % | 56 |
| `free_float_pct` | 2,679 | 67.9 % | - |
| `dividend_yield` | 1,510 | 38.2 % | 37 |
| `inst_holding` | 728 | 18.4 % | 267 |
| `roe` | 691 | **17.5 %** | 0 |
| `eps` | 439 | 11.1 % | - |
| `book_value` | 433 | 11.0 % | - |
| `face_value` | 1 | 0.03 % | - |
| `profit_growth`, `sales_growth`, `debt_to_equity`, `roe_ttm`, `operating_margin`, `pledged_pct`, `beta` | **0** | 0 % | - |

camelCase aliases still carried on the same rows: `peRatio` 432 (38 zeros),
`priceToBook` 448, `returnOnEquity` 442, `returnOnAssets` 123, `marketCap` 8;
`earningsGrowth` / `revenueGrowth` / `priceToSales` / `earningsPerShare` /
`bookValuePerShare` are **0**. `screener_fundamentals` has only **3** rows.
Join health: 992 of 3,948 fundamental symbols (**~25 %**) are not in
`symbols_master` (mostly `_SME`, 527 of them); there are 0 dotted symbols.

**The one integration candidate, deliberately not integrated:**
`quarterly_results` (9,095 rows / 745 symbols / 77 `report_date`s spanning
`Dec 2005 .. Sep 2026`; `net_profit` non-null 5,994; 716 symbols with >= 5
periods) is the only legitimate provenance for `profit_growth`
(`(net_profit_t - net_profit_{t-4}) / |net_profit_{t-4}| * 100`). Adding it would
introduce a **new metric definition** that feeds a rank that feeds the score, so
it is out of scope under the "do not change metric definitions or scoring" rule.
It is documented here as the next phase's first task, with the definition above.

## 8. Metric definitions, units, provenance, freshness policy

Definitions (locked, must not be guessed):

| Metric | Source | Definition |
|--------|--------|------------|
| `avg_volume_20d` | `technical_data.volume` | mean of the last 20 trading sessions (shares) |
| `avg_delivery_20d` | `technical_data.delivery_pct` | mean daily delivery **percentage** (0-100); derive `delivery*100/volume` only when `delivery_pct` IS NULL |
| `roe` | `fundamentals.roe` | percent, as stored |
| `ProfitGrowth` | `fundamentals.profit_growth` | percent, as stored (currently 0 % populated) |
| delivery quantity | `technical_data.delivery` | equals `delivery_pct * volume / 100`; **not** a separate metric |

Unit traps confirmed on live data: `roe` is a **percent** (`RELIANCE` 8.91,
`TCS` 45.57, `INFY` 32.0) while `net_margin` is a **fraction**
(`RELIANCE` 0.0665); `market_cap` is absolute INR (`RELIANCE` 1.568e13).
`promoter_holding_pct` (50.48 / 71.77) and `dividend_yield` (0.51) are percents.
`inst_holding` is **not trustworthy** for ranking: values are implausibly small
and source-tagged `UPSTOX` (`RELIANCE` 0.51, `TCS` 3.01, `INFY` 4.69) with 267
zeros. `roe` has extremes (`WINNY_SME` 1486.63, min `-147.39`), so a rank layer
must stay robust to outliers. One symbol (`TATAMOTORS`) has a `fundamentals` row
with every field empty.

Provenance: `source_ms = 'MORNINGSTAR'`, `source_nse = 'YFINANCE'`.

Freshness policy (measured, three overlapping columns - do not conflate them):

| Column | non-null | meaning | range |
|--------|----------|---------|-------|
| `last_updated` | 3,934 | **ingestion/retrieval** timestamp (mixed date-only + datetime strings) | 2026-04-04 .. 2026-10-13 |
| `date` | 3,372 | **source data / report date** | 2026-05-14 .. 2026-10-12 |
| `last_fundamental_update` | 3,612 | **ingestion** timestamp (mixed tz-aware ISO + date-only) | - |

Policy: a staleness guard must be built on **`date`** (the source data date), not
on `last_updated`/`last_fundamental_update` (which only say when *we* fetched, and
which move even on a failed/empty fetch). No such guard exists today - that is a
known gap (section 11), not a defect fixed here. `fundamental_score`
(`myra_app/score_components_v2.py:129-146`) already degrades gracefully (neutral
0.4 / 0.3) when a metric is absent, which was verified not to fabricate values.

## 9. Tests added and exact results

New/updated tests, all run on the isolated fixture DBs (`tests/fixtures/*.db`),
never on a live DB:

* `tests/conftest.py` - `_wal_committed_frames()` + `_db_signatures()` WAL term.
* `tests/test_task_offload.py` - **fix**: an autouse `_isolated_db(fixture_db_dir)`
  fixture so the module stops writing the live `task_registry` (section 3).
* `tests/test_db_isolation.py` - new section 7:
  `test_wal_only_write_is_detected` (writes a table that is **not** covered by the
  `sync_log` digest, so the WAL term is genuinely exercised) and
  `test_wal_frames_ignore_empty_and_junk_sidecars`; plus new section 8
  `test_task_registry_writer_modules_request_isolation` (a module that calls
  `_spawn_task` must request `fixture_db_dir`).
* `tests/test_portfolio_delivery_alerts.py` - **new**: production-faithful
  `technical_data` (`delivery` set, `delivery_qty` NULL, as in the live DB) that
  asserts the ABSORPTION and DELIVERY SURGE alerts fire and render `80.0 %`.
* `tests/test_ranking_inputs.py` - **+2 (Phase 7 WS4)**: `test_diagnostics_run_read_only_and_write_nothing`
  (runs the rank-input queries over a `mode=ro` connection and asserts the main
  DB file's SHA-256 is byte-identical afterwards) and
  `test_duplicate_symbols_produce_one_entry_and_no_row_multiplication`
  (duplicate symbol lists cannot double-count or multiply result rows).

Results (exact):

| Command | Result |
|---------|--------|
| `pytest tests/test_ranking_inputs.py tests/test_shadow_ranking.py tests/test_portfolio_delivery_alerts.py tests/test_db_isolation.py tests/test_db_isolation_order.py -q` | **51 passed** in 14.49 s |
| `pytest tests/test_db_isolation.py tests/test_db_isolation_order.py tests/test_task_offload.py tests/test_ranking_inputs.py -q` | **48 passed** in 18.59 s |
| `pytest tests/test_db_isolation.py -k "task_registry_writer or wal_only or wal_frames" -q` | **3 passed** |
| `pytest tests/test_task_offload.py -q` (after the isolation fixture) | **4 passed**, and the row-digest probe logs **no** live-DB change |
| `pytest tests/test_portfolio_delivery_alerts.py -q` | **1 passed** in 0.70 s |
| WAL-guard regression, guard fix reverted | **1 failed** - "a committed WAL-only write was not detected" (proves the new test is load-bearing) |
| Live-write regression, isolation fixture reverted | guard fails the session naming `myra_metadata.db ... wal_frames=12` (proves the fix is load-bearing) |
| Portfolio regression, fix reverted | **1 failed** - `TypeError` at `myra_app/portfolio_db.py` (proves the fix is load-bearing) |
| `black` 26.10.1 / `flake8` (7.3.0) | clean on all touched files |
| `tools/performance_guard.py` | **268 violations / 0 CRITICAL** - identical to the pre-existing baseline |
| Full suite | **1223 passed / 0 failed** in 265 s (1217 before + 6 new), guard clean |

WS4 validation of the Phase 6 opt-in (re-run 2026-10-14):

* `ranking_inputs_enabled()` with no argument/env = **False**; `False`/`True`
  explicit pass through unchanged -> the production ordering path is untouched.
* `tools/compare_v25_orderings.py --strategy rs_rating --snapshot myra_reports/scan_rs_rating.csv`
  ran to completion and left **every** live DB's main-file size and mtime
  **unchanged** (verified by hashing signatures before/after the subprocess).
  It reproduced the Phase 6 conclusion (moderate re-ordering, no improvement):
  candidates 938, Spearman **0.6625**, top-20 overlap **13/20**, coverage
  `roe=71 / ProfitGrowth=0 / avg_delivery_20d=639 / avg_volume_20d=639`.

## 10. Runtime / query-performance impact

* The WAL-frame term adds one `stat` + a 32-byte header read per DB per check -
  below noise (the isolation modules still run in seconds and the full suite is
  unchanged at ~4.4 min).
* `myra_app/ranking_inputs.compute_ranking_inputs` is window-bounded and uses
  `idx_tech_symbol_date`: **2.14 s / 1.75 s** for the two snapshots, versus
  27.39 s / 15.57 s for the unbounded form (~13x faster) - the only perf-sensitive
  code touched this phase. It is opt-in and off by default.
* The portfolio delivery-alert fix is an extra `COALESCE` on a 5-row query -
  no measurable effect.
* No new dependency, index, table, or cache was added.

## 11. Unresolved issues and the next evidence-gathering step

1. **Residual test rows in production:** the WS1 bug left three expired
   `task_registry` rows (ids 2530-2532) in the live `myra_metadata.db` before it
   was fixed. They are `Done` rows with an `expiry`, so
   `task_tracker.get_active_tasks()` purges them on its next call; they were
   deliberately not deleted by hand (no manual production deletes). *Next step:*
   none required - just confirm they are gone after the next live scan.
2. **`profit_growth` has no producer** despite `quarterly_results` being a
   legitimate source (section 7). *Next step:* implement and validate the
   `t` vs `t-4` definition behind the flag, with out-of-sample evidence.
3. **`roe` coverage is only 17.5 %** on the join universe; **`inst_holding` is
   untrustworthy**; **`market_cap`** relies on `shares_outstanding`. Data funding,
   not scoring.
4. **No `last_fundamental_update`/`date` staleness guard** (section 8).
5. **Fixtures can mask production-only defects:** `tests/fixtures/build_fixture_db.py`
   fills *every* real column, so it cannot reproduce a "column never populated in
   production" bug - exactly why the `delivery_qty` crash survived. *Next step:*
   add a fixture mode that leaves known-dead columns NULL.
6. **Documentation drift:** `AGENTS.md` says "~2.25 M technical rows" (actually
   8.0 M) and "8 SQLite sidecars" (there are 12 populated DBs + 3 stray empty
   files). Flagged, not silently edited.
7. **Pre-existing, not introduced here:** `myra_web/routes/portfolio.py` swallows
   exceptions with `except Exception: pass` (which is *why* the delivery alerts
   never surfaced); `universe_loader` still `0`-defaults ~40 cache columns
   (conflating "absent" with `0`); `EXTREME GREED` regime branch unreachable;
   `results_manager.py:94,102` dead `.fillna`; `tools/backtest_dcb_traction.py`
   hardcodes DB paths; CI `Node.js 20 deprecated` / `/usr/bin/git exit 128`
   warnings.

## 12. Explicit statement: default ranking order and scoring weights are unchanged

The default production ranking order and the scoring weights are **unchanged** by
Phase 7, exactly as Phase 6 left them:

* Score weights remain `0.25 / 0.15 / 0.20 / 0.10 / 0.10` (+ 0.30 fundamentals),
  the 70/30 split, regime multipliers `1.15 / 0.9`, and drawdown `0.8 / 0.95`.
* The Phase 6 live-input path stays **opt-in and off by default**
  (`ranking_inputs_enabled()` = `False` with no argument/env; the gate at
  `myra_app/universe_loader.py:321` is only entered when it returns `True`).
* The known Phase 4/5 anchor scalars are unchanged: `_BULL` -> 71.1,
  EXTREME FEAR -> 81.8, EXTREME GREED -> 64.0, drawdown 0.3 -> 67.5,
  drawdown 0.5 -> 56.9, `{"close": 1}` -> 20.3 (covered by the existing scorer
  tests, all green in the full-suite run above).
* The `myra_app/portfolio_db.py` fix corrects a *crash* in a delivery-alert
  reader; it does not feed the positional score, and it changed no stored data.
