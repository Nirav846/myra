# MYRA Project Audit Report

## 1. Performance Hotspots

* **File:** `myra_app/feature_enrichment.py`, Line 284
  * **Description:** N+1 query pattern where an `ALTER TABLE` is executed individually for each SMC column inside a loop.
  * **Suggested Fix:** Execute `ALTER TABLE` operations in a batched execution or consolidate schema alterations to execute sequentially in a single pass outside of the row-loop logic.

* **File:** `myra_app/librarian.py`, Lines 304, 426
  * **Description:** Contains an N+1 query pattern iterating `PRAGMA table_info('technical_data')` for individual symbols inside loops.
  * **Suggested Fix:** Fetch and cache `table_info` once at connection/instantiation time instead of querying it for each symbol request.

* **File:** `myra_app/data_adapter.py`, Line 209
  * **Description:** Contains an N+1 query pattern executing `PRAGMA table_info('fundamentals')` inside a `get_latest_funda` loop for every symbol.
  * **Suggested Fix:** Extract schema checking to cache it at class level rather than validating inside a symbol fetch loop.

* **File:** `myra_app/utils/institutional_sync.py`, Line 81
  * **Description:** Contains an N+1 query (`PG-NPLUS1`) pattern iterating to update the institutional data.
  * **Suggested Fix:** Use `.executemany` instead of `.execute` inside loops to perform batch inserts.

* **File:** `myra_app/utils/fundamentals_sync.py`, Line 158
  * **Description:** Contains an N+1 query pattern (`PG-NPLUS1`).
  * **Suggested Fix:** Refactor to execute a batch statement instead of invoking SQLite queries repeatedly in a loop.

* **File:** `myra_app/feature_enrichment.py`, Line 233
  * **Description:** Calling `.tail(500000)` on a very large DataFrame causes huge memory loads and limits calculation speed.
  * **Suggested Fix:** Reduce the window calculation or optimize with PyArrow/Polars native streaming methods instead of converting back to Pandas dataframe (`to_pandas()`) to compute the final chunk.

* **File:** `myra_app/feature_enrichment.py`, Line 314
  * **Description:** Iterating over `smc_today.to_pandas().iterrows()` to construct batch data performs extremely poorly for large datasets.
  * **Suggested Fix:** Use vectorized column filtering or list comprehension over dictionary records (`to_dict('records')`) instead.

* **File:** `myra_web/src/views/AdvancedChart.tsx`, Line 106 (and surrounding state definitions)
  * **Description:** Uses over 20+ individual `useState` hooks for indicators leading to cascading component re-renders when multiple toggles change.
  * **Suggested Fix:** Combine these configurations into a single `useReducer` or object-based `useState` to batch updates and prevent excessive re-renders.

* **File:** `myra_web/src/views/ReversionEngine.tsx`, Line 615
  * **Description:** Uses multiple extensive `useEffect` rendering loops leading to compounding re-calculations.
  * **Suggested Fix:** Extract and decouple state triggers to prevent excessive re-renders when updating state models simultaneously.

## 2. Error Handling Gaps

* **File:** `myra_app/background_orchestrator.py`, Lines 135-136
  * **Description:** Silent `except Exception:` block with `pass` in `_already_ingested_today` masks potential critical errors checking ingestion status.
  * **Suggested Fix:** Log the exception instead of passing silently.
  ```python
  except Exception as e:
      logger.warning(f"[MYRA BG] Unexpected error verifying ingestion metadata: {e}")
  ```

* **File:** `myra_app/background_orchestrator.py`, Lines 669-670
  * **Description:** Captures generic `Exception` and silently defaults to seeding the ETF database without logging the failure.
  * **Suggested Fix:** Explicitly log the error before setting the fallback.
  ```python
  except Exception as e:
      logger.error(f"[MYRA BG] Could not verify ETF blocklist metadata: {e}")
      _needs_seed = True
  ```

* **File:** `myra_app/daily_ingestor.py`, Lines 125-126
  * **Description:** In `run_daily_update`, catching `Exception` to silence archiving failure does not exit or fallback gracefully causing cascading logic failures.
  * **Suggested Fix:** Explicitly raise critical block warnings.
  ```python
  except Exception as e:
      print(f"⚠️ Could not save CSV archive: {e}")
      # Fallback logic here
  ```

* **File:** `myra_app/mass_backfill.py`, Lines 238-240
  * **Description:** Masks complete chunk processing crashes inside of large `executemany` CSV iteration blocks.
  * **Suggested Fix:** Replace basic prints with structured trace logging using `logging.error(f"Backfill batch failed: {e}", exc_info=True)`.

