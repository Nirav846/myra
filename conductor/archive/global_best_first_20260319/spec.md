# Specification: Global "Best First" Architecture

## Objective
Centralize sorting logic into a Global Result Processor to ensure all MYRA scan outputs follow a standardized "Gold Standard" hierarchy and include visual decorators for high-density reading.

## Requirements
1. **Global Sorting (ResultsManager.py)**:
    - **Stage Dominance**: Stage 2 (↗) is always top.
    - **Conviction Multiplier**: 5 Stars > 4 Stars > ...
    - **Volume/Liquidity**: Within same star rating, highest Money Flow (₹Cr) wins.
2. **Row Decorators (UI)**:
    - **The Diamond Row**: Top 5% of results get a Cyan background for the Ticker name.
    - **Danger Dimming**: Stage 4 stocks (the bottom) must have dimmed text color.
3. **AI Prompt Optimization**:
    - Select only the top 10 candidates after global sorting for the `MYRA_AI_READY.txt` prompt.

## Technical Details
- **Module**: `myra_app/results_manager.py`.
- **Primary Function**: `apply_global_ranking(df)`.
- **UI Hook**: `display_discovery_table` in `ResultsManager`.
