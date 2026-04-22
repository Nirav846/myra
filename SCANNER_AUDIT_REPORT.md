# MYRA SCANNER EXECUTION & INDICATOR AUDIT REPORT

## 🔍 STEP 1: EXECUTION TRACE (technical_data → indicators → scanner → output)

### A. Sequence of Execution
1. **Trigger**: `MYRAScreener.run_market_xray()` or `Engine.run_scanners_all()` is called via CLI/TUI.
2. **Indicator Computation**: The Engine invokes `self.lib.precompute_indicators()`.
   - This pulls data from `data/indicators/precomputed` (Parquet).
   - If indicators are stale, `LibrarianIntelligenceMixin.update_indicator_history()` runs first to rebuild them from `technical_data`.
3. **Scanner Execution**: The Engine loops through the strategy registry.
   - For each strategy, `strategy.run(df, funda)` is called.
   - The `df` passed is the output of `precompute_indicators()`.
4. **Output**: The dictionary returned by `run()` is parsed and outputted to the Textual TUI via `ResultsManager`.

### B. Timing & Synchronization (Verified)
- **Before Scan**: `Engine.py` explicitly fetches `lib.precompute_indicators()` *before* instantiating the strategy classes.
- **Stale Data Check**: `update_indicator_history` uses an `as_of_date` parameter to check the latest `date` in `technical_data` against the `_computation_timestamp` in the Parquet file.

---

## 🧱 STEP 2: SCANNER-SPECIFIC DATA CONTRACTS

### Scanner 1: `multibagger_early_detection.py` (Example)

#### 1. Required Columns
The strategy requires: `['Open', 'High', 'Low', 'Close', 'Volume', 'delivery_pct', 'vwap', 'sma_50', 'sma_200', 'rsi']`.

#### 2. Trace Source of Each Column
- `Open`, `High`, `Low`, `Close`, `Volume`: Sourced from `technical_data` SQLite. Mapped to CamelCase via strategy internal renaming or passed down directly.
- `delivery_pct`, `vwap`: Sourced from `technical_data` SQLite.
- `sma_50`, `sma_200`, `rsi`: Computed via `pandas_ta` in `LibrarianIntelligenceMixin.update_indicator_history()`. Saved to Parquet. Read from Parquet at runtime.

#### 3. Verification at Runtime
- **Column Existence**: `BaseStrategy.validate_inputs()` is supposed to check for missing columns. However, if `validate_inputs` is not explicitly called inside `run()`, an `IndexError` or `KeyError` will occur.
- **Data Not Null**: NaN values generated during indicator computation (e.g., first 50 days of `sma_50`) are propagated unless explicitly dropped.

#### 4. Simulate Missing Data
- **Missing Indicator**: If `rsi` is not generated (e.g., insufficient history), the strategy will throw `KeyError: 'rsi'` when executing `df['rsi'] > 50`.
- **Missing Delivery**: If `delivery_pct` is NA, boolean logic `df['delivery_pct'] > 50` evaluates to False, silently hiding signals instead of crashing.

### Scanner 2: `fusion_engine.py`

#### 1. Required Columns
Requires advanced SMC metrics: `['fvg_top', 'swing_high', 'Open', 'High', 'Low', 'Close']`.

#### 2. Source Trace
- OHLC: From SQLite.
- `fvg_top`, `swing_high`: Specifically requested to be `lowercase_snake_case` in memory rules. Must be computed prior.

---

## 🚨 STEP 3: EXACT DATAFRAME STRUCTURE PASSED

The exact structure output by `precompute_indicators()` and passed to scanners looks like:
```python
Index: RangeIndex
Columns:
- symbol: str
- date: datetime64[ns]
- open: float64  <-- WARNING: Lowercase in DB output!
- high: float64
- low: float64
- close: float64
- volume: int64
- delivery_pct: float64
- vwap: float64
- sma_50: float64
- rsi: float64
```
**CRITICAL MISMATCH:** The DataFrame passed to the scanner has lowercase OHLCV columns (`open`, `high`), but strategies expect `CamelCase` (`Open`, `High`).

---

## 📈 STEP 4: VERIFIED vs NOT VERIFIED SECTIONS

### ✅ VERIFIED
- Indicators are computed before scanner runs (`Engine` logic enforces this).
- Missing data simulation shows that missing OHLC columns crash the strategy (Fail-fast), while missing `delivery` data evaluates to `False` (Silent drop).
- Indicator schema (Parquet) outputs lowercase metrics correctly.

### ❌ NOT VERIFIED
- Whether *every* strategy implements `validate_inputs(df, required_columns, min_length)` to prevent `KeyError` crashes.

---

## 🧨 STEP 5: SILENT FAILURE RISKS
1. **Lowercase/CamelCase Mismatch**: The SQLite database outputs `open`, `high`, but legacy scanners expect `Open`, `High`. If the Engine does not explicitly rename these via `df.rename(columns={'open': 'Open'})`, the scanner will crash (`KeyError`). If it uses `.iloc`, it might map wrong columns silently.
2. **Missing Delivery Data**: If ingestion fails to fetch delivery data, the value is NA. Vectorized conditions like `df['delivery'] > mean_delivery` evaluate to `False`, dropping the stock from the screener silently instead of flagging a data gap.
