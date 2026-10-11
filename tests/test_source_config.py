"""Unit tests for the operator-editable source configuration.

The config lives in ``myra_app/data_sources/source_config.py`` (catalogue +
persisted overrides in the metadata table of the meta sidecar). These tests
exercise the defaults, the persisted update/reset round-trip, validation, and
the registry integration (specs built from config, disabled sources excluded).
"""

import os
import shutil

import pytest

from myra_app.data_sources import source_config as sc
from myra_app.data_sources.registry import SourceRegistry, SourceSpec


@pytest.fixture(autouse=True)
def _reset_config_cache():
    """The module caches the merged config; tests must not leak into each other."""
    sc.invalidate_cache()
    yield
    sc.invalidate_cache()


@pytest.fixture
def writable_meta_db(fixture_db_dir, tmp_path, monkeypatch):
    """Per-test copy of the meta fixture DB (see test_fundamental_sync_guard)."""
    src = os.path.join(fixture_db_dir, "myra_metadata.db")
    dst = tmp_path / "myra_metadata.db"
    shutil.copy2(src, dst)
    # LibrarianCore captures DB_DIR at import time; patch the module attribute.
    monkeypatch.setattr("myra_app.librarian_core.DB_DIR", str(tmp_path))
    yield str(dst)


# --------------------------------------------------------------------------- #
# Catalogue defaults
# --------------------------------------------------------------------------- #
def test_defaults_all_enabled():
    sources = sc.list_sources()
    assert [s["name"] for s in sources] == [
        "screener_in",
        "google_finance",
        "finology",
        "yfinance",
        "nse_shareholding",
        "morningstar",
    ]
    for entry in sources:
        assert entry["enabled"] is True
        assert entry["implemented"] in (True, False)
    assert sc.is_enabled("screener_in") is True


def test_unknown_source_is_enabled():
    # A source we don't know about must never be reported disabled.
    assert sc.is_enabled("some_future_source") is True


def test_finology_is_flagged_unimplemented():
    info = sc.get_source("finology")
    assert info["implemented"] is False
    assert info["disable_warning"]


def test_screener_in_carries_disable_warning():
    assert "only source" in (sc.get_source("screener_in")["disable_warning"] or "")


def test_screener_capabilities_match_adapter_no_eps():
    # Regression: the catalog once declared `eps` for screener_in, but the
    # adapter never returns it. That made the resolver route `eps` to Screener,
    # record it empty, and cool down the *primary* source. `eps` is yfinance's.
    assert "eps" not in sc.CATALOG["screener_in"].capabilities
    assert "eps" in sc.CATALOG["yfinance"].capabilities


# --------------------------------------------------------------------------- #
# Persisted overrides round-trip
# --------------------------------------------------------------------------- #
def test_update_and_reset_roundtrip(writable_meta_db):
    assert sc.is_enabled("screener_in") is True

    updated = sc.update_source("screener_in", {"enabled": False, "rate_per_sec": 0.2})
    assert updated["enabled"] is False
    assert updated["rate_per_sec"] == 0.2
    assert sc.is_enabled("screener_in") is False

    # A fresh read (cache dropped) must still see the persisted change.
    sc.invalidate_cache()
    assert sc.is_enabled("screener_in") is False

    reset = sc.reset_source("screener_in")
    assert reset["enabled"] is True
    assert reset["rate_per_sec"] == pytest.approx(
        sc.CATALOG["screener_in"].default_rate_per_sec
    )
    sc.invalidate_cache()
    assert sc.is_enabled("screener_in") is True


def test_update_validates_bounds(writable_meta_db):
    with pytest.raises(ValueError):
        sc.update_source("yfinance", {"rate_per_sec": -1})
    with pytest.raises(ValueError):
        sc.update_source("yfinance", {"ttl_s": "not-a-number"})
    with pytest.raises(ValueError):
        sc.update_source("yfinance", {"enabled": "yes"})
    with pytest.raises(ValueError):
        sc.update_source("yfinance", {})
    with pytest.raises(KeyError):
        sc.update_source("not_a_source", {"enabled": False})


def test_disabling_one_source_leaves_others_enabled(writable_meta_db):
    sc.update_source("morningstar", {"enabled": False})
    assert sc.is_enabled("morningstar") is False
    assert sc.is_enabled("yfinance") is True
    assert sc.is_enabled("screener_in") is True
    assert sc.disabled_names() == {"morningstar"}


