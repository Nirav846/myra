"""
Random Control baseline strategy.

Phase 1 Task 2 of the backtest refactor.

Purpose
-------
``RandomControl`` is a deliberate *control* signal — it produces uniform
random scores per (date, symbol) pair. It serves two roles:

  1. Sanity check for the harness: if the engine can't even run with
     random scores, real signals won't work either.
  2. Baseline for significance testing: any future real strategy must
     beat random over many dates, otherwise it is no better than chance.

Design constraints
------------------
* **Reproducible**: same ``(date, universe)`` ⇒ identical scores across
  runs and processes. We use a local ``random.Random`` instance seeded
  from the constructor seed XORed with the date ordinal. We do NOT
  touch Python's global ``random`` module state.
* **Per-date determinism**: a different date always produces a different
  score sequence (the seed changes), but the same date is bit-stable.
* **Stateless**: no instance state is mutated across calls; two
  ``RandomControl()`` instances configured identically are interchangeable.
* **Protocol-compatible**: exposes ``requires_delivery = False`` and a
  ``score(date, universe, conn) -> pd.Series`` method, matching the
  ``SignalFunction`` protocol in ``myra_app.backtest_engine``.

Performance notes
-----------------
* We use ``random.Random`` (Mersenne Twister) instead of NumPy because
  the score count is small (universe-sized, typically <2k symbols) and
  the Python RNG is sufficient and dependency-free.
* No DB access — ``conn`` is accepted for protocol conformance only.
"""
from __future__ import annotations

import random
import sqlite3
from typing import Optional

import pandas as pd


class RandomControl:
    """Random control signal — uniform random scores per (date, universe).

    The RNG is seeded deterministically from a fixed base seed combined
    with the date's ordinal so:

      * Same date, same universe  → identical scores (reproducible).
      * Different date             → uncorrelated scores.
      * Different universe on the same date → scores shift accordingly.

    Attributes
    ----------
    seed : int
        Base seed. Default 42 (matches the spec).
    requires_delivery : bool
        Always False — random does not need delivery data.
    """

    requires_delivery: bool = False

    def __init__(self, seed: int = 42) -> None:
        self.seed: int = int(seed)

    def score(
        self,
        date: pd.Timestamp,
        universe: list[str],
        conn: Optional[sqlite3.Connection] = None,
    ) -> pd.Series:
        """Return a uniform random score for every symbol in ``universe``.

        Parameters
        ----------
        date : pd.Timestamp
            The signal date. Used to perturb the seed for per-date
            determinism.
        universe : list[str]
            Candidate symbols. The returned Series is indexed by these
            symbols in the same order.
        conn : sqlite3.Connection | None
            Accepted for ``SignalFunction`` protocol conformance. Random
            does not need DB access; the argument is ignored.

        Returns
        -------
        pd.Series
            Float values in [0, 1), indexed by symbol (name="symbol").
        """
        if not universe:
            return pd.Series(dtype=float, name="symbol")

        # Combine base seed with the date ordinal so each calendar day
        # gets a distinct but reproducible RNG state. ``.toordinal()``
        # is int → fast and stable across Python versions.
        ts = pd.Timestamp(date)
        day_seed = (self.seed + ts.toordinal()) & 0x7FFFFFFF

        # Local Random instance — does NOT touch the global random state.
        rng = random.Random(day_seed)
        scores = [rng.random() for _ in range(len(universe))]
        return pd.Series(scores, index=pd.Index(universe, name="symbol"))


# ──────────────────────────────────────────────────────────────────────────────
# Registry hook-up.
# ──────────────────────────────────────────────────────────────────────────────
#
# Importing this module re-binds the ``random`` key in
# ``myra_app.backtest_engine.SIGNAL_REGISTRY`` to ``RandomControl``,
# replacing the legacy ``RandomSignal`` stub. Existing tests that import
# ``RandomSignal`` directly continue to work because the class is left
# in place under its old name — we only re-point the registry.
#
# We do the rebind at import time so that any code calling
# ``run_backtest(config=BacktestConfig(signal='random'))`` automatically
# picks up the new strategy without further wiring.
def _register() -> None:
    """Replace the legacy 'random' registry entry with RandomControl."""
    from myra_app.backtest_engine import SIGNAL_REGISTRY

    SIGNAL_REGISTRY["random"] = RandomControl


_register()
