# Phase 19 — V2.5 Technical-Signal Contract & Screening Blocker Repair

**Baseline:** `main` @ `398c17c` (Phase 18), in sync with `origin/main`, tree clean,
three stashes preserved (`stash@{0..2}`).
**Outcome:** four proven execution defects repaired, the v2.5 input contract
settled into mandatory/optional/gated/unsourced classes, and an evidence-based
build-readiness verdict — the lake **builder** now works, the lake still **cannot**
produce a trustworthy `MYRA_Score_v25`.
**Not done, on purpose:** no lake build, no OHLCV rebuild, no fundamental refresh,
no scheduler start, no weight/threshold/formula/ordering change, no growth-ranking
activation.

The full field-by-field contract lives in
[`docs/V25_TECHNICAL_SIGNAL_CONTRACT.md`](V25_TECHNICAL_SIGNAL_CONTRACT.md).
This document records what was changed, what was verified, and what is still a
decision.

---

## 1. Repairs

### F1 — the lake builder could not write a single artifact (build blocker)

`_compute_symbol_indicators` returned a frame with **duplicate column names**
(`delivery_pct`, `vwap`, `delivery_divergence_score`), and `to_parquet` rejects
that:

```
PARQUET WRITE FAILED: ValueError Duplicate column names found: ['symbol', 'open', ...]
```

Mechanism: `get_ohlcv` already returns some lower-case, already-enriched columns;
the producer's `capitalize()` pass renames them (`delivery_pct` → `Delivery_pct`),
so its own later assignment to `delivery_pct` appends a *second* column instead of
overwriting, and the final lower-casing materialises both. `save_indicators`
re-raises, and `build_indicator_lake` catches it per symbol — so a build reported
`failed` for **every** symbol, honestly but permanently, and
`data/indicators/precomputed` could never be populated by anything.

**Fix:** a one-line dedup after the lower-casing, using the same idiom the method
already used after `capitalize()` `(keep="first")`, with the reasoning recorded
in place.

*Why `keep="first"`:* the dropped duplicate for `delivery_pct` is
`(Delivery_qty / Volume).fillna(0.0) × 100`, and `Delivery_qty` is absent from
`technical_data` and ~7% populated in the OHLCV cache — so it is a fabricated
zero. The kept column is the measured NSE delivery percentage (verified exact:
`delivery / volume × 100 == delivery_pct` for 3190/3190 rows on the latest
session). Keeping the measured value rather than the zero is required by the
project invariant. Which source *should* win for the two competing names is
recorded as an open decision, not decided silently.

*Verified (read-only, `RELIANCE`):* before — `ValueError`, no file. After —
`to_parquet` succeeds, 49 columns, no duplicates on read-back, index and `symbol`
tag preserved, `delivery_pct` at the latest row = 59.55 (measured, not 0).

### F2 — the lake's SMC indicators were fabricated zeros

`librarian_intelligence` did `from myra_app.engine import SMCManager`, but
`myra_app.engine` contains **zero** occurrences of `SMCManager`. The `ImportError`
was swallowed by a blanket `except`, so every lake artifact carried
`fvg = 0.0 / bos = False / choch = False` as if measured.

**Fix:** import from `myra_app.smc_manager` (the module that defines the class,
and where `worker_pool` already imports it from), moved **outside** the `try` so a
misnamed module becomes a loud, countable failure rather than a silent zero; the
fallback now logs a per-symbol warning.

*Verified end-to-end:* with a synthetic symbol the artifact now carries real
values — `fvg` in {−1, 0, 1} (sum 8, latest row 1), `bos` sum 37.

### F3 — the worker path never bound `fvg_mid`

Both success paths referenced `fvg_mid` and neither assigned it, so `NameError`
fired for **every** candidate and the worker returned `status: FAILED` for the
whole universe — no scan could surface a result whatever the data held.

**Fix:** bind `fvg_mid = fvg_zone["mid"] if fvg_zone else None` immediately after
the zone is built. The definition is established, not invented:
`fvg_zone["mid"]` is `(top + bottom) / 2`, and the repository's canonical
`SMCManager.get_fvg_buy_zone` returns exactly that as `mid`. Both call sites keep
their own written falsy-fallback, and `screener.py` already recovers a falsy
`Entry` from `LTP × 1.005`, so nothing downstream needed changing.

