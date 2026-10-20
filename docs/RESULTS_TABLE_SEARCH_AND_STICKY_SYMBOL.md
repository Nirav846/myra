# Results Table Usability — Symbol Search and Pinned Symbol Column

Scope: frontend only. Two changes — a symbol filter box and a pinned (sticky)
symbol column — applied to the scanner result tables that share the
`ScrollableTable` + `handleSort` layout.

Starting point: `05389a2` (the table-sort determinism commit), working tree
clean, `HEAD == origin/main`.

---

## 1. Inventory — what actually exists

Measured against the code, not against `docs/SCANNER_PERFORMANCE_AND_RESULTS_UI_AUDIT.md`
(three of that audit's search findings did not hold up; see §5.1).

| Measure | Count |
| --- | --- |
| Views rendering a native `<table>` | 39 |
| Views wrapping that table in `ScrollableTable` | 24 |
| Views with any symbol search **before** this change | 3 |

**Shared table plumbing.** `components/ScrollableTable.tsx` is a wrapper, not a
table: it renders the scroll container, a floating horizontal scrollbar, and a
`ResizeObserver`-driven height. It also establishes the app-wide table styling
through `.scanner-table-scroll` in `index.css` (sticky `<thead>` at `z-index: 5`,
row hover, zebra striping). Twenty-four views share that wrapper, which is why a
single CSS change and a single small component reach all of them.

**Two dead components confirmed dead.** `components/ui/ScannerTable.tsx` has
zero consumers and `components/ui/VirtualizedTable.tsx` has one
(`CrossBuyScanner.tsx`), which is already broken for HTML table markup. Neither
was touched, per scope; neither is on the active result-table path.

**Symbols are not always in column 1.** Reading the headers of every covered
table: `DarvasBoxProScanner` leads with `sector`, and `MyPositionsWithAlerts`
leads with an alert badge. (An earlier heuristic suggested more — `ReversionEngine`,
`Leaderboard` and `SmartMoneyBargain` turned out to lead with the symbol, and
`DarvasBoxProScanner`'s `.includes(` is CSV escaping, not search.) That is still
enough to rule out a blanket `th:first-child` sticky rule, so the pin is opt-in
per view.

**Existing search, audited properly:**

| View | Search | Where |
| --- | --- | --- |
| `DeliveryAnomalyScanner` | client-side, trim + upper-case + `includes`, over the full result set | `hooks/useDeliveryScanner.ts:311-315` |
| `TractionBoard` | server-side (`/api/fund-traction/board?search=`) | `TractionBoard.tsx:256` |
| `PortfolioView` | client-side symbol search (not a scanner result table) | `PortfolioView.tsx` |

`DarvasBoxProScanner`'s `.includes(` is CSV escaping, not search — checked
because a grep heuristic flagged it.

---

## 2. Implementation

### Shared pieces (new)

| File | Purpose |
| --- | --- |
| `src/lib/symbolFilter.ts` | `normalizeQuery`, `matchesSymbol`, `filterBySymbol`, `countSymbolMatches` — framework-free, unit tested |
| `src/lib/useSymbolFilter.ts` | `useState` wrapper: `{ query, setQuery, clear, normalized, active }` |
| `src/components/SymbolFilterInput.tsx` | the input: search icon, X clear button, `Esc` to clear, `aria-live` "3 of 42" / "No symbol matches" hint, red border on no-match |

Named `symbolFilter`/`SymbolFilterInput` deliberately: `components/SymbolSearch.tsx`
already exists and is the global symbol **navigator** (DB-backed autocomplete that
navigates away). It is a different job and was left alone.

### Search contract

- Case-insensitive, **partial** (`TATA` matches `TATAMOTORS`, `MOTORS` also matches).
- Leading/trailing whitespace ignored; whitespace-only = "no filter", not "match nothing".
- Applies to the **complete locally held result set**, inside the view's existing
  `useMemo` filter chain — not to the rendered slice, the current page, or a
  post-slice window.
- No request per keystroke: every covered view already holds the full candidate
  list in memory, so this is one `Array#filter`. No debounce, no new dependency.
- `filterBySymbol` **always returns a new array**, even when the query is empty.
  Every covered view feeds the result straight into `.sort()`; returning the
  input by reference would have reintroduced exactly the state-mutation defect
  fixed in `05389a2`.
- Clearing: X button, `Esc`, or emptying the box. `aria-live` announces the
  no-match state; the input border turns red and the table keeps its own
  empty-state row.

### Pinned column contract

Opt-in via `sticky-symbol` on the symbol `th` and `td`. Rules live in
`index.css` under "Pinned symbol column":

| Property | Header | Body |
| --- | --- | --- |
| `position` | `sticky`, `left: 0` | `sticky`, `left: 0` |
| `z-index` | `7` (above the `thead` rule's `5`) | `2` (above plain cells) |
| background | existing header gradient (opaque) | `--sticky-symbol-bg` `#1a1c24` (opaque) |
| width | `min-width: 7.5rem` | `min-width: 7.5rem` |
| divider | `box-shadow: 1px 0 0` | `box-shadow: 1px 0 0` |

Details that matter:

- **Specificity.** `.scanner-table-scroll thead th` already sets `z-index: 5` and
  a gradient at specificity `(0,1,2)`, which beats a plain `th.sticky-symbol`
  `(0,1,1)`. The pinned header is therefore restated as
  `.scanner-table-scroll thead th.sticky-symbol` `(0,2,1)`. Without this the
  column would paint *under* the header when scrolled.
- **`border-collapse`.** Several tables collapse borders, where sticky cells lose
  their borders. The right-hand divider is a `box-shadow` instead, so it survives.
- **Hover transform.** `.scanner-table-scroll tbody tr:hover` applies
  `transform: scale(1.0001)`. A transformed ancestor becomes the containing block
  for `position: sticky` descendants, which would un-pin the symbol of the row
  under the cursor. `.scanner-table-scroll tbody tr:has(td.sticky-symbol):hover`
  drops that invisible scale for pinned rows only.
- **Hover/zebra fill.** The pinned cell keeps its own opaque fill, so it does not
  become a transparent hole; `tr:hover` and `:nth-child(even)` get dedicated
  fill colours.
- **No architectural rewrite.** No table, no layout, no scroll container changed.
  `ScrollableTable` supplies the horizontal overflow; the column simply sticks to
  it.
- **Not added to non-symbol tables.** No `:first-child` rule exists; a source-level
  test asserts that.

---

## 3. Coverage — 14 views, search **and** pinned column

| # | View | Registered scanner |
| --- | --- | --- |
| 1 | `TriggerScanner` | ✅ Trigger |
| 2 | `FloatExhaustionScanner` | ✅ Float Exhaustion |
| 3 | `InvisibleHandScanner` | ✅ Invisible Hand |
| 4 | `WyckoffAutomaton` | ✅ Wyckoff |
| 5 | `LiquidityFlipDetector` | ✅ LFD |
| 6 | `OperatorFingerprintScanner` | ✅ OFP |
| 7 | `SeasonalDeliveryHarvester` | ✅ Seasonal Delivery |
| 8 | `DCBBargain` | ✅ DCB (endpoint-only) |
| 9 | `BottomHunter` | |
| 10 | `ClimaxAccumulation` | |
| 11 | `DarvasBoxProScanner` | |
| 12 | `MultibaggerProScanner` | |
| 13 | `LaunchpadScanner` | |
| 14 | `PriceDeliveryDivergenceScanner` | |

All eight registered scanners are covered. In each view the filter is inserted as
the **first** step of the existing filter chain, so it composes with mcap range,
sector, tier/grade/phase filters, watchlist-only and the sort column rather than
overriding them. Because the filtered array is the single source the table, the
KPI cards and the CSV export already read from, search narrows all three
consistently — and is a no-op when the box is empty.

Number formatting/ranking untouched: no default sort column was changed, no
comparator added, `lib/tableSort.ts` was not modified. (`WyckoffAutomaton`
already delegates to it from the previous change.)

### Files changed

New (5): `src/lib/symbolFilter.ts`, `src/lib/useSymbolFilter.ts`,
`src/components/SymbolFilterInput.tsx`, `src/lib/symbolFilter.test.ts`,
`src/lib/stickySymbol.test.ts`.

Modified (16): `src/index.css`, `package.json` (test script), and the 14 view
files listed above.

---

## 4. Tests and build — actual results

| Check | Command | Result |
| --- | --- | --- |
| Frontend tests | `pnpm test` | **47 passed, 0 failed** (3.1 s) |
| Type check | `pnpm lint` (`tsc --noEmit`) | **exit 0** |
| Production build | `pnpm build` | **built in 16.41 s**, only the pre-existing `INEFFECTIVE_DYNAMIC_IMPORT` warning |

47 = 21 `tableSort` (pre-existing) + 18 `symbolFilter` (new) + 8 `stickySymbol` (new).

`symbolFilter.test.ts` covers: case-insensitive partial matching (including a
mid-symbol hit and `BAJAJ-AUTO`), trimming, blank/`null`/`undefined` input,
non-string input, no-match → empty array (not the input), clearing restores the
full set in original order, order preservation so ranking/sort survives, search
across a 500-row set where the hit is the last row, `null`/`undefined` symbols
skipped without throwing, `countSymbolMatches` agreeing with `filterBySymbol`,
and immutability — including "sorting the returned array must not reorder the
source", which is the `05389a2` regression in reverse.

`stickySymbol.test.ts` reads `index.css` and pins: `position: sticky`/`left: 0`
on both cell types, numeric `min-width`, header `z-index` > body `z-index` and
> 5, an **opaque hex** fill (not `rgba`), hover/zebra fills, the `:has()`
transform reset, no blanket `:first-child` pinning, and the hidden native
search-cancel button.

**Not tested, and not claimed:** rendered geometry. There is no jsdom,
testing-library or Playwright in this project, and no browser was driven for this
change. The CSS tests assert the *rules*, not that a browser obeys them. A manual
visual pass is still the honest gate for "does the column really stay put".

---

## 5. Exceptions and limitations

### 5.1 The audit was wrong on search (3 views)

`docs/SCANNER_PERFORMANCE_AND_RESULTS_UI_AUDIT.md` states that 0 of 54 scanner
views had a symbol search box. That is not accurate: `DeliveryAnomalyScanner`
has a working client-side symbol search, `TractionBoard` has a server-side one,
and `PortfolioView` has one. The "0 sticky columns" finding **was** correct.

### 5.2 Covered views still have pre-existing quirks

- **`PriceDeliveryDivergenceScanner` renders at most 500 rows**
  (`filteredData.slice(0, 500)`). Search runs over the full filtered set, but a
  query matching more than 500 symbols still shows only the first 500. This cap
  predates the change and was left alone; the "N of M" counter reports the true
  M, so the truncation is visible in the count.
- **KPI cards narrow while searching** in views that read `filteredData` for
  their summary numbers (Trigger, Climax, PDD, …). Identical to previous
  behaviour when the box is empty.

### 5.3 Not covered (and why)

`DeliveryAnomalyScanner` — already has symbol search; it renders *two* tables
(detail and summary, switched by `viewMode`) whose symbol columns are at
different positions, so pinning needs a per-mode decision rather than a copy of
the same two classes.

`TractionBoard` — server-side search already covers the full dataset; adding a
second, client-side box would be ambiguous.

Left unchanged entirely (own layout conventions; follow-up): `RecoveryLadder`,
`ReversionEngine`, `ValueRanker`, `SmartMoneyBargain`, `MFSmartMoney`,
`Leaderboard`, `FVGScanner`, `MyPositionsWithAlerts`, `NearTriggerWatchlist`,
`CrossBuyScanner` (via the broken `VirtualizedTable`), plus non-symbol tables
(`DataLake`, `FundamentalsView`, `FullFundamentalsView`, `HistoricalSearch`,
`MLLabView`, `BacktestPanel`, `FiiDiiScanner`).

### 5.4 Pinned column caveats

- **`:has()`** is required for the hover-transform reset. Baseline in current
  Chrome/Safari/Firefox. Without it the column still pins, but un-pins for the
  single row under the cursor while hovering. Degradation, not breakage.
- **Panel tone.** `--sticky-symbol-bg` is `#1a1c24`, the panel colour of nearly
  every result table. Three views sit on `#262730` cards and will show a slightly
  darker pinned strip. The variables can be overridden per view; none needed it
  for legibility.
- **Width** is a `min-width`, not a fixed width: symbol lengths differ
  (`M&M` vs `BAJAJ-AUTO`) and columns are already uniform across rows.
- **Symbol-only matching.** Typing a company name (`Tata Motors`) will not match
  `TATAMOTORS`. The source of truth is the ticker. Adding a `name → symbol` index
  is a separate feature.

---

## 6. Confirmation of untouched surfaces

- `git diff --name-only` contains **only** `myra_web/**` — no `.py` file, no
  `schema/`, no `models/`, no workflow change beyond nothing.
- Scanner logic, thresholds, ranking formulas, scoring, freshness rules and
  backend API contracts: unchanged. The 14 views only filter an array they
  already had.
- Databases: none opened, none written. No scan, ingestion, enrichment or
  backfill was started. No service was restarted.
- Default sort columns, sort direction and backend result ordering: unchanged.
  The ascending-symbol tie-break in `lib/tableSort.ts` (deterministic ordering,
  not a ranking) was not extended to the other views in this task.
- `VirtualizedTable` / `ScannerTable`: left alone, per scope.
