# Implementation Plan: Enhance Bottom Hunter Strategy with Dynamic Support Reversals

## Phase 1: Engine Math & Indicator Audit [checkpoint: eaf7f9b]
- [x] Task: Audit `librarian.py` to ensure `low_1y/2y/3y` are delta-computed accurately 8172a50
- [x] Task: Write Tests: Verify "Absorption" logic using `rel_spread` and `closing_pos` 7a3b57b
- [x] Task: Conductor - User Manual Verification 'Phase 1: Engine Math & Indicator Audit' (Protocol in workflow.md) 8172a50

## Phase 2: Strategy Refinement
- [x] Task: Implement Feature: Enhance `myra_app/strategies/bottom_hunter.py` with dynamic floor triggers 53d7bf8
- [x] Task: Implement Feature: Add "Institutional Vibe" (RDV + SMART Score) to bottom detection 53d7bf8
- [x] Task: Write Tests: Verify reversal triggers on mock historical floor data 53d7bf8
- [x] Task: Conductor - User Manual Verification 'Phase 2: Strategy Refinement' (Protocol in workflow.md) 53d7bf8

## Phase 3: UI & Piped Optimization [checkpoint: 8603c69]
- [x] Task: Implement Feature: Update Option 27 in `myra_app/myra.py` to support enhanced metrics 53d7bf8
- [x] Task: Implement Feature: Add "Support Reversal" playbook to Piped Playbook (Option 26) 53d7bf8
- [x] Task: Conductor - User Manual Verification 'Phase 3: UI & Piped Optimization' (Protocol in workflow.md) 53d7bf8
