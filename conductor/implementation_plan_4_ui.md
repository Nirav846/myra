# IMPLEMENTATION PLAN: Next-Gen Terminal UI (Plan 4)

## Objective
Upgrade the MYRA dashboard to a multi-widget terminal layout using `rich` for a professional, Bloomberg-style aesthetic and real-time market intelligence.

## Key Files & Context
- `myra_app/UI_Manager.py`: Handles dashboard layout and widget rendering.
- `myra_app/myra.py`: Orchestrates the main loop and UI refresh.

## Implementation Steps

### Phase 1: Sidebar Intelligence Widgets
1.  **Implement `MYRA_UI.get_ias_leaderboard(librarian)`**:
    - Query `db/governance.db` for symbols with the highest `ias_score` in the last 30 days.
    - Render a compact table showing Symbol, IAS Score, and Tag.
2.  **Implement `MYRA_UI.get_fii_dii_flow(librarian)`**:
    - Render a summary of the latest FII/DII activity from `governance.db`.

### Phase 2: Multi-Pane Terminal Layout
1.  **Refactor `draw_dashboard`**:
    - Split the 'body' layout horizontally:
        - `body_left` (Ratio 3): Existing Menu Grid.
        - `body_right` (Ratio 1): Sidebar with IAS Leaderboard and Market Flow.
2.  **Add Visual Styling**:
    - Standardize border colors: `cyan` for technicals, `magenta` for alpha, `green` for strategic.
    - Use `Live` context in `myra.py` for flicker-free updates (if feasible with input loop).

### Phase 3: Performance Optimization
1.  **Vectorized Indicator Math**:
    - Update `myra_app/librarian.py` to ensure indicator precomputation uses pure `numpy`/`pandas` vectorization.
2.  **Asynchronous Widget Updates**:
    - Ensure sidebar data is cached to prevent database locks during UI redraws.

## Verification & Testing
1.  **Layout Stress Test**: Verify the UI scales correctly on standard (80 col) vs wide (150 col) terminals.
2.  **Real-time Accuracy**: Confirm the IAS Leaderboard reflects the latest `governance.db` sync.
3.  **Flicker Check**: Ensure the dashboard remains readable during background sync operations.
