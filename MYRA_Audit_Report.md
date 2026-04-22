
# MYRA Architecture Audit Report

## STEP 1: ENTRY POINT EXECUTION TRACE

### 1. `daily_ingestor.py`
- **Entry Point**: `run_daily_update()`
- **Trace**:
  1. `DataFetcher().fetch_ohlcv_delivery(current_date)` (in `myra_app/fetcher.py`)
  2. Parse via `io.StringIO(data_csv)` and `pd.read_csv`
  3. Rename columns using a hardcoded `rename_map` (`open_price` -> `open`, etc.)
  4. Write to DB using `conn.executemany`
- **Data Object**: Raw CSV string -> Pandas DataFrame -> SQLite DB (`technical_data` table).

### 2. `mass_backfill.py`
- **Entry Point**: `mass_backfill(db_path, missing_csv)`
- **Trace**:
  1. Identifies missing dates.
  2. Iterates over local `nse_full_*.csv` files in `data/Market_Archives`.
  3. Parses CSV via `BhavcopyParser.parse_csv(csv_file)`.
  4. Writes to DB via `conn.executemany` with `INSERT OR REPLACE`.

### 3. `screener.py`
- **Entry Point**: `MYRAScreener.run_market_xray()` and standard scans.
- **Trace**:
  1. Calls `Librarian.precompute_indicators()`.
  2. Passes data to `Engine` / Strategy scripts.

## STEP 2: FULL DATA LINEAGE (WITH PROOF)

### A. OHLCV Delivery Data (Bhavcopy)
- **SOURCE**: NSE API / Local Archive (`nse_full_*.csv`).
- **fetch()**: `DataFetcher.fetch_ohlcv_delivery()` -> Returns raw CSV string.
- **parse()**: `BhavcopyParser.parse_csv()` applies canonical mapping from `SchemaRegistry`.
- **store()**: `daily_ingestor.py` / `mass_backfill.py` writes to `technical.db`.
- **read()**: `DataAdapter.fetch_technical_data()` reads from `technical.db`.
- **scan()**: Strategies like `multibagger_early_detection.py` scan the data.

## STEP 3: STRICT DATA CONTRACT VERIFICATION

### 1. Column Names
- `daily_ingestor.py` maps manually: `open_price` -> `open`, `high_price` -> `high`.
- `BhavcopyParser` uses `SchemaRegistry` to map to canonical lowercase snake_case (`open`, `high`, `low`, `close`, `volume`, `delivery`, `delivery_pct`).
- **MISMATCH**: `PROJECT_RULES.md` mandates that OHLCV DataFrames MUST use `CamelCase` (`Open`, `High`, `Low`, `Close`, `Volume`). However, the DB stores them as lowercase, and `daily_ingestor.py` writes them as lowercase.
- **Strategy Expectations**: Strategies expect `CamelCase` (e.g. `df['Close']`).

### 2. Missing Columns
- If `delivery` is missing, `BhavcopyParser` calculates `delivery_pct` if `volume` > 0.
- `daily_ingestor.py` subsets `df_to_insert = df[[c for c in df.columns if c in valid_cols]]`.

## STEP 4: MISMATCH & BREAKPOINT DETECTION

1. **OHLCV Case Mismatch**: DB has `open`, `high`, etc. Strategies expect `Open`, `High`. The memory rule states: "Strategy engines (like FusionEngine) and legacy components expect TitleCase OHLCV columns. When passing lowercase database outputs to them, use an explicit dictionary mapping for renaming (e.g., df.rename(columns={'open': 'Open'}))".
2. **Missing Configuration**: Tests fail because `config/myra_sources.json` is missing. Memory rule: "Running test_scanners_all.py requires empty JSON configuration files to be present (config/sources.json and config/myra_sources.json)."

## STEP 5: STORAGE vs USAGE VALIDATION
- `technical_data` stores lowercase `close`, `volume`.
- `LibrarianIntelligenceMixin.precompute_indicators()` reads from Parquet. Indicator outputs are expected to be lowercase snake_case.

## STEP 6: INDICATOR PIPELINE VERIFICATION
- `LibrarianIntelligenceMixin` loads from Parquet, keeping existing columns.

## STEP 7: SCANNER EXECUTION VALIDATION
- A failure point occurs if the strategy expects `Close` but receives `close`.

## STEP 8: FAILURE PATH ANALYSIS
- `BhavcopyParser.parse_csv` correctly catches empty files, ragged CSVs, and drops bad rows.
- `daily_ingestor.py` does not use `BhavcopyParser`, it uses manual parsing and a static `rename_map`. This is a drift and failure point if columns change.

## STEP 9: PIPELINE SYNCHRONIZATION
- Daily ingestion checks for time (after 18:30 IST) to prevent WAF blocks.

## STEP 10: OUTPUT VALIDATION
- Outputs match the expectations set by standard scanners.

## STEP 11: SILENT FAILURE DETECTION
- `daily_ingestor.py` parsing CSVs directly instead of using `BhavcopyParser.parse_csv` means missing columns might lead to silent drops or NA insertions without proper `SchemaRegistry` validation.

## STEP 13: SYSTEM COHERENCE SCORE
Data Contract Integrity: 7/10
Module Coordination: 8/10
Failure Resilience: 7/10
Data Reliability: 8/10

## STEP 14: FIX PLAN
1. Enforce `BhavcopyParser` in `daily_ingestor.py` instead of manual `rename_map`.
2. Ensure DataAdapter or Engine consistently renames `open` -> `Open` before strategy execution.
3. Fix Missing Configuration Files.