*Verified:* the primitive path publishes `Entry = 11.0` for a 10–12 zone; the
class path publishes the same, and with no zone falls back to
`round(close × 1.005, 2)`. `screening_modes.worker_path_blocker()` now reports
`None`.

### F4 — the Polars read had no declared schema

`pl.read_database(query, sqlite_conn)` gets no types from the DBAPI cursor, so
Polars inferred from a leading sample. `technical_data`'s leading rows are
NULL-heavy, so an all-NULL sample resolved those columns to the `Null` dtype and
the first real value then failed to append:

```
ComputeError: could not append value: 40.0 of type: f64 to the builder;
make sure that all rows have the same schema or consider increasing `infer_schema_length`
```

**Fix:** `myra_app/utils/sqlite_schema.py` (new) —
`declared_type_to_polars()` plus `sqlite_schema_overrides(connection, table,
columns=None)` reading `PRAGMA table_info`; wired into **both** `pl.read_database`
sites in `engine.py`. An unknown table raises `ValueError` rather than returning
`{}`, because `{}` would silently restore the inference this fixes.

*Verified:* the repro above fails without overrides and reads correctly with them;
`infer_schema_length=None` was rejected as the alternative because it leaves 17
all-NULL columns typed `Null` (breaking comparisons, joins and `to_pandas()`).
A full scan of all 11 INTEGER-declared columns of `technical_data` (8,022,725
rows) found **zero** non-integral values, so `Int64` is exact for the real data;
the one lossy edge (a non-integral REAL stored in an INTEGER-declared column) is
pinned by a test. Empty result sets now return typed columns rather than `Null`,
and the helper works through a `mode=ro` connection.

---

## 2. The v2.5 input contract

`V25_REQUIRED_INPUTS` is now split, and the mode decision follows it:

| Class | Fields | If missing |
|---|---|---|
| Mandatory (7) | `sma50`, `sma150`, `sma200`, `pct_above_ma50_60d`, `avg_delivery_20d`, `avg_volume_20d`, `atr_pct` | `blocked` / `mandatory_inputs_unsourced` |
| Optional (5) | `keltner_upper`, `keltner_lower`, `atr5`, `drawdown`, `smart_money_score` | `degraded` / `optional_inputs_unsourced` |
| Lake-produced | `sma50`, `sma200` | — |
| Gated | `avg_volume_20d`, `avg_delivery_20d` | produced by `ranking_inputs`, behind the off-by-default `MYRA_V25_RANKING_INPUTS` |
| Unsourced (8) | `sma150`, `pct_above_ma50_60d`, `atr_pct`, `atr5`, `keltner_upper`, `keltner_lower`, `drawdown`, `smart_money_score` | no producer anywhere in the repository |

A gap in a mandatory input keeps the mode **blocked**: the scorer would still
return a number, and that number would be arithmetic on substituted placeholders,
which is precisely what must not be published as `MYRA_Score_v25`. A gap in an
optional input only withholds a squeeze bonus or a drawdown penalty, so the mode
is `degraded` and never `operational`.

`evaluate()["v25_input_contract"]` exposes all five lists, so a consumer does not
have to re-derive them from the scorer's source. Two Phase 18 assertions were
updated deliberately (the intent was strengthened, not weakened):
`test_unreadable_lake_fields_are_reported_not_assumed_fine` now expects
`mandatory_inputs_unsourced`, and
`test_a_lake_missing_inputs_cannot_be_promoted_to_operational` expects `blocked`
plus a non-empty `missing_mandatory`.

### The ATR / zero-fill defect — recorded, not applied

Absent `atr_pct` → `universe_loader` writes `0` → `base_score` sees `0.0 < 0.03`
→ awards **+0.5, the maximum** formation bonus, so missing data is *rewarded*.
Conversely a zero-filled `pct_above_ma50_60d` makes `stability_score` return **0**,
a fabricated worst case. Recorded fix (a scoring-behaviour change, so not applied
here): stop zero-filling `atr_pct` and let absence reach the scorer's documented
neutral fallback, or make `base_score` distinguish a measured `0.0` from absence.

---

## 3. Verification

