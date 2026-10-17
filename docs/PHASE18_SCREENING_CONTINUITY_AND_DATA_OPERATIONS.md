# Phase 18 — Graceful Screening, Independent Data Operations and Scheduler Recovery

Starting point: `e36b22d` (Phase 17). This phase does not attempt to make the
v2.5 ranking work. It makes MYRA **honest about what does work**, gives the
operator **one place to look** and **one place to act**, and stops the UI from
giving advice that was wrong in the common case.

Nothing in this phase builds the indicator lake, rebuilds the OHLCV cache,
refreshes fundamentals, changes a production weight or threshold, enables
growth, or claims a recovery that was not observed.

## 1. Baseline (measured before any change)

| Fact | Value at `e36b22d` |
|---|---|
| Overall readiness | `blocked` — displayed as "Screening blocked" |
| Screening verdict | one axis: the indicator lake is usable or it is not |
| Indicator lake | `data/indicators/precomputed` — **0 files** |
| Web/API `TriggerScanner` | healthy; 37 candidates on 2026-10-16 (Phase 17 §2a) |
| CLI v2.5 turbo path | three independent blockers (B1 fixed in Phase 17; B2, B3 open) |
| Scheduler | `degraded`; lock file **absent**; no python process running |
| Port 8000 | 0 listeners |
| `DISABLE_SCHEDULER` | unset |

The contradiction that drove this phase: the operator was shown
**"Screening blocked"** while a fully working scanner was available, and the
banner's advice ("Leave DISABLE_SCHEDULER unset to run it here") was wrong — the
variable was already unset, and nothing had started the process that owns the
scheduler.

## 2. The core decision — one state per screening mode

MYRA has two independent screening modes, and a single verdict over both
produced two opposite lies:

- a working `TriggerScanner` was **hidden** behind a global blocked state the
  moment the lake was empty, so a usable mode looked unusable; and
- on a machine with a built lake, a healthy `TriggerScanner` would make the
  incomplete v2.5 ranking look **operational**.

`myra_app/screening_modes.py` (new) reports one state per mode, using the three
values this phase defines:

| State | Meaning |
|---|---|
| `operational` | The mode has the data and functioning calculations it needs. |
| `degraded` | The mode can run, but optional inputs are missing/stale, so its output is qualified, not wrong. |
| `blocked` | The mode cannot execute correctly: a mandatory source is absent, or a genuine runtime defect prevents it. |

The two modes it distinguishes:

| Mode | Reads | Emits | Depends on the lake |
|---|---|---|---|
| `trigger` — `TriggerScanner` (web/API) | `technical_data` (OHLCV + delivery), market cap | `trigger_score` | No |
| `v25_turbo` — CLI positional ranking | the derived indicator lake via `universe_loader` | `MYRA_Score_v25` | Yes |

Rules encoded in the module, each one a lie it refuses to tell:

- A blocked v2.5 mode does **not** block the trigger mode, and vice versa. The
  aggregate is `blocked` only when **no** mode can run.
- `degraded` is never promoted to `operational`: a mode with unsourced required
  inputs is reported `degraded` even though the code would run and produce a
  number.
- The two modes' scores are declared **not comparable**, and that declaration
  is a field in the payload (`comparability`), not a comment.
- Missing inputs are reported **by name**. No missing value is turned into a
  zero, a score, or a clean bill of health.

The runtime blockers are **derived, not asserted**: `worker_path_blocker()`
inspects the live `worker_pool` source for an unassigned `fvg_mid`, and
`polars_schema_blocker()` inspects `engine` for an implicit `pl.read_database`.
Both clear themselves the moment the defect is fixed, so this report cannot go
stale.

## 3. What readiness now reports (live evidence)

`myra_app/data_readiness.py` gained `_column_population()` (one index-backed
aggregate over one session, one `IS NOT NULL` share per trigger column) and
`screening_mode_facts()`, and `assess()` now escalates from the **mode
aggregate** instead of the single lake verdict. `screening` is retained in the
payload, so Phase 16's contract and its tests still hold.

Live assessment on this machine, after the change:

