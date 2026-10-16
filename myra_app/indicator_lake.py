"""Cheap, read-only status probe for the indicator lake.

The turbo/CLI screening path reads precomputed per-symbol indicator parquet
files from ``<repo>/data/indicators/precomputed/<SYMBOL>.parquet``. That
directory is produced by ``LibrarianIntelligenceMixin.update_indicator_history``
and consumed by ``precompute_indicators`` via ``load_universe``.

Two rules shape this module:

1. **Resolution happens at call time.** ``constants.DATA_DIR`` is read inside
   the functions, never bound at import. A caller (or a test) that repoints it
   must not end up reading — or worse, writing — the live lake. This mirrors the
   call-time resolution ``data_readiness._db`` already uses for ``DB_DIR``.

2. **Existence is not validity.** A directory full of zero-byte or malformed
   parquet files is indistinguishable from a healthy lake if all you do is
   ``glob``. So the probe lists the directory *and* opens a bounded sample to
   confirm the artifacts still satisfy the contract the consumer enforces
   (a ``symbol`` column plus a date-bearing index that ``reset_index()`` turns
   into a ``date`` column).

Nothing here writes, creates directories, or imports the pandas stack unless a
parquet file actually has to be opened — the common "lake is empty" case stays
free.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Any, Optional

logger = logging.getLogger(__name__)

#: The strategy namespace the producer writes and the turbo scorer reads.
LAKE_STRATEGY_ID = "precomputed"

#: Columns the consumer needs after ``reset_index()``.
REQUIRED_COLUMNS = ("symbol", "date")

#: How many artifacts are opened to confirm the contract still holds. A sample,
#: not an audit — deliberately small so the probe stays cheap on a warm lake.
DEFAULT_SAMPLE_LIMIT = 3


# ── Paths ────────────────────────────────────────────────────────────────────


def indicators_root() -> str:
    """``<repo>/data/indicators``, resolved against the *current* DATA_DIR."""
    from myra_app import constants

    return os.path.join(constants.DATA_DIR, "indicators")


def strategy_dir(strategy_id: str) -> str:
    """Directory holding one strategy's artifact namespace. Never creates it."""
    return os.path.join(indicators_root(), strategy_id.lower())


def lake_dir() -> str:
    """The indicator lake the screening path depends on."""
    return strategy_dir(LAKE_STRATEGY_ID)


def symbol_path(strategy_id: str, symbol: str) -> str:
    """Canonical artifact path for one symbol. Pure path arithmetic."""
    return os.path.join(strategy_dir(strategy_id), f"{symbol.upper()}.parquet")


# ── Listing ──────────────────────────────────────────────────────────────────


def _scan(strategy_id: str) -> Optional[list[os.DirEntry]]:
    """List artifact entries, or ``None`` when the directory cannot be read.

    ``FileNotFoundError`` is a legitimate "no lake yet" and yields an empty
    list; any other OS error is reported as ``None`` so the caller can tell
    "absent" from "unreadable".
    """
    directory = strategy_dir(strategy_id)
    try:
        with os.scandir(directory) as it:
            return sorted(
                (e for e in it if e.is_file() and e.name.endswith(".parquet")),
                key=lambda e: e.name,
            )
    except (FileNotFoundError, NotADirectoryError):
        return []
    except OSError as exc:
        logger.debug("indicator lake: cannot list %s (%s)", directory, exc)
        return None


def list_artifacts(strategy_id: str = LAKE_STRATEGY_ID) -> list[str]:
    """Artifact filenames present, sorted. Empty when there is no lake."""
    entries = _scan(strategy_id)
    return [e.name for e in entries] if entries else []


def count(strategy_id: str = LAKE_STRATEGY_ID) -> int:
    return len(list_artifacts(strategy_id))


def has_artifact(symbol: str, strategy_id: str = LAKE_STRATEGY_ID) -> bool:
    return os.path.isfile(symbol_path(strategy_id, symbol))


def artifact_mtime(symbol: str, strategy_id: str = LAKE_STRATEGY_ID) -> Optional[float]:
    """Modification time of one artifact, or ``None`` when absent."""
    path = symbol_path(strategy_id, symbol)
    try:
        return os.path.getmtime(path)
    except OSError:
        return None


