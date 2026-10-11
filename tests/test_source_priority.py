"""Tests for source-ownership claims and the Screener takeover window."""

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from myra_app import source_priority as sp


def _conn():
    conn = sqlite3.connect(":memory:")
    sp.ensure_table(conn)
    return conn


def test_priority_ordering():
    assert sp.get_priority("screener_in") == 100
    assert sp.get_priority("SCREENER") == 100
    assert sp.get_priority("yfinance") == 40
    assert sp.get_priority("morningstar") == 60
    assert sp.get_priority("nse_shareholding") == 80
    assert sp.get_priority("totally_unknown") == sp.DEFAULT_PRIORITY
    assert sp.get_priority(None) == sp.DEFAULT_PRIORITY


def test_record_success_and_claim_age_roundtrip():
    conn = _conn()
    sp.record_source_success("RELIANCE", "screener_in", conn=conn)
    age = sp.screener_claim_age_days("RELIANCE", conn=conn)
    assert age is not None
    assert 0.0 <= age < 1.0
    assert sp.screener_claim_is_fresh("RELIANCE", conn=conn) is True
    # Unknown symbol has no claim to protect.
    assert sp.screener_claim_age_days("UNKNOWN", conn=conn) is None


def test_claim_goes_stale_past_takeover_window(monkeypatch):
    conn = _conn()
    now = datetime.now(timezone.utc)
    old = (now - timedelta(days=90)).isoformat()
    conn.execute(
        "INSERT OR REPLACE INTO fundamental_source_freshness (symbol, source, last_success_at) "
        "VALUES (?, ?, ?)",
        ("RELIANCE", "screener_in", old),
    )
    monkeypatch.setattr(sp, "takeover_days", lambda: 60)
    assert sp.screener_claim_is_fresh("RELIANCE", conn=conn) is False
    # A claim 30 days old is still authoritative.
    fresh = (now - timedelta(days=30)).isoformat()
    conn.execute(
        "UPDATE fundamental_source_freshness SET last_success_at = ? WHERE symbol = ?",
        (fresh, "RELIANCE"),
    )
    assert sp.screener_claim_is_fresh("RELIANCE", conn=conn) is True


def test_screener_itself_is_never_blocked():
    conn = _conn()
    sp.record_source_success("RELIANCE", "screener_in", conn=conn)
    values = {"pe": 22.5, "roe": 17.0, "shares_outstanding": 1e9}
    assert (
        sp.filter_owned_fields("RELIANCE", "screener_in", dict(values), conn=conn)
        == values
    )


def test_lower_priority_source_is_vetoed_on_owned_fields_only():
    conn = _conn()
    sp.record_source_success("RELIANCE", "screener_in", conn=conn)
    values = {"pe": 22.5, "roe": 17.0, "shares_outstanding": 1e9, "industry": "Oil"}
    allowed = sp.filter_owned_fields("RELIANCE", "yfinance", dict(values), conn=conn)
    assert "pe" not in allowed
    assert "roe" not in allowed
    assert allowed["shares_outstanding"] == 1e9
    assert allowed["industry"] == "Oil"


def test_stale_claim_allows_takeover(monkeypatch):
    conn = _conn()
    now = datetime.now(timezone.utc)
    conn.execute(
        "INSERT OR REPLACE INTO fundamental_source_freshness (symbol, source, last_success_at) "
        "VALUES (?, ?, ?)",
        ("RELIANCE", "screener_in", (now - timedelta(days=90)).isoformat()),
    )
    monkeypatch.setattr(sp, "takeover_days", lambda: 60)
    values = {"pe": 22.5, "roe": 17.0}
    assert (
        sp.filter_owned_fields("RELIANCE", "yfinance", dict(values), conn=conn)
        == values
    )


def test_filter_writes_is_per_field():
    conn = _conn()
    sp.record_source_success("RELIANCE", "screener_in", conn=conn)
    field_sources = {
        "pe": "yfinance",  # vetoed: owned + lower priority
        "shares_outstanding": "yfinance",  # not Screener-owned -> kept
        "market_cap": "screener_in",  # screener priority -> kept
    }
    allowed = sp.filter_writes("RELIANCE", dict(field_sources), conn=conn)
    assert "pe" not in allowed
    assert allowed["shares_outstanding"] == "yfinance"
    assert allowed["market_cap"] == "screener_in"


def test_unknown_symbol_has_nothing_to_veto():
    conn = _conn()
    values = {"pe": 22.5, "roe": 17.0}
    assert (
        sp.filter_owned_fields("NEVER_FETCHED", "yfinance", dict(values), conn=conn)
        == values
    )


def test_describe_payload():
    conn = _conn()
    sp.record_source_success("RELIANCE", "screener_in", conn=conn)
    info = sp.describe("RELIANCE", conn=conn)
    assert info["symbol"] == "RELIANCE"
    assert info["screener_claim_age_days"] is not None
    assert info["screener_owned"] is True
