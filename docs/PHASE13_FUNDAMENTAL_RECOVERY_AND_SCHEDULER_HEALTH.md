# Phase 13 — Fundamental data recovery, margin semantics, and scheduler health

Two questions, one cycle:

1. Can a **serving** API silently not be scheduling? Yes — that was the Phase 12
   incident, and it was invisible on every status surface.
2. Is the "missing" fundamental data actually recoverable? Mostly no — but the
   recorded reason for `net_margin` was wrong, and that mattered.

---

## Part 1 — The scheduler incident

### What happened

The API process was killed hard. The scheduler lock file survived with a dead
PID. On restart:

1. `_pid_alive(dead_pid)` **failed closed** — any error resolved to "probably
   alive";
2. `acquire()` therefore concluded a live scheduler existed, logged one line,
   and started nothing;
3. the EOD recovery watchdog was frozen at `22:39:28` while the clock read
   `23:00`;
4. `/api/health` returned HTTP 200 with every database "connected".

The API was up. The scheduler was dead. Nothing on screen said so.

### What changed

| Area | Before | After |
|---|---|---|
| Lock read | absent and corrupt conflated | `LockRecord` with `ok` / `absent` / `corrupt` |
| Ownership | binary "has an owner" | tri-state: `alive` / `dead` / `unknown` / `absent` |
| Reclaim | overwrote whatever was there | only a **confirmed dead** owner; refuse on `unknown` |
| Races | read-then-write | `O_EXCL` create; tmp + `os.replace` + re-read (CAS) when reclaiming |
| PID reuse | not considered | per-record process creation token |
| Release | could delete a lock it never held | deletes only when the record names *this* process |
| Orchestrator | one log line; `except` swallowed at `debug` | records the refusal reason; a failed lock probe logs at `warning` |
| Health | `{health, coverage}` | adds `scheduler` |

The orchestrator now records *why* it declined, so "the API is up" and "the
scheduler is running" can no longer be confused:

```
POST start() -> acquired?
   no  -> _last_start_refusal = {reason, detail, pid, at}
            reason in {"disabled", "lock_held", "lock_probe_failed"}
```

### Stale-lock policy

Reclamation requires the previous owner to be **provably** gone. "Cannot tell"
is not "gone" — misreading a healthy lock as dead is how one gets destroyed.
So `dead` reclaims (under CAS, so a race still yields exactly one winner) and
`unknown` refuses.

---

## Part 2 — Scheduler health on screen

`GET /api/health` now returns a `scheduler` object:

```json
"scheduler": {
  "state": "running",
  "reason": "watchdog heartbeat is advancing",
  "owner_pid": 4242,
  "watchdog_last_tick_age_seconds": 12.3
}
```

| `state` | meaning |
|---|---|
| `running` | the lock is held and the heartbeats are advancing |
| `starting` | the lock is held; the first tick is not due yet |
| `degraded` | **the API is up and nothing is scheduling** |
| `disabled` | `DISABLE_SCHEDULER` is set — intentional |
| `unknown` | the lock or the task registry could not be read |

Only `running` and `disabled` count as healthy. The source of truth is the
watchdog's own heartbeat: it rewrites `task_registry.updated_at` every 60s, so
a frozen timestamp means a frozen watchdog, regardless of what the scheduler
*thinks* it is doing. The state is memoised for a few seconds so the UI's
polling (default 10s) does not turn into a DB read per poll.

`DISABLE_SCHEDULER` exists so that "the scheduler is off" is something an
operator **chose**, rather than something inferred from a missing lock file.
It defaults to unset and changes nothing unless set.

The React banner is bound to the returned state, so a live-but-not-scheduling
API now has a visible failure on screen rather than a line in a log file.

---

## Part 3 — Fundamental data

### `net_margin` is a fraction; the "MIXED" defect was wrong

The recorded units were:

> `MIXED - observed both as a fraction (~0.14) and as a percent (up to 699).`

That is not what the data says.

* The producer is a plain pass-through — the mapping
  `netMargin -> net_margin` applies no scaling in either direction.
