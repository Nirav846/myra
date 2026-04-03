# Implementation Plan: NSE Surpriver v2

## Phase 1: Core Strategy Development
- [x] Create `myra_app/strategies/surpriver_v2.py`.
- [x] Implement multi-window Z-score logic.
- [x] Add Buying Wick absorption filter.

## Phase 2: Integration & UI
- [x] Map strategy to Choice 34 in `myra_app/myra.py`.
- [x] Add custom `hero_cols` for Anomaly and Windows metrics.
- [x] Update `GLOSSARY` with new terminology.

## Phase 3: Validation
- [ ] Run `python myra_app/myra.py` and select Option 34.
- [ ] Verify that results include high-conviction "Basing" or "Accumulation" phases.
- [ ] Check if `Anomaly_Score` correctly identifies stocks with high delivery absorption.
