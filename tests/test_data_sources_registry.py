"""Phase 0 tests — cost-aware source registry."""

from __future__ import annotations

from myra_app.data_sources.registry import SourceRegistry, SourceSpec


def _registry() -> SourceRegistry:
    return SourceRegistry(
        [
            SourceSpec("slow_src", frozenset({"roe"}), cost="slow", priority=0),
            SourceSpec("fast_src", frozenset({"roe", "mcap"}), cost="fast"),
            SourceSpec("mid_src", frozenset({"mcap"}), cost="medium"),
        ]
    )


def test_candidates_ordered_cheapest_first():
    reg = _registry()
    names = [s.name for s in reg.candidates("roe")]
    assert names == ["fast_src", "slow_src"]


def test_candidates_only_those_providing_capability():
    reg = _registry()
    assert [s.name for s in reg.candidates("mcap")] == ["fast_src", "mid_src"]


def test_cooldown_skips_failing_source():
    reg = _registry()
    reg.record_failure("fast_src", is_rate_limit=True)
    assert reg.is_available("fast_src") is False
    assert [s.name for s in reg.candidates("roe")] == ["slow_src"]
    # Success clears the cooldown.
    reg.record_success("fast_src")
    assert reg.is_available("fast_src") is True


def test_failure_streak_and_snapshot():
    reg = _registry()
    reg.record_failure("mid_src")
    reg.record_failure("mid_src")
    assert reg.health("mid_src")["fail_streak"] == 2
    assert "mid_src" in reg.snapshot()


def test_unknown_source_is_available_by_default():
    reg = _registry()
    assert reg.is_available("does-not-exist") is True


def test_record_empty_cools_only_after_threshold():
    reg = _registry()
    for _ in range(SourceRegistry.EMPTY_FAIL_THRESHOLD - 1):
        reg.record_empty("fast_src")
    # Below threshold: still available, no failure streak.
    assert reg.is_available("fast_src") is True
    assert reg.health("fast_src")["fail_streak"] == 0

    reg.record_empty("fast_src")  # reaches the threshold
    assert reg.is_available("fast_src") is False


def test_record_success_resets_empty_streak():
    reg = _registry()
    reg.record_empty("fast_src")
    reg.record_empty("fast_src")
    reg.record_success("fast_src")
    assert reg.health("fast_src")["empty_streak"] == 0
    assert reg.is_available("fast_src") is True


def test_restore_health_round_trips_known_keys():
    reg = _registry()
    reg.record_failure("mid_src")
    snapshot = reg.snapshot()

    fresh = _registry()
    fresh.restore_health(snapshot)
    assert fresh.health("mid_src")["fail_streak"] == 1
    assert fresh.is_available("mid_src") is False


def test_restore_health_ignores_unknown_sources_and_keys():
    reg = _registry()
    reg.restore_health(
        {
            "ghost_src": {"fail_streak": 99},
            "fast_src": {"fail_streak": 2, "bogus_key": "x"},
        }
    )
    assert "ghost_src" not in reg.snapshot()
    assert reg.health("fast_src")["fail_streak"] == 2
    assert "bogus_key" not in reg.health("fast_src")


def test_restore_health_none_is_noop():
    reg = _registry()
    reg.restore_health(None)
    assert reg.health("fast_src")["fail_streak"] == 0
