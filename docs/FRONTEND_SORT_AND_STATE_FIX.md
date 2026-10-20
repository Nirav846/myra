# Frontend sort and React state fix

Scope: the two confirmed correctness defects in the Wyckoff results table.
No scanner logic, ranking formula, filter or backend behaviour was touched.

---

## 1. Confirmed root causes

### 1.1 The comparator chose text vs numeric from the value *pair*, not the column

`WyckoffAutomaton.tsx` sorted like this:

```ts
const aVal = a[sortCol as keyof Candidate] ?? 0;   // null/undefined -> 0
const bVal = b[sortCol as keyof Candidate] ?? 0;
if (typeof aVal === 'string' && typeof bVal === 'string') {
  return sortAsc ? aVal.localeCompare(bVal) : bVal.localeCompare(aVal);
}
return sortAsc ? (aVal as number) - (bVal as number) : ...;   // 0 - 'C' => NaN
```

The `typeof` test only takes the text branch when **both** sides are strings.
As soon as one row has a value and the other is null, the null becomes `0` and
the pair falls through to subtraction. `0 - 'C'` is `NaN`.

`Array#sort` requires a consistent comparator. With `NaN` results the comparison
is neither negative, zero nor positive, and the engine's output is unspecified -
so the rendered order depended on the order the array happened to arrive in.

Measured on the live cache (`models/wyckoff_cache.json`, 1,389 rows):

| Column | Null rows | NaN comparisons (of 963,966 pairs) |
|---|---|---|
| `grade` | 1,333 | **74,648** |
| `spring_score` | 1,333 | 0 (`0 - 72` is a valid number) |
| `lower_wick_ratio`, `close_location`, `grab_depth_pct` | 1,333 | 0 (same reason) |
| all other sortable columns | 0 | 0 |

74,648 is exactly `1,333 x 56` - every ungraded row against every graded row.
`grade` is a sortable header, so one click on it left the table in an
effectively arbitrary order.

Permutation stability of the **old** comparator, over all 21 sortable columns x
both directions:

```
old comparator non-deterministic on 40 of 42 column/direction combinations
new comparator non-deterministic/unsorted/mutating on 0 of 42
```

Only `symbol` was stable, and only because it is unique and all-string. Note
that three columns (`spring_score`, `market_cap_cr`, `phase_complete_pct`) were
already permutation-dependent **without** producing NaN - they have large groups
of equal values, and the sort was stable, so ties inherited input order.

### 1.2 Sorting mutated the array held in React state

```ts
const candidates = scanStatus?.candidates ?? [];   // array inside state
const filteredData = useMemo(() => {
  let data = candidates;                            // same reference
  if (mcapRange) data = data.filter(...);           // only these copy...
  ...
  data.sort(...);                                   // ...and this mutates
  return data;
}, [...]);
```

Every filter assigns a **new** array, so the mutation is only reachable when no
filter branch runs. With the shipped defaults `maxDaysFilter` is `30`, so the
day filter normally copies and hides the defect. It becomes live when:

- `mcapRange === null` (the market-cap cache is empty or its fetch failed, so
  the auto-range on mount never runs), **and**
- watchlist / sector / event / phase / quality filters are all at their default,
  **and**
- the day filter is set to **All Days** (`999`).

Then `data === candidates` and `.sort()` reorders the array inside
`scanStatus`, without a `setState`. That is both an input mutation and a
render-consistency hazard: the same array is being reordered under React.

The audit's `WyckoffAutomaton.tsx:293` reference matches `let data = candidates;`
in the current file (line numbers shifted by one after the import was added).

---

## 2. Files changed

| File | Change |
|---|---|
| `myra_web/src/lib/tableSort.ts` | **new** - null-safe comparator, non-mutating `sortRows`, Wyckoff column contract |
| `myra_web/src/lib/tableSort.test.ts` | **new** - 21 regression tests |
| `myra_web/src/views/WyckoffAutomaton.tsx` | inline comparator replaced by `sortWyckoffCandidates` (-9 / +5 lines) |
| `myra_web/package.json` | `"test": "tsx --test src/lib/tableSort.test.ts"` (no new dependency - `tsx` was already present) |
| `.github/workflows/ci.yml` | one step, `pnpm test`, after the type-check |

The view diff is the whole UI change:

```diff
-    data.sort((a, b) => {
-      const aVal = a[sortCol as keyof Candidate] ?? 0;
-      ...
-    });
-    return data;
+    // Non-mutating: sortWyckoffCandidates returns a sorted copy, so this can
+    // never reorder the array held in React state (`data === candidates` when
+    // no filter above matched).
+    return sortWyckoffCandidates(data, sortCol, sortAsc);
```

---

## 3. Comparator rules and tie-breaking policy

### 3.1 The column declares the kind

