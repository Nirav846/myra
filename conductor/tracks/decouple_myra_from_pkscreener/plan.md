# Implementation Plan: Decouple MYRA from PKScreener

## Phase 1: Research & Discovery
- [x] **Recursive Import Tracing**: Use a script to find all non-standard and non-local imports in `myra_app/`.
- [x] **Identify Source Locations**: Locate the source code for each identified dependency (in `site-packages` or `pkscreener/` root).
- [x] **Check for Hidden Dependencies**: Look for dynamic imports, config files, or non-Python dependencies.

## Phase 2: Setup `myra_core`
- [x] **Initialize Package**: Create `myra_core/` with `__init__.py`.
- [x] **Copy Code**: Extract only the necessary files/classes from the identified dependencies into `myra_core/`.
- [x] **Fix Internal Imports**: Ensure the extracted files in `myra_core/` import from each other correctly using relative or absolute paths within `myra_core`.

## Phase 3: Refactor `myra_app`
- [x] **Update Imports**: Systematically replace `import PKDevTools` etc. with `import myra_core`.
- [x] **Local Testing**: Run `python myra_app/myra.py` and verify it starts and performs basic scans.

## Phase 4: Final Cleanup
- [x] **Delete root `pkscreener/`**: Safely remove the local folder.
- [x] **Update `requirements.txt`**: Remove `PKDevTools`, `PKNSETools`, `PKBrokers` if they are fully localized.
- [x] **Final Verification**: Run a full suite of tests (if available) or manual end-to-end verification.
