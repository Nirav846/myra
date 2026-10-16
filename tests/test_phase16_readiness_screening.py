"""Phase 16: readiness must not report ``ready`` when screening is impossible.

Freshness and capability are different facts. ``technical_facts`` answers "do
we hold the session we should?"; ``screening_facts`` answers "are the derived
artifacts the screening path reads present and usable?". MYRA was in the state
where the first was true and the second was false — and reported ``ready``.

Every test here redirects ``DATA_DIR`` to a temporary directory, so the real
lake is never read or created.
"""

import os
import sys
import time

import pandas as pd
import pytest

import myra_app.constants as constants
import myra_app.data_readiness as dr
import myra_app.indicator_lake as lake


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    """Redirect DATA_DIR so the real lake is untouchable from this module."""
    dest = tmp_path / "data"
    dest.mkdir()
    monkeypatch.setattr(constants, "DATA_DIR", str(dest))
    return str(dest)


class _Lake:
    """Create artifacts in the redirected lake."""

    def __init__(self, count: int = 1, *, symbol: str = "AAA"):
        self.count = count
        self.symbol = symbol

    def __enter__(self):
        directory = lake.lake_dir()
        os.makedirs(directory, exist_ok=True)
        for i in range(self.count):
            symbol = self.symbol if i == 0 else f"{self.symbol}{i}"
            frame = pd.DataFrame(
                {"symbol": [symbol], "close": [100.0], "rsi": [55.0]},
                index=pd.to_datetime(["2026-01-02"]),
            )
            frame.index.name = "date"
            path = os.path.join(directory, f"{symbol}.parquet")
            frame.to_parquet(path, compression="snappy", index=True)
            # Newer than any plausible stored session, so the lake reads fresh.
            now = time.time()
            os.utime(path, (now, now))
        return self

    def __exit__(self, *exc):
        return False


def _technical(latest: str = "2026-01-02") -> dict:
    return {"latest_date": latest, "expected_date": latest}


# ── 1. screening_facts reports capability, not existence ────────────────────


def test_an_unbuilt_lake_blocks_screening(data_dir):
    facts = dr.screening_facts(_technical())

    assert facts["state"] == dr.STATE_BLOCKED
    assert facts["reason"] == "lake_empty"
    assert facts["available"] is False
    assert facts["lake"]["reason"] == "missing"
    assert "precomputed" in facts["detail"]


def test_probing_screening_does_not_create_the_lake(data_dir):
    """A probe that materialises what it is checking for is self-defeating."""
    dr.screening_facts(_technical())

    assert not os.path.exists(lake.lake_dir())


def test_a_usable_lake_reads_ready(data_dir, monkeypatch):
    monkeypatch.setattr(dr, "_active_universe_size", lambda: 2)
    with _Lake(2):
        facts = dr.screening_facts(_technical())

    assert facts["state"] == dr.STATE_READY
    assert facts["reason"] == "ok"
    assert facts["available"] is True
    assert facts["expected"] == 2
    assert facts["coverage"] == 1.0


def test_a_lake_that_predates_the_stored_session_is_stale(data_dir, monkeypatch):
    """A stale lake loads fine and would silently rank on old prices."""
    monkeypatch.setattr(dr, "_active_universe_size", lambda: 1)
    with _Lake(1) as created:
        old = time.time() - 40 * 86400
        os.utime(os.path.join(lake.lake_dir(), "AAA.parquet"), (old, old))
        assert created.count == 1
        facts = dr.screening_facts(_technical("2099-01-01"))

    assert facts["state"] == dr.STATE_STALE
    assert facts["reason"] == "stale"
    assert facts["available"] is True


def test_a_corrupt_artifact_blocks_screening(data_dir):
    directory = lake.lake_dir()
    os.makedirs(directory, exist_ok=True)
    with open(os.path.join(directory, "AAA.parquet"), "wb") as handle:
        handle.write(b"not parquet")

    facts = dr.screening_facts(_technical())

    assert facts["state"] == dr.STATE_BLOCKED
    assert facts["reason"] == "invalid_artifact"
    assert "AAA.parquet" in facts["detail"]


