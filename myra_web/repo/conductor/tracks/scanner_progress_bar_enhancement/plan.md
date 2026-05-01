# Conductor Track: Scanner Progress Bar Enhancement - Implementation Plan

## Objective
Implement a minimalist, single-line scanner progress bar in `myra_app/screener.py` as per the `PROMPT.txt` specification, ensuring a clean, non-pyramiding display in Windows CMD/PowerShell.

## Steps

### Phase 1: Research & Preparation
- [x] **Step 1.1: Confirm existing progress bar usage.**
  - **Action:** Identify where `tqdm` (or similar progress bar library) is currently used within `myra_app/screener.py`, specifically within or around the `execute_scan` method.
  - **Verification:** Confirm the exact line(s) and context of the existing progress bar implementation.

- [x] **Step 1.2: Determine optimal placement for `myra_log` utility function.**
  - **Action:** Decide whether to implement `myra_log` directly within `MYRAScreener` class, or as a standalone utility function in `myra_core/utils/` (create if necessary).
  - **Verification:** Justify the chosen placement based on code organization and reusability.

### Phase 2: Implementation
- [x] **Step 2.1: Implement `myra_log` function.**
  - **Action:** Write the `myra_log` function based on the provided Python code in `PROMPT.txt`.
  - **Verification:** Ensure the function correctly calculates percentage, throttles updates, and uses `sys.stdout.write` with `\r` and `sys.stdout.flush()`.

- [x] **Step 2.2: Integrate `myra_log` into `execute_scan` method.**
  - **Action:** Replace existing progress bar calls within the `execute_scan` method (and any other relevant scanning loops) with calls to `myra_log`.
  - **Verification:** Ensure `myra_log` is called with `current` and `total` values appropriately within the scanning loop.


### Phase 3: Testing & Validation
- [ ] **Step 3.1: Manual Functional Testing.**
  - **Action:** Run a scanner operation (e.g., `run_custom_scout` or `run_full_market_scout`) and visually verify that the new minimalist progress bar is displayed correctly in a single line without the "pyramid" effect.
  - **Verification:** Observe the terminal output and confirm it matches the desired "Hacker-Style" minimalist log.

- [ ] **Step 3.2: Automated Test Updates (if applicable).**
  - **Action:** If existing tests cover progress bar display, update them to reflect the new implementation. If not, consider adding a basic test to assert the presence of the new log format (though visual verification is primary for UI elements).
  - **Verification:** All relevant tests pass.
