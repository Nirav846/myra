# AEON UI Polish & Precision Formatting

## Objective
Restore missing institutional columns (D-POC) to Strategy 31 and implement universal precision formatting (2 decimal places) for all institutional metrics.

## Scope
- `myra_app/myra.py`: Add `d_poc` to Strategy 31 hero columns.
- `myra_app/results_manager.py`: Implement rounding logic for discovery tables.

## Steps
1.  **Column Restoration:** Update `myra_app/myra.py` to include `d_poc` in the hero columns for AEON.
2.  **Formatting Engine:** Update `myra_app/results_manager.py` to round all `hero_cols` numeric values to 2 decimal places.
3.  **Verification:** Run Strategy 31 and confirm clean output.
