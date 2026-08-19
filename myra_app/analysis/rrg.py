"""
RRG (Relative Rotation Graphs) — core computation engine.

Reads EOD2 CSV data, computes RS-Ratio and RS-Momentum for NSE indices
against a benchmark, and classifies each into quadrants (Leading / Weakening /
Lagging / Improving).  Results are cached and invalidated when meta.json
changes.
"""

import datetime
import json
import logging
import os
import time
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────
DATA_FOLDER = r"D:\01screener\Myra\eod2\src\eod2_data\daily"
META_PATH = r"D:\01screener\Myra\eod2\src\eod2_data\meta.json"
MAX_HISTORY_DAYS = 365 * 5
CACHE_TTL_HOURS = 6

# Sector keywords used to filter index files from the full CSV listing.
# Files whose stem (lowercased) contains any of these are treated as indices.
_INDEX_KEYWORDS = [
    "nifty", "banknifty", "sensex", "bankex", "nifty next",
    "nifty midcap", "nifty smallcap", "nifty alpha", "nifty beta",
    "nifty quality", "nifty momentum", "nifty value", "nifty low vol",
    "nifty high beta", "nifty equal weight", "nifty dividend",
    "nifty growth", "nifty top", "nifty cpse", "nifty mnc",
    "nifty pse", "nifty psu", "nifty it", "nifty pharma", "nifty auto",
    "nifty bank", "nifty energy", "nifty metal", "nifty realty",
    "nifty fmcg", "nifty media", "nifty infra", "nifty commodities",
    "nifty consumption", "nifty housing", "nifty rural", "nifty ipo",
    "nifty services", "nifty financial", "nifty private",
    "nifty consumer", "nifty healthcare", "nifty hospital",
    "nifty telecom", "nifty digital", "nifty ev", "nifty mobility",
    "nifty capital", "nifty cement", "nifty chemicals",
    "nifty construction", "nifty insurance", "nifty nbfc",
    "nifty sugar", "nifty defence", "nifty railway", "nifty tourism",
    "nifty india", "nifty shariah", "nifty reit", "nifty sme",
    "nifty total market", "nifty largemidcap", "nifty microcap",
    "nifty midsmall", "nifty small finance",
]

# Fallback well-known indices (used only if dynamic discovery returns nothing).
_FALLBACK_INDICES = [
    "nifty 50", "nifty bank", "nifty it", "nifty pharma", "nifty auto",
    "nifty metal", "nifty realty", "nifty fmcg", "nifty energy",
    "nifty financial services", "nifty private bank", "nifty psu bank",
    "nifty midcap 50", "nifty midcap 100", "nifty midcap 150",
    "nifty smallcap 50", "nifty smallcap 100", "nifty smallcap 250",
    "nifty next 50", "nifty next 100", "nifty 500", "nifty 200",
    "nifty 100",
]


# ── Helpers ──────────────────────────────────────────────────────────────────
def _pretty_label(stem: str) -> str:
    """Convert a CSV filename stem to a human-readable label."""
    return stem.replace("-", " ").replace("_", " ").title()


def _is_index_file(stem: str) -> bool:
    """Return True if the CSV stem looks like an NSE index."""
    low = stem.lower()
    return any(kw in low for kw in _INDEX_KEYWORDS)


def _load_csv(index_id: str) -> Optional[pd.DataFrame]:
    """Load a CSV from DATA_FOLDER, return DataFrame or None."""
    path = Path(DATA_FOLDER) / f"{index_id}.csv"
    if not path.exists():
        logger.warning("CSV not found: %s", path)
        return None
    try:
        df = pd.read_csv(path, parse_dates=["Date"])
        df = df.sort_values("Date").reset_index(drop=True)
        return df
    except Exception as exc:
        logger.error("Failed to load %s: %s", path, exc)
        return None


def _resample_weekly(df: pd.DataFrame) -> pd.DataFrame:
    """Resample daily OHLCV to weekly (last price of each week)."""
    df = df.set_index("Date")
    weekly = df.resample("W-FRI").last().dropna(subset=["Close"])
    weekly = weekly.reset_index()
    return weekly


