# Track: Scanner & UI Stability Fix
**Status**: ACTIVE
**Owner**: Gemini CLI

## Objective
Fix the blinking footer issue and restore scanner functionality in myra.py.

## Files
- myra_app/myra.py
- myra_app/screener.py
- myra_app/UI_Manager.py
- myra_app/librarian.py

## Plan
- [x] Investigate myra.py Live loop for rapid refresh/crashes. (Fixed: Lowered refresh rate, optimized breadth update, disabled auto_refresh to fix input blocking)
- [x] Debug screener.py execute_scan for hangs or silent failures. (Fixed: Suppressed standard prints, fixed engine imports/return types)
- [x] Verify Librarian.sync_market_data for blocking or failing operations. (Fixed: Syntax error in Librarian class fixed)
- [x] Fix UI_Manager.py footer logic to stop blinking. (Fixed: Cached DB stats in Librarian)
- [x] Final Validation: User to run `myra.py` and confirm stability. (Fixed: Completely removed global `Live` context that was conflicting with `console.status` and causing silent UI freezes).
