# Implementation Plan: Librarian Modularization

## Objective
Surgically extract responsibilities from `Librarian` into specialized modules using the **Extract Class** pattern.

## Stage 1: Baseline & Preparation (Auditor)
- [x] Run `python test/logic_guardian.py` to record the baseline mathematical state.
- [x] Create `myra_app/librarian_core.py` to house the `Librarian` base class and locking logic.

## Stage 2: Extraction of Intelligence (Engineer)
- [x] Create `myra_app/librarian_intelligence.py`.
- [x] Move `update_indicator_history` and Turbo-SQL logic to `IntelligenceLayer` class.
- [x] Update `Librarian` to delegate indicator calls to `IntelligenceLayer`.

## Stage 3: Extraction of Ingestion (Engineer)
- [x] Create `myra_app/librarian_ingestor.py`.
- [x] Move `_fetch_archives`, `_fetch_range`, and `_ingest_into_sqlite` to `BhavcopyIngestor`.
- [x] Update `Librarian` to delegate ingestion calls.

## Stage 4: Extraction of Sync & Schema (Engineer)
- [x] Create `myra_app/librarian_sync.py` for background thread management.
- [x] Create `myra_app/librarian_schema.py` for `_create_tables` and `_migrate_schema`.
- [x] Final refactor of `myra_app/librarian.py` as a facade.

## Stage 5: Final Validation (Auditor)
- [x] Re-run `Logic Guardian` audit.
- [x] Verify `DataFetcher` connectivity via `Ghost Engine`.
- [x] Perform a full sync test.
