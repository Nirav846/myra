# MYRA Database Context — Read This Before Any DB Task

## The rule
All DB access goes through `LibrarianCore.DB_MAP`. Never hardcode a filename.
Never use `os.getcwd()` for paths — use `constants.py` (`DB_DIR`, `DATA_DIR`, `PROJECT_ROOT`).

## DB file locations
All files live in `myra_app/db/`. DB_MAP keys → filenames (9 keys):

| Key          | File                      | Primary tables                                      |
|--------------|---------------------------|-----------------------------------------------------|
| "technical"  | myra_technical.db         | technical_data, launchpad_events, launchpad_features |
| "meta"       | myra_metadata.db          | symbols_master, index_constituents, benchmarks, metadata, etf_blocklist, task_registry, lineage_tracking, etf_sync_log, sync_log |
| "valuation"  | myra_valuation.db         | fundamentals, quarterly_results, fund_traction, fund_cross_buy, full_fundamental_cache |
| "institutional" | myra_institutional.db  | insider_trades, large_deals, bulk_deals, block_deals, fii_dii_daily |
| "governance" | myra_governance.db        | sast_disclosures, pledged_history, shareholding_history, ias_history |
| "scoring"    | myra_scoring.db           | ias_scores, fundamental grades |
| "calendar"   | myra_calendar.db          | market_calendar |
| "cache"      | myra_cache_network.db     | network cache |
| "options"    | myra_options.db           | option_chain, pcr_snapshot |

> `myra_news.db` and `myra_portfolio.db` also exist but are **not** in `DB_MAP` (portfolio is gitignored).

## Where sector and index data lives
- Sector / industry per symbol → myra_metadata.db → symbols_master (columns: sector, industry, raw_sector, raw_industry, source, confidence, last_updated_sector, sector_locked)
- Index constituents (NIFTY 50, NIFTY 500 etc.) → myra_metadata.db → index_constituents (index_name TEXT, symbol TEXT)
- Benchmark OHLCV (^NSEI prices) → myra_metadata.db → benchmarks
- **DO NOT query valuation.db for sector lookups** — that is wrong. Use meta.db → symbols_master.

## fund_traction / fund_cross_buy (myra_valuation.db)
- `fund_traction` — PK `(symbol, month)`; columns: symbol, month, traction_score, number_of_funds, adds_new, reduces_closes, sma_30, month_end_close, close_latest, pct_vs_sma. Plus a `sync_metadata` table.
- `fund_cross_buy` — PK `(symbol, month)`; columns: symbol, month, total_funds, large_funds, mid_funds, small_funds, multi_funds, other_funds, cross_buy_ratio, signal_tag, last_updated.
- Written by `myra_app/fund_traction_sync.py` and `myra_app/cross_buy_processor.py`.
- **Do not modify these tables directly** — re-sync via the pipeline tasks (`fund-traction-sync`, `cross-buy-sync`).

## symbols_master full schema
symbol TEXT PRIMARY KEY,
first_seen TEXT, last_seen TEXT,
in_active_universe INTEGER DEFAULT 0,
in_nifty500 INTEGER DEFAULT 0,
sector TEXT,          -- normalized sector name
industry TEXT,        -- normalized industry name
raw_sector TEXT,      -- original string from source
source TEXT,          -- NSE_INDEX | MORNINGSTAR | SCREENER | YFINANCE
confidence REAL,      -- 1.0=official, 0.8=screener, 0.6=yfinance
last_updated_sector TEXT,
sector_locked INTEGER DEFAULT 0,  -- 1 = skip automated updates
is_active INTEGER DEFAULT 1,
instrument_type TEXT DEFAULT 'EQUITY',
last_fundamental_update TEXT

