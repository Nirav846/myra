# Myra React Dashboard - Manual Data Wiring Guide

This document serves as a failsafe manual for rewiring or debugging the connection between the React UI and the Python Quantitative Engine.

## The `Librarian` Bridge Architecture

All React components use the shared `src/lib/Librarian.ts` class to retrieve data.
The Librarian acts as a proxy, sending `POST` requests to your local Python server (FastAPI).

### API Endpoint

By default, the React app looks for the backend at:
`http://localhost:8000/api`
_(You can modify this live in the Dashboard's **Settings Config** tab)._

### Request Format

When a tab requests data (e.g., the Historical Search tab or Sector Flow), it sends a JSON payload to `/api/query`:

```json
{
  "database": "_tech_conn",
  "query": "SELECT date, close, volume, delivery_qty, delivery_pct FROM raw_ohlcv LIMIT 100",
  "args": {}
}
```

_Valid `database` targets match your sidecar names:_ `_tech_conn`, `_inst_conn`, `_meta_conn`, `_gov_conn`.

## Required Folder Structure & Database Location

If you save this UI project in your `Myra` home folder as `myra_web` (or any other name), your directory structure should look like this so the backend can automatically find the databases:

```text
Myra/
├── myra_app/
│   └── db/
│       ├── myra_technical.db    <-- Real DB
│       ├── myra_metadata.db
│       └── ...
├── myra_core/
└── myra_web/                    <-- This React Project
    ├── src/
    ├── package.json
    └── myra_fastapi_server.py   <-- Run this API bridge from here
```

By keeping `myra_web` side-by-side with `myra_app`, the Python FastAPI server (`myra_fastapi_server.py`) will resolve the database path using `../myra_app/db/`.

## Required Database Schema Constraints

To support the newly integrated features (Historical Search, Sector Flow, Ghost Simulator, Institutional DOM), your Python SQLite databases MUST map to the following schemas.

### 1. `_tech_conn` (`myra_technical.db`) -> `technical_data` table

The UI strictly expects these columns for historical charts and volume profile (DOM) generation:

- `symbol` (TEXT)
- `date` (TEXT YYYY-MM-DD)
- `open`, `high`, `low`, `close` (REAL)
- `volume` (INTEGER)
- **`delivery`** or **`delivery_qty`** (INTEGER)
- **`delivery_pct`** (REAL)

### 2. `_meta_conn` -> `fundamentals` table

The Value Ranker and Sector Flow Heatmap expect:

- `ticker` (TEXT)
- `sector` (TEXT) - _Used for Grouping Sector Flow_
- `current_price` (REAL)
- `graham_value` (REAL)
- `margin_of_safety` (REAL)

## Handling Connection Failures (Demo Fallback)

If the React interface receives a `500` or `Failed to fetch` error from FastAPI, the `Librarian` class automatically toggles `demoMode = true`.
When this happens, the individual components (e.g., `HistoricalSearch.tsx` or `InstDOM.tsx`) intercept the failure and trigger functions like `generateMockData()`. These functions exist solely to keep the UI from crashing and represent realistic mathematical mockups of your database.

To restore true data:

1. Ensure your FastAPI server is running: `uvicorn myra_fastapi_server:app --reload --port 8000`
2. Check the FastApi terminal logs for SQL execution errors.
3. Refresh the React UI.
