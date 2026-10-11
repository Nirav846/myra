"""Ungated fetch paths now honour the Data Sources switches.

Covers the paths *outside* the cost-aware resolver / DataFetcher chain: the
sector manager's Morningstar bulk pull, ``DataFetcher.fetch_deep_history``, the
full-fundamentals fetcher, and the standalone screener enricher. Each must skip
its source when the operator has switched it off.
"""

import pytest

from myra_app import sector_manager
from myra_app.data_sources import source_config
from myra_app.db.enrichers import screener_enricher
from myra_app.fetcher import DataFetcher
from myra_app.fetchers import full_fundamentals


@pytest.fixture(autouse=True)
def _reset_config_cache():
    source_config.invalidate_cache()
    yield
    source_config.invalidate_cache()


# -- sector manager (Morningstar) ------------------------------------------- #
def test_morningstar_bulk_skipped_when_disabled(monkeypatch):
    monkeypatch.setattr(sector_manager, "_source_enabled", lambda name: False)

    def _no_network(*_a, **_k):
        raise AssertionError("Morningstar must not be contacted when disabled")

    monkeypatch.setattr("requests.get", _no_network)
    mgr = sector_manager.SectorManager.__new__(sector_manager.SectorManager)
    assert mgr.fetch_morningstar_bulk() == {}


def test_symbol_fallback_skips_both_sources_when_disabled(monkeypatch):
    monkeypatch.setattr(sector_manager, "_source_enabled", lambda name: False)

    class _BoomSession:
        def get(self, *_a, **_k):
            raise AssertionError("Screener must not be contacted when disabled")

    mgr = sector_manager.SectorManager.__new__(sector_manager.SectorManager)
    mgr.session = _BoomSession()
    assert mgr.fetch_symbol_fallback("RELIANCE") is None


# -- DataFetcher.fetch_deep_history (Screener) ------------------------------ #
def test_fetch_deep_history_returns_none_when_screener_disabled(monkeypatch):
    monkeypatch.setattr(source_config, "is_enabled", lambda name: False)
    fetcher = DataFetcher.__new__(DataFetcher)
    # Never reaches the cache read or the session when the source is off.
    assert fetcher.fetch_deep_history("RELIANCE") is None


# -- full fundamentals fetcher (Screener + yfinance) ------------------------ #
def test_full_fundamentals_skips_both_when_disabled(monkeypatch):
    monkeypatch.setattr(full_fundamentals, "_source_enabled", lambda name: False)

    def _boom(*_a, **_k):
        raise AssertionError("no source may be fetched when both are disabled")

    for name in (
        "get_company_id",
        "_get_company_id_requests",
        "fetch_screener_snapshot",
        "fetch_screener_ratios",
        "fetch_timeseries",
        "fetch_yfinance_data",
    ):
        monkeypatch.setattr(full_fundamentals, name, _boom)

    result = full_fundamentals.fetch_full_fundamentals("RELIANCE")
    assert result["sources"] == []
    assert result["snapshot"] == {}
    assert result["yfinance"] == {}
    assert "disabled" in (result["warning"] or "")


def test_full_fundamentals_runs_only_yfinance_when_screener_off(monkeypatch):
    monkeypatch.setattr(
        full_fundamentals, "_source_enabled", lambda name: name == "yfinance"
    )

    def _boom(*_a, **_k):
        raise AssertionError("Screener must not run when screener_in is off")

    monkeypatch.setattr(full_fundamentals, "get_company_id", _boom)
    monkeypatch.setattr(full_fundamentals, "_get_company_id_requests", _boom)
    monkeypatch.setattr(full_fundamentals, "fetch_screener_snapshot", _boom)
    monkeypatch.setattr(full_fundamentals, "fetch_screener_ratios", _boom)
    monkeypatch.setattr(
        full_fundamentals, "fetch_yfinance_data", lambda s: {"pe": 21.5}
    )

    result = full_fundamentals.fetch_full_fundamentals("RELIANCE")
    assert result["sources"] == ["yfinance"]
    assert result["yfinance"]["pe"] == 21.5
    assert result["snapshot"] == {}


def test_full_fundamentals_runs_only_screener_when_yfinance_off(monkeypatch):
    monkeypatch.setattr(
        full_fundamentals, "_source_enabled", lambda name: name == "screener_in"
    )

    def _boom(*_a, **_k):
        raise AssertionError("yfinance must not run when it is off")

    monkeypatch.setattr(full_fundamentals, "fetch_yfinance_data", _boom)
    monkeypatch.setattr(full_fundamentals, "get_company_id", lambda s: "123")
    monkeypatch.setattr(
        full_fundamentals, "fetch_screener_snapshot", lambda s: {"pe": 30}
    )
    monkeypatch.setattr(full_fundamentals, "fetch_screener_ratios", lambda s: {})
    monkeypatch.setattr(full_fundamentals, "fetch_timeseries", lambda cid, key: [])

    result = full_fundamentals.fetch_full_fundamentals("RELIANCE")
    assert result["sources"] == ["screener-scrape"]
    assert result["yfinance"] == {}
    assert "yfinance disabled" in (result["warning"] or "")


# -- standalone screener enricher (Screener) -------------------------------- #
def test_screener_enricher_skipped_when_disabled(monkeypatch):
    monkeypatch.setattr(screener_enricher, "_source_enabled", lambda name: False)

    def _no_db(*_a, **_k):
        raise AssertionError("must not open the DB when screener_in is off")

    monkeypatch.setattr(screener_enricher.sqlite3, "connect", _no_db)
    # force=True bypasses the auto-run gate so we exercise the source gate.
    screener_enricher.enrich_screener_fundamentals(force=True)