* `3309` non-null rows: `95.1%` have `|v| <= 1`; the median is `0.0644`.
* **Every** stored value above `1` is a fraction over a near-zero-revenue
  denominator — penny/shell/distressed names (`MTNL`, `RCOM`, `JETAIRWAYS`,
  `RAINBOWPAP -26570.08`, `ESSARSHPNG 699.92`).
* Those extremes match the **live source** byte-for-byte, and `ESSARSHPNG`'s
  value is identical across three archived snapshots while its `pe` drifts
  (`3.72 -> 3.72 -> 3.87`) — a stable upstream input, not a percent.
* A live probe of the source returns fractions directly:
  `20MICRONS 0.07137`, `360ONE 0.37408`, `3BBLACKBIO 0.37129`.

So there is no mixed-scale defect. There is a units **trap**: a stored
`0.0644` means 6.44%. Display code multiplies by 100; ranking code must not
treat it as a percentage. The provenance now says so, and the test that
asserted `MIXED` was replaced with one asserting a single unit plus that
warning.

No stored data was modified — only the recorded interpretation.

### `roe_ttm`: the column exists but can never populate

There is a writer, and it cannot fire. Two independent defects, and both must
be fixed for the column to populate:

1. **The field is never requested.** `roeTTM` is absent from the Morningstar
   `securityDataPoints` string, and the API honours that list. Probed live:
   with the current field list the response contains no `roeTTM` key at all;
   adding `roeTTM` to the request returns values (`0.16485` = 16.485%).
2. **The merge reads a key the fetch never wrote.** The fetch stores the value
   under `roe_ttm`; the merge reads `["roeTTM"]`.

Left NULL on purpose: it has no consumer, the source value is a fraction while
the sibling `roe` row is a percent, and populating it would mean deliberately
adding a new signal. Recorded instead, with both causes and the units.

### The rest

| Field | Finding |
|---|---|
| `profit_growth`, `sales_growth` | A real source exists — `upstox_income_statement` provider YoY strings (`"+9.83%"`), verified against `INFY 182972 -> 166590`. But populating them feeds the ranking pipeline, i.e. it switches on a signal. **Deferred** pending an explicit decision. |
| `debt_to_equity` | **Not derivable.** `upstox_balance_sheet` carries only `total_asset` / `total_liability`; there are no debt or equity components. |
| `smart_money_score` | **No definition, no writer.** Read with a `0` default; left alone. |

---

## Files

**Scheduler**

* `myra_app/utils/scheduler_lock.py` — tri-state ownership, creation token, atomic acquire, safe release
* `myra_app/scheduler_health.py` *(new)* — state reporter, TTL-cached, injectable
* `myra_app/constants.py` — `DISABLE_SCHEDULER`
* `myra_app/background_orchestrator.py` — honours the flag; records the refusal; surfaces a failed lock probe
* `myra_web/routes/health.py` — `scheduler` in the response
* `myra_web/src/lib/Librarian.ts`, `myra_web/src/hooks/useHealthStatus.ts`, `myra_web/src/App.tsx` — banner

**Fundamentals**

* `myra_app/fundamental_freshness.py` — corrected `net_margin`, documented `roe_ttm`

## Tests

* `tests/test_scheduler_health.py` *(new)* — ownership, reclamation, refusal,
  safe release, PID reuse, all five health states, refusal recording
* `tests/test_fundamental_freshness.py` — the `MIXED` assertion replaced by a
  single-unit assertion plus the percent warning, and a new `roe_ttm` test

## Verification

* `pytest` — full suite green, including the pre-existing scheduler-lock tests
  unmodified (they monkeypatch `_pid_alive` and write the legacy two-line lock
  format, so backward compatibility is asserted by their continued passing);
* `black`, `flake8` clean;
* frontend `npm run build` succeeds;
* no test touches the real lock file or the live databases.

## Deferred

* Populating `profit_growth` / `sales_growth` from Upstox YoY — it enables a
  ranking signal, so it needs an explicit decision, not a quiet backfill.
* `roe_ttm` — both wiring defects are documented with reproduction steps; the
  fix is one field-list addition and one key lookup.
