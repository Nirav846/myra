# Myra — Agent Context

## Project Scope
AI-powered Indian stock market screener (NSE/BSE). FastAPI + React/TypeScript frontend + Python backend (Polars, SQLite). 8 SQLite sidecar databases, 8 scanners (7 API-registered + 1 endpoint-only), XGBoost ML models, SMC enrichment pipeline.

---

## Read this before writing any query, migration, or schema-touching code

You (Qwen Coder, Jules, or any other repo-only agent) do **not** have access to the live local databases. Do not guess column names, types, or table structure. The source of truth is checked into the repo:

- `schema/*.sql` — full DDL (`CREATE TABLE` / `CREATE INDEX`) dumped directly from the live DBs, no data.
- `schema/schema_manifest.json` — per-DB list of tables, columns, types, and row counts.
- `myra_app/schema_registry.py` — 32 table schemas, the canonical Python-side definition.

If `schema/*.sql` looks stale or missing for a table you need, **stop and ask**, or regenerate it with `python scripts/dump_schema.py` (read-only against the live DB) rather than inventing a schema.

## Current Project State

| Metric | Value |
|--------|-------|
| Python version | 3.12 |
| Total data rows | ~2.25M (technical_data) |
| Symbols tracked | 3,000+ |
| Fundamentals symbols | 2,309 (with promoter data) |
| Enrichment completion | 99.4% |
| Databases | 8 SQLite sidecars (WAL mode) |
| Registered scanners | 7 (Trigger, Float Exhaustion, Invisible Hand, Wyckoff, LFD, OFP, Seasonal Delivery) + 1 endpoint-only (DCB Bargain) |
| Portfolio Tracker | ✅ CLI tool with auto-refresh, scanner overlap, risk metrics, smart caching |
| Scanner candidates (typical) | 2–623 per run |
| ML models | 2 (forward_return.xgb + launchpad_xgb.joblib) |
| Test suite | 413 passed + 1 skipped, all green |
| CI | GitHub Actions (push/PR to main) |

## Architecture References
- `myra_app/schema_registry.py` — 32 table schemas
- `myra_app/librarian_core.py:52` — `DB_MAP` database filenames
- `myra_app/constants.py` — `DB_DIR` and other path/config constants; DBs must always be resolved through here, never hardcoded
- `myra_app/feature_enrichment.py:140` — daily enrichment pipeline
- `myra_app/feature_enrichment.py:494` — batch `enrich_from_dataframe`
- `myra_app/fundamental_sync.py` — `MS_CANONICAL_MAP` (10 camelCase→snake_case mappings)
- `myra_app/background_orchestrator.py` — daemon thread management
- `tools/enrich_history.py` — optimized batch backfill
- `myra_app/strategies/` — 57 scanner strategy files (7 registered via API)
- `myra_app/strategies/dcb_bargain.py` — DCB Bargain scanner (Delivery Cost Basis, endpoint-only)

## Data granularity — daily only, no intraday

MYRA works exclusively with **daily (EOD) data** — there is no intraday/lower-timeframe feed (no 1-min, 5-min, tick data, etc.) and none is planned. This has concrete implications for anything an agent builds:

- **Do not build live/real-time refresh, websocket streaming, or sub-minute polling** — there is no data source to back it, and it would just hammer NSE/data endpoints for no benefit.
- If a feature genuinely needs a "refresh" during market hours (e.g. a dashboard staleness check), poll at **5–10 minute intervals at most**, not continuously — and only if there's a real reason to check more often than once after market close.
- Design scanners, enrichment, and UI update cycles around the daily bhavcopy cadence (one ingestion per trading day after 18:30 IST close), not intraday ticks.
- If a task description implies real-time/streaming behavior, treat that as a misunderstanding of the data model and flag it rather than implementing it.

## Never commit database files — do not touch .gitignore for this

All `*.db`, `*.db-shm`, and `*.db-wal` files (production DBs, fixture DBs, anything under `myra_app/db/` or `tests/fixtures/`) are gitignored **on purpose** — they're large, regenerable, and/or contain real market data that shouldn't be in version control.

- **Never edit `.gitignore` to un-exclude any `.db`/`.db-shm`/`.db-wal` pattern.** If a DB file looks "untracked" or "missing," that's expected — it's not a bug to fix.
- If you're unsure whether a file should be tracked, **ask, don't reconfigure `.gitignore` and commit**.
- Fixture DBs (`tests/fixtures/*.db`) are meant to be built locally via `python tests/fixtures/build_fixture_db.py`, not committed — the builder script is the source of truth, not the binary output.
- If a `git status`/`git add -A` shows a pile of unexpected `.db` files staged, stop and flag it rather than pushing.

## Invariants — do not violate these

- **Never silently overwrite a valid metric with null/0/NA on a failed or partial fetch.** Distinguish a legitimately-absent value (e.g. delisted stock) from a failed fetch; a failed fetch must skip the write and surface the failure, not clobber existing data.
- **DB access always goes through `DB_MAP` / `DB_DIR` in `myra_app.constants`**, never a hardcoded path or a locally-redefined map.
- **`SchemaRegistry` only adds columns** (`ALTER TABLE ... ADD COLUMN`) — never drop or destructively modify existing columns.
- **Pipeline failures/warnings must be visible to the user on-screen**, not just logged; a task failure should not silently stop other tasks from running.
- **Consolidation/backfill operations must be idempotent** — safe to re-run without duplicating or corrupting data (see the `UPDATE WHERE (canonical IS NULL OR canonical = 0) AND alias IS NOT NULL AND alias != 0` pattern).
- **camelCase → snake_case mapping happens in `_merge_and_insert()`** at record-building time, not in the fetch layer — don't reintroduce mapping logic upstream.
- **Calendar-vs-trading-day conversion uses factor 0.6** (conservative 5/7 ratio) — don't substitute a different constant without discussion.

## Testing & linting

- Run the full suite: `pytest`
- Code must be `black` and `flake8` clean before you consider a change done — run both locally and fix any violations yourself, don't leave them for review.
- Tests run against `tests/fixtures/*.db` — small synthetic databases with the real schema but fake rows, wired in via `conftest.py` so tests never touch production DBs. Never point a test at a live `myra_*.db` file.
- If you add or change a table, add/update the corresponding fixture and a schema-contract smoke test asserting the expected columns exist.

## Regenerating schema docs after a real migration

```
python scripts/dump_schema.py
```
Commit the resulting `schema/*.sql` and `schema/schema_manifest.json` changes alongside the migration itself, in the same PR.

## Key Decisions
- camelCase → snake_case mapping happens in `_merge_and_insert()` at record-building time, not in the MS API fetch layer.
- Consolidation backfill is idempotent: `UPDATE WHERE (canonical IS NULL OR canonical = 0) AND alias IS NOT NULL AND alias != 0`.
- SchemaRegistry only ADD COLUMNS (ALTER TABLE), never drops.
- Calendar-vs-trading-day conversion uses factor 0.6 (conservative 5/7 ratio).