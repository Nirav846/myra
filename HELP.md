# MYRA Command Reference

## Pipeline & Data Ingestion

### Start the pipeline (headless, crash‑safe)
python run_pipeline.py

### Start the full stack (pipeline, FastAPI, Vite)
start_myra_web.bat

### Trigger daily bhavcopy ingest NOW
python -m uvicorn myra_web.myra_fastapi_server:app --host 0.0.0.0 --port 8000
curl -X POST http://localhost:8000/api/tools/ingest

### Full rebuild from local bhavcopy archive
python -m myra_app.mass_backfill --full

---

## Database Maintenance

### Run DB Doctor (health check + auto‑fix)
python -c "from tools.db_doctor import DbDoctor; d = DbDoctor(); d.run()"

### Rebuild technical_data with correct PRIMARY KEY
python tools/rebuild_technical_index.py

### Check latest trading date in the database
python -c "import sqlite3; conn=sqlite3.connect('myra_app/db/myra_technical.db'); print(conn.execute('SELECT MAX(date) FROM technical_data').fetchone()[0]); conn.close()"

### Verify database indexes
python -c "import sqlite3; conn=sqlite3.connect('myra_app/db/myra_technical.db'); print([i[0] for i in conn.execute('SELECT name FROM sqlite_master WHERE type=\"index\"').fetchall()]); conn.close()"

### Create missing composite index on (symbol, date)
python -c "import sqlite3; conn=sqlite3.connect('myra_app/db/myra_technical.db'); conn.execute('CREATE INDEX IF NOT EXISTS idx_tech_sym_date ON technical_data(symbol, date)'); conn.close(); print('Index created')"

### Create metadata indexes for faster frontend loads
python -c "import sqlite3, os; from myra_app.constants import DB_DIR; from myra_app.librarian_core import LibrarianCore; meta=os.path.join(DB_DIR, LibrarianCore.DB_MAP['meta']); conn=sqlite3.connect(meta); conn.execute('CREATE INDEX IF NOT EXISTS idx_symbols_sector ON symbols_master(sector)'); conn.execute('CREATE INDEX IF NOT EXISTS idx_constituents_symbol ON index_constituents(symbol)'); conn.commit(); conn.close(); print('Metadata indexes created')"

### Deduplicate technical_data (if the unique index was missing)
python -c "import sqlite3; conn=sqlite3.connect('myra_app/db/myra_technical.db'); before=conn.execute('SELECT COUNT(*) FROM technical_data').fetchone()[0]; conn.execute('DELETE FROM technical_data WHERE rowid NOT IN (SELECT MIN(rowid) FROM technical_data GROUP BY symbol, date)'); conn.commit(); after=conn.execute('SELECT COUNT(*) FROM technical_data').fetchone()[0]; print(f'Removed {before-after} duplicate rows'); conn.close()"

---

## Enrichment (SMC indicators)

### Run daily enrichment for the latest date
python -c "from myra_app.librarian import Librarian; from myra_app.feature_enrichment import process_enrichment_pipeline; import sqlite3, os; from myra_app.constants import DB_DIR; from myra_app.librarian_core import LibrarianCore; db_path=os.path.join(DB_DIR, LibrarianCore.DB_MAP['technical']); conn=sqlite3.connect(db_path); lib=Librarian(read_only=False); process_enrichment_pipeline(lib, conn); conn.close(); lib.close()"

### Backfill historical SMC enrichment from 2024‑01‑01
python -m tools.enrich_history

### Check SMC column population for the last 5 dates
python -c "import sqlite3, os; from myra_app.constants import DB_DIR; from myra_app.librarian_core import LibrarianCore; conn=sqlite3.connect(os.path.join(DB_DIR, LibrarianCore.DB_MAP['technical'])); dates=[r[0] for r in conn.execute('SELECT DISTINCT date FROM technical_data ORDER BY date DESC LIMIT 5').fetchall()]; [print(f'{d}: {conn.execute(f'SELECT COUNT(*) FROM technical_data WHERE date=? AND bullish_fvg IS NOT NULL',(d,)).fetchone()[0]} SMC rows') for d in dates]; conn.close()"

---

## Fundamentals Sync

