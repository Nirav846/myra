import logging
import math
from myra_app.strategies.scanner_utils import sanitize_float
import sqlite3
import os
import numpy as np
import pandas as pd
from datetime import date, timedelta
from myra_app.constants import DB_DIR
from myra_app.librarian_core import LibrarianCore
from myra_app.db.bulk_loader import (
    load_ohlcv_for_universe,
    rows_for_symbol,
    COLUMNS_13,
)

logger = logging.getLogger(__name__)

# Weights feeding the Spring `spring_score` (NOT `e["quality"]` — for Spring,
# `quality` comes from `_event_quality("Spring", ...)` = del/75*50 + rec/5*50,
# a separate formula these weights do not control). Four base weights scale the
# four component scores (summing to 90), the two bonuses are added on top, and
# `_compute_spring_score` clamps the total to [0, 100].
#
# Calibration attempted 2026-08 via tools/calibrate_wyckoff_weights.py
# (random search, 400 symbols / 12 scan dates / 800 combos, seed 42):
# ABANDONED. On the fresh dataset no candidate passed the out-of-sample
# VALIDATION gate (best-on-train VAL Q5-Q1 -2.14% < this default's +11.21%).
# Re-verified 2026-08-29 on the full dataset (same params): outcome identical —
# best-on-train VAL Q5-Q1 -2.14% (gap -13.35%) vs default +11.21%, so the
# shipping weights are still optimal. These remain the shipping weights.
DEFAULT_SPRING_WEIGHTS = {
    "delivery_absorption": 30,
    "lower_wick": 30,
    "close_location": 20,
    "grab_depth": 10,
    "equal_low_bonus": 10,
    "two_candle_bonus": 5,
}


class WyckoffAutomaton:
    _bulk_data = None
    _BULK_COLUMNS = COLUMNS_13
    # Class-level alias so both `WyckoffAutomaton.DEFAULT_SPRING_WEIGHTS` and the
    # module-level constant refer to the same dict.
    DEFAULT_SPRING_WEIGHTS = DEFAULT_SPRING_WEIGHTS

    def __init__(
        self,