def _get_last_update() -> Optional[datetime.datetime]:
    """Read meta.json and return lastUpdate as a datetime."""
    try:
        with open(META_PATH, "r", encoding="utf-8") as f:
            meta = json.load(f)
        raw = meta.get("lastUpdate", "")
        if raw:
            return datetime.datetime.strptime(raw, "%d %B %Y %H:%M:%S")
    except Exception as exc:
        logger.debug("Cannot read meta.json: %s", exc)
    return None


# ── Cache ────────────────────────────────────────────────────────────────────
class _RRGCache:
    """Simple in-memory cache with meta.json invalidation + TTL fallback."""

    def __init__(self):
        self._data: Optional[Dict] = None
        self._timestamp: float = 0.0
        self._meta_ts: Optional[str] = None

    def get(self) -> Optional[Dict]:
        if self._data is None:
            return None
        # Check meta invalidation
        last_update = _get_last_update()
        current_meta_ts = str(last_update) if last_update else None
        if current_meta_ts and current_meta_ts != self._meta_ts:
            logger.info("Cache invalidated: meta.json changed")
            self._data = None
            return None
        # Check TTL
        age_hours = (time.time() - self._timestamp) / 3600
        if age_hours > CACHE_TTL_HOURS:
            logger.info("Cache expired (age %.1fh)", age_hours)
            self._data = None
            return None
        return self._data

    def set(self, data: Dict) -> None:
        self._data = data
        self._timestamp = time.time()
        last_update = _get_last_update()
        self._meta_ts = str(last_update) if last_update else None


_rrg_cache = _RRGCache()


# ── Public API ───────────────────────────────────────────────────────────────
def discover_indices() -> List[Dict]:
    """Scan DATA_FOLDER for index CSVs. Returns list of {id, label}."""
    data_dir = Path(DATA_FOLDER)
    if not data_dir.exists():
        logger.error("Data folder not found: %s", data_dir)
        return []

    indices = []
    seen = set()
    for csv_file in sorted(data_dir.glob("*.csv")):
        stem = csv_file.stem
        if stem in seen:
            continue
        if _is_index_file(stem):
            seen.add(stem)
            indices.append({"id": stem, "label": _pretty_label(stem)})

    if not indices:
        logger.warning("Dynamic discovery found nothing; using fallback list")
        for name in _FALLBACK_INDICES:
            stem = name.replace(" ", " ").strip()
            indices.append({"id": stem, "label": _pretty_label(stem)})

    logger.info("Discovered %d indices", len(indices))
    return indices


