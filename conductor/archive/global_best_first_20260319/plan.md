# Implementation Plan: Global "Best First" Architecture

## Phase 1: Global Sorting Gatekeeper
- [x] Task: Implement `apply_global_ranking(df)` in `myra_app/results_manager.py` cc4f0c0
- [x] Task: Refactor `sanitize_results` to call the global ranking function cc4f0c0
- [x] Task: Conductor - User Manual Verification 'Phase 1: Global Sorting Gatekeeper' (Protocol in workflow.md) cc4f0c0

## Phase 2: Row Decorators & High-Density UI
- [x] Task: Implement "Diamond Row" highlighting (Top 5%) in `display_discovery_table` cc4f0c0
- [x] Task: Implement "Danger Dimming" for Stage 4 in `display_discovery_table` cc4f0c0
- [x] Task: Conductor - User Manual Verification 'Phase 2: Row Decorators & High-Density UI' (Protocol in workflow.md) cc4f0c0

## Phase 3: AI Prompt & Polish
- [x] Task: Update `generate_ai_prompt` to strictly use Top 10 results cc4f0c0
- [x] Task: Cleanup any redundant sort-by logic in `myra.py` cc4f0c0
- [x] Task: Conductor - User Manual Verification 'Phase 3: AI Prompt & Polish' (Protocol in workflow.md) cc4f0c0