| Kind | Columns |
|---|---|
| `string` | `symbol`, `sector`, `grade`, `wyckoff_event`, `phase`, `event_date` |
| `boolean` | `equal_low_zone`, `two_candle_confirm` |
| `number` | `spring_score`, `close`, `lower_wick_ratio`, `close_location`, `grab_depth_pct`, `market_cap_cr`, `phase_complete_pct`, `days_since_event`, `event_delivery_pct`, `vol_ratio`, `event_quality`, `range_low_90`, `range_high_90` |

The map lives in `WYCKOFF_SORT_KINDS` and is asserted complete by a test.
`event_date` stays textual on purpose: `"YYYY-MM-DD hh:mm:ss"` compares
lexicographically and chronologically at the same time, which is the existing
behaviour.

### 3.2 Missing values

A value that cannot be represented in the column's kind is **missing**:

- `null`, `undefined`
- `NaN`, `Infinity`, `-Infinity`
- `''` or whitespace-only strings
- a non-numeric string in a numeric column (`"N/A"`, `"0x10"`, `"abc"`)

Missing values sort **last in both directions**. Flipping a column never buries
real rows behind blanks, and two missing values compare equal (they fall through
to the tie-break).

This deliberately replaces the old `?? 0`, which sorted a missing value as if it
were zero - the reason `grade` had 1,333 rows sitting at position 0.

### 3.3 Numeric strings

The old comparator coerced numeric strings implicitly through `-`. That is kept,
but only for strings matching a strict decimal/scientific literal
(`/^[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?$/`). Hex literals, `"1,234"` and
other inventively-numeric strings are treated as missing rather than silently
reinterpreted. Everything else in a string column is compared with
`localeCompare`, as before.

### 3.4 Direction

`ascending` reverses the comparison of **present** values only:

```ts
if (base === 0) return 0;             // never return -0
return ascending ? base : -base;
```

Numbers and booleans compare by sign (`<` / `>`), not by subtraction, so no
`Infinity` arithmetic and no `-0` can escape.

### 3.5 Tie-break

`sortWyckoffCandidates` breaks ties on ascending `symbol`
(`sortRows` takes the tie-break as a parameter; `null` means "keep input order").

Symbols are unique across a scan result - 1,389 rows, 1,389 distinct symbols on
the live cache - so the comparator defines a **total order**. The same rows
always produce the same sequence, whatever order they arrive in. That is what
makes the permutation test in §4 pass for every column.

**Visible consequence, measured.** The old within-tie order was the scan's
universe-iteration order, which carries no ranking meaning (within
`phase_complete_pct = 90` the first rows were `CHEMBONDCH, FAIRCHEMOR,
LOTUSDEV, MAZDA, ...`). Replacing it with a total order moves rows:

| Column (descending) | Rows changing position vs the old order |
|---|---|
| `phase_complete_pct` (default view) | 1,387 / 1,389 (99.9 %) |
| `grade` | 1,388 / 1,389 (99.9 %) |
| `event_quality` | 549 / 1,389 (39.5 %) |
| `market_cap_cr` | 8 / 1,389 (0.6 %) |

The default column has only three distinct values (25 / 75 / 90), so almost
every row is a tie and almost every row moves. The *bands* are unchanged and
correct (90 first when descending); only the order inside a band is now
deterministic instead of arbitrary. If a ranked-within-band default is wanted
(e.g. `event_quality` descending as the second key), that is a one-line change
to the tie-break - it was not made here because it would be a product decision,
not a correctness fix.

---

## 4. Regression tests and actual validation results

### 4.1 What the tests cover

`myra_web/src/lib/tableSort.test.ts` - 21 tests, `node:test` + `node:assert`,
run through the already-installed `tsx` (no new dependency, no jsdom):

| Area | Tests |
|---|---|
| Never NaN | all 20 messy values x 20 x 3 kinds x 2 directions, plus antisymmetry of the whole matrix |
| Missing handling | null / undefined / NaN / ±Infinity / `''` / `"N/A"` sort last in both directions |
| Numeric strings | `"9" < "100"`, `" 12 "`, `"1e3"`; `"0x10"`, `"abc"` are missing |
| String columns | null vs `"C"` returns a sign, not arithmetic |
| Booleans | `false < true`, `"false"` == `false` |
| Direction | exact sign reversal for present values; `0` never becomes `-0` |
| Immutability | input array order unchanged, rows unchanged, output is a new array |
| Ties | input order preserved without a tie-break; `symbol` applied with one |
| Real cache shape | 56 graded + 1,333 ungraded rows: no NaN, ungraded last, sorted run monotonic |
| Determinism | 4 seeded permutations x 21 columns x 2 directions produce identical order |
| Repeatability | three consecutive sorts deep-equal |
| Duplicate symbols | documented fallback to stable input order |
| State safety | sorting a state-like array leaves it byte-identical |

