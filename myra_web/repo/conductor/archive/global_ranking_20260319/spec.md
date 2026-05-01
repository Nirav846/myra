# Specification: Global Results Ranking & Highlighting

## Objective
Implement a global "Best First" result processor that standardizes sorting across all scanners and adds visual decorators to the UI to highlight top performers and dim high-risk stocks.

## Requirements
1. **Global Sorting (ResultsManager.py)**:
    - Sort all scan results by:
        1. **Stage Dominance**: Stage 2 (↗) first.
        2. **Conviction**: Star rating (Descending).
        3. **Liquidity**: Money Flow (Descending).
2. **Visual Decorators (UI/ResultsManager)**:
    - **Diamond Row**: Highlight the top 5% of results with a Cyan background for the Ticker name.
    - **Danger Dimming**: Dim the text for Stage 4 stocks to reduce distraction.
3. **AI Prompt Optimization**:
    - Update `MYRA_AI_READY.txt` generation to include only the top 10 candidates *after* the global sort is applied.

## Technical Details
- **Primary File**: `myra_app/results_manager.py`.
- **Secondary File**: `myra_app/UI_Manager.py` (if applicable for row styling).
- **Sort Keys**: `Stage`, `Rating` (Stars), `Money Flow`.
