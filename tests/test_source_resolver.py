"""Phase 0 tests — cost-aware fallback resolver."""

from __future__ import annotations

import time

from myra_app.data_sources.cache import TtlCache
from myra_app.data_sources.registry import SourceRegistry, SourceSpec
from myra_app.data_sources.resolver import resolve, resolve_many


def _reg() -> SourceRegistry:
    return SourceRegistry(
        [
            SourceSpec("fast", frozenset({"roe", "mcap"}), cost="fast", timeout_s=2),
            SourceSpec("slow", frozenset({"roe"}), cost="slow", timeout_s=2),
        ]
    )


def test_falls_back_when_fast_source_fails(tmp_path):
    reg = _reg()
    calls = {"fast": 0}

    def fast(symbol):
        calls["fast"] += 1
        raise RuntimeError("rate limited")

    def slow(symbol):
        return {"roe": 12.5}

    out = resolve("roe", "X", {"fast": fast, "slow": slow}, reg)
    assert out.ok and out.source == "slow" and out.values["roe"] == 12.5
    assert calls["fast"] == 1


def test_first_successful_source_wins():
    reg = _reg()
    out = resolve(
        "roe",
        "X",
        {"fast": lambda s: {"roe": 8.0}, "slow": lambda s: {"roe": 99.0}},
        reg,
    )
    assert out.ok and out.source == "fast" and out.values["roe"] == 8.0


def test_timeout_is_treated_as_failure_and_falls_back():
    reg = _reg()

    def slow_hang(symbol):
        time.sleep(3)
        return {"roe": 1.0}

    out = resolve(
        "roe",
        "X",
        {"fast": slow_hang, "slow": lambda s: {"roe": 7.0}},
        reg,
        cache=None,
    )
    # fast has timeout_s=2 in _reg; 3s sleep must be abandoned and 'slow' wins.
    assert out.ok and out.source == "slow"


def test_stale_cache_used_when_all_sources_fail(tmp_path):
    reg = _reg()
    cache = TtlCache(db_path=str(tmp_path / "cache.db"))

    def fast(symbol):
        return {"roe": 11.0}

    first = resolve(
        "roe",
        "X",
        {"fast": fast, "slow": lambda s: {}},
        reg,
        cache=cache,
        cache_base="sym:X",
    )
    assert first.ok

    # Now every source fails AND its cache entry is considered expired, so the
    # stale copy (written above) must be used as the last resort.
    reg2 = SourceRegistry(
        [
            SourceSpec("fast", frozenset({"roe"}), cost="fast", timeout_s=2, ttl_s=0),
            SourceSpec("slow", frozenset({"roe"}), cost="slow", timeout_s=2, ttl_s=0),
        ]
    )

    def boom(symbol):
        raise RuntimeError("down")

    stale = resolve(
        "roe",
        "X",
        {"fast": boom, "slow": boom},
        reg2,
        cache=cache,
        cache_base="sym:X",
    )
    assert stale.ok and stale.stale and stale.values["roe"] == 11.0


def test_no_source_available_returns_not_ok():
    reg = SourceRegistry()
    out = resolve("roe", "X", {}, reg)
    assert out.ok is False and out.values == {}


def test_resolve_many_merges_and_attributes():
    reg = SourceRegistry(
        [
            SourceSpec("fast", frozenset({"mcap"}), cost="fast"),
            SourceSpec("slow", frozenset({"roe"}), cost="slow"),
        ]
    )
    values, provenance = resolve_many(
        ["mcap", "roe"],
        "X",
        {"fast": lambda s: {"mcap": 1e12}, "slow": lambda s: {"roe": 9.0}},
        reg,
    )
    assert values == {"mcap": 1e12, "roe": 9.0}
    assert provenance == {"mcap": "fast", "roe": "slow"}