```
overall state : partial          <- was: blocked
label         : Partial data
explanation   : Latest market date 2026-10-16 matches the expected session
                (2026-10-16). TriggerScanner (web/API) is operational, but
                MYRA_Score_v25 turbo ranking (CLI) is unavailable. Scanning can
                continue through the operational mode; results carry that mode's
                score, which is not interchangeable with the others.

modes.overall : degraded — Partial screening availability
                operational_modes: ["trigger"]   unavailable_modes: ["v25_turbo"]

trigger       : operational  (reads_lake=false, score=trigger_score)
v25_turbo     : blocked  reason=lake_empty
                missing  = sma50, sma150, sma200, atr_pct, pct_above_ma50_60d,
                           keltner_upper, keltner_lower, atr5, drawdown
                blocked_by = worker_pool_fvg_mid_unassigned,
                             engine_read_database_implicit_schema
```

The escalation mapping is deliberate and not the same as Phase 16's:

```
degraded aggregate -> STATE_PARTIAL     (a mode still works)
blocked  aggregate -> STATE_BLOCKED     (no mode can run)
```

Escalation only, never a downgrade: a genuine ingestion `failed` is untouched,
and `ready` still requires every mode to be operational.

### 3b. The impact test was updated deliberately

`tests/test_data_readiness.py::test_current_data_with_an_unusable_screening_path_is_blocked`
previously asserted `blocked` for "fresh data + unusable screening". Under this
phase the honest answer is `partial`, because the trigger mode *is* available.

The test's intent — **never report `ready` when a screening path is unusable** —
is preserved and strengthened: it is renamed
`..._is_never_ready`, still asserts `!= ready`, and now also asserts that the
explanation names the unavailable mode. The `blocked` case is kept as a separate
test, `test_no_runnable_screening_mode_is_blocked`, so both branches are pinned
rather than the one being traded for the other. This follows the Phase 16
precedent of updating a contract test alongside the contract, with the reason
recorded here.

## 4. The unified data-operations front door

`myra_app/data_ops.py` (new) is the single entry point:

```
python -m myra_app.data_ops status      [--json]
python -m myra_app.data_ops recommend   [--json]
python -m myra_app.data_ops list
python -m myra_app.data_ops run <op>    [--yes] [--limit N] [--force]
                                        [--symbols A,B] [--acknowledge-scheduler]
python -m myra_app.data_ops             # interactive menu on a terminal
```

It is a front door, not a second pipeline:

- **Discovered, not reimplemented.** The scheduler-backed operations are derived
  from `myra_app.tasks.registry.TASKS`, so the list cannot drift from the single
  source of truth. They are invoked through the real contract
  (`executor.resolve_entrypoint` + a synthetic `TaskContext`) — one task, once,
  in this process: no thread, no period, no lock ownership.
- **The registry's own decisions are respected.** `watchdog` is a self-managed
  loop and is not offered (`running it here would spin forever`); the
  registry-disabled `screener-enrich` is not offered either. Neither is ever
  recommended.
- **No second writer.** Every operation takes the existing cross-process
  `task_lock` and releases it in a `finally`, so a crash cannot wedge it.
- **Conservative, explicit classification.** Each operation declares `writes`,
  `network` and `heavy` explicitly (a test enforces that no scheduler task
  inherits a default), and **everything that writes requires confirmation**.
- **Import-safe.** All heavy imports are function-local. `fundamental_sync`
  reads `os.environ["MORNINGSTAR_TOKEN"]` at import time and raises `KeyError`
  when it is unset, so a top-level import in this module would break `status` on
  an unconfigured machine. A test asserts — in a subprocess with the variable
  removed — that importing `data_ops` does not pull that module in.

### 4b. The missing-data taxonomy

The point of naming these is that **only some of them are data problems at all**.
Collapsing "we never built the derived artifact" into "missing data" is what led
operators to reach for the wrong tool repeatedly.

| Category | Meaning | Fixable by an operation? |
|---|---|---|
| `runnable` | Nothing is wrong; the operation is available. | — |
| `stale` | Local data is behind the source's cadence. | Yes — a refresh. |
| `not_ingested` | A source exists but was never fetched. | Yes — a sync. |
| `derived_missing` | Raw inputs exist; the derived artifact was never built. | Yes — a build. |
| `mapping_mismatch` | The value exists under a different key or unit. | **No** — a code change. |
| `unsourced` | No source exists in this repository. | **No** — an implementation decision. |
| `blocked` | A prerequisite or runtime defect prevents it. | **No** — not a data task. |

`recommend()` is deterministic, read-only, and **never dispatches anything**. Live
output on this machine:

```
[blocked]         No scheduler is running
                  ...Start the application through the entry point that owns the
                  scheduler (python run_fastapi.py) rather than clearing the
                  lock: nothing is holding it, so the lock is not the fault.
                  -> no operation can resolve this

[blocked]         The v2.5 candidate path fails at runtime
                  ...worker_pool_fvg_mid_unassigned,
                  engine_read_database_implicit_schema. This is a code defect,
                  not a data operation.
                  -> no operation can resolve this

[derived_missing] The indicator lake the v2.5 ranking loads was not built
                  Building it supplies the inputs it does produce (sma200,
                  sma50), out of 9 the scorer reads.
                  -> python -m myra_app.data_ops run lake-build --yes

[unsourced]       Some v2.5 inputs have no producer in this repository
                  7 of the 9 inputs the scorer reads are not emitted by any code
                  path (atr5, atr_pct, drawdown, keltner_lower, keltner_upper,
                  pct_above_ma50_60d, sma150); they are zero-filled instead. No
                  data operation can supply them - this needs an implementation
                  decision, and until then MYRA_Score_v25 must not be published
                  as authoritative.
                  -> no operation can resolve this
```

That last entry is the honest statement Phase 17's evidence supports: a lake
build is a real, bounded job, **and it is not sufficient**. Both facts are
reported, side by side, so nobody runs `lake-build` expecting a trustworthy score.

## 5. Concurrency and ownership guarantees

| Risk | Guard |
|---|---|
| The same operation run twice concurrently | Existing `task_lock` per operation name, released in `finally`. |
| Two data operations writing the same DB/cache | The scheduler's task registry is the shared writer set; a task a **live** scheduler owns is refused. |
| A duplicate scheduler started by accident | No scheduler is started, ever. `data_ops` never claims the scheduler lock. |
| A crash wedging a lock | `finally` release, plus the existing dead-pid reclamation. |
| Silently importing newly edited modules mid-run | One process, one operation, resolved at dispatch. |

Liveness is treated the *safe* way: `unknown` counts as **live**, so the
operation refuses rather than risking two writers. The override
(`--acknowledge-scheduler`) is explicit and separately named.

## 6. Mission Control: honest scheduler reporting

Three defects were found and fixed in `myra_web/src/App.tsx`:

1. **`disabled` was never surfaced.** `schedulerUnhealthy` covered only
   `degraded` and `unknown`, so a scheduler switched off by configuration — just
   as unavailable as one that died — rendered **no banner at all**. The
   `SchedulerState` union already carried `disabled`.
2. **The advice was wrong in the common case.** Every degraded state showed
   "Leave DISABLE_SCHEDULER unset to run it here". The variable is already
   unset; the actual cause is that nothing started the scheduler-owning process.
   Each state now states its own remedy, and the stopped case names
   `python run_fastapi.py` and says explicitly that clearing the lock is not the
   fix.
3. **`schedulerLabel` was computed and then not used** — the banner hardcoded
   "Scheduler Not Running". The label is now rendered, and a `Scheduler Disabled`
   label exists.

`DataReadinessPanel.tsx` now renders a **per-mode screening availability** block
(one row per mode: label, state chip, the score it emits, and the names of the
unavailable inputs). Showing the score name is the point: it is what stands
between a `trigger_score` and a `MYRA_Score_v25`. The types live in
`myra_web/src/lib/readiness.ts`; `screening_modes` is optional there so an older
backend renders nothing rather than having the client infer a mode's health.

## 7. Deliberately NOT done

- **Workstream 1.4 code fixes (`fvg_mid`, explicit Polars schema, key-name
  remaps) were not applied.** Each needs a semantic decision that is not a data
  operation's to make, and fixing `fvg_mid` first would convert a *visible*
  failure into an *invisible* wrong answer scored on fabricated zeros. Instead
  the turbo path is reported **blocked with its exact runtime blockers named**,
  which is more useful to the operator than a partial fix and does not risk a
  silently incorrect ranking.
- **The indicator lake was not built, no OHLCV cache rebuild, no fundamental
  refresh, no heavy enrichment.** All are operator-authorised actions. The lake
  directory is still 0 files.
- **The scheduler was not started.** Doing so would begin scheduled ingestion
  and EOD recovery and hold port 8000. The report names the exact command and
  reason instead, which is the operator's decision to make.
- **No production weights, thresholds, scoring formulas, or growth activation
  were touched.**

## 8. Verification

