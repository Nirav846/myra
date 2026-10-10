"""Cost-aware, health-tracked source registry for the resilient enrichment layer.

This is a *replacement* for the inert :class:`myra_app.data_sources.base.SourceManager`
(whose ``get_available_sources()`` is never consulted).  It is deliberately small and
usable standalone so the resolver and tests do not need a DB.

Concepts
--------
* ``SourceSpec``  — static description of a data source: what capabilities it can
  provide, how expensive it is (``fast``/``medium``/``slow``), its default priority,
  timeout, rate limit and cache TTL.
* ``SourceResult`` — the outcome of one fetch attempt.
* ``SourceRegistry`` — holds specs + live health (failure streak, cooldown).  A
  cooled-down source is skipped by the resolver instead of being hammered.

See ``docs/ENRICHMENT_PIPELINE_PLAN.md`` §4.1/§4.2.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Iterable, Optional

# Lower rank == tried first.  Cost is about latency/politeness, not quality.
COST_RANK = {"fast": 0, "medium": 1, "slow": 2}


def cost_rank(cost: str) -> int:
    return COST_RANK.get(cost, 1)


@dataclass(frozen=True)
class SourceSpec:
    """Static description of a data source."""

    name: str
    capabilities: frozenset[str]
    cost: str = "medium"
    priority: int = 0
    timeout_s: float = 15.0
    rate_per_sec: float = 2.0
    ttl_s: int = 3600
    is_rate_limit: bool = False  # hint: failures are usually rate limits

    @property
    def rank(self) -> int:
        return cost_rank(self.cost)


@dataclass
class SourceResult:
    """Outcome of a single source fetch (or cache read)."""

    ok: bool
    source: Optional[str]
    values: dict = field(default_factory=dict)
    error: Optional[str] = None
    elapsed_s: float = 0.0
    from_cache: bool = False
    stale: bool = False

    def has(self, capability: str) -> bool:
        return self.values.get(capability) is not None


class SourceRegistry:
    """Holds ``SourceSpec`` objects and tracks per-source availability."""

    #: Cooldowns (seconds) after a failure.
    RATE_LIMIT_COOLDOWN = 3600
    GENERIC_COOLDOWN = 600
    #: A source that keeps returning nothing (no exception) is only cooled down
    #: after this many *consecutive* empty results.  A source that returns data
    #: but simply lacks one capability is never penalised — cooling it down would
    #: starve the same source for the other capabilities resolved in the pass.
    EMPTY_FAIL_THRESHOLD = 5

    def __init__(self, specs: Optional[Iterable[SourceSpec]] = None):
        self._lock = threading.RLock()
        self._specs: dict[str, SourceSpec] = {}
        self._health: dict[str, dict] = {}
        if specs:
            for spec in specs:
                self.register(spec)

    # -- registration ------------------------------------------------------

    def register(self, spec: SourceSpec) -> None:
        with self._lock:
            self._specs[spec.name] = spec
            self._health.setdefault(
                spec.name,
                {
                    "fail_streak": 0,
                    "empty_streak": 0,
                    "cooldown_until": 0.0,
                    "last_ok": None,
                },
            )

    def spec(self, name: str) -> Optional[SourceSpec]:
        return self._specs.get(name)

    def names(self) -> list[str]:
        return list(self._specs)

    # -- candidate selection ----------------------------------------------

    def is_available(self, name: str, now: Optional[float] = None) -> bool:
        now = time.time() if now is None else now
        with self._lock:
            entry = self._health.get(name)
            return entry is None or entry["cooldown_until"] <= now

    def candidates(self, capability: str) -> list[SourceSpec]:
        """Specs providing ``capability``, cheapest first, cooled-down skipped."""
        now = time.time()
        with self._lock:
            out = [
                spec
                for spec in self._specs.values()
                if capability in spec.capabilities
                and self._health.get(spec.name, {}).get("cooldown_until", 0.0) <= now
            ]
        out.sort(key=lambda s: (s.rank, s.priority, s.name))
        return out

    def candidates_any(self, capabilities: Iterable[str]) -> list[SourceSpec]:
        wanted = set(capabilities)
        now = time.time()
        with self._lock:
            out = [
                spec
                for spec in self._specs.values()
                if spec.capabilities & wanted
                and self._health.get(spec.name, {}).get("cooldown_until", 0.0) <= now
            ]
        out.sort(key=lambda s: (s.rank, s.priority, s.name))
        return out

    def specs_for(self, capability: str) -> list[SourceSpec]:
        """Every spec providing ``capability``, **ignoring cooldown**.

        Used by the resolver's stale-cache last resort: a source in cooldown may
        still have a perfectly good cached value worth serving.
        """
        with self._lock:
            out = [
                spec for spec in self._specs.values() if capability in spec.capabilities
            ]
        out.sort(key=lambda s: (s.rank, s.priority, s.name))
        return out

    # -- health ------------------------------------------------------------

    def record_success(self, name: str) -> None:
        with self._lock:
            entry = self._health.setdefault(
                name,
                {
                    "fail_streak": 0,
                    "empty_streak": 0,
                    "cooldown_until": 0.0,
                    "last_ok": None,
                },
            )
            entry["fail_streak"] = 0
            entry["empty_streak"] = 0
            entry["cooldown_until"] = 0.0
            entry["last_ok"] = time.time()

    def record_failure(self, name: str, is_rate_limit: bool = False) -> None:
        with self._lock:
            entry = self._health.setdefault(
                name,
                {
                    "fail_streak": 0,
                    "empty_streak": 0,
                    "cooldown_until": 0.0,
                    "last_ok": None,
                },
            )
            entry["fail_streak"] = entry.get("fail_streak", 0) + 1
            cooldown = (
                self.RATE_LIMIT_COOLDOWN if is_rate_limit else self.GENERIC_COOLDOWN
            )
            entry["cooldown_until"] = time.time() + cooldown

    def record_empty(self, name: str) -> None:
        """A source returned an empty result (no data, no exception).
        Increments a soft streak and only applies a cooldown once it reaches
        :attr:`EMPTY_FAIL_THRESHOLD`.  ``None``/missing-capability results must
        NOT call this — they are not failures.
        """
        with self._lock:
            entry = self._health.setdefault(
                name,
                {
                    "fail_streak": 0,
                    "empty_streak": 0,
                    "cooldown_until": 0.0,
                    "last_ok": None,
                },
            )
            entry["empty_streak"] = entry.get("empty_streak", 0) + 1
            if entry["empty_streak"] >= self.EMPTY_FAIL_THRESHOLD:
                entry["cooldown_until"] = time.time() + self.GENERIC_COOLDOWN

    def health(self, name: str) -> dict:
        with self._lock:
            return dict(
                self._health.get(
                    name,
                    {
                        "fail_streak": 0,
                        "empty_streak": 0,
                        "cooldown_until": 0.0,
                        "last_ok": None,
                    },
                )
            )

    def snapshot(self) -> dict[str, dict]:
        with self._lock:
            return {name: dict(entry) for name, entry in self._health.items()}

    def restore_health(self, snapshot: Optional[dict]) -> None:
        """Merge a previously persisted health ``snapshot`` back in.

        Only known sources are restored, and only known health keys are copied,
        so a stale/foreign blob can never inject surprises.  Cooldowns are
        absolute epoch times, so restoring them after a restart is meaningful.
        """
        if not snapshot:
            return
        with self._lock:
            for name, entry in snapshot.items():
                if name not in self._specs or not isinstance(entry, dict):
                    continue
                base = self._health.setdefault(
                    name,
                    {
                        "fail_streak": 0,
                        "empty_streak": 0,
                        "cooldown_until": 0.0,
                        "last_ok": None,
                    },
                )
                for key in ("fail_streak", "empty_streak", "cooldown_until", "last_ok"):
                    if key in entry:
                        base[key] = entry[key]
