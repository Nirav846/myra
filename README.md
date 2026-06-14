# MYRA — Personal NSE Stock Screening & Analysis Platform

[![Python 3.12](https://img.shields.io/badge/python-3.12-blue.svg)](https://python.org)
[![React 19](https://img.shields.io/badge/react-19-61dafb.svg)](https://react.dev)
[![SQLite](https://img.shields.io/badge/sqlite-wal%20mode-003b57.svg)](https://sqlite.org)
[![Tests](https://img.shields.io/badge/tests-61%20passing-brightgreen.svg)](tests/)
[![CI](https://github.com/Nirav846/myra/actions/workflows/ci.yml/badge.svg)](https://github.com/Nirav846/myra/actions)

MYRA is a comprehensive stock screening and analysis platform for the National Stock Exchange (NSE) of India. It combines daily automated data ingestion, Smart Money Concepts (SMC) enrichment, institutional tracking, a suite of quantitative scanners, ML-based breakout prediction, and an interactive React frontend — all running locally with SQLite.

---

## Architecture

```
NSE Market Archives (bhavcopy CSVs)
         │
         ▼
mass_backfill / daily_ingestor ───────────────────────────────────┐
         │                                                       │
         ▼                                                       ▼
myra_technical.db (OHLCV + delivery)     Morningstar API / yfinance
         │                                       │
         ▼                                       ▼
feature_enrichment.py (SMC: FVG,          fundamental_sync.py
  swing levels, liquidity distance,       (PE, ROE, market_cap, ...)
  trend alignment, delivery MA)                 │
         │                                       ▼
         ▼                               myra_valuation.db
myra_technical.db (enriched)                   │
         │                                       │
         └───────────────┬───────────────────────┘
                         │
                         ▼
                FastAPI Server (:8000)
                         │
              ┌──────────┼──────────┐
              ▼          ▼          ▼
         /api/health  /api/scan   /api/ml/*
         /api/pipeline  ...       /api/finstack/*
              │
              ▼
         React Frontend (:3000)
         (7 scanner views, AdvancedChart,
          MissionControl dashboard, Leaderboard...)
```

## Features

- **Daily automated ingestion** — EOD bhavcopy + delivery data from NSE Market Archives; auto-detects missing days and backfills.
- **Gap-driven backfill** — `mass_backfill.py` detects date gaps and fills historical OHLCV + delivery for all symbols.
- **SMC Enrichment** — Polars-based batch enrichment computes Fair Value Gaps (FVG), swing high/low levels, liquidity distance, trend alignment (50/200 SMA), and delivery MA. Full 2.2M-row backfill completed in ~57 min.
- **Fundamentals sync** — Morningstar bulk API + yfinance fallback for PE, ROE, profit margins, market cap, dividend yield, enterprise value and 30+ other metrics. Promoter/free-float columns protected from overwrite.
- **7 stock scanners** — Quantitative strategies run against enriched data; each with configurable parameters. See [Scanners table](#scanners).
- **ML breakout prediction** — XGBoost models trained on 14 features predict forward returns. Separate Launchpad model identifies breakout candidates.
- **Institutional tracking** — Insider trades, large deals, bulk deals, block deals, FII/DII daily flows via NSE-MCP.
- **Data health dashboard** — Real-time `/api/data-health` endpoint reporting OHLCV freshness, enrichment completeness, fundamentals coverage, database status, and pipeline task timestamps.
- **Persistent task tracker** — SQLite-backed task registry logs every pipeline run with status, duration, and error context.
- **Automated daily backups** — All 8 databases backed up daily with rotation; WAL checkpoints before copy.
- **Background orchestration** — Daemon threads for DB Doctor audit, stale-database catch-up, and scheduled pipeline tasks.
- **Interactive frontend** — React 19 + TypeScript with Plotly charts, sector flow analysis, leaderboards, and preset scanner controls.
- **61-test suite + CI** — pytest suite covers core scoring functions; GitHub Actions runs on every push/PR.

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.12, FastAPI, Uvicorn |
| Data Processing | Polars (batch enrichment), Pandas (scanner logic), NumPy |
| Database | SQLite 8-database sidecar architecture (WAL mode) |
| ML / AI | XGBoost, scikit-learn, TensorFlow (CNN forecast, AEON) |
| Frontend | React 19, TypeScript, Vite, Tailwind CSS 4 |
| Charts | Plotly.js, Recharts |
| State | Zustand |
| Data APIs | Morningstar (fundamentals), yfinance (fallback), NSE-MCP (institutional), screener.in (live snapshots) |
| Infra | Docker, GitHub Actions (CI) |

## Quick Start

### Prerequisites

- Python 3.12
- Node.js 20+
- Git
- 8 GB RAM recommended

### Setup

```bash
# 1. Clone the repository
git clone https://github.com/Nirav846/myra.git
cd myra

# 2. Set up Python virtual environment
python -m venv venv
venv\Scripts\activate   # Windows
source venv/bin/activate  # Linux / macOS

# 3. Install Python dependencies
pip install -r requirements.txt

# 4. Install frontend dependencies
cd myra_web
npm install
cd ..

# 5. Configure environment
copy .env.dev .env   # Windows; or: cp .env.dev .env
# Edit .env to set MORNINGSTAR_TOKEN if you have one (optional)

# 6. Start the backend (FastAPI on port 8000)
python run_fastapi.py

# 7. In a second terminal, start the frontend (Vite on port 3000)
cd myra_web
npm run dev
```

Open **http://localhost:3000** in your browser.

> **Data note:** The repository includes a pre-populated database with 2.2M+ rows of historical OHLCV data. If you need to refresh from scratch, run `python myra_app/mass_backfill.py`.

## Project Structure

```
myra/
├── myra_app/                     # Backend application
│   ├── strategies/               # 57 scanner strategy files
│   │   ├── trigger_scanner.py
│   │   ├── float_exhaustion_scanner.py
│   │   ├── invisible_hand_scanner.py
│   │   ├── wyckoff_automaton.py
│   │   ├── liquidity_flip_detector.py
│   │   ├── operator_fingerprint_scanner.py
│   │   ├── seasonal_delivery_harvester.py
│   │   └── ... (additional strategies)
│   ├── db/                       # SQLite database files (8 sidecars)
│   ├── librarian*.py             # Core DB abstraction (schema, sync, intelligence, ingestor)
│   ├── feature_enrichment.py     # SMC enrichment pipeline (Polars)
│   ├── fundamental_sync.py       # Morningstar + yfinance sync
│   ├── mass_backfill.py          # Historical gap backfill
│   ├── daily_ingestor.py         # Daily bhavcopy ingestion
│   ├── background_orchestrator.py# Daemon thread management
│   ├── ml_trainer.py             # XGBoost model training
│   ├── task_tracker.py           # Persistent pipeline logging
│   └── schema_registry.py        # 30-table schema definitions
├── myra_web/                     # Frontend React application
│   ├── src/
│   │   ├── views/                # 15+ view components
│   │   ├── lib/                  # API client, utilities
│   │   └── components/           # Reusable UI components
│   └── package.json
├── myra_core/                    # Core data fetching utilities
├── tools/                        # Maintenance scripts
│   ├── enrich_history.py         # Batch enrichment backfill
│   ├── db_doctor.py              # Schema/data quality audit
│   └── ...
├── tests/                        # 61-test pytest suite
├── docs/                         # Documentation
│   └── screenshots/              # Screenshot images (add your own)
├── models/                       # Trained ML models + scanner caches
├── data/                         # Market archives, NIFTY CSVs, OHLCV cache
├── config/                       # YAML/JSON configuration
├── .github/workflows/ci.yml      # GitHub Actions CI pipeline
├── run_fastapi.py                # FastAPI launcher
├── docker-compose.yml            # Docker orchestration
└── requirements.txt              # Python dependencies
```

## Scanners

| Scanner | Endpoint | Description | Default Parameters |
|---------|----------|-------------|-------------------|
| **Trigger** | `/api/trigger/scan` | Stocks approaching a breakout: float-utilization pinch, volume contraction, tight price range, smart-float ratio | min_mcap=200, max_mcap=50000, min_float_util=8%, vol_pinch=0.75 |
| **Float Exhaustion** | `/api/float-exhaustion/scan` | Volume has exhausted available free float, indicating potential price dislocation | min_mcap=200, max_mcap=50000, window=20d, min_float_util=10% |
| **Invisible Hand** | `/api/invisible-hand/scan` | Institutional accumulation via volume-delivery divergence, FVG proximity, trend alignment | min_mcap=200, max_mcap=50000, window=20d, hist_window=60d, min_ih_score=35 |
| **Wyckoff Automaton** | `/api/wyckoff/scan` | Automated Wyckoff method: accumulation (SC, AR, ST, Spring) and distribution phases | min_mcap=200, max_mcap=50000, lookback=90d |
| **Liquidity Flip** | `/api/liquidity-flip/scan` | Transition from low-liquidity to high-liquidity regime signalling institutional entry | min_mcap=200, max_mcap=50000, lookback=95d |
| **Operator Fingerprint** | `/api/operator-fingerprint/scan` | Smart-money fingerprint patterns in price-volume action over 45-day window | min_mcap=200, max_mcap=50000, lookback=45d |
| **Seasonal Delivery** | `/api/seasonal-delivery/scan` | Historically high delivery volumes in a specific month using multi-year seasonal patterns | min_mcap=200, max_mcap=50000, min_hist_del=40%, min_consistency=55%, min_years=2 |

All scanners are thread-safe, cache results to JSON, and support configurable market-cap ranges.

## Portfolio Tracker

The CLI portfolio tracker (`tools/portfolio.py`) manages your personal NSE holdings with auto-refreshed pricing, risk analytics, and scanner overlap — all from MYRA's own databases without external API calls.

### Quick Start

```bash
# 1. One-time import from your broker's XLSX
python tools/portfolio.py import path/to/export.xlsx

# 2. View your portfolio
python tools/portfolio.py view

# 3. Next day: prices auto-refresh during daily ingest
python tools/portfolio.py view
```

### Key Commands

| Command | Description |
|---------|-------------|
| `import <xlsx>` | One-time import from broker XLSX file |
| `view` | Portfolio with P&L, delivery%, SMA/52w position, fundamentals, alerts |
| `view --compact` | Minimal 4-column view |
| `view --detailed` | Full 9-column deep dive with all enrichment |
| `view --live` | Live yfinance prices (15-min delayed) |
| `refresh` | Force-refresh all cached data from MYRA databases |
| `performance` | Per-stock breakdown + sector allocation pie chart |
| `scanner` | Cross-reference holdings with all 7 MYRA scanners |
| `alerts` | Delivery anomaly alerts per holding |
| `risk` | Concentration, drawdown, volatility, diversification score |
| `snapshot` | Save daily NAV snapshot |
| `history` | NAV history over time |
| `add <symbol> <qty> <avg_cost>` | Add a new position |
| `sell <symbol> <qty> <price>` | Reduce or close a position |
| `status` | Show data freshness and last refresh time |

### Data Security

All portfolio data lives in `myra_portfolio.db` (automatically gitignored). After the one-time broker XLSX import, prices come exclusively from MYRA's own bhavcopy database — no external API calls unless `--live` is used. Fundamentals are cached locally from `myra_valuation.db`. Smart caching means the first `view` of the day takes ~3s (one database trip per symbol); subsequent views are instant.

## API Overview

### Health & Monitoring

| Method | Route | Description |
|--------|-------|-------------|
| GET | `/api/health` | Database connectivity + fundamentals coverage |
| GET | `/api/data-health` | Pipeline metrics (latest date, enrichment %, backup age, scanner cache counts) |
| GET | `/api/db-size` | Technical database size (MB) |
| GET | `/api/system-info` | CPU / memory usage |
| GET | `/api/logs/recent` | Last 5 lines of pipeline log |

### Data Pipeline

| Method | Route | Description |
|--------|-------|-------------|
| GET | `/api/pipeline/status` | Per-task last_run / last_status / duration |
| POST | `/api/pipeline/run` | Start a pipeline task (`ingest`, `enrichment`, `fundamentals_sync`, `institutional_sync`, etc.) |
| GET | `/api/pipeline/schedule` | Current schedule configuration |
| POST | `/api/pipeline/toggle-schedule` | Enable/disable a scheduled task |

### ML

| Method | Route | Description |
|--------|-------|-------------|
| GET | `/api/ml/status` | XGBoost model existence check |
| POST | `/api/ml/train` | Train a new model |
| GET | `/api/ml/predict` | Today's predictions for all symbols |
| GET | `/api/ml/feature-importance` | Feature importance from latest model |
| GET/POST | `/api/ml/launchpad/*` | Launchpad breakout predictions (separate model) |

### Data

| Method | Route | Description |
|--------|-------|-------------|
| GET | `/api/fundamentals/live/{symbol}` | Fundamentals + live snapshot from screener.in |
| GET | `/api/search/symbols?q=` | Symbol search |
| GET | `/api/market-breadth` | Advances / declines for latest trading day |

### Example: `/api/data-health` Response

```json
{
  "latest_ohlcv_date": "2026-06-12",
  "days_behind": 0,
  "enrichment_complete_pct": 99.4,
  "fundamentals_symbols": 2309,
  "nifty_benchmark_date": "2026-06-12",
  "last_backup_date": "2026-06-12",
  "scanner_cache_counts": {
    "wyckoff": 527,
    "invisible_hand": 263,
    "trigger": 39,
    "float_exhaustion": 623,
    "liquidity_flip": 2,
    "operator_fingerprint": 241,
    "seasonal_delivery": 0
  }
}
```

## Databases

All 8 SQLite databases reside in `myra_app/db/` and are referenced exclusively via `LibrarianCore.DB_MAP` — never hardcoded.

| Database | File | Purpose |
|----------|------|---------|
| Technical | `myra_technical.db` | OHLCV, delivery, enrichment (~2.2M rows) |
| Valuation | `myra_valuation.db` | Fundamentals (54 columns across 3,000+ symbols) |
| Institutional | `myra_institutional.db` | Insider trades, large/bulk/block deals, FII/DII flows |
| Meta | `myra_metadata.db` | Symbols master, index constituents, ETF blocklist |
| Governance | `myra_governance.db` | Compliance, SAT disclosures, pledge history |
| Scoring | `myra_scoring.db` | Pre-materialized scores (IAS, fundamental grades) |
| Calendar | `myra_calendar.db` | Market trading calendar |
| Network Cache | `myra_cache_network.db` | HTTP response cache |

## Testing & CI

```bash
# Run the full test suite
pytest tests/ -v

# Expected output: 61 passed

# Verify Python syntax (enforced for PRs)
python -c "import ast; ast.parse(open('myra_app/strategies/trigger_scanner.py').read()); print('OK')"
```

The CI pipeline (`.github/workflows/ci.yml`) runs on every push/PR to `main`:
- Ubuntu latest
- Python 3.12
- `pip install -r requirements.txt`
- `pytest tests/ -v` (61 tests)

## Screenshots

> To add screenshots, capture the following views and save them to `docs/screenshots/`:

| Screenshot | What to capture | File |
|-----------|----------------|------|
| **Dashboard** | The main MissionControl view with HealthStatusBar at top, market breadth, and scanner result cards | `docs/screenshots/dashboard.png` |
| **Scanner Results** | A scanner results page — e.g., Trigger Scanner showing 39 candidate cards with scores | `docs/screenshots/scanner-results.png` |
| **Data Health** | The raw response from `GET /api/data-health` displayed in the browser or a tool like curl | `docs/screenshots/data-health.png` |
| **Advanced Chart** | An interactive Plotly chart showing OHLCV with an indicator overlay (e.g., SMA, FVG) | `docs/screenshots/advanced-chart.png` |

To capture:
1. Start the backend (`python run_fastapi.py`) and frontend (`cd myra_web && npm run dev`)
2. Open http://localhost:3000
3. Use your system screenshot tool (Win+Shift+S on Windows, Cmd+Shift+4 on macOS)
4. Save images to `docs/screenshots/`
5. Update the image paths above if needed

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for detailed guidelines.

Key rules:
- No `os.getcwd()` — use `constants.py` path resolution
- No hardcoded DB filenames — use `LibrarianCore.DB_MAP["key"]`
- No `df.append()` in loops — use list + `pd.concat()`
- No DB calls inside per-symbol loops — batch after all fetches
- Always verify syntax: `python -c "import ast; ast.parse(open('your_file.py').read()); print('OK')"`
- Run `npx tsc --noEmit` before submitting frontend changes

## License

MIT License