def test_a_lake_too_thin_to_screen_is_blocked(data_dir, monkeypatch):
    monkeypatch.setattr(dr, "_active_universe_size", lambda: 100)
    with _Lake(10):
        facts = dr.screening_facts(_technical())

    assert facts["state"] == dr.STATE_BLOCKED
    assert facts["reason"] == "coverage_insufficient"
    assert facts["coverage"] == 0.1


def test_partial_coverage_is_partial_not_ready(data_dir, monkeypatch):
    monkeypatch.setattr(dr, "_active_universe_size", lambda: 100)
    with _Lake(60):
        facts = dr.screening_facts(_technical())

    assert facts["state"] == dr.STATE_PARTIAL
    assert facts["reason"] == "coverage_partial"
    assert facts["coverage"] == 0.6


def test_a_broken_probe_cannot_crash_readiness(data_dir, monkeypatch):
    def _boom(**kwargs):
        raise OSError("cannot list the lake")

    monkeypatch.setattr(lake, "status", _boom)

    facts = dr.screening_facts(_technical())

    assert facts["state"] == dr.STATE_UNKNOWN
    assert facts["reason"] == "probe_failed"
    assert "OSError" in facts["detail"]


def test_a_missing_probe_is_reported_as_such(data_dir, monkeypatch):
    monkeypatch.setitem(sys.modules, "myra_app.indicator_lake", None)

    facts = dr.screening_facts(_technical())

    assert facts["state"] == dr.STATE_UNKNOWN
    assert facts["reason"] == "probe_unavailable"


def test_a_missing_reference_date_still_detects_an_empty_lake(data_dir):
    """``technical`` only supplies the staleness reference, not the verdict.

    Emptiness and validity are judgeable without it, so a caller that cannot
    say which session the lake should reflect still learns that it is unusable.
    """
    facts = dr.screening_facts()

    assert facts["state"] == dr.STATE_BLOCKED
    assert facts["reason"] == "lake_empty"


# ── 2. Escalation is one-directional ────────────────────────────────────────


@pytest.mark.parametrize(
    "state,expected",
    [
        (dr.STATE_READY, dr.STATE_BLOCKED),
        (dr.STATE_WAITING, dr.STATE_BLOCKED),
        (dr.STATE_STALE, dr.STATE_BLOCKED),
    ],
)
def test_blocked_screening_escalates_every_freshness_state(state, expected):
    result, explanation = dr._apply_screening(
        state, "storage says so", {"state": dr.STATE_BLOCKED, "detail": "no lake"}
    )

    assert result == expected
    assert "storage says so" in explanation
    assert "no lake" in explanation


@pytest.mark.parametrize("state", [dr.STATE_FAILED, dr.STATE_UNKNOWN, dr.STATE_PARTIAL])
def test_a_stronger_freshness_signal_is_never_downgraded(state):
    """A real ingestion failure outranks an unusable artifact set."""
    result, explanation = dr._apply_screening(
        state, "the real problem", {"state": dr.STATE_BLOCKED}
    )

    assert result == state
    assert explanation == "the real problem"


@pytest.mark.parametrize(
    "verdict,expected",
    [
        (dr.STATE_PARTIAL, dr.STATE_PARTIAL),
        (dr.STATE_STALE, dr.STATE_STALE),
        (dr.STATE_READY, dr.STATE_READY),
    ],
)
def test_the_worst_screening_verdict_wins(verdict, expected):
    result, _ = dr._apply_screening(dr.STATE_READY, "fresh data", {"state": verdict})

    assert result == expected


@pytest.mark.parametrize("screening", [None, {}, {"state": "nonsense"}])
def test_an_unusable_verdict_leaves_the_state_alone(screening):
    result, explanation = dr._apply_screening(dr.STATE_READY, "fresh data", screening)

    assert result == dr.STATE_READY
    assert explanation == "fresh data"


def test_the_blocked_state_is_described_in_plain_language():
    assert dr.STATE_LABELS[dr.STATE_BLOCKED] == "Screening blocked"
    assert "STATE_BLOCKED" in dr.__all__
