# Track: UI & Logic Polish
**Status**: ACTIVE
**Owner**: Gemini CLI

## Objective
Fix the Rich Markup error in the glossary and investigate the 0% Tightness metric.

## Plan
- [x] Fix mismatched tags in `GLOSSARY` string in `myra.py`. (Fixed: Corrected yellow/green mismatch).
- [x] Verify `std20` calculation and persistence in `librarian.py`. (Fixed: Added `std20` to schema and `INSERT` statement, added `PRIMARY KEY` to `calculated_indicators`).
- [x] Ensure `engine.py` is receiving and processing non-zero `std20` values. (Fixed: `Tightness` now calculates as `% of price`).
- [x] Data Repair: Recomputed entire `calculated_indicators` table to populate `std20`.
- [ ] Final Validation: Run `1:14` and verify non-zero `Tightness`.
