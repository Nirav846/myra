"""Tests for canonical-first fundamentals reads.

Several fundamentals fields have a canonical snake_case column that a live
Morningstar writer now populates, sitting beside a legacy camelCase column
holding a value frozen when the Upstox writer was removed. Both
`/api/fundamentals/live/{symbol}` and the portfolio endpoint read canonical
first and fall back to legacy only when canonical is NULL.

The failure this guards against is a truthiness bug: `canonical or legacy` would
silently discard a real 0.0 (a company with no dividend paid, or no quick
assets) and hand back the frozen legacy value instead.
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from myra_app.fundamentals_staleness import canonical_or_legacy
from myra_app.ratio_sanitize import sanitize_ratio
from myra_web.routes import fundamentals as fundamentals_route
from myra_web.routes.fundamentals import get_live_fundamentals

NOW = datetime.now(timezone.utc)
FRESH = (NOW - timedelta(days=1)).isoformat()
FROZEN = (NOW - timedelta(days=200)).isoformat()


# --- unit level: canonical_or_legacy -------------------------------------


def test_canonical_wins_when_both_present():
    row = {"quick_ratio": 1.9, "quickRatio": 0.000011}
    assert canonical_or_legacy(row, "quick_ratio", "quickRatio") == 1.9


def test_legacy_used_only_when_canonical_is_null():
    row = {"quick_ratio": None, "quickRatio": 0.9}
    assert canonical_or_legacy(row, "quick_ratio", "quickRatio") == 0.9


def test_none_when_both_null():
    assert (
        canonical_or_legacy({"quick_ratio": None}, "quick_ratio", "quickRatio") is None
    )


def test_zero_canonical_beats_nonzero_legacy():
    """The regression guard: 0.0 is a real value, not missing data.

    DYNPRO has payout_ratio 0.0 in live data; a truthiness fallback would throw
    that away and serve the frozen legacy value instead.
    """
    row = {"payout_ratio": 0.0, "payoutRatio": 22.0}
    assert canonical_or_legacy(row, "payout_ratio", "payoutRatio") == 0.0


def test_zero_canonical_with_null_legacy():
    row = {"payout_ratio": 0, "payoutRatio": None}
    assert canonical_or_legacy(row, "payout_ratio", "payoutRatio") == 0


def test_zero_legacy_used_when_canonical_null():
    row = {"payout_ratio": None, "payoutRatio": 0.0}
    assert canonical_or_legacy(row, "payout_ratio", "payoutRatio") == 0.0


def test_missing_keys_are_tolerated():
    assert canonical_or_legacy({}, "quick_ratio", "quickRatio") is None


# --- route level: the repoint reaches the response -----------------------


@pytest.fixture
def valuation_db(tmp_path, monkeypatch):
    path = os.path.join(str(tmp_path), "myra_valuation.db")
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE fundamentals (
            symbol TEXT PRIMARY KEY,
            quickRatio REAL, payoutRatio REAL, currentRatio REAL,
            quick_ratio REAL, payout_ratio REAL, current_ratio REAL,
            source_ms TEXT, last_updated TEXT
        )
        """
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(fundamentals_route, "DB_DIR", str(tmp_path))

    def _no_cli(*args, **kwargs):
        raise FileNotFoundError("screener CLI not available in tests")

    monkeypatch.setattr(fundamentals_route.subprocess, "run", _no_cli)
    return path


def _insert(path, symbol, last_updated, **values):
    cols = ["symbol", "last_updated", "source_ms"] + list(values)
    conn = sqlite3.connect(path)
    conn.execute(
        f"INSERT INTO fundamentals ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
        [symbol, last_updated, "MORNINGSTAR"] + list(values.values()),
    )
    conn.commit()
    conn.close()


def _funda(symbol):
    return asyncio.run(get_live_fundamentals(symbol))["fundamentals"]


def test_route_prefers_live_canonical_over_frozen_legacy(valuation_db):
    """The 3227-row case: live canonical beside a frozen legacy value."""
    _insert(valuation_db, "LIVE", FRESH, quick_ratio=1.85, quickRatio=0.000011)

    funda = _funda("LIVE")
    assert funda["quick_ratio"] == 1.85
    # ...and the fresh canonical value is not reported as stale.
    assert funda["quick_ratio_stale"] is False


def test_route_zero_payout_ratio_survives(valuation_db):
    """The falsy-vs-NULL case, end to end through the route."""
    _insert(valuation_db, "NOPAY", FRESH, payout_ratio=0.0, payoutRatio=22.0)

    funda = _funda("NOPAY")
    assert funda["payout_ratio"] == 0.0
    assert funda["payout_ratio_stale"] is False


def test_route_falls_back_to_legacy_and_flags_stale(valuation_db):
    """No canonical value: the frozen legacy value is still served, but disclosed."""
    _insert(valuation_db, "LEGACYONLY", FROZEN, quickRatio=0.9)

    funda = _funda("LEGACYONLY")
    assert funda["quick_ratio"] == 0.9
    assert funda["quick_ratio_stale"] is True


def test_route_suppresses_implausible_quick_ratio(valuation_db):
    """A live 13935.9 must not reach the API as a quick ratio."""
    _insert(valuation_db, "WEIRD", FRESH, quick_ratio=13935.914815)

    funda = _funda("WEIRD")
    assert funda["quick_ratio"] is None
    # Nothing is served, so nothing misleading to flag.
    assert funda["quick_ratio_stale"] is False


def test_route_suppresses_implausible_current_ratio(valuation_db):
    """current_ratio has the identical near-zero-denominator pathology."""
    _insert(valuation_db, "WEIRDC", FRESH, current_ratio=13936.014815)

    assert _funda("WEIRDC")["current_ratio"] is None


def test_route_keeps_plausible_current_ratio(valuation_db):
    _insert(valuation_db, "OKC", FRESH, current_ratio=1.978245)

    funda = _funda("OKC")
    assert funda["current_ratio"] == 1.978245
    assert funda["current_ratio_stale"] is False


def test_sanitize_applies_after_fallback(valuation_db):
    """Suppression must catch an implausible legacy value too, not just canonical."""
    _insert(valuation_db, "WEIRDLEG", FROZEN, quickRatio=5000.0)

    assert _funda("WEIRDLEG")["quick_ratio"] is None


def test_payout_ratio_is_not_sanitized(valuation_db):
    """payout_ratio's observed range is plausible and must pass through untouched."""
    _insert(valuation_db, "BIGPAY", FRESH, payout_ratio=16.6667)

    assert _funda("BIGPAY")["payout_ratio"] == 16.6667


def test_sanitize_and_fallback_compose_without_helper_leakage():
    """Documented behaviour of the two helpers used together at call sites."""
    row = {"payout_ratio": 0.0, "payoutRatio": 22.0, "last_updated": FRESH}
    resolved = canonical_or_legacy(row, "payout_ratio", "payoutRatio")
    assert sanitize_ratio(resolved, field_name="payout_ratio") == 0.0
