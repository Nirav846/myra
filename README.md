# MYRA – Personal NSE Stock Screening & Analysis Platform

MYRA is a comprehensive stock screening and analysis platform for the National Stock Exchange (NSE) of India, combining institutional data tracking, SMC (Smart Money Concepts) enrichment, and interactive visualization tools.

[![Contributors Welcome](https://img.shields.io/badge/contributions-welcome-brightgreen.svg)](CONTRIBUTING.md)

Already cloned MYRA and made improvements? [Open a pull request](CONTRIBUTING.md) and we'll merge it.

## Key Features

- **Daily bhavcopy ingestion** – Automated EOD data ingestion from NSE Market Archives
- **Fundamentals sync** – Morningstar API integration for PE, ROE, margins, and valuation metrics
- **SMC enrichment** – Fair Value Gaps (FVG), swing levels, liquidity distance, trend alignment
- **Institutional data** – Insider trades, large deals, bulk deals, block deals, FII/DII flows
- **Interactive charts** – Plotly-powered visualization with custom indicator registry
- **Scanner suite** – 15+ pre-built scanners (FVG, Ghost, InstDOM, Price-Delivery Divergence, Value Ranker)
- **CI/CD** – GitHub Actions with lint, type-check, and security scanning

## Tech Stack

- **Backend:** Python 3.12, FastAPI, SQLite (WAL mode), Polars, Pandas, yfinance
- **Frontend:** React + Vite, TypeScript, Plotly, Zustand
- **Data Processing:** Vectorized operations, multiprocessing worker pool
- **Database:** SQLite sidecars (technical, valuation, institutional, meta)

## Prerequisites

- Python 3.12
- Node.js 20
- Git
- 8 GB RAM recommended

## Quick Start

1. **Clone the repository:**
   ```bash
   git clone https://github.com/Nirav846/myra.git
   cd myra
   ```

2. **Set up Python virtual environment:**
   ```bash
   python -m venv venv
   venv\Scripts\activate  # On Windows
   ```

3. **Install Python dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

4. **Install frontend dependencies:**
   ```bash
   cd myra_web
   npm install
   cd ..
   ```

5. **Prepare data:**
   - Ensure `data/Market_Archives/` contains bhavcopy CSV files, or
   - Run `python myra_app/mass_backfill.py` to backfill historical data

6. **Start the web application:**
   ```bash
   start_myra_web.bat  # On Windows (recommended)
   # Or manually:
   # Terminal 1: python run_fastapi.py   (backend on port 8000)
   # Terminal 2: cd myra_web && npm run dev  (frontend on port 3000)
   ```

7. **Optional – FinStack MCP:**
   - Install the `finstack` package to enable the Morning Brief feature
   - Without it, `/api/finstack/morning-brief` returns a graceful "unavailable" status

## 🤝 Contributors Welcome

MYRA is open‑source and actively maintained.  
We welcome contributions of all kinds—scanners, indicators, documentation, tests, and bug fixes.

### How to contribute

1. If you've already cloned MYRA and made improvements, fork the repo, push your branch, and open a PR – we'd love to merge it.
2. **Fork** the repo and clone it locally.
3. Pick an issue from the [Issues tab](https://github.com/Nirav846/myra/issues) or open a new one.
4. Create a feature branch (`feature/my‑new‑scanner`) and make your changes.
5. Run `npx tsc --noEmit` and `flake8 myra_app` to verify your code.
6. Submit a **Pull Request** against `main`.

Please read [CONTRIBUTING.md](CONTRIBUTING.md) for code style, branch naming, and detailed instructions on adding scanners or indicators.

### Good first issues

Look for issues labeled **`good first issue`**—they're small, self‑contained tasks ideal for new contributors.

## Usage

1. Open http://localhost:3000 in your browser
2. Navigate the dashboard to view market breadth, sector flow, and scanner results
3. Run scanners from the sidebar (FVG Scanner, Ghost Simulator, InstDOM, etc.)
4. Configure scanner parameters using PresetChip controls
5. Export results to CSV or view interactive charts

## Folder Structure

- `myra_app/` – Backend application (engine, ingestors, managers, strategies)
- `myra_web/` – Frontend React application (FastAPI server, views, chart engine)
- `myra_core/` – Core utilities and NSE data fetching
- `tools/` – Maintenance scripts (db_doctor, rebuild_technical_index, performance_guard)
- `data/` – SQLite databases (technical.db, valuation.db, institutional.db, meta.db)
- `conductor/` – Workflow orchestration and background tasks
- `config/` – Configuration files
- `logs/` – Application logs

## Documentation

- [ARCHITECTURE.md](ARCHITECTURE.md) – Data flow, database schema, and design decisions
- [CONTRIBUTING.md](CONTRIBUTING.md) – Development setup, code style, and contribution guidelines

## License

MIT License