## technical_data full schema
symbol TEXT NOT NULL, date TEXT NOT NULL,
open REAL, high REAL, low REAL, close REAL,
volume INTEGER, delivery INTEGER, trades INTEGER, vwap REAL,
delivery_pct REAL, delivery_ratio REAL, delivery_qty REAL,
stock_return REAL, market_return REAL,
delivery_divergence_score REAL, volatility_compression_score REAL,
relative_volume_score REAL, nifty_outperformance_score REAL,
delivery_source TEXT,          -- e.g. "eod2_adjusted"
sma_50 REAL, high_52w REAL, low_52w REAL, delivery_ma_60 REAL,
FVG / swing / liquidity / trend / enrichment columns (added via enrichment pipeline),
PRIMARY KEY (symbol, date)

> The live table has more columns than the SchemaRegistry definition. EOD2 sync (`eod2_sync.py`) maps BhavDesk headers (`Date,Open,High,Low,Close,Volume,Series,TOTAL_TRADES,QTY_PER_TRADE,DLV_QTY`) and writes 16 columns via `_INSERT_COLS`.

## Connections (from LibrarianCore)
self._tech_conn  → technical.db
self._meta_conn  → meta.db
self._val_conn   → valuation.db
self._inst_conn  → institutional.db
self._gov_conn   → governance.db

## How sector updates work
SectorManager (myra_app/sector_manager.py):
- Primary source: Morningstar bulk API (4000 symbols, confidence 1.0)
- Secondary: NiftyIndices.com CSV (official 4-tier classification, confidence 1.0)
- Fallback: screener.in per-symbol (0.8), yfinance (0.6)
- Writes to: myra_metadata.db → symbols_master
- Update trigger: incremental_sync() runs on every sync_market_data() call
- Targets: NULL sectors + last_updated_sector older than 90 days
- sector_locked=1 symbols are never overwritten

## Critical rules for agent/CI tasks
- Adding a column to technical_data → also add to TECHNICAL_EXPECTED_COLS in tools/db_doctor.py
- Adding a column to symbols_master → also add to META_EXPECTED_COLS in tools/db_doctor.py
- ALTER TABLE ADD COLUMN must use IF NOT EXISTS guard (match delivery_source pattern)
- No df.append() in loops → list + pd.concat
- No .strftime() on Pandas Series → .dt.strftime()
- CamelCase OHLCV in DataFrames (Open/High/Low/Close/Volume), lowercase in DB inserts
- WAL mode must stay on — never set journal_mode=DELETE
- Prefer `myra_app/db/bulk_loader.load_ohlcv_for_universe` for scanner data — never per-symbol SQLite connects

## KNOWN ISSUE: `fundamentals` legacy camelCase columns are now unwritten

Recorded 2026-09-29. **Reader staleness signalling: done. Live source: still
open.** Verified against the live DB (`myra_valuation.db`, 3917 `fundamentals`
rows) and `schema/valuation.sql`.

**The columns.** `fundamentals` has 59 columns, **20 of them legacy camelCase**
(`peRatio`, `priceToBook`, `revenueGrowth`, `marketCap`, `returnOnEquity`,
`operatingMargin`, `grossMargin`, `netMargin`, `dividendYield`, `payoutRatio`,
`currentRatio`, `quickRatio`, `freeCashFlowYield`, `returnOnAssets`,
`debtToEquity`, `enterpriseValue`, `priceToSales`, `earningsPerShare`,
`bookValuePerShare`, `earningsGrowth`) sitting alongside 39 canonical
snake_case/lowercase ones. They are residue from pre-consolidation writes plus
the Upstox fetcher, which wrote camelCase names.

**Nobody writes them any more.** `myra_app/fundamental_sync.py` is the canonical
writer and emits snake_case only — its `MS_CANONICAL_MAP` (line 308) maps the
Morningstar camelCase keys onto canonical columns and *drops* unmapped ones
("to prevent schema drift"). `myra_app/fetchers/full_fundamentals.py` likewise
normalises (`data["roe"] = g("returnOnEquity")`, line 387) into its own cache
table, not `fundamentals`. So this is a **reader** problem, not a writer problem.