The comparator is also checked for **monotonicity of its own output** (every
adjacent pair in a sorted run compares `<= 0`), which would fail if the
comparator were inconsistent.

DOM-level rendering is not tested: the project has no jsdom or
`@testing-library/react`, and adding them would mean new dependencies. The
regression is enforced structurally instead - the only path to a sorted view is
`sortWyckoffCandidates`, which copies before sorting - and the copy is asserted
directly.

### 4.2 Commands actually run and their results

```powershell
cd myra_web
pnpm test        # tsx --test src/lib/tableSort.test.ts
pnpm lint        # tsc --noEmit
pnpm build       # vite build
```

| Check | Result |
|---|---|
| `pnpm test` | **21 passed, 0 failed** |
| `pnpm lint` (`tsc --noEmit`) | **exit 0**, no output |
| `pnpm build` | **built in 32.56 s**; only pre-existing `INEFFECTIVE_DYNAMIC_IMPORT` and chunk-size warnings |

### 4.3 End-to-end check against the live cache

A one-off script loaded the real 1,389-row `models/wyckoff_cache.json`, sorted
it with the old and the new comparator under three seeded permutations, and
checked the result:

```
col                  old: perm-stable   new: perm-stable   new: monotonic   state intact
  grade                false              true               true             true
  phase_complete_pct   false              true               true             true
  ... (all 21 columns, both directions)

old comparator non-deterministic on 40 of 42 column/direction combinations
new comparator non-deterministic/unsorted/mutating on 0 of 42
```

---

## 5. Remaining table-specific exceptions

Each view has its own comparator - there is no shared module, and the 54 scanner
views do not agree. Only the Wyckoff table was changed. The rest, verified by
reading them:

### Same defect class (mixed-type arithmetic can produce NaN)

| File | Line | Status |
|---|---|---|
| `src/components/ui/ScannerTable.tsx` | 57 | **Not fixed.** Generic component, **zero consumers** (`<ScannerTable` appears nowhere; it is only re-exported by `components/ui/index.ts`). |
| `src/components/ui/VirtualizedTable.tsx` | 66 | **Not fixed.** One consumer, `CrossBuyScanner.tsx`. The audit already flagged this component as structurally broken for HTML tables (absolutely positioned `<tr>` inside a real `<table>`), so fixing its comparator would polish dead weight. |

### Different defect class (deterministic, but numbers compared as text)

Fourteen views use `String(av).localeCompare(String(bv))`, which never returns
NaN but orders `9` after `100`:

`TriggerScanner`, `TractionBoard`, `SmartMoneyBargain`, `SeasonalDeliveryHarvester`,
`PriceDeliveryDivergenceScanner`, `OperatorFingerprintScanner`,
`MultibaggerProScanner`, `LiquidityFlipDetector`, `InvisibleHandScanner`,
`FloatExhaustionScanner`, `DCBBargain`, `DarvasBoxProScanner`,
`ClimaxAccumulation`, `BottomHunter`; plus `ConfluenceView.tsx:239`
(`String(a[sortKey] ?? '')`).

This is a wrong-order bug, not a non-determinism bug, and changing 15 views is
outside "minimal, isolated". Left alone.

### Already correct

- `DeliveryAnomalyScanner.tsx:112-142` handles nulls explicitly (`null` first
  through `return 1 / -1`) before the type branches and returns `0` for mixed
  types - no NaN, and it copies with `[...summaryData]`. It is a good precedent
  for the same fix if the others are ever touched.
- `ValueRanker.tsx:213-222` returns `0` for mixed types rather than doing
  arithmetic, so it cannot produce NaN. It sorts a `.map()` result, not state.
- `PortfolioView.tsx:599-608` checks `typeof va === 'string'` and calls
  `localeCompare`, which coerces its argument to text - wrong order for mixed
  types, but no NaN.
- `useDeliveryScanner.ts:401,543` sorts ISO date strings with `localeCompare` -
  correct by construction.

---

## 6. Limitations and follow-ups

- **No DOM test.** Covered above: the invariant is tested at the function that
  the component now calls, not through a renderer.
- **Visible reorder.** See §3.5. Determinism was chosen over preserving an
  arbitrary order; the measured churn is documented rather than hidden.
- **Tie-break is `symbol` only.** If the scanner later emits the same symbol
  twice (e.g. one row per event), the comparator stops being a total order and
  the permutation guarantee weakens to "stable within identical rows". The test
  `duplicate symbols stay stable` documents the assumption, and adding
  `event_date` as a second key would restore a total order.
- **CI wiring added** (`pnpm test` in the frontend job) so the regression tests
  actually run. Nothing else in CI was changed.
- **Not addressed, by instruction:** symbol search, sticky columns, new table
  libraries, the AR/ST scanner defect, and any backend ranking semantics.
