# Specification: Background Sync Status Summary in MYRA UI

## 1. Goal
Replace the static "Breadth: ↗ 0 | ↘ 0" in the MYRA footer with a live, responsive summary of background data fetching tasks (e.g., current task and percentage completion).

## 2. Requirements
### 2.1 SyncStatus Tracker (`myra_app/librarian.py`)
- [ ] Implement a `SyncStatus` class to store:
    - `task_name`: Current activity (e.g., "Fetching NIFTY 500 OHLCV").
    - `completed_count`: Number of items processed.
    - `total_count`: Total items in the current batch.
    - `percentage`: Computed property `(completed / total) * 100`.
- [ ] Add a `status` instance to the `Librarian` class.
- [ ] Update `Librarian._fetch_range()` and other sync loops to set `task_name`, `total_count`, and increment `completed_count`.

### 2.2 UI Integration (`myra_app/UI_Manager.py`)
- [ ] Update `MYRA_UI.get_footer()` to retrieve `librarian.status`.
- [ ] If `status.task_name` is present, display: `[bold cyan]Sync:[/] {status.task_name} ({status.percentage}%)`.
- [ ] Fallback to the original Breadth display if no sync is active.

## 3. Implementation Plan
- **Phase 1**: Implement `SyncStatus` class and update `Librarian` methods.
- **Phase 2**: Update `UI_Manager.py` footer logic.
- **Phase 3**: Verify live updates in `myra.py` within the `console.screen()` context.

## 4. Success Criteria
- [ ] The footer dynamically updates during background sync.
- [ ] No race conditions between the sync thread and UI refresh.
- [ ] The UI remains responsive and flicker-free.