* New `tests/test_phase19_signal_contract.py` — **27 tests**: the artifact is
  writable and duplicate-free; a measured delivery percentage survives a
  null delivery quantity; the index/`symbol` contract; real SMC values; the SMC
  import target read from the parsed AST (so the comment recording the old
  mistake cannot satisfy it); both `fvg_mid` call sites, behaviourally; the SQLite
  schema helper (type mapping, unknown table, column filter, the null-heavy
  repro, coercion edge, deterministic empty schema, read-only connection);
  mandatory/optional/gated/unsourced classification; v2.5 blocked vs degraded;
  TriggerScanner unaffected; the growth flag still off by default.
* Targeted suites (`phase19`, `phase18_screening_modes`, `data_readiness`,
  `phase18_data_ops`, `phase15_atr_characterization`, `score_components_v2`,
  `positional_engine`, `missing_data_safety`, `fundamental_freshness`):
  **245 passed**.
* Full suite: **1749 passed** (Phase 18 was 1722; +27 new).
* `black` and `flake8` clean on every changed file.
* No frontend files changed; `myra_web` checks not re-run.
* **No production writes:** the lake is still 0 files, every production read went
  through `mode=ro`, `live_db_guard` passed, no `.myra_task_*` lock was left
  behind, and the working tree contains only the seven intended files.
* `myra_technical.db`'s mtime is still `2026-10-17 11:55:59` — the same value
  recorded mid-Phase-18, so it did not move during this phase. The earlier
  advance from `2026-10-16` remains **undiagnosed** and is carried forward rather
  than claimed as stable.

---

## 4. Build-readiness verdict

**The builder can now write artifacts. The lake still cannot produce a
trustworthy `MYRA_Score_v25`.**

* **Buildable at the file level:** yes, after F1. Before it, `build_indicator_lake`
  failed for every symbol.
* **Sufficient for v2.5:** no. Only 2 of 17 consumed fields are lake-produced; 2
  more are produced, by `ranking_inputs`, behind an off-by-default flag; 8 have no
  producer at all and are definitional gaps no build can close.
* **Trustworthy values:** not yet either. The lake's price series is a different
  series from `technical_data`'s (see below), so a build would produce indicators
  that reconcile with nothing in the database.

Consequently the read-only turbo path remains unverifiable end-to-end, and
TriggerScanner remains the usable mode.

### Two data findings that sit upstream of every indicator

1. **Two sessions carry a fabricated 50-day mean.** On 2026-10-13 and 2026-10-07,
   `technical_data` stores `sma_50 = close` for ~the whole universe (3220/3223,
   3223/3224) and `sma_200` NULL for every symbol (3221/3224). Other sessions
   show ≤7 such rows; the latest session is clean.
2. **The OHLCV cache and `technical_data` disagree by exactly 2×.** For
   `RELIANCE`, 736 of 1216 shared sessions differ, median ratio exactly 2.0000,
   for every session up to and including 2026-02-01; from 2026-02-02 they agree
   exactly. The cache also carries 73 dates the database lacks. One series is
   corporate-action adjusted and the other is not — a decision, not a repair.

---

## 5. Scheduler status (read-only, separate from screening)

| Concern | State | Requires |
|---|---|---|
| Manual screening | available (TriggerScanner) | nothing |
| Individual data-ops commands (`python -m myra_app.data_ops run <op>`) | available | nothing |
| Scheduled ingestion / EOD recovery | **not running** | the app must be started: `python run_fastapi.py` |

Lock absent, `state = degraded`, `DISABLE_SCHEDULER` unset,
`MYRA_EMBED_SCHEDULER` unset, 0 python processes, port 8000 with 0 listeners —
the scheduler is degraded because **nothing has started the process that owns
it**, not because of a lock or a configuration flag. Deliberately not started in
this phase; the command is named instead.

---

## 6. Open decisions

1. Authorise or reject F1–F4 (F1 in particular is a prerequisite for any build).
2. The eight unresolved definitions: `sma150` source, `atr_pct` lookback/denominator,
   `atr5`, Keltner centre line + multiplier, `drawdown` lookback,
   `smart_money_score` composition, `F_Score`.
3. The missing-`atr_pct` zero-fill, and the `pct_above_ma50_60d` zero-fill, in
   `universe_loader`.
4. Whether `MYRA_V25_RANKING_INPUTS` is ever enabled (changes published ordering).
5. The price-basis question above, before any lake indicator is trusted.
6. The 2026-10-07 / 2026-10-13 rows, and the ingestion that wrote them.
7. The undiagnosed `myra_technical.db` mtime advance from Phase 18.

Phase 20 must not begin from this document.
