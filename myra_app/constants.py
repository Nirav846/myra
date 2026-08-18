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
USE_EOD2_DATA = False

# Master kill-switch for all ingestion.  Set to False before swapping databases
# to guarantee no writes happen during the rename window.
ENABLE_DAILY_INGEST = True
