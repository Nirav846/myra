# QA Validation Workflow
**Description:** Run this workflow before finalizing any code hand-off to ensure MYRA syntax compliance.

**Steps:**
1. Open the integrated terminal.
2. Execute the command: `python legacy_pkscreener/check_myra_syntax.py`
3. Read the output logs.
4. If errors exist, automatically fix them and re-run. If tests pass, notify the user.
