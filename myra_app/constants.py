"""
MYRA Project Constants
Single source of truth for all filesystem paths.
"""

import os

# Project root — two levels up from this file
# myra_app/constants.py -> myra_app/ -> project root
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Standard directory paths
DB_DIR = os.path.join(PROJECT_ROOT, "myra_app", "db")
DATA_DIR = os.path.join(PROJECT_ROOT, "data")
CACHE_DIR = os.path.join(PROJECT_ROOT, ".jules", "cache")
LOGS_DIR = os.path.join(PROJECT_ROOT, "logs")
MODELS_DIR = os.path.join(PROJECT_ROOT, "models")

# Feature flags
# When False, the Screener.in fundamentals enricher only runs when invoked
# explicitly with force=True (e.g. `python run_pipeline.py --enrich-screener`).
# Set to True to re-enable automatic weekly runs via the background orchestrator.
SCREENER_ENRICH_AUTO_ENABLED = False

# ─── Data source toggle ─────────────────────────────────────────────────────
# Set USE_EOD2_DATA = True after swapping to the eod2-adjusted database.
# When True, the daily ingest task reads from eod2_data/daily/ CSVs instead of
# fetching raw NSE bhavcopy data.  This prevents unadjusted data from
# overwriting corporate-action-adjusted rows.
USE_EOD2_DATA = True
# Master kill-switch for all ingestion.  Set to False before swapping databases
# to guarantee no writes happen during the rename window.
ENABLE_DAILY_INGEST = True

# ─── Fundamentals writer ownership toggle ───────────────────────────────────
# When True, ALL Myra-side writers to the `fundamentals` table in
# myra_valuation.db are disabled.  The upstox_fetcher process now owns
# the fundamentals table, so Myra's legacy writers (Morningstar, yfinance,
# Screener.in, BSE shareholding, market-cap sync) must not clobber the
# upstox-fed rows.
#
# Writers to OTHER tables (quarterly_results, fund_traction, fund_cross_buy,
# full_fundamental_cache, sync_metadata) and all read operations (SELECT)
# are unaffected.
#
# Set DISABLE_FUNDAMENTAL_WRITERS=True in the environment or .env file to
# engage the lock.  Set to False (default) to restore legacy writes.
DISABLE_FUNDAMENTAL_WRITERS = (
    os.getenv("DISABLE_FUNDAMENTAL_WRITERS", "False").strip().lower() == "true"
)

# ─── Fund Traction Sync ─────────────────────────────────────────────────────
# GitHub Pages base URL for cross-fund-holdings-traction monthly JSON files.
# Replace <username> with the actual GitHub username before first run.
TRACTION_BASE_URL = "https://nirav846.github.io/cross-fund-holdings-traction/data/"
