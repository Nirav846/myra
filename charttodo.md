# Advanced Chart Component — Diagnosis & Phased Rebuild Plan (v2)

## Part 1: Diagnosis of Current Implementation (`AdvancedChart.tsx`)

### A. Architecture & Code Quality Issues

#### 1. **Monolithic Component (2,247 lines)**
   - `AdvancedChart.tsx` is a single file with ~2,250 lines combining view logic, data fetching, indicator calculations, trace building, layout management, and state persistence.
   - **Impact**: Extremely difficult to debug, test, or extend. High cognitive load for any modification.

#### 2. **Tight Coupling Between Concerns**
   - Data fetching logic is embedded directly in the view component (lines 1400-1600+).
   - Indicator calculations are done inline via `useMemo` chains instead of being delegated to dedicated services.
   - Plotly layout construction is intertwined with indicator state.
   - **Impact**: Changes to one concern (e.g., data fetching) risk breaking unrelated functionality (e.g., rendering).

#### 3. **Excessive useMemo Dependencies**
   - Many `useMemo` hooks have massive dependency arrays including entire objects (`toggles`, `allIndicatorData`, `baseData`).
   - Example: The main `computed` useMemo (around line 800) depends on 20+ variables, causing unnecessary recalculations.
   - **Impact**: Poor render performance; changing one toggle triggers recalculation of all indicators.

#### 4. **Inconsistent Data Flow Pattern**
   - Some indicators use the registry pattern (`chartRegistry.getIndicatorSync('sma')`).
   - Others use direct computation functions imported from libs (`computeLiquidityVoids`, `computeSmartMoneyPrints`).
   - Date-based builders (`buildSmartMoneyDivergence`, `buildDeliveryClusters`) return date-mapped data that must be adapted to candle indexes later.
   - **Impact**: Inconsistent mental model; harder to onboard new developers.

---

### B. Concrete Bugs & Limitations

#### 1. **Worker Fallback Race Condition** (Lines 285-317)
```typescript
useEffect(() => {
    if (!worker || !data || data.length === 0) return;
    // ... calculates with worker
    return () => { cancelled = true; };
}, [worker, data, toggles.showSwings, toggles.showBreakerBlocks]);
```
   - If the worker fails or is slow, the fallback `useMemo` runs immediately with stale/empty `workerResults`.
   - The `cancelled` flag only prevents setting state, not the fallback calculation itself.
   - **Symptom**: Occasional flickering or incorrect ATR/swings values during rapid toggle changes.

#### 2. **Decimated Data Mismatch** (Lines 221-229)
```typescript
const decimatedData = useMemo(() => {
    if (!data || data.length <= MAX_DATA_POINTS) return data;
    return decimateOHLCVData(data, MAX_DATA_POINTS);
}, [data]);

const renderData = decimatedData || data;
```
   - When data is decimated, indicator calculations still run on `renderData`, but viewport/index mappings reference the original `dates` array length.
   - `dateToIndex` map is built from original `dates`, but rendered candles are from decimated data.
   - **Symptom**: Crosshair shows wrong dates; shapes (FVGs, Order Blocks) appear at incorrect x-positions after decimation kicks in.

#### 3. **Viewport Sync Issues** (PlotlyCanvas.tsx + chartStore)
   - `handleRelayout` debounces at 150ms but doesn't account for chunk-loading latency.
   - When user zooms near the left edge, viewport updates trigger chunk fetches, but the viewport state may reset before the new data renders.
   - **Symptom**: "Jumping" chart when scrolling left; viewport resets to default after chunk load.

#### 4. **Memory Leak Risk — AbortController Management**
   - Multiple `AbortController` refs (`chunkLoadControllerRef`, `retryControllersRef`) but cleanup is inconsistent.
   - `fetchSymbolData` creates controllers per-symbol but they're stored in a Map that's never cleared on unmount.
   - **Symptom**: After switching symbols rapidly, network requests continue firing in background.

#### 5. **Pane Layout Calculation Fragility** (Lines 570-600)
```typescript
const paneLayout = useMemo(() => {
    const gap = 0.04;
    const activePanes = [toggles.showRsi, toggles.showDelAD, ...].filter(Boolean).length;
    const paneHeight = activePanes > 0 ? Math.min(0.16, Math.max(0.05, (0.6 - (activePanes * gap)) / activePanes)) : 0;
    // ... manual domain calculations
}, [toggles.showRsi, toggles.showDelAD, ...]);
```
   - Hardcoded magic numbers (`0.04`, `0.16`, `0.05`, `0.6`).
   - Adding a new pane requires manually updating 5+ domain calculations.
   - No validation that total domain space ≤ 1.0.
   - **Symptom**: New panes overlap or disappear; y-axis domains become invalid when >6 panes active.

