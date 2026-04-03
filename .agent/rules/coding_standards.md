# MYRA Coding Standards (Hardened v2.5)

## 1. Database Operations
- **Method**: Always use `Librarian.safe_execute(sql, params, conn)` for database interactions.
- **Security**: Never use f-strings or `.format()` inside an `.execute()` call. Use `?` placeholders.
- **Transactions**: Use `BEGIN TRANSACTION` and `COMMIT/ROLLBACK` for batch inserts (like in `_fetch_range`).
- **Schema**: Keep `lowercase_snake_case` for all table and column names.

## 2. DataFrame Handling
- **OHLCV**: Always rename raw database columns to `CamelCase`:
  ```python
  df.rename(columns={'open':'Open','high':'High','low':'Low','close':'Close','volume':'Volume'})
  ```
- **Timeframe**: Structural analysis (SMC/FVG) requires a **3-year lookback** (756 bars).
- **Date Conversion**: Use `pd.to_datetime(df['date'])` and ensure the index is a DatetimeIndex for `pandas_ta` compatibility.

## 3. Strategy Implementation
- **Result Payload**: Ensure all strategy `run()` methods return a dictionary with:
  - `signal`: Boolean
  - `metrics`: Dictionary of keys matching the `hero_cols` defined in `myra.py`.
- **Institutional Alignment**: Use **Delivery Quantity** Z-scores as the primary signal for accumulation detection.
- **FVG Magnets**: Implement `SMCManager.get_fvg_buy_zone(df)` to provide high-precision "Best Buy" levels.

## 4. UI & Rendering
- **Formatting**: Round all numeric outputs to **2 decimal places** in the discovery table.
- **Responsiveness**: Use `ResultsManager` responsive column pruning for narrow terminals (< 130 chars).
- **Casing**: UI keys should be `snake_case` internally and mapped to `Title Case` or `Short_Name` in the table headers.

## 5. Maintenance
- **Performance**: Run `python myra_app/tuner.py` after significant data backfills to refresh indices and `VACUUM` the database.
- **Help Docs**: Update `docs/SCANNERS_HELP.md` via `/Scribe` when adding new technical scanners (101+) or strategies (1-99).