def compute_rrg(
    benchmark_id: str,
    sector_ids: List[str],
    timeframe: str = "weekly",
    trail: int = 8,
) -> Dict:
    """
    Compute RRG data for given sectors against a benchmark.

    Returns:
        {
            "current": [{"id", "label", "x", "y", "quadrant"}],
            "trails":  {"sector_id": [[x, y], ...]},
            "meta":    {"timeframe", "trail", "benchmark", "date"}
        }
    """
    # Load benchmark
    bench_df = _load_csv(benchmark_id)
    if bench_df is None or len(bench_df) < 60:
        raise ValueError(f"Benchmark '{benchmark_id}' not found or too short")

    # Slice to MAX_HISTORY_DAYS
    cutoff = bench_df["Date"].max() - pd.Timedelta(days=MAX_HISTORY_DAYS)
    bench_df = bench_df[bench_df["Date"] >= cutoff].copy()

    if timeframe == "weekly":
        bench_df = _resample_weekly(bench_df)

    bench_prices = bench_df["Close"].values
    bench_dates = bench_df["Date"].values

    # Reference period for momentum (52 weeks or 252 days)
    ref_period = 52 if timeframe == "weekly" else 252

    current_x = {}
    current_y = {}
    trails_data: Dict[str, List[List[float]]] = {}

    for sector_id in sector_ids:
        sec_df = _load_csv(sector_id)
        if sec_df is None or len(sec_df) < 60:
            logger.warning("Skipping %s: not found or too short", sector_id)
            continue

        sec_df = sec_df[sec_df["Date"] >= cutoff].copy()
        if timeframe == "weekly":
            sec_df = _resample_weekly(sec_df)

        # Align on common dates
        common_dates = set(bench_df["Date"].values) & set(sec_df["Date"].values)
        if len(common_dates) < ref_period + trail:
            logger.warning("Skipping %s: insufficient overlapping data", sector_id)
            continue

        bench_aligned = bench_df[bench_df["Date"].isin(common_dates)].sort_values("Date").reset_index(drop=True)
        sec_aligned = sec_df[sec_df["Date"].isin(common_dates)].sort_values("Date").reset_index(drop=True)

        bench_close = bench_aligned["Close"].values
        sec_close = sec_aligned["Close"].values

        # RS-Ratio: sector price / benchmark price (normalised to 100 at start)
        rs_raw = sec_close / bench_close
        rs_ratio = (rs_raw / rs_raw[0]) * 100

        # RS-Momentum: (current RS / RS N periods ago) - 1
        n = min(ref_period, len(rs_ratio) - 1)
        rs_momentum = np.zeros(len(rs_ratio))
        rs_momentum[n:] = (rs_ratio[n:] / rs_ratio[:-n]) - 1

        # Normalise to z-scores across the last period will be done after
        # collecting all sectors; store raw values for now.
        current_x[sector_id] = float(rs_ratio[-1])
        current_y[sector_id] = float(rs_momentum[-1])

        # Trail: last `trail` points of (rs_ratio, rs_momentum)
        trail_start = max(0, len(rs_ratio) - trail)
        trails_data[sector_id] = [
            [float(rs_ratio[i]), float(rs_momentum[i])]
            for i in range(trail_start, len(rs_ratio))
        ]

    if not current_x:
        raise ValueError("No valid sectors found")

    # Normalise to z-scores
    x_vals = np.array(list(current_x.values()))
    y_vals = np.array(list(current_y.values()))
    x_mean, x_std = float(x_vals.mean()), float(x_vals.std()) or 1.0
    y_mean, y_std = float(y_vals.mean()), float(y_vals.std()) or 1.0

    # Build current list
    indices = discover_indices()
    label_map = {idx["id"]: idx["label"] for idx in indices}

    current = []
    for sector_id, raw_x in current_x.items():
        raw_y = current_y[sector_id]
        nx = (raw_x - x_mean) / x_std
        ny = (raw_y - y_mean) / y_std

        if nx > 0 and ny > 0:
            quadrant = "Leading"
        elif nx > 0 and ny < 0:
            quadrant = "Weakening"
        elif nx < 0 and ny < 0:
            quadrant = "Lagging"
        else:
            quadrant = "Improving"

        current.append({
            "id": sector_id,
            "label": label_map.get(sector_id, _pretty_label(sector_id)),
            "x": round(nx, 4),
            "y": round(ny, 4),
            "quadrant": quadrant,
        })

    # Normalise trails to same z-score scale
    norm_trails: Dict[str, List[List[float]]] = {}
    for sector_id, pts in trails_data.items():
        norm_trails[sector_id] = [
            [round((p[0] - x_mean) / x_std, 4), round((p[1] - y_mean) / y_std, 4)]
            for p in pts
        ]

    last_date = str(bench_dates[-1])[:10] if len(bench_dates) > 0 else ""

    return {
        "current": current,
        "trails": norm_trails,
        "meta": {
            "timeframe": timeframe,
            "trail": trail,
            "benchmark": benchmark_id,
            "date": last_date,
        },
    }


def get_rrg_cached(
    benchmark_id: str,
    sector_ids: Optional[List[str]],
    timeframe: str = "weekly",
    trail: int = 8,
    refresh: bool = False,
) -> Dict:
    """Return RRG data, using cache when possible.

    If *refresh* is True, bypass the cache entirely and recompute.
    The cache key includes benchmark, timeframe, trail, and the sector list
    so that different sector selections are cached separately.
    """
    # Build a stable cache key from the sector list
    sector_key = tuple(sorted(sector_ids)) if sector_ids else None

    if not refresh:
        cached = _rrg_cache.get()
        if cached is not None:
            meta = cached.get("meta", {})
            cached_sectors = meta.get("sectors")
            if (
                meta.get("benchmark") == benchmark_id
                and meta.get("timeframe") == timeframe
                and meta.get("trail") == trail
                and cached_sectors == sector_key
            ):
                return cached

    if sector_ids is None:
        indices = discover_indices()
        sector_ids = [idx["id"] for idx in indices]
        sector_key = tuple(sorted(sector_ids))

    result = compute_rrg(benchmark_id, sector_ids, timeframe, trail)
    # Store the sector key in meta so cache lookup can match it
    result["meta"]["sectors"] = sector_key
    _rrg_cache.set(result)
    return result