### Force a full fundamentals sync (Morningstar + NSE)
curl -X POST http://localhost:8000/api/tools/sync/fundamentals

### Check latest fundamentals date
python -c "import sqlite3, os; from myra_app.constants import DB_DIR; from myra_app.librarian_core import LibrarianCore; val=os.path.join(DB_DIR, LibrarianCore.DB_MAP['valuation']); conn=sqlite3.connect(val); print(conn.execute('SELECT MAX(date) FROM fundamentals').fetchone()[0]); conn.close()"

---

## Frontend

### Install frontend dependencies
cd myra_web && npm install

### Start the Vite dev server (standalone)
cd myra_web && npm run dev

### TypeScript type check (no files changed)
cd myra_web && npx tsc --noEmit

### Production build
cd myra_web && npm run build

### Run Prettier on all frontend files
cd myra_web && npx prettier --write "src/**/*.{ts,tsx}"

---

## ML & Factor Discovery

### Get launchpad predictions for all stocks
curl http://localhost:8000/api/ml/launchpad/predict

### Train the launchpad model
curl -X POST http://localhost:8000/api/ml/launchpad/train

### Run event labelling
curl -X POST http://localhost:8000/api/ml/launchpad/label

### Get factor importance rankings
curl http://localhost:8000/api/ml/factor-importance

### Train the forward‑return model
curl -X POST http://localhost:8000/api/ml/train

---

## FinStack MCP Integration

### Fetch the morning market brief
curl http://localhost:8000/api/finstack/morning-brief

### Get Nifty directional outlook
curl http://localhost:8000/api/finstack/nifty-outlook

### Get FII/retail divergence signal
curl http://localhost:8000/api/finstack/fii-retail-divergence

### Scan for pledge risks
curl http://localhost:8000/api/finstack/scan-pledge-risks

### Get SEBI enforcement alerts
curl http://localhost:8000/api/finstack/sebi-alerts

### Get stock‑level pledge alert (example: RELIANCE)
curl http://localhost:8000/api/finstack/pledge-alert/RELIANCE

### Get stock brief (6‑agent debate)
curl http://localhost:8000/api/finstack/stock-brief/RELIANCE

---

## CI/CD & Git

### Run local static checks
bandit -r myra_app -ll
flake8 myra_app --select=E9,F63,F7,F82 --show-source --statistics

### Bypass pre‑commit hooks for a quick push
git commit -m "message" --no-verify
git push origin main

---

## Useful Diagnostics

### Show all columns in technical_data and their NULL percentage
python -c "import sqlite3, os; from myra_app.constants import DB_DIR; from myra_app.librarian_core import LibrarianCore; conn=sqlite3.connect(os.path.join(DB_DIR, LibrarianCore.DB_MAP['technical'])); cols=conn.execute('PRAGMA table_info(technical_data)').fetchall(); total=conn.execute('SELECT COUNT(*) FROM technical_data').fetchone()[0]; [print(f'{c[1]:30s} {conn.execute(f'SELECT COUNT(*) FROM technical_data WHERE {c[1]} IS NOT NULL').fetchone()[0]:>10,} / {total:,} ({(conn.execute(f'SELECT COUNT(*) FROM technical_data WHERE {c[1]} IS NOT NULL').fetchone()[0]/total)*100:.0f}%)') for c in cols]; conn.close()"

### Check ETF contamination in technical_data
python -c "import sqlite3, os; from myra_app.constants import DB_DIR; from myra_app.librarian_core import LibrarianCore; meta=os.path.join(DB_DIR, LibrarianCore.DB_MAP['meta']); tech=os.path.join(DB_DIR, LibrarianCore.DB_MAP['technical']); mconn=sqlite3.connect(meta); tconn=sqlite3.connect(tech); etfs=set(r[0] for r in mconn.execute('SELECT symbol FROM etf_blocklist').fetchall()); contam=[(sym, cnt) for sym in etfs if (cnt:=tconn.execute('SELECT COUNT(*) FROM technical_data WHERE symbol=?',(sym,)).fetchone()[0])>0]; print(f'{len(contam)} ETF symbols found in technical_data' if contam else 'No ETF contamination'); mconn.close(); tconn.close()"

### View the pipeline log (last 10 lines)
Get-Content pipeline.log -Tail 10
