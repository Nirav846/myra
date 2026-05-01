# Track: Librarian Modularization (2026-03-30)

## Overview
Decompose the `myra_app/librarian.py` God Class into a modular architecture. This reduces technical debt, improves testability, and isolates high-risk Turbo-SQL logic from core database persistence.

## Current State
- **File Size:** ~900 lines.
- **Responsibilities:** Connections, Schemas, Sync, Ingestion, and Intelligence (Turbo-SQL).
- **Risk:** Any syntax error in one component (e.g., Indicators) breaks the entire data acquisition layer.

## Success Criteria
- [ ] `myra_app/librarian.py` refactored into a lightweight facade (< 200 lines).
- [ ] Logic isolated into `librarian_core.py`, `librarian_intelligence.py`, `librarian_sync.py`, and `librarian_ingestor.py`.
- [ ] All existing tests and `Logic Guardian` audits pass.
- [ ] Public API of `Librarian` remains unchanged (Zero breakage for `Screener`).

## Related Tracks
- [system_sanitization_20260330](../system_sanitization_20260330/index.md)

## Implementation Plan
[Plan](./plan.md)
