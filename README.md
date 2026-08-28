# MYRA — Personal NSE Stock Screening & Analysis Platform

[![Python 3.12](https://img.shields.io/badge/python-3.12-blue.svg)](https://python.org)
[![Node.js 22](https://img.shields.io/badge/node-22.x-blue.svg)](https://nodejs.org)
[![React 19](https://img.shields.io/badge/react-19-61dafb.svg)](https://react.dev)
[![SQLite](https://img.shields.io/badge/sqlite-wal%20mode-003b57.svg)](https://sqlite.org)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-367%20passing-brightgreen.svg)](tests/)
[![Tests Status](https://github.com/Nirav846/myra/actions/workflows/ci.yml/badge.svg)](https://github.com/Nirav846/myra/actions/workflows/ci.yml)
[![Build Status](https://github.com/Nirav846/myra/actions/workflows/ci.yml/badge.svg)](https://github.com/Nirav846/myra/actions)

MYRA is a comprehensive stock screening and analysis platform for the National Stock Exchange (NSE) of India. It combines daily automated data ingestion (via EOD2/BhavDesk CSVs), Smart Money Concepts (SMC) enrichment, institutional & mutual-fund tracking, a suite of 15 quantitative scanners, ML-based breakout prediction, and an interactive React frontend — all running locally with SQLite.

---

## Architecture

```
EOD2 / BhavDesk CSVs (daily OHLCV + delivery)      Fund Traction (GitHub Pages)      RupeeVest MF holdings CSVs
          │                                                     │                              │
          ▼                                                     ▼                              ▼
  eod2_sync.py (USE_EOD2_DATA=True)              fund_traction_sync.py             cross_buy_processor.py
  incremental per-symbol                          (fund_traction table)             (fund_cross_buy table)
          │                                                     │                              │
          ▼                                                     └──────────────┬───────────────┘
  myra_technical.db (OHLCV + delivery)                                        ▼
          │                                                     myra_valuation.db (fundamentals + traction + cross-buy)
          ▼
  feature_enrichment.py (SMC: FVG, swing levels,
    liquidity distance, trend alignment, delivery MA)
          │
          ▼
  myra_technical.db (enriched columns)
          │
          └────────────────────┬───────────────────────┐
                              │                       │
                              ▼                       ▼
                    FastAPI Server (:8000)      myra_analysis (RRG engine, reads EOD2)
                    19 routers / 33 scanner            │
                    endpoints                          ▼
                              │                 /api/rrg/*
                              ▼
                     React Frontend (:3000)
                     (42 routes: MissionControl, scanners,
                     RRG, Deep Fundamentals, Fund Traction,
                     Cross-Buy, FinStack reports...)
```

## Features

- **EOD2 / BhavDesk ingestion** — Daily OHLCV + delivery synchronised from `eod2/src/eod2_data/daily/` CSVs (`USE_EOD2_DATA=True`), with incremental inserts and automatic enrichment on new dates.
- **SMC Enrichment** — Polars-based batch enrichment computes Fair Value Gaps (FVG), swing high/low levels, liquidity distance, trend alignment (50/200 SMA), and delivery MA. Full 2.2M-row backfill completes in ~57 min.
- **Fundamentals sync** — Morningstar bulk API + yfinance fallback for PE, ROE, profit margins, market cap, dividend yield, enterprise value and 30+ other metrics. Promoter/free-float columns protected from overwrite.
- **Deep Fundamentals** — `GET /api/full-fundamentals/{symbol}` combines Screener.in, its chart API, and yfinance to compute Graham Number + defensive criteria, a simplified Piotroski F-Score, two-stage DCF intrinsic value, and 12+ insight categories.
- **Fund Traction** — `fund_traction` table tracks month-over-month mutual-fund holder counts for every symbol, synced from public GitHub Pages data (`/api/fund-traction/*`).
- **Cross-Buy** — `fund_cross_buy` table computes cross-buy ratios across fund categories from local RupeeVest holdings CSVs, with signal tags (`STRONG_CROSS_BUY`, `CROSS_BUY`, `MIXED`), exposed via `/api/cross-buy/*`.
- **RRG Dashboard** — Relative Rotation Graph engine in `myra_app/analysis/rrg.py` reads EOD2 data directly and classifies indices into Leading / Weakening / Lagging / Improving quadrants (`/api/rrg/*`), with weekly daily resampling and z-score normalisation.
- **15 stock scanners** — Quantitative strategies run against enriched data, each with configurable parameters and JSON cache. See [Scanners table](#scanners).
- **ML breakout prediction** — XGBoost models trained on technical + fundamental features predict forward returns; a separate Launchpad model identifies breakout candidates.
- **Institutional tracking** — Insider trades, large deals, bulk deals, block deals, FII/DII daily flows.
- **FinStack reports** — `GET /api/finstack/*` endpoints provide morning brief, NIFTY outlook, stock briefs, pledge alerts, and unusual-activity scans (surfaced in MissionControl widgets).
- **Data health dashboard** — `/api/data-health` reports OHLCV freshness, enrichment completeness, fundamentals coverage, database status, and pipeline task timestamps.
- **Persistent task tracker** — SQLite-backed `task_registry` + a declarative `TaskSpec` executor logs every pipeline run with status, duration, and error context.
- **Bulk loader** — `myra_app/db/bulk_loader.py` replaces per-symbol fetch loops with a single bulk SQL query per scan (applied to 13 scanners), cutting scan time substantially.
- **Automated daily backups** — All databases backed up daily with rotation; WAL checkpoints before copy.
- **Background orchestration** — Declarative task registry with self-loop, catch-up, stagger, and watchdog daemon threads.
- **Interactive frontend** — React 19 + TypeScript with Plotly charts, MissionControl dashboard, RRG charts, sector flow analysis, leaderboards, and preset scanner controls.
- **367-test suite + CI** — pytest suite covers core scoring functions and scanner logic; GitHub Actions runs pytest + frontend build on every push/PR.

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.12, FastAPI, Uvicorn |
| Data Processing | Polars (batch enrichment), Pandas (scanner logic), NumPy |
| Database | SQLite 9-database sidecar architecture (WAL mode) |
| ML / AI | XGBoost, scikit-learn |
| Frontend | React 19, TypeScript, Vite 6, Tailwind CSS 4 |
| Charts | Plotly.js, Recharts |
| State | Zustand |
| Data APIs | EOD2/BhavDesk (market data), Morningstar (fundamentals), yfinance (fallback), Screener.in (deep fundamentals), NSE-MCP / FinStack (institutional + reports) |
| Infra | Docker, GitHub Actions (CI) |

## Quick Start

### Prerequisites

- Python 3.12
- Node.js 20+ (CI uses Node 22)
- Git
- 8 GB RAM recommended
- **EOD2 / BhavDesk data** — the `eod2/` folder with daily CSVs and `meta.json`

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

# 6. Ensure EOD2 data is available
# Place the BhavDesk daily CSVs under eod2/src/eod2_data/daily/
# (20microns.csv format: Date,Open,High,Low,Close,Volume,Series,TOTAL_TRADES,QTY_PER_TRADE,DLV_QTY)

# 7. Start the backend (FastAPI on port 8000)
python run_fastapi.py   # auto-relaunches; use the active venv

# 8. In a second terminal, start the frontend (Vite on port 3000)
cd myra_web
npm run dev
```

Open **http://localhost:3000** in your browser. The backend is at **http://localhost:8000** (interactive docs at `/docs`).

> **Data note:** The repository includes a pre-populated database with 2.2M+ rows of historical OHLCV data. If you need to refresh from scratch, run `python myra_app/mass_backfill.py`.

## Project Structure

```
myra/
├── myra_app/                      # Backend application
│   ├── strategies/                # 63 scanner strategy files (incl. alpha/)
│   │   ├── trigger_scanner.py
│   │   ├── float_exhaustion_scanner.py
│   │   ├── invisible_hand_scanner.py
│   │   ├── wyckoff_automaton.py
│   │   ├── liquidity_flip_detector.py
│   │   ├── operator_fingerprint_scanner.py
│   │   ├── seasonal_delivery_harvester.py
│   │   ├── dcb_bargain.py
│   │   ├── smart_money_bargain.py
│   │   ├── darvas_box_scanner.py
│   │   ├── bottom_hunter.py
│   │   ├── climax_accumulation.py
│   │   ├── delivery_divergence_scanner.py
│   │   ├── multibagger_early_detection.py
│   │   └── ...
│   ├── tasks/                     # Declarative TaskSpec registry + executor
│   │   ├── registry.py            # TaskSpec dataclass + 12 task entries
│   │   └── executor.py            # Duration-logged task execution
│   ├── analysis/
│   │   └── rrg.py                 # RRG rotation engine
│   ├── fetchers/
│   │   └── full_fundamentals.py   # Screener.in + chart API + yfinance deep dive
│   ├── db/bulk_loader.py          # Single-query OHLCV loader for scanners
│   ├── eod2_sync.py               # EOD2/BhavDesk incremental sync
│   ├── fund_traction_sync.py      # MF holder traction sync
│   ├── cross_buy_processor.py     # MF cross-buy computation
│   ├── feature_enrichment.py      # SMC enrichment pipeline (Polars)
│   ├── fundamental_sync.py        # Morningstar + yfinance sync
│   ├── mass_backfill.py           # Historical gap backfill
│   ├── librarian*.py              # Core DB abstraction (schema, sync, intelligence)
│   └── schema_registry.py         # 32-table schema definitions
├── myra_web/                      # Frontend + API bridge
│   ├── myra_fastapi_server.py     # FastAPI app: 19 routers (API bridge)
│   ├── routes/                    # 19 router modules (scanners, rrg, fund-traction, ...)
│   └── src/
│       ├── views/                 # Scanner + analysis view components
│       └── App.tsx                # 42 frontend routes
├── tools/                         # Maintenance scripts (enrich_history, portfolio, db_doctor)
├── tests/                         # 367-test pytest suite
├── docs/                          # Documentation
│   ├── ARCHITECTURE.md
│   ├── USER_GUIDE.md
│   ├── PERFORMANCE.md
│   ├── DB_CONTEXT.md
│   ├── FRONTEND_DB_CONTRACT.md
│   └── screenshots/               # Screenshot images (add your own)
├── models/                        # Trained ML models + scanner caches
├── data/                          # Market archives, NIFTY CSVs, OHLCV cache
├── eod2/                          # EOD2/BhavDesk daily data source
├── config/                        # YAML/JSON configuration
├── .github/workflows/ci.yml       # GitHub Actions CI (pytest + frontend build)
├── run_fastapi.py                 # FastAPI launcher
└── requirements.txt               # Python dependencies
```

## Scanners

MYRA registers **15 scanners**. Thirteen are registered through the scanner factory in `myra_web/routes/scanners.py` (each exposes `GET /{name}/status` + `POST /{name}/scan`); Launchpad and Multibagger are custom, non-factory scanners. All are thread-safe, cache results to JSON, and support configurable market-cap ranges and a holding-universe filter.

| Scanner | Endpoint | Description | Default Parameters |
|---------|----------|-------------|-------------------|
| **The Trigger** | `/api/trigger/scan` | Stocks approaching a breakout: float-utilization pinch, volume contraction, tight price range, smart-float ratio | min_mcap=200, max_mcap=50000, min_float_util=8%, vol_pinch=0.75 |
| **Float Exhaustion** | `/api/float-exhaustion/scan` | Volume has exhausted available free float, indicating potential price dislocation | min_mcap=200, max_mcap=50000, window=20d, min_float_util=10% |
| **Invisible Hand** | `/api/invisible-hand/scan` | Institutional accumulation via volume-delivery divergence, FVG proximity, trend alignment. Supports historical `scan_date` time-travel | min_mcap=200, max_mcap=50000, window=20d, hist_window=60d, min_ih_score=35 |
| **Wyckoff Automaton** | `/api/wyckoff/scan` | Automated Wyckoff method: accumulation (SC, AR, ST, Spring) and distribution phases. All baselines rolling-to-signal-day (no look-ahead); confirmed Springs dated on the confirmation candle | min_mcap=510, max_mcap=530000, lookback=90d |
| **Liquidity Flip** | `/api/liquidity-flip/scan` | Transition from low-liquidity to high-liquidity regime signalling institutional entry | min_mcap=200, max_mcap=50000, lookback=95d |
| **Operator Fingerprint** | `/api/operator-fingerprint/scan` | Smart-money fingerprint patterns in price-volume action over a 45-day window | min_mcap=200, max_mcap=50000, lookback=45d |
| **Seasonal Delivery** | `/api/seasonal-delivery/scan` | Historically high delivery volumes in a specific month using multi-year seasonal patterns | min_mcap=200, max_mcap=50000, min_hist_del=40%, min_consistency=55%, min_years=2 |
| **DCB Bargain** | `/api/dcb-bargain/scan` | Stocks trading below institutional Delivery Cost Basis (delivery-weighted average price over ~6 months) with positive delivery absorption | min_mcap=200, max_mcap=50000, lookback=120d |
| **Smart Money Bargain** | `/api/smart-money-bargain/scan` | Extends DCB Bargain with smart-money delivery clues for discounted institutional accumulation | lookback=120d |
| **Darvas Box Pro** | `/api/darvas/scan` | Darvas box breakout: new-high boxes with volume confirmation | box_window=20d |
| **Bottom Hunter** | `/api/bottom-hunter/scan` | Flags abrupt delivery spikes near price lows (accumulation footprints) | spike threshold configurable |
| **Climax Accumulation** | `/api/climax-accumulation/scan` | Marks climax-style accumulation candles for reversals | window configurable |
| **Delivery Divergence** | `/api/delivery-divergence/scan` | Flags symbols where price direction diverges from delivery pressure | window=20d |
| **Launchpad** (custom) | `/api/launchpad/scan` | XGBoost model (`launchpad_xgb.joblib`) identifies breakout candidates in digestion phase | in-memory, no cache file |
| **Multibagger Pro** (custom) | `/api/multibagger/scan` | Early multibagger detection strategy (`multibagger_early_detection.py`) | global result, status endpoint |

**PCR Market Regime** — `get_market_mood()` reads Put-Call Ratio snapshots from `myra_options.db` as the primary market-regime signal (BULLISH→GREED, BEARISH→FEAR), falling back to VIX when unavailable. Current PCR snapshots are exposed via `GET /api/pcr/status`.

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
| `scanner` | Cross-reference holdings with all 15 MYRA scanners |
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

The backend (`myra_web/myra_fastapi_server.py`) wires **19 routers**. Key ones:

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
| GET | `/api/full-fundamentals/{symbol}` | Deep dive: Graham, Piotroski F-Score, DCF, + insights |
| GET | `/api/search/symbols?q=` | Symbol search |
| GET | `/api/market-breadth` | Advances / declines for latest trading day |
| GET | `/api/pcr/status` | PCR snapshots for all indices |
| GET | `/api/ai-opinion/{ticker}` | Gemini LLM second opinion — BUY/SELL/HOLD signal |

### Analysis & Institutional (new)

| Method | Route | Description |
|--------|-------|-------------|
| GET | `/api/rrg/` | Relative Rotation Graph (quadrants, benchmark, weekly/daily timeframe) |
| GET | `/api/rrg/indices` | Discovered indices for RRG |
| GET | `/api/fund-traction/batch` | Fund traction data batch |
| GET | `/api/fund-traction/months` | Available traction months |
| GET | `/api/fund-traction/scanner` | Fund traction scanner results |
| GET | `/api/cross-buy/months` | Available cross-buy months |
| GET | `/api/cross-buy/scanner` | Cross-buy scanner results |
| GET | `/api/finstack/morning-brief` | Morning market brief |
| GET | `/api/finstack/nifty-outlook` | NIFTY technical outlook |
| GET | `/api/finstack/stock-brief/{symbol}` | Per-stock fundamental brief |
| GET | `/api/finstack/pledge-alert/{symbol}` | Pledge risk alerts |
| GET | `/api/finstack/unusual-activity` | Unusual volume/price activity |

### Example: `/api/data-health` Response

```json
{
  "latest_ohlcv_date": "2026-08-26",
  "days_behind": 0,
  "enrichment_complete_pct": 99.4,
  "fundamentals_symbols": 2309,
  "nifty_benchmark_date": "2026-08-26",
  "last_backup_date": "2026-08-26",
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

All SQLite databases reside in `myra_app/db/` and are referenced exclusively via `LibrarianCore.DB_MAP` — never hardcoded. There are **9 configured databases** (WAL mode).

| Database | File | Purpose |
|----------|------|---------|
| Technical | `myra_technical.db` | OHLCV, delivery, enrichment (~2.2M rows) |
| Valuation | `myra_valuation.db` | Fundamentals + `fund_traction` + `fund_cross_buy` |
| Institutional | `myra_institutional.db` | Insider trades, large/bulk/block deals, FII/DII flows |
| Meta | `myra_metadata.db` | Symbols master, index constituents, ETF blocklist, `task_registry`, lineage tracking |
| Governance | `myra_governance.db` | Compliance, SAT disclosures, pledge history |
| Scoring | `myra_scoring.db` | Pre-materialized scores (IAS, fundamental grades) |
| Calendar | `myra_calendar.db` | Market trading calendar |
| Network Cache | `myra_cache_network.db` | HTTP response cache |
| Options | `myra_options.db` | Option-chain + PCR snapshots for market regime |

## Testing & CI

```bash
# Run the full test suite
pytest tests/ -v

# Expected output: 367 passed

# Verify Python syntax (enforced for PRs)
python -c "import ast; ast.parse(open('myra_app/strategies/trigger_scanner.py').read()); print('OK')"
```

The CI pipeline (`.github/workflows/ci.yml`) runs on every push/PR to `main`:

- **`test`** job — ubuntu-latest, Python 3.12, `pip install -r requirements.txt`, `pytest tests/ -v` (367 tests).
- **`frontend-build`** job — ubuntu-latest, Node 22, `npm ci`, `npm run build`, verifies `dist/`.

## Screenshots

> To add screenshots, capture the following views and save them to `docs/screenshots/`:

| Screenshot | What to capture | File |
|-----------|----------------|------|
| **Dashboard** | The main MissionControl view with HealthStatusBar at top, market breadth, and scanner result cards | `docs/screenshots/dashboard.png` |
| **RRG** | The Relative Rotation Graph view showing Leading / Weakening / Lagging / Improving quadrants | `docs/screenshots/rrg.png` |
| **Scanner Results** | A scanner results page — e.g., Trigger Scanner showing candidate cards with scores | `docs/screenshots/scanner-results.png` |
| **Data Health** | The raw response from `GET /api/data-health` displayed in the browser or a tool like curl | `docs/screenshots/data-health.png` |
| **Advanced Chart** | An interactive Plotly chart showing OHLCV with an indicator overlay (e.g., SMA, FVG) | `docs/screenshots/advanced-chart.png` |

To capture:
1. Start the backend (`python run_fastapi.py`) and frontend (`cd myra_web && npm run dev`)
2. Open http://localhost:3000
3. Use your system screenshot tool (Win+Shift+S on Windows, Cmd+Shift+4 on macOS)
4. Save images to `docs/screenshots/`
5. Update the image paths above if needed

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for detailed guidelines and [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the system reference.

Key rules:
- No `os.getcwd()` — use `constants.py` path resolution
- No hardcoded DB filenames — use `LibrarianCore.DB_MAP["key"]`
- No `df.append()` in loops — use list + `pd.concat()`
- No DB calls inside per-symbol loops — batch after all fetches (prefer `bulk_loader.load_ohlcv_for_universe`)
- Always verify syntax: `python -c "import ast; ast.parse(open('your_file.py').read()); print('OK')"`
- Run `npx tsc --noEmit` before submitting frontend changes
- Schema changes: only ADD COLUMNS via ALTER TABLE, never drop

## License

MIT License