**The reader problem.** `myra_web/routes/fundamentals.py:208-233` (the
`/api/fundamentals` payload) reads several legacy columns with **no fallback to
the canonical column**. Populated counts below are out of 3917, measured
2026-09-29:

| API field | column read | populated | canonical column | canonical populated |
|---|---|---|---|---|
| `pb` | `priceToBook` | 448 | none | — |
| `ps` | `priceToSales` | 0 | none | — |
| `operating_margin` | `operatingMargin` | 7 | `operating_margin` | 0 |
| `gross_margin` | `grossMargin` | 7 | `gross_margin` | 0 |
| `current_ratio` | `currentRatio` | 7 | `current_ratio` | 0 |
| `quick_ratio` | `quickRatio` | 120 | `quick_ratio` | 113 |
| `free_cash_flow_yield` | `freeCashFlowYield` | 6 | `free_cash_flow_yield` | 0 |
| `revenue_growth` | `revenueGrowth` | 0 | `sales_growth` | 0 |
| `earnings_growth` | `earningsGrowth` | 0 | `profit_growth` | 0 |
| `payout_ratio` | `payoutRatio` | 6 | **does not exist** | — |

`revenueGrowth` / `earningsGrowth` *do* have canonical columns — `sales_growth`
and `profit_growth` — because `MS_CANONICAL_MAP` maps them. They are simply
empty, since Morningstar returns null for both (see below). At the time of the
original 2026-09-29 measurement, `payout_ratio` and `pb` had no canonical column
at all; `price_to_book` and `payout_ratio` have since been added to
`MS_CANONICAL_MAP` and `schema_registry.py` (see the correction below — the
`payout_ratio` addition immediately proved valuable, because Morningstar *does*
return that field). All counts in the table above are the pre-fix 2026-09-29
snapshot.

**Why it got worse on 2026-09-29.** Commit `d2f0de6` in `D:\01screener\upstox_fetcher`
removed the Upstox fundamentals write. The legacy columns that Upstox populated
are now permanently frozen at their last sync:

| legacy column | UPSTOX-sourced rows | last `last_updated` |
|---|---|---|
| `priceToBook` | 448 | 2026-09-01T03:49:59Z |
| `returnOnEquity` | 442 | 2026-09-01T03:49:59Z |
| `quickRatio` | 113 | 2026-09-01T03:49:59Z |

`priceToBook` is the worst case: it is the *only* source of the API's `pb` field,
it was the best-populated legacy column, and it will not be refreshed again. The
`operatingMargin` / `grossMargin` / `currentRatio` / `freeCashFlowYield` /
`payoutRatio` rows are older Morningstar writes with `last_updated IS NULL` and
were already stale before this commit.

**Also observed:** `marketCap` (8 rows, `last_updated 2026-04-04`,
`source_ms IS NULL` — i.e. not Upstox-sourced) and `market_cap` (2936 rows)
disagree on 2 rows. The same metric stored twice with different values; "which
is right" needs a re-sync, not a coin flip.

**Do not** add a new writer for these columns.

**The canonical columns are not an independent fresher source.** This was
checked before deciding how to fix the reader, and it rules out the obvious
"just read the canonical column" fix for `roe` / `quick_ratio`. Where a
canonical column is populated, it is a **byte-identical mirror** of the same
frozen legacy value — Upstox wrote both columns:

| pair | rows | same symbols | value disagreements |
|---|---|---|---|
| `returnOnEquity` (442) vs `roe` (443) | 442 match | 442 | **0** |
| `quickRatio` (120) vs `quick_ratio` (113) | 113 match | 113 | **0** |

So switching the reader to canonical-first would serve the *same* frozen
2026-09-01 numbers while labelling them as Morningstar data on rows where
`source_ms = 'UPSTOX'` — provenance laundering, and no gain in freshness.