#### 6. **Shape Rendering Order Bug**
   - `allShapes = [...shapes, ...trendShapes]` — trend background shapes are rendered AFTER interactive shapes.
   - Plotly renders shapes in array order; later shapes overlay earlier ones.
   - **Symptom**: Trend regime backgrounds (rgba fills) obscure FVG/Order Block highlights.

#### 7. **Type Safety Gaps**
   - Extensive use of `any` for trace/layout objects.
   - `TraceBuilderContext` and indicator result types are loosely defined.
   - **Impact**: Refactoring is risky; type errors only surface at runtime.

---

### C. Data Model Violations (Per AGENTS.md)

#### 1. **No Live Refresh Needed — But Code Suggests Otherwise**
   - AGENTS.md explicitly states: "daily-only data model (no intraday, no live refresh)".
   - Yet the component has:
     - Web Worker infrastructure designed for real-time recalculation.
     - `performanceMode` toggle optimizing for high-frequency updates.
     - Complex viewport sync logic more suited to tick-level data.
   - **Impact**: Unnecessary complexity; misleading architecture for new developers.

#### 2. **Schema Drift Risk**
   - Frontend queries hardcode column names (line 1500+):
     ```sql
     SELECT date, open, high, low, close, volume, delivery, trades, vwap, ...
     FROM technical_data WHERE symbol = ?
     ```
   - Backend schema (in `schema_registry.py`) defines 32 tables with specific columns, but frontend has no validation layer.
   - **Risk**: If backend adds/removes columns, frontend breaks silently (returns `undefined` values).

---

### D. What Actually Works (Do Not Break)

1. **Basic Candlestick Rendering** — OHLCV displays correctly for daily data.
2. **SMA/VWAP Overlays** — Moving averages render in correct price domain.
3. **Volume Pane** — Histogram shows in separate y-axis domain.
4. **RSI Pane** — Oscillator renders with correct 0-100 domain.
5. **Crosshair Tool** — Date/price display works when not decimated.
6. **Chunk Loading** — Fetching historical data in 2-year blocks functions.
7. **Fast Scroll Mode** — Keyboard/wheel navigation through symbol list works.
8. **Filter System** — Index/Sector/Market Cap filters correctly narrow symbol list.

---

## Part 2: Phased Rebuild Plan (`charttodo.md`)

### Guiding Principles
- **Side-by-side development**: New component lives as `AdvancedChartV2.tsx` until proven stable.
- **Zero new dependencies**: Use existing Plotly, Zustand, Comlink, Tailwind.
- **Daily-data-first**: No websocket/live-refresh code paths.
- **Incremental swap**: Each phase must be independently reviewable and rollbackable.

---

### Phase 1: Foundation — Static Fixture Data → Base Candlestick + Visual Comparison
**Goal**: Prove the new component can render a basic candlestick chart from mock data, with early visual drift detection.

**Scope**:
- Create `myra_web/src/views/AdvancedChartV2.tsx` (new file, parallel to existing).
- Extract minimal Plotly configuration into `myra_web/src/components/chart/ChartLayout.ts`.
- Build `CandlestickRenderer` module that accepts static OHLCV array.
- Implement single-pane layout (price only, no sub-panes yet).
- **Add visual comparison step**: Create a temporary dev-only route or toggle that renders old `AdvancedChart.tsx` and new `AdvancedChartV2.tsx` side-by-side on identical fixture data.

**Done Criteria**:
- [ ] `AdvancedChartV2.tsx` renders candlesticks from hardcoded fixture data (no API calls).
- [ ] X-axis shows date labels (index-based mapping).
- [ ] Y-axis shows price scale (linear, not log).
- [ ] Hover tooltip displays O/H/L/C for hovered candle.
- [ ] Zoom/pan via Plotly's built-in controls works.
- [ ] File passes TypeScript strict mode (`tsc --noEmit`).
- [ ] No console errors in browser dev tools.
- [ ] **NEW**: Dev comparison view exists showing old vs. new chart on same fixture data.
- [ ] **NEW**: Visual drift checklist completed (candle widths, colors, axis labels match within tolerance).

