# MYRA User Guide

MYRA is a local-first stock screening platform for the NSE. This guide walks you through setup, running the pipeline, and using the scanners and analysis views.

---

## Getting Started

### Prerequisites

- **Python 3.12**
- **Node.js 20+** (Node 22 recommended — CI uses it)
- **Git**
- **8 GB RAM** recommended
- **EOD2/BhavDesk data** — a folder of daily OHLCV CSVs (the source of your market data)

### 1. Clone & install

```bash
git clone https://github.com/Nirav846/myra.git
cd myra

python -m venv venv
venv\Scripts\activate            # Windows
source venv/bin/activate        # Linux / macOS

pip install -r requirements.txt

cd myra_web
npm install
cd ..
```

### 2. Set up EOD2 / BhavDesk data

MYRA reads market data from **EOD2/BhavDesk** CSVs (not the NSE website directly). Place the daily CSVs in:

```
eod2/src/eod2_data/daily/
```

Each file is named after a symbol (e.g. `20microns.csv`) with this header:

```
Date,Open,High,Low,Close,Volume,Series,TOTAL_TRADES,QTY_PER_TRADE,DLV_QTY
```

A `meta.json` in `eod2/src/eod2_data/` records data freshness (`lastUpdate`). The `USE_EOD2_DATA=True` flag in `myra_app/constants.py` routes ingestion to `eod2_sync.py`.

### 3. Configure environment

```bash
copy .env.dev .env      # Windows
cp .env.dev .env        # Linux / macOS
```

Set `MORNINGSTAR_TOKEN` if you have one (optional; used for fundamentals).

### 4. Run the backend

```bash
python run_fastapi.py
```

This launches the FastAPI bridge on **http://localhost:8000** (interactive API docs at `/docs`). `run_fastapi.py` auto-relaunches and uses your active venv.

### 5. Run the frontend

In a second terminal:

```bash
cd myra_web
npm run dev
```

Open **http://localhost:3000**.

> **First-run note:** The repository ships with a pre-populated ~2.2M-row database. To rebuild from scratch, run `python myra_app/mass_backfill.py`.

---

## Running the Data Pipeline

The pipeline runs in the background (declarative task registry): daily ingest (EOD2 sync), enrichment, fundamentals sync, fund-traction sync, cross-buy sync, institutional sync, and backups. Watch status at:

```
GET http://localhost:8000/api/pipeline/status
```

You can trigger tasks manually:

```bash
POST /api/pipeline/run   # body: {"task": "ingest"}
```

Health and freshness live at:

```
GET http://localhost:8000/api/data-health
```

Key fields: `latest_ohlcv_date`, `days_behind`, `enrichment_complete_pct`, `scanner_cache_counts`.

> **Data freshness:** If scans feel stale or `days_behind > 0`, run the daily ingest. Scans always run against the latest available EOD2 data.

---

## Using the Scanners

Myra ships **15 scanners** across price action, institutional/flow, ML/momentum, and delivery/volume categories. From the frontend, each lives under its own route; from the API each exposes `GET /{name}/status` and `POST /{name}/scan`.

| Scanner | Category | What it finds | Typical defaults |
|---------|----------|---------------|------------------|
| The Trigger | Price Action | Breakout setups: float pinch, volume contraction, tight range | min_mcap=200, min_float_util=8% |
| Bottom Hunter | Price Action | Abrupt delivery spikes near price lows | spike threshold |
| Climax Accumulation | Price Action | Climax-style accumulation candles | window |
| Wyckoff Automaton | Price Action | Wyckoff accumulation/distribution phases (SC, AR, ST, Spring) | lookback=90d |
| Liquidity Flip | Price Action | Low→high liquidity regime flip | lookback=95d |
| Darvas Box Pro | Price Action | Darvas box new-high breakouts | box_window=20d |
| FVG Scanner | Price Action | Fair-value-gap based setups | — |
| Invisible Hand | Institutional | Institutional accumulation: volume-delivery divergence + FVG + trend | min_ih_score=35 |
| DCB Bargain | Institutional | Below institutional Delivery Cost Basis with delivery absorption | lookback=120d |
| Smart Money Bargain | Institutional | DCB + smart-money delivery clues | lookback=120d |
| Float Exhaustion | Institutional | Free float exhausted by volume | window=20d |
| Operator Fingerprint | Institutional | Smart-money price-volume fingerprints | lookback=45d |
| Fund Traction | Institutional/Flow | MF holder-count momentum | — |
| Cross-Buy | Institutional/Flow | MF cross-buy ratios and signals | — |
| Launchpad | ML/Momentum | XGBoost breakout candidates | — |
| Multibagger Pro | ML/Momentum | Early multibagger detection | — |
| Price-Delivery Divergence | Delivery/Volume | Price vs delivery pressure divergence | window=20d |
| Delivery Anomaly | Delivery/Volume | Unusual delivery events | — |
| Seasonal Delivery | Delivery/Volume | High delivery in a specific month (seasonal) | min_consistency=55% |

> The exact scanner set evolves; the API `/api/{name}/status` endpoints always reflect what is currently registered.

