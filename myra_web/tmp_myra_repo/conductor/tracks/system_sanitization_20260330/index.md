# Track: System Sanitization & Repository Hardening (2026-03-30)

## Overview
This track addresses the "messy" state of the PKScreener/MYRA root directory. It aims to enforce strict directory isolation as per **Hardened Architecture v2.5**, moving research, debug, and utility scripts into dedicated subdirectories.

## Current State
- **Root Pollution:** ~60+ scripts (debug, research, one-offs) in the project root.
- **Scattered Data:** `.db`, `.txt`, and `.sqlite` files are mixed with code.
- **Test Inconsistency:** Some tests are in `test/`, others are in the root as `test_*.py`.

## Success Criteria
- [ ] Root directory contains only essential config files (`.env`, `requirements.txt`, etc.) and the main entry points.
- [ ] No `test_*.py` files remain in the root.
- [ ] A clear `research/` directory contains all one-off analysis scripts.
- [ ] A clear `tools/` directory contains all utility and maintenance scripts.
- [ ] All database/data files are isolated in a `data/` or `db/` folder (where appropriate).

## Related Tracks
- [system_hardening_audit_20260324](../system_hardening_audit_20260324/index.md)
- [decouple_myra_from_pkscreener](../decouple_myra_from_pkscreener/index.md)

## Implementation Plan
[Plan](./plan.md)
