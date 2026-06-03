---

# Contributing to MYRA

Thank you for your interest in contributing. MYRA is a personal NSE stock screening 
platform built for long-term investment research. Contributions of all kinds are 
welcome — new scanners, indicators, bug fixes, documentation, and tests.

## Getting Started

1. Fork the repo and clone your fork locally
2. Create a feature branch: `git checkout -b feature/your-scanner-name`
3. Make your changes following the rules below
4. Verify syntax before committing: `python -c "import ast; ast.parse(open('changed_file.py').read()); print('OK')"`
5. Open a Pull Request against `main`

## Branch Naming

| Type | Pattern | Example |
|------|---------|---------|
| New scanner | `feature/scanner-name` | `feature/delivery-spike-scanner` |
| Bug fix | `fix/description` | `fix/market-breadth-query` |
| Documentation | `docs/description` | `docs/update-architecture` |
| Refactor | `refactor/description` | `refactor/fundamental-ranker` |

## Coding Rules (strictly enforced — violations will be rejected)

These rules exist because MYRA runs on a single Windows machine with large SQLite 
databases. Violations cause silent data corruption or performance collapse.

| # | Rule | Why |
|---|------|-----|
| 1 | No `os.getcwd()` — use `DB_DIR`, `DATA_DIR`, `CACHE_DIR` from `constants.py` | Breaks on any machine where cwd ≠ project root |
| 2 | No hardcoded DB filenames — use `LibrarianCore.DB_MAP["key"]` | DB names can change; the map is the single source of truth |
| 3 | No `df.append()` in loops — use list + `pd.concat()` | O(n²) memory copies on large datasets |
| 4 | No `.strftime()` on a Series — use `.dt.strftime()` | Raises TypeError on pandas Series |
| 5 | No chained indexing `df[x][y]` — use `.loc[x, y]` | Causes silent SettingWithCopyWarning data corruption |
| 6 | No broad `except Exception: pass` — at minimum `logger.error(e)` | Swallows real failures silently |
| 7 | No DB queries inside per-symbol loops — batch insert after all fetches | N+1 query problem kills performance across 3,000 symbols |
| 8 | Always verify syntax before submitting | Prevents broken commits reaching main |

## Adding a New Scanner

Scanners live in `myra_app/strategies/`. Each scanner is a function that:

1. Takes a `Librarian` instance and optional parameters
2. Queries `technical_data` via `librarian._tech_conn` (read-only)
3. Returns a list of dicts with at minimum `{"symbol": str, "score": float, "reason": str}`
4. Never writes to any database
5. Never makes network calls

A FastAPI endpoint in `myra_fastapi_server.py` exposes the scanner result.
The frontend calls it via `Librarian.ts`.

## Adding a New Indicator

Indicators are computed in `feature_enrichment.py` using Polars/Pandas vectorized 
operations and written back to `technical_data`. New indicators must:

1. Be added as a column to `technical_data` via a schema migration in `librarian_schema.py`
2. Be computed in a vectorized batch (no per-row Python loops)
3. Handle `None`/`NaN` values gracefully

## Pull Request Checklist

Before submitting a PR, confirm:

- [ ] `python -c "import ast; ast.parse(open('changed_file.py').read()); print('OK')"` passes for every changed `.py` file
- [ ] No `os.getcwd()` introduced
- [ ] No hardcoded DB filenames introduced
- [ ] No DB queries inside loops
- [ ] No `df.append()` in loops
- [ ] New scanners return `{"symbol", "score", "reason"}` at minimum
- [ ] New indicators have a corresponding schema column in `librarian_schema.py`

## Project Goals

MYRA is built exclusively for **long-term buying opportunities** — stocks positioned 
for sustained upside over weeks to months. No short-selling setups. Any scanner or 
indicator contribution should be evaluated against this goal.

## Questions

Open an issue or start a Discussion on GitHub. PRs with questions in the description 
are also welcome.
