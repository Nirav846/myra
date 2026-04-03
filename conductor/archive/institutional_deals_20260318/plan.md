# Implementation Plan: Implement Bulk & Block Deal Momentum Scanner

## Phase 1: Data Verification & Schema Audit
- [x] Task: Write Tests: Verify `large_deals` table population from NSE streams 48ef34f
- [x] Task: Audit `librarian.py` for efficient indexing on `large_deals` (symbol, date) 48ef34f
- [x] Task: Conductor - User Manual Verification 'Phase 1: Data Verification & Schema Audit' (Protocol in workflow.md) 48ef34f

## Phase 2: Intensity Logic & Strategy Implementation
- [x] Task: Write Tests: Implement "Institutional Intensity" math in `engine.py` 48ef34f
- [x] Task: Implement Feature: Create `myra_app/strategies/large_deal_momentum.py` 48ef34f
- [x] Task: Conductor - User Manual Verification 'Phase 2: Intensity Logic & Strategy Implementation' (Protocol in workflow.md) 48ef34f

## Phase 3: UI & Piped Scan Integration
- [x] Task: Implement Feature: Add Option 16 to `myra_app/myra.py` for Large Deal Scanner 48ef34f
- [x] Task: Implement Feature: Update `screener.py` to handle large deal metrics in Hero columns 48ef34f
- [x] Task: Write Tests: Verify cross-referencing between Large Deals and Weinstein Stages 48ef34f
- [x] Task: Conductor - User Manual Verification 'Phase 3: UI & Piped Scan Integration' (Protocol in workflow.md) 48ef34f