* **File:** `myra_app/librarian_ingestor.py`, Lines 29, 34, 39, 44, 73, 94
  * **Description:** Multiple instances of bare `except:` blocks that silently `pass`. This is a Python anti-pattern and masks KeyboardInterrupt and SystemExit.
  * **Suggested Fix:** Replace `except:` with `except Exception as e:` and log the underlying error message.

* **File:** `myra_app/librarian.py`, Lines 111, 136
  * **Description:** Contains bare `except:` blocks that swallow exceptions in the `get_market_holidays` loops.
  * **Suggested Fix:** Change bare `except:` to `except Exception as e:` and log warning.

## 3. Database Schema Improvements

* **File:** `myra_app/librarian_schema.py`, Line 167 (and `myra_app/schema_registry.py`)
  * **Description:** The `fundamentals` table has a primary key on `symbol`, but it lacks an index on `sector`, even though scanning queries actively search using `WHERE sector = ?`.
  * **Suggested Fix:** Add an index on `sector` to speed up sector-based scanning.
  ```sql
  CREATE INDEX IF NOT EXISTS idx_funda_sector ON fundamentals (sector);
  ```

* **File:** `myra_app/librarian_schema.py`, Line 108
  * **Description:** The `technical_data` table properly defines a `PRIMARY KEY (symbol, date)` constraint and has single column indexes on `date` and `symbol`. However, to optimize queries fetching recent data for a symbol, a composite index `(symbol, date DESC)` would be beneficial.
  * **Suggested Fix:** Add a composite index on `(symbol, date DESC)` to optimize range scanners.
  ```sql
  CREATE INDEX IF NOT EXISTS idx_technical_symbol_date ON technical_data (symbol, date DESC);
  ```

* **File:** `myra_app/librarian_schema.py`, Line 38
  * **Description:** In `symbols_master`, multiple columns such as `symbol`, `is_active`, etc., do not use explicit `NOT NULL` constraints despite being core identification metrics, which could cause data corruption issues.
  * **Suggested Fix:** Explicitly apply `NOT NULL` constraints to foundational identifiers.
  ```sql
  symbol TEXT PRIMARY KEY NOT NULL
  ```

* **File:** `myra_app/librarian_schema.py`, Line 167
  * **Description:** In `fundamentals`, critical baseline metrics (like `symbol`, `sector`) lack `NOT NULL` constraints which can cause fallback ingestion failures.
  * **Suggested Fix:** Explicitly define `NOT NULL`.
  ```sql
  symbol TEXT PRIMARY KEY NOT NULL
  ```

## 4. Architectural Smells

* **File:** `myra_app/feature_enrichment.py`, Line 140
  * **Description:** `process_enrichment_pipeline` is too long (> 200 lines) and has too many responsibilities (database loading, Polars conversion, calculation, chunking, batched SQL execution).
  * **Suggested Fix:** Split into three functions: `extract_market_data`, `compute_smc_features`, and `load_features_to_db`.

* **File:** `myra_app/data_adapter.py`, Line 66
  * **Description:** `get_price_df` handles cache checking, SQLite connections, DDL validation, and on-the-fly TA calculations within a single block.
  * **Suggested Fix:** Extract caching layer into its own decorator or mixin, and keep the data adapter strictly concerned with fetch operations.

* **File:** `myra_app/background_orchestrator.py`, Line 272
  * **Description:** `start()` function is nearly 200 lines long, responsible for setting up multiple specific initial seedings for ETFs, fundamentals, and index constituents before it even starts background threads.
  * **Suggested Fix:** Split out the database seeding functions into separate initialization methods (e.g., `_seed_databases()`).

* **File:** `myra_app/librarian.py`, Line 37
  * **Description:** God Object anti-pattern. Librarian handles caching, querying fundamentals, executing raw queries, validation, generating reports, and tracking market holidays.
  * **Suggested Fix:** Break down responsibility into isolated domain-specific data providers (e.g., `ValuationProvider`, `MarketCalendarProvider`).

* **File:** `myra_web/src/views/AdvancedChart.tsx`, Line 106
  * **Description:** Huge React component managing mock generation, state for 20+ indicators, search input, and presentation.
  * **Suggested Fix:** Refactor indicator toggles and UI config into a custom hook `useChartSettings` and extract the UI mapping to smaller sub-components.

## 5. Dead Code

The following standalone modules in `myra_app/` are not imported or referenced by any core pipeline or script and appear to be completely unused dead code:
* `myra_app/auditor.py`
* `myra_app/backfill_technical.py`
* `myra_app/calendar_generator.py`
* `myra_app/code_backup.py`
* `myra_app/data_exporter.py`
* `myra_app/debug_zero_scanners.py`
* `myra_app/strategy_engine.py`
* `myra_app/ui_components.py`

*(Note: `ingest_bhavcopy.py` and `missing_detector.py` have been excluded from this list as they are tracked and executed independently by users per project documentation.)*