def is_fresh(
    symbol: str, reference_date: Optional[str], strategy_id: str = LAKE_STRATEGY_ID
) -> bool:
    """True when an artifact exists and is at least as new as ``reference_date``.

    The freshness gate a rebuild uses to skip work that is already done: an
    artifact written on or after the newest stored market session already
    reflects that session, so recomputing it adds nothing. With no reference
    date there is nothing to compare against and the answer is ``False``
    ("cannot prove it is fresh"), never a hopeful ``True``.
    """
    if not reference_date:
        return False
    mtime = artifact_mtime(symbol, strategy_id)
    if mtime is None:
        return False
    return datetime.fromtimestamp(mtime).date().isoformat() >= reference_date


# ── Contract validation ──────────────────────────────────────────────────────


def validate_artifact(path: str) -> Optional[str]:
    """Return ``None`` when an artifact satisfies the consumer contract.

    Otherwise return a short reason. This is the check that separates "a file
    is here" from "the screening path can actually use it".
    """
    try:
        import pandas as pd  # imported lazily: only reachable if a file exists

        df = pd.read_parquet(path)
    except Exception as exc:  # noqa: BLE001 - any read failure is "unusable"
        return f"unreadable ({type(exc).__name__})"

    if df is None or df.empty:
        return "empty"
    if "symbol" not in df.columns:
        return "no 'symbol' column"
    row = df.reset_index()
    if "date" not in row.columns:
        return "no date-bearing index"
    return None


# ── Status ───────────────────────────────────────────────────────────────────


def status(
    strategy_id: str = LAKE_STRATEGY_ID,
    reference_date: Optional[str] = None,
    sample_limit: int = DEFAULT_SAMPLE_LIMIT,
) -> dict[str, Any]:
    """Assess the lake without writing anything.

    ``reference_date`` is the newest market session the lake is expected to
    represent (normally ``technical_data``'s MAX(date)). When supplied, a lake
    whose newest artifact predates it is reported ``stale`` — a stale lake loads
    perfectly well and would otherwise silently produce stale rankings.

    ``reason`` is one of: ``missing``, ``unreadable``, ``invalid_artifact``,
    ``stale``, ``ok``.
    """
    out: dict[str, Any] = {
        "directory": strategy_dir(strategy_id),
        "present": False,
        "count": 0,
        "freshest": None,
        "stale": False,
        "stale_vs": reference_date,
        "sampled": 0,
        "invalid": [],
        "valid": False,
        "error": None,
        "reason": "missing",
    }

    entries = _scan(strategy_id)
    if entries is None:
        out["error"] = "indicator lake directory could not be read"
        out["reason"] = "unreadable"
        return out
    if not entries:
        return out

    out["present"] = True
    out["count"] = len(entries)

    newest = 0.0
    for entry in entries:
        try:
            newest = max(newest, entry.stat().st_mtime)
        except OSError:  # a file that vanished mid-scan must not abort the probe
            continue
    if newest:
        out["freshest"] = datetime.fromtimestamp(newest).isoformat(timespec="seconds")
        if reference_date:
            out["stale"] = (
                datetime.fromtimestamp(newest).date().isoformat() < reference_date
            )

    if sample_limit and sample_limit > 0:
        for entry in entries[:sample_limit]:
            out["sampled"] += 1
            reason = validate_artifact(entry.path)
            if reason:
                out["invalid"].append({"file": entry.name, "reason": reason})

    if out["invalid"]:
        out["reason"] = "invalid_artifact"
    elif out["stale"]:
        out["reason"] = "stale"
    else:
        out["reason"] = "ok"
    out["valid"] = out["reason"] == "ok"
    return out


__all__ = [
    "LAKE_STRATEGY_ID",
    "REQUIRED_COLUMNS",
    "DEFAULT_SAMPLE_LIMIT",
    "indicators_root",
    "strategy_dir",
    "lake_dir",
    "symbol_path",
    "list_artifacts",
    "count",
    "has_artifact",
    "artifact_mtime",
    "validate_artifact",
    "status",
]