**Files to Create**:
- `myra_web/src/views/AdvancedChartV2.tsx`
- `myra_web/src/components/chart/ChartLayout.ts`
- `myra_web/src/components/chart/CandlestickRenderer.ts`
- `myra_web/src/views/ChartComparisonDev.tsx` (temporary dev-only comparison view)

**Files to Modify**: None (side-by-side development).

---

### Phase 2: Sub-Panes — Unified Volume + Delivery Pane
**Goal**: Add configurable sub-panes below the main price chart with merged Volume+Delivery display.

**Scope**:
- Implement `PaneLayoutManager` to calculate y-axis domains dynamically.
- **Merge Volume and Delivery into single pane**: Show volume histogram with delivery intensity overlay (color saturation based on delivery %).
- Add optional second pane for Delivery Percentage only (0-100% scale) if needed for detailed analysis.
- Refactor layout config to support N panes (not hardcoded to 3).
- **New indicators using OHLCV+Delivery data**:
  - **Delivery Intensity**: Color-coded volume bars (darker = higher delivery %)
  - **Volume-Delivery Divergence**: Oscillator showing when volume trends diverge from delivery trends
  - **High-Delivery Clusters**: Highlight candles where delivery > 2σ above 20-day average
  - **VWAP Deviation Bands**: Show standard deviation bands around VWAP

**Done Criteria**:
- [ ] Toggle buttons for "Show Volume+Delivery" and "Show Delivery %" work.
- [ ] Volume pane shows histogram with up/down colors AND delivery intensity overlay.
- [ ] Delivery intensity uses color saturation (light green/red = low delivery %, dark = high %).
- [ ] Optional second pane shows delivery % values (0-100%) with inverse coloring.
- [ ] Pane heights adjust automatically when toggles change.
- [ ] Layout persists across page reloads (localStorage).
- [ ] New delivery-based indicators render correctly (clusters highlighted, divergence oscillator).

**Files to Create**:
- `myra_web/src/components/chart/panes/PaneLayoutManager.ts`
- `myra_web/src/components/chart/panes/VolumeDeliveryPane.ts`
- `myra_web/src/components/chart/panes/DeliveryPercentagePane.ts`
- `myra_web/src/components/chart/indicators/DeliveryIntensity.ts`
- `myra_web/src/components/chart/indicators/VolumeDeliveryDivergence.ts`
- `myra_web/src/components/chart/indicators/HighDeliveryClusters.ts`
- `myra_web/src/components/chart/indicators/VWAPDeviationBands.ts`

**Files to Modify**:
- `AdvancedChartV2.tsx` (integrate pane system)

---

### Phase 3: Overlay Indicators — SMA, VWAP, FVG
**Goal**: Implement the indicator registry pattern with typed config objects.

**Scope**:
- Define `IndicatorConfig` interface with minimal config field for future extensibility:
  ```typescript
  interface IndicatorConfig {
    id: string;
    type: 'line' | 'area' | 'highlight' | 'shape';
    visible: boolean;
    color: string;
    zIndex: number;
    settings?: Record<string, any>; // Placeholder for future per-indicator settings
  }
  ```
- Create `IndicatorRegistry` class/map to manage active indicators.
- Implement rendering logic for:
  - **Line overlays:** SMA (Simple Moving Average).
  - **Area/Line overlays:** VWAP (Volume Weighted Average Price).
  - **Highlight boxes:** FVG (Fair Value Gaps).
- Ensure indicators respect the `visible` flag and `zIndex` from config.
- Wire up basic toggles in the dev comparison view to enable/disable indicators.

**Done Criteria**:
- [ ] Indicators render correctly over candles from fixture data.
- [ ] Toggling an indicator on/off works via registry state.
- [ ] `IndicatorConfig` structure is in place with `settings` placeholder for future extensibility without code rewrite.
- [ ] No hardcoded indicator logic in the main chart component; all driven by registry.
- [ ] Each indicator has a minimal `config` field (even if unused today) rather than hardcoding behavior inline.

**Files to Create**:
- `myra_web/src/components/chart/overlays/SMAOverlay.ts`
- `myra_web/src/components/chart/overlays/VWAPOverlay.ts`
- `myra_web/src/components/chart/overlays/FVGOverlay.ts`
- `myra_web/src/components/chart/registry/IndicatorRegistry.ts`