| Check | Result |
|---|---|
| Full suite | **1722 passed** (Phase 17: 1628; +42 readiness/modes, +52 data ops) |
| `black` / `flake8` on every changed file | Clean (via pinned pre-commit) |
| Frontend `tsc --noEmit` | Clean |
| Frontend `vite build` | Succeeds (pre-existing chunk-size warnings only) |
| Production DB writes | None — see the note below; the lake is still 0 files |
| Live readiness | `partial` (was `blocked`); trigger operational, v25 blocked |
| Lock hygiene | No `.myra_task_*.pid` left behind after CLI runs |

**Note on database modification (reported, not buried).** Every read path this
phase added goes through `data_readiness._ro`, which opens with
`file:{path}?mode=ro`, and `scheduler_health` does the same; nothing added here
can write. Two independent checks confirm no data was written:

- `tests/conftest.py`'s session-wide `live_db_guard` compares, for **every** live
  database, `size:mtime_ns` plus the count of committed WAL frames (and a
  `sync_log` content digest for `myra_metadata.db`). It passed in **both** full
  suite runs, so no live database changed *during testing*.
- Content is identical to the pre-change baseline: ingestion `last_success`
  `2026-10-17T11:21:30`, `latest_date 2026-10-16`, universe `3213`, recovery
  `advanced 2026-10-14 -> 2026-10-15`. `myra_technical.db-wal` is 0 bytes.

**One unexplained observation**, recorded rather than omitted:
`myra_technical.db`'s mtime advanced to `11:55:59` mid-session. It is the only
database that moved, re-running `python -m myra_app.data_ops status` does **not**
reproduce it, and no Python process remained afterwards — most consistent with an
external connection to that specific sidecar (a WAL checkpoint on connection
close) rather than a data write, but it was **not** diagnosed to a root cause.
No other `myra_*.db` moved. Phase 17's claim of "all 13 mtimes unchanged" is
therefore not repeatable as stated.

Against the phase's own acceptance list: a working scanner is available with an
empty lake; trigger results carry `trigger_score` and are declared incomparable;
missing optional data yields a visible partial status; failed mandatory inputs
stay explicit; a missing score cannot be fabricated (`missing` is names, never
values); producer/consumer mismatches are reported as mapping/unsourced rather
than fixed by guesswork; the menu and `run` dispatch only to existing
implementations; recommendations never auto-execute; writes require explicit
confirmation; duplicates and conflicting writers are refused; `DB_DIR`/`DB_MAP`
conventions are untouched; scheduler ownership is distinguished from an
env-disable and from an unknown; and growth remains disabled with production
weights unchanged. Phase 15's ATR characterisation tests still pass.

## 9. Outstanding operator decisions (carried from Phase 17 §9)

1. `drawdown` lookback (252 assumed).
2. Keltner definition (SMA20 vs EMA20, and the multiplier).
3. `pct_above_ma50_60d` unit (0–1 fraction, pinned by a test).
4. `atr5` definition (Wilder ATR(5) vs mean of the last 5 true ranges).
5. Source of truth for `sma150` (extend the lake producer vs reuse
   `data_adapter.compute_common_indicators`).
6. Authorise the B2/B3 repairs **before** any lake build, so a built lake lands
   on a path that can actually return a candidate.

Until 6 is decided, the correct operational answer is: screen through the
TriggerScanner, and do not publish a `MYRA_Score_v25`.

## What this phase changed

| File | Change |
|---|---|
| `myra_app/screening_modes.py` | **New.** Per-mode screening capability + honest aggregate. |
| `myra_app/data_readiness.py` | `_column_population`, `screening_mode_facts`, `_apply_mode_capability`; `assess()` escalates from the mode aggregate; `fingerprint` and `__all__` extended. |
| `myra_app/data_ops.py` | **New.** Unified status / recommend / run / menu front door. |
| `myra_web/src/App.tsx` | `disabled` surfaced; correct per-state advice; `schedulerLabel` actually rendered. |
| `myra_web/src/lib/readiness.ts` | `ScreeningModeState`, `ReadinessScreeningMode(s)`; `screening_modes` on `DataReadiness`. |
| `myra_web/src/components/DataReadinessPanel.tsx` | Per-mode screening availability block. |
| `tests/test_phase18_screening_modes.py` | **New.** 40 tests. |
| `tests/test_phase18_data_ops.py` | **New.** 52 tests. |
| `tests/test_data_readiness.py` | Mode axis stubbed per the Phase 16 precedent; the "unusable screening" test updated to `partial` and the `blocked` case kept as its own test. |