### Scanner workflow

1. Open a scanner view (e.g. `/trigger`).
2. Tune parameters with the **PresetChip** controls.
3. Optionally enable the **holding-universe filter** to scan only your portfolio holdings (much faster).
4. Click **Scan**. Results stream progress (10–92%+); each scanner caches JSON so repeat calls are fast.
5. Sort columns or **export to CSV**.

### Holding-universe filter

Restricts a scan to your `myra_portfolio.db` holdings instead of the full 3,000+ symbol universe. Drastically reduces scan time (e.g. DCB Bargain ~58s full → ~8s with the filter). See [PERFORMANCE.md](PERFORMANCE.md).

---

## Deep Fundamentals

Route **`/deep-fundamentals`** gives a per-symbol deep dive via `GET /api/full-fundamentals/{symbol}`. It combines Screener.in, Screener.in's chart API, and yfinance to produce:

- **Graham Number** + defensive criteria pass/fail
- **Piotroski F-Score** (simplified 6-criterion) with per-criteria breakdown
- **Two-stage DCF intrinsic value** + margin of safety
- 12+ insight categories (PBV, P/E, ROCE, market-cap-to-sales, ROE, analyst ratings, D/E, growth, beta, sector)

Results are cached in `myra_valuation.db` (`full_fundamental_cache`); pass `?refresh=true` to force a refresh.

---

## Fund Traction & Cross-Buy

### Fund Traction (`/fund-traction`)

Shows month-over-month mutual-fund **holder-count traction** for each symbol (`fund_traction` table). Sync from GitHub Pages:

```bash
POST /api/pipeline/run   # {"task": "fund-traction-sync"}
```

Available months: `GET /api/fund-traction/months`. Scanner view: `/fund-traction` (data from `/api/fund-traction/scanner`).

### Cross-Buy (`/cross-buy`)

Shows **cross-buy ratios** across fund categories (`fund_cross_buy` table), computed from local RupeeVest holdings CSVs. Signal tags:

| Tag | Criteria |
|-----|----------|
| `STRONG_CROSS_BUY` | total_funds ≥ 5 and ratio ≥ 0.7 |
| `CROSS_BUY` | ratio ≥ 0.5 |
| `MIXED` | ratio ≥ 0.25 |
| `STYLE_CONCENTRATED` | otherwise |

Trigger via `{"task": "cross-buy-sync"}`. Scanner view: `/cross-buy` (from `/api/cross-buy/scanner`).

---

## RRG Dashboard

Route **`/rrg`** displays the Relative Rotation Graph for NSE indices/sectors. It classifies each instrument into a quadrant:

- **Leading** — strong relative strength and momentum
- **Weakening** — strong momentum fading
- **Lagging** — weak relative strength and momentum
- **Improving** — weak momentum building

Controls: benchmark (default `nifty 50`), timeframe (`weekly`/`daily`), and trailing period (2–26 weeks, default 8). Backend: `GET /api/rrg/` and `GET /api/rrg/indices`.

---

## Filters, Watchlist & Portfolio

- **Holding-universe filter** — restrict any scanner to your portfolio holdings.
- **Portfolio** (`/portfolio`) — web dashboard; the full CLI portfolio tracker lives in `tools/portfolio.py` (view, import, risk, alerts, snapshot, scanner overlap). See `README.md` for CLI commands.

---

## Troubleshooting

### Scans return stale / empty results

- Check `GET /api/pipeline/status` for the daily `ingest` task. If it's behind, run `POST /api/pipeline/run {"task":"ingest"}`.
- Verify EOD2 data freshness: the `lastUpdate` in `eod2/src/eod2_data/meta.json` should be recent.
- A scanner returning 0 candidates may just mean no symbols met the current threshold on the latest data — check `GET /api/{name}/status`.

### Scans are slow

- Enable the **holding-universe filter** (see [PERFORMANCE.md](PERFORMANCE.md)).
- The bulk loader already replaces per-symbol DB fetches — large universes (full 3,000+ symbols) are inherently slower; expect ~1 min for the heaviest (Invisible Hand full scan).

### Clear a stale scanner cache

```http
DELETE /api/cache/{scanner_name}
```

e.g. `DELETE /api/cache/invisible-hand`. This deletes the JSON result cache so the next scan recomputes.

### Frontend can't reach the backend

- Confirm the backend is on port **8000** and the frontend on **3000**.
- CORS only allows `http://localhost:3000` and `http://localhost:5173`.

### `days_behind > 0` in `/api/data-health`

- The latest EOD2/BhavDesk CSV may be missing. Re-run ingest after the market day's data lands.

### Bear-market flag / market regime

- Market regime is derived from PCR snapshots in `myra_options.db` (`get_market_mood()`), falling back to VIX. If PCR data is missing, ingest `options` data via the pipeline.

---

## Further Reading

- [ARCHITECTURE.md](ARCHITECTURE.md) — system design reference
- [PERFORMANCE.md](PERFORMANCE.md) — scan benchmarks and optimisation tips
- [README.md](../README.md) — project overview, API reference, CLI portfolio tracker
