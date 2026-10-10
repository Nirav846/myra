"""Phase 0 tests — SQLite-backed TTL cache (never raises)."""

from __future__ import annotations

from myra_app.data_sources.cache import TtlCache


def test_set_and_get_roundtrip(tmp_path):
    cache = TtlCache(db_path=str(tmp_path / "cache.db"))
    cache.set("k", {"a": 1, "b": [1, 2]}, ttl_seconds=60)
    assert cache.get("k") == {"a": 1, "b": [1, 2]}


def test_expiry_returns_none_but_stale_still_available(tmp_path):
    cache = TtlCache(db_path=str(tmp_path / "cache.db"))
    cache.set("k", {"v": 2})
    # ttl=0 means "already expired".
    assert cache.get("k", ttl_seconds=0) is None
    # ...but the stale value is still readable as a last resort.
    assert cache.get_stale("k") == {"v": 2}


def test_missing_key_returns_none(tmp_path):
    cache = TtlCache(db_path=str(tmp_path / "cache.db"))
    assert cache.get("nope") is None
    assert cache.get_stale("nope") is None
    assert cache.age_seconds("nope") is None


def test_age_seconds_is_finite(tmp_path):
    cache = TtlCache(db_path=str(tmp_path / "cache.db"))
    cache.set("k", {"v": 1})
    age = cache.age_seconds("k")
    assert age is not None and 0 <= age < 60


def test_never_raises_on_bad_path(tmp_path):
    # A directory that does not exist -> connect fails -> degrade to miss/None.
    cache = TtlCache(db_path=str(tmp_path / "missing_dir" / "cache.db"))
    assert cache.get("k") is None
    cache.set("k", {"v": 1})  # must not raise
    assert cache.get_stale("k") is None
