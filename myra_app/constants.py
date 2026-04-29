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