**Files to Modify**:
- `AdvancedChartV2.tsx` (add overlay pipeline)
- `chartRegistry.ts` (verify all required indicators registered)

---

### Phase 4: SMC Indicators — Order Blocks, Liquidity Voids, Swings + Delivery-Backed Variants
**Goal**: Integrate Smart Money Concepts indicators from existing codebase with delivery-data enhancements.

**Rationale for Keeping Separate from Phase 3**: Overlays (Phase 3) are price-domain lines/shapes with simple calculations; SMC indicators (Phase 4) are complex pattern-detection algorithms with multiple shape types. Merging would create a ~500-line phase with two distinct review concerns (rendering pipeline vs. pattern logic).

**Scope**:
- Port `computeSmartMoneyPrints`, `computeLiquidityVoids` to new architecture.
- Add Order Blocks detection (via registry).
- Add Swing Points detection (via registry).
- Add Equal Highs/Lows detection.
- **New delivery-backed SMC indicators** (from user requirements):
  - **Delivery-Weighted Average Price (DWAP)**: Like VWAP but uses only delivery volume instead of total volume
  - **Institutional Conviction Blocks**: Filter order blocks by delivery % (solid color if >2σ above 20-day avg delivery, hollow if low)
  - **Price-Delivery Divergence Oscillator**: Compare price slope vs delivery volume slope to detect trend weakness
  - **Delivery-Adjusted Accumulation/Distribution (DA-AD)**: A/D line using delivery volume instead of total volume
  - **Delivery Thrust Candles**: Highlight candles where delivery >200% of 20-day SMA AND close in top/bottom 25% of range
- Ensure all SMC shapes respect viewport (only render visible range).

**Done Criteria**:
- [ ] Order Blocks highlight institutional zones (bullish/bearish colors).
- [ ] Liquidity Voids show shaded areas for low-volume gaps.
- [ ] Swing Points mark local highs/lows with labels.
- [ ] Equal Highs/Lows draw horizontal lines at tested levels.
- [ ] DWAP renders as separate line overlay (distinct from VWAP).
- [ ] Institutional Conviction Blocks show solid/hollow styling based on delivery validation.
- [ ] Price-Delivery Divergence Oscillator renders in sub-pane.
- [ ] DA-AD line renders correctly.
- [ ] Delivery Thrust Candles highlighted with special colors (cyan/gold for bullish thrusts, magenta/purple for bearish).
- [ ] All SMC indicators have settings panels (thresholds, lookbacks).
- [ ] Shapes update when viewport changes (zoom/pan).

**Files to Create**:
- `myra_web/src/components/chart/smc/OrderBlocks.ts`
- `myra_web/src/components/chart/smc/LiquidityVoids.ts`
- `myra_web/src/components/chart/smc/SwingPoints.ts`
- `myra_web/src/components/chart/smc/InstitutionalConviction.ts`
- `myra_web/src/components/chart/indicators/DWAP.ts`
- `myra_web/src/components/chart/indicators/PriceDeliveryDivergence.ts`
- `myra_web/src/components/chart/indicators/DeliveryAdjustedAD.ts`
- `myra_web/src/components/chart/indicators/DeliveryThrustCandles.ts`
- `myra_web/src/components/chart/SMCSettingsPanel.tsx`

**Files to Modify**:
- `AdvancedChartV2.tsx` (add SMC pipeline)
- `indicatorWorker.ts` (ensure SMC calculations supported)

---

### Phase 5: Data Wiring & Performance — API Integration + Decimation
**Rationale for Consolidation**: Data wiring (fetching, caching, chunk loading) and performance (decimation, virtualization) are tightly coupled—you cannot decimate data you haven't fetched, and both concern the "data preparation pipeline." Merging reduces phase count without sacrificing reviewability since both touch the same data flow.

**Goal**: Replace fixture data with real API calls and optimize for large datasets.

**Scope**:
- Integrate with `/api/chart/{symbol}` endpoint (existing route).
- Implement data fetching hook with caching (per-symbol, per-range).
- Add error handling (404, network failures, empty datasets).
- Support date range selection (1M, 3M, 6M, 1Y, All).
- Implement chunk loading for "All" range (fetch 2 years at a time).
- Implement LTTB decimation for datasets >5000 candles.
- Add virtualized shape rendering (only render shapes in viewport).
- Optimize Web Worker communication (batch indicator requests).