def test_config_fails_open_when_db_is_absent(tmp_path, monkeypatch):
    # Point the metadata DB at a directory with no databases in it.
    monkeypatch.setattr("myra_app.librarian_core.DB_DIR", str(tmp_path))
    sc.invalidate_cache()
    assert sc.is_enabled("screener_in") is True
    assert sc.disabled_names() == set()
    assert len(sc.list_sources()) == 6


# --------------------------------------------------------------------------- #
# Write safety: an unreadable blob must not be clobbered
# --------------------------------------------------------------------------- #
def test_update_source_refuses_to_clobber_unreadable_config(
    writable_meta_db, monkeypatch
):
    # A real override exists...
    sc.update_source("morningstar", {"enabled": False})
    assert sc.is_enabled("morningstar") is False

    # ...then the blob becomes unreadable. The write must fail loudly rather than
    # persist `{yfinance: ...}` alone (which would drop morningstar's override).
    real_reader = sc._read_raw_ok
    monkeypatch.setattr(sc, "_read_raw_ok", lambda: ({}, False))
    with pytest.raises(RuntimeError):
        sc.update_source("yfinance", {"enabled": False})

    # Restore only our patch (monkeypatch.undo() would also drop the fixture's
    # DB_DIR redirect): the morningstar override survived intact.
    monkeypatch.setattr(sc, "_read_raw_ok", real_reader)
    sc.invalidate_cache()
    assert sc.is_enabled("morningstar") is False
    assert sc.is_enabled("yfinance") is True


def test_reset_source_refuses_to_clobber_unreadable_config(
    writable_meta_db, monkeypatch
):
    sc.update_source("morningstar", {"enabled": False})
    real_reader = sc._read_raw_ok
    monkeypatch.setattr(sc, "_read_raw_ok", lambda: ({}, False))
    with pytest.raises(RuntimeError):
        sc.reset_source("yfinance")
    monkeypatch.setattr(sc, "_read_raw_ok", real_reader)
    sc.invalidate_cache()
    assert sc.is_enabled("morningstar") is False


def test_failed_read_is_retried_not_cached_forever(monkeypatch):
    # A transient read failure must recover, not pin "all enabled" until restart.
    monkeypatch.setattr(sc, "_FAILURE_TTL_S", 0.0)
    calls = {"n": 0}

    def _flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            return {}, False
        return {"sources": {"screener_in": {"enabled": False}}}, True

    monkeypatch.setattr(sc, "_read_raw_ok", _flaky)
    sc.invalidate_cache()
    assert sc.is_enabled("screener_in") is True  # failed read -> fail open
    assert sc.is_enabled("screener_in") is False  # TTL elapsed -> retried
    assert calls["n"] == 2


# --------------------------------------------------------------------------- #
# Registry integration
# --------------------------------------------------------------------------- #
def test_build_specs_only_includes_registry_sources():
    names = {spec.name for spec in sc.build_specs()}
    assert names == {"yfinance", "screener_in", "nse_shareholding"}


def test_build_specs_reflects_tuning(writable_meta_db):
    sc.update_source("yfinance", {"rate_per_sec": 0.7, "priority": 5})
    spec = next(s for s in sc.build_specs() if s.name == "yfinance")
    assert spec.rate_per_sec == 0.7
    assert spec.priority == 5


def test_apply_to_registry_disables_sources(writable_meta_db):
    sc.update_source("screener_in", {"enabled": False})
    reg = SourceRegistry(sc.build_specs())
    sc.apply_to_registry(reg)

    assert reg.is_enabled("screener_in") is False
    assert reg.is_enabled("yfinance") is True
    # Candidate selection must not offer a disabled source, fresh or stale.
    assert all(s.name != "screener_in" for s in reg.candidates("pe"))
    assert all(s.name != "screener_in" for s in reg.candidates_any(("roe", "pe")))
    assert all(s.name != "screener_in" for s in reg.specs_for("roe"))
    assert reg.is_available("screener_in") is False


def test_registry_set_enabled_excludes_from_all_selection_paths():
    specs = [
        SourceSpec(name="a", capabilities=frozenset({"pe"}), cost="fast"),
        SourceSpec(name="b", capabilities=frozenset({"pe"}), cost="slow"),
    ]
    reg = SourceRegistry(specs)
    reg.set_enabled("b", False)
    assert reg.is_enabled("b") is False
    assert [s.name for s in reg.candidates("pe")] == ["a"]
    assert [s.name for s in reg.candidates_any(("pe",))] == ["a"]
    assert [s.name for s in reg.specs_for("pe")] == ["a"]
    reg.set_enabled("b", True)
    assert reg.is_enabled("b") is True
    assert {s.name for s in reg.candidates("pe")} == {"a", "b"}
