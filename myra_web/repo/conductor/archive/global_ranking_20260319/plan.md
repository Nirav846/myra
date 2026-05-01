# Implementation Plan: Global Results Ranking & Highlighting

## Phase 1: Global Sorting Logic
- [x] Task: Implement `apply_global_ranking(df)` in `myra_app/results_manager.py` cc4f0c0
- [x] Task: Ensure all display methods in `ResultsManager` call this ranking function cc4f0c0
- [x] Task: Write Tests: Verify sorting order (Stage 2 -> Stars -> Money Flow) cc4f0c0
- [x] Task: Conductor - User Manual Verification 'Phase 1: Global Sorting Logic' (Protocol in workflow.md) cc4f0c0

## Phase 2: UI Row Decorators
- [x] Task: Update `ResultsManager.display_discovery_table` to implement "Diamond" highlighting (top 5%) cc4f0c0
- [x] Task: Implement "Danger" dimming for Stage 4 stocks in the table rendering cc4f0c0
- [x] Task: Conductor - User Manual Verification 'Phase 2: UI Row Decorators' (Protocol in workflow.md) cc4f0c0

## Phase 3: AI Prompt & Polish
- [x] Task: Update AI prompt generation to select only top 10 sorted results cc4f0c0
- [x] Task: Cleanup any redundant sorting logic in `engine.py` or `screener.py` cc4f0c0
- [x] Task: Conductor - User Manual Verification 'Phase 3: AI Prompt & Polish' (Protocol in workflow.md) cc4f0c0