**Done Criteria**:
- [ ] Symbol search returns valid ticker suggestions.
- [ ] Chart loads data for selected symbol within 2 seconds (cached).
- [ ] Range selector (1M/3M/6M/1Y/All) updates chart data.
- [ ] Chunk loading fetches older data when scrolling left (for "All" range).
- [ ] Error messages display for invalid symbols or network issues.
- [ ] Mock data fallback available for offline testing (toggle in settings).
- [ ] Chart renders 10k candles in <100ms (decimated to 2k points).
- [ ] Scrolling/zooming maintains 60 FPS.
- [ ] Web Worker processes 10 indicators in <50ms.
- [ ] Memory usage stays under 100MB for typical session.

**Files to Create**:
- `myra_web/src/hooks/useChartData.ts`
- `myra_web/src/lib/chartDataCache.ts`
- `myra_web/src/utils/chartPerformance.ts`

**Files to Modify**:
- `AdvancedChartV2.tsx` (replace fixture with hook, add decimation)
- `routes/chart.py` (verify endpoint supports required fields)
- `dataDecimator.ts` (verify LTTB integration)
- `indicatorWorker.ts` (optimize batch processing)

---

### Phase 6: Feature Parity — Crosshair, Annotations, Settings
**Goal**: Match all UX features from original `AdvancedChart.tsx`.

**Scope**:
- Crosshair tool with date/price display.
- Right-axis annotations (current indicator values).
- Settings sidebar (all toggles from original).
- Keyboard shortcuts (arrows for symbol scroll, Home/End for viewport).
- Fast Scroll mode (wheel navigation through symbols).

**Done Criteria**:
- [ ] Crosshair snaps to nearest candle, shows O/H/L/C/Vol/Del%.
- [ ] Right-axis labels show SMA/VWAP/RSI current values.
- [ ] Settings sidebar matches original (grouped by category).
- [ ] Keyboard shortcuts work (← → for crosshair, ↑ ↓ for symbol).
- [ ] Fast Scroll mode navigates symbol list smoothly.
- [ ] All settings persist to localStorage.

**Files to Create**:
- `myra_web/src/components/chart/CrosshairTool.tsx`
- `myra_web/src/components/chart/AnnotationLayer.ts`
- `myra_web/src/components/chart/ChartSettingsSidebar.tsx`

**Files to Modify**:
- `AdvancedChartV2.tsx` (integrate UX features)
- `useCrosshair.ts` (adapt for new architecture)

---

### Phase 7: Testing & Validation
**Goal**: Prove new component is stable and ready for production.

**Scope**:
- Write unit tests for core utilities (pane layout, decimation, indicator calcs).
- Write integration tests (data fetching, viewport sync).
- Manual QA checklist (all toggles, ranges, symbols).
- Performance benchmarks vs. original component.

**Done Criteria**:
- [ ] Unit test coverage >80% for new utility modules.
- [ ] Integration tests pass for happy path + error cases.
- [ ] Manual QA checklist completed (document in `CHART_QA.md`).
- [ ] Performance benchmarks show ≥20% improvement over original.
- [ ] Zero TypeScript errors, zero ESLint warnings.

**Files to Create**:
- `myra_web/src/components/chart/__tests__/` (test suite)
- `docs/CHART_QA.md` (manual QA checklist)

**Files to Modify**: None.

---

### Phase 8: Dual-Chart Integration
**Goal**: V1 and V2 coexist permanently — user-selectable, same pattern as scanner tab selection. No deprecation planned.

**Scope**:
- V1/V2 toggle on `/chart` route, persisted to localStorage.
- V2 wired to real data via `useChartData` hook.
- Both charts remain available; user choice persists across sessions.
- Future: consider making V2 the default after sufficient real-world validation.

**Current V2 Indicator Status (verified against live RELIANCE data):**

| Indicator | Status | Source |
|-----------|--------|--------|
| SMA (20, 50, 200) | ✅ Working | V2 native (registry) |
| VWAP | ✅ Working | V2 native (daily-reset fallback; DB column is NULL) |
| FVG | ✅ Working | V2 native (registry) |
| Price-Delivery Divergence Oscillator | ✅ Working | V2 native (SMCRegistry) |
| Order Blocks | ✅ Working | V2 native (SMCRegistry) |
| Swing Points | ✅ Working | V2 native (SMCRegistry) |
| DA-AD | ✅ Working | V2 native (SMCRegistry) |
| Delivery Thrust Candles | ✅ Working | V2 native (SMCRegistry) |
| DWAP | ✅ Working | V2 native (fixed field name) |