**CORRECTED 2026-10-01 — the claim below was wrong.** The earlier version of
this note asserted that no live source existed for *any* of the six concepts.
That was inferred from the state of the legacy columns, not measured against a
live Morningstar sync, and it was wrong for two of the six. `quickRatio` and
`payoutRatio` were never dead concepts — they were simply **absent from
`MS_CANONICAL_MAP`**, so Morningstar's values were being *discarded* at the sync
boundary and never had a canonical column to land in. Once the map entries were
added, the very next sync populated them. Live counts on the 3355
MORNINGSTAR-sourced rows:

| concept | MS rows with data | source |
|---|---|---|
| `quick_ratio` | **3227** | live Morningstar |
| `payout_ratio` | **3236** | live Morningstar |
| `price_to_book` (`priceToBook`) | 0 | none — MS still null |
| `roe` (`returnOnEquity`) | 0 | none — 442 frozen Upstox rows remain |
| `sales_growth` (`revenueGrowth`) | 0 | none |
| `profit_growth` (`earningsGrowth`) | 0 | none |

So the open question is **four** concepts, not six. The rest of this section's
evidence still holds: Morningstar requests all of them in the same
`securityDataPoints` call, and the following come back null on a sync that is
demonstrably working:

```
net_margin=3279  dividend_yield=1457  sector=3337  date=3339   <- arriving fine
roe=0  roe_ttm=0  sales_growth=0  profit_growth=0
eps=0  book_value=0  debt_to_equity=0                        <- all null
```

The only other candidate, `full_fundamental_cache` (the yfinance path, which
already normalises `price_to_book` / `roe` / `revenue_growth` /
`earnings_growth`), holds 3 rows and populates none of these fields. Why
Morningstar returns null for exactly this remaining subset is
**uninvestigated** — see the open question below.

**Data-quality flag on `quick_ratio` (unresolved).** The newly live
`quick_ratio` values range **1.1e-05 to 13935.9, avg 10.9**. A quick ratio of
13935 is not plausible for a listed company, so either the units are wrong
(ratio vs percentage), the field is unrelated data reused under that name, or
certain sectors produce nonsense. `payout_ratio` (0–16.67, avg 0.12) looks
sane by comparison. Do not surface `quick_ratio` to users until the raw
Morningstar field has been inspected for an extreme-value symbol.

**What was done (2026-09-29).** Rather than swap sources, the reader now
*discloses* staleness. `/api/fundamentals/live/{symbol}` adds a sibling
`<field>_stale` boolean next to each of the six values, plus one shared
`data_as_of` timestamp, and `HistoricalSearch.tsx` renders a muted value with a
`*` tooltip on stale ones. Staleness is **age-based**
(`FROZEN_SOURCE_FIELDS` / `STALENESS_MAX_AGE_DAYS = 30` in
`myra_web/routes/fundamentals.py`), not hardcoded — if a real writer ever
lands and refreshes the row, `last_updated` advances and the flags clear on
their own. A field with a null value is never flagged; there is nothing
misleading to mark. Legacy column reads are deliberately unchanged.

**Open follow-up.** Investigate whether the null Morningstar data points are
retired, renamed, or plan-gated (compare the raw response body against
`full_fundamentals.py`'s normalisation). Only once a genuine source populates
the canonical columns does a reader-side source swap make sense. Note
`myra_app/ai_second_opinion.py:288` still does
`COALESCE(roe, returnOnEquity)` and is not covered by the staleness flag.

`tools/consolidate_fundamentals_columns.py` is the existing consolidation
backfill (idempotent: `WHERE (canonical IS NULL OR canonical = 0) AND alias IS
NOT NULL AND alias != 0`). It is **not** a fix for this issue — copying legacy
values into canonical columns would only make frozen data look canonical.
Dropping the legacy columns is a separate decision — `SchemaRegistry` only
ever adds, per `AGENTS.md`.