**V1 Indicators NOT yet ported to V2 (explicitly tracked):**
- EMA, WMA, SMMA (moving average variants)
- Bollinger Bands, Keltner Channel (volatility envelopes)
- RSI (momentum oscillator in sub-pane)
- Liquidity Voids (SMC — partially in V1 worker, not ported to V2)

**Done Criteria**:
- [x] V1/V2 toggle rendered on `/chart` route (commit `1f53683`).
- [x] Toggle persists choice to localStorage (`chart-version` key).
- [x] V2 receives real symbol + date range from global context.
- [x] REST API returns delivery, delivery_pct, vwap alongside OHLCV (commit `57c35b6`).
- [x] 9 indicators verified against live data (SMA, VWAP, FVG, Price-Delivery Divergence, Order Blocks, Swing Points, DA-AD, Delivery Thrust, DWAP).

**Files Modified**:
- `myra_web/src/App.tsx` (toggle wiring)
- `myra_web/src/views/AdvancedChartV2WithData.tsx` (new wrapper)
- `myra_web/routes/chart.py` (import fix + extended SELECT + date range params)
- `myra_web/src/services/chartDataService.ts` (CandleData type)
- `myra_web/src/components/chart/overlays/VWAPOverlay.ts` (daily-reset VWAP)
- `myra_web/src/components/chart/indicators/DeliveryAdjustedAD.ts` (field fix)
- `myra_web/src/components/chart/indicators/DeliveryThrustCandles.ts` (field fix)
- `myra_web/src/components/chart/indicators/DWAPOverlay.ts` (field fix)

---

## Review Gates

| Phase | Reviewer | Approval Criteria |
|-------|----------|-------------------|
| Phase 1 | Tech Lead | Renders correctly, no TypeScript errors, visual drift <5% vs old chart |
| Phase 2 | Tech Lead | Panes don't overlap, toggles work |
| Phase 3 | Domain Expert | Indicator values match expected calculations |
| Phase 4 | Domain Expert | SMC shapes align with price action correctly |
| Phase 5 | QA Engineer | API integration handles all error cases, performance benchmarks met |
| Phase 6 | QA Engineer | All UX features match original behavior |
| Phase 7 | Tech Lead + QA | Test coverage >80%, zero critical bugs |
| Phase 8 | Tech Lead + Product | Toggle works, both charts render with live data, user choice persists |

---

## Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Decimation causes shape misalignment | Medium | High | Rigorous testing with known datasets; visual regression tests |
| Web Worker adds latency for small datasets | Low | Medium | Threshold tuning; main-thread fallback for <500 candles |
| Pane layout breaks with many active panes | Medium | Low | Max pane limit (6); scrollable pane container as fallback |
| API endpoint missing required fields | Low | High | Contract test in CI; schema validation layer |
| Performance regressions vs. original | Medium | High | Benchmark suite in CI; block merge if >10% slower |

---

## Out of Scope (Explicitly Not Building)

- ❌ Live/real-time data updates (per AGENTS.md: daily-only model).
- ❌ WebSocket connections for price streaming.
- ❌ Sub-minute timeframes (1m, 5m, 15m candles).
- ❌ New charting library (staying with Plotly).
- ❌ Mobile touch gestures (desktop-first; future enhancement).
- ❌ Multi-chart layouts (single symbol per view for now).

---

## Next Steps

1. **Review this document** — Confirm diagnosis accuracy and phase priorities.
2. **Approve Phase 1 scope** — Greenlight to begin foundation work.
3. **Schedule check-ins** — Weekly reviews at end of each phase.

---

*Document created: $(date)*
*Author: AI Agent (per TASK instructions)*
*Status: Pending User Review*

---

## Notes: Future Test Automation

**Consideration for Later**: Adding Vitest as a lightweight test runner would enable automated regression testing. With the existing Vite setup, Vitest typically requires minimal configuration and no additional Babel/Jest infrastructure. This is outside the current chart task scope but worth evaluating post-launch for ongoing maintenance. See `_deferred_tests/` folder for test files ready for future implementation.
