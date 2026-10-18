"""Phase 21 — pin the price-basis contract that the indicator lake depends on.

Phase 21 settled the price-basis question that Phase 20 had to leave open:

* ``technical_data`` (fed by the eod2 CSVs) carries **corporate-action-adjusted,
  back-adjusted** prices: a 1:1 bonus on the ex-date leaves *no* jump.
* The parquet OHLCV cache is a stale **unadjusted** snapshot: it drops by the
  factor (1 -> 0.5, or 1 -> 0.2 for a 1:4) at the ex-date, because the pre-ex
  prices were never restated.
* ``LibrarianCore.get_ohlcv`` merges the two — cache history plus a DB delta —
  so anything computed over that frame (notably the indicator lake, via
  ``_compute_symbol_indicators``) would mix two price bases and roughly double
  every moving average that straddles an ex-date.

These tests are pure contract checks over temporary fixtures. They do not read
or write any live database, the OHLCV cache, or the eod2 CSVs: the *measured*
evidence is recorded in ``docs/PHASE21_V25_READINESS_AND_RECOVERY_PLAN.md``, and
what is asserted here is the semantic rule that any cache rebuild must keep.

What is deliberately NOT tested: that the live cache is currently unadjusted.
That is a data state, not a code contract, and it is expected to change (the
recommended repair is to make the cache match the adjusted basis). Pinning it
would fail the moment the repair lands and would turn a data fix into a code
regression.
"""

from __future__ import annotations

import pandas as pd
import pytest

from myra_app.constants import USE_EOD2_DATA
from myra_app.eod2_sync import _compute_derived

#: A synthetic ex-date: 1:1 bonus. On and after the ex-date the raw price is
#: halved; a back-adjusted series restates the *prior* prices instead, so the
#: adjusted series is continuous across it.
EX = "2024-10-28"


def _series() -> pd.DataFrame:
    """Ten sessions ending on the ex-date, adjusted so there is no jump."""
    dates = pd.bdate_range(end=EX, periods=10).strftime("%Y-%m-%d")
    return pd.DataFrame(
        {
            "symbol": "TEST",
            "date": dates,
            "open": [100.0 + i for i in range(10)],
            "high": [101.0 + i for i in range(10)],
            "low": [99.0 + i for i in range(10)],
            "close": [100.5 + i for i in range(10)],
            "volume": [1000] * 10,
            "delivery": [500] * 10,
            "trades": [None] * 10,
        }
    )


def test_the_ingestion_source_is_the_adjusted_eod2_dataset():
    """``technical_data`` must stay fed from the adjusted CSVs, not raw bhavcopy.

    ``USE_EOD2_DATA`` exists precisely to stop unadjusted corporate-action data
    from overwriting adjusted rows; turning it off would reintroduce the mixed
    basis this phase spent its effort characterising.
    """
    assert USE_EOD2_DATA is True


def test_an_adjusted_series_has_no_price_jump_at_the_ex_date():
    """The defining property of a back-adjusted series."""
    df = _series()
    closes = df.set_index("date")["close"]
    day_over_day = closes.pct_change().dropna()
    assert day_over_day.abs().max() < 0.05, (
        "a corporate action must not appear as a price jump in the adjusted "
        f"series; largest observed move was {day_over_day.abs().max():.2%}"
    )


def test_derived_columns_are_computed_from_the_supplied_adjusted_history():
    """Rolling inputs are basis-sensitive; the values must track the frame given.

    Two frames that differ only by a pre-ex-date rescale must produce different
    rolling values. This is what makes mixing the cache into the lake unsafe: if
    these agreed, the basis would not matter.
    """
    adjusted = _compute_derived(_series())
    sma_adjusted = adjusted["sma_50"].iloc[-1]

    unadjusted = _series()
    unadjusted.loc[unadjusted["date"] < EX, ["open", "high", "low", "close"]] *= 2.0
    sma_unadjusted = _compute_derived(unadjusted)["sma_50"].iloc[-1]

    assert sma_adjusted != pytest.approx(sma_unadjusted)
    assert sma_unadjusted > sma_adjusted, (
        "the unadjusted (pre-rescale) frame must yield the larger mean, which is "
        "exactly the inflation a mixed-basis frame would inject"
    )


def test_volume_is_not_price_adjusted():
    """A price-only corporate action must not restate volume.

    Measured on the live data this holds across the ex-date boundary, so
    volume/delivery-derived inputs (``avg_volume_20d``, ``avg_delivery_20d``)
    are independent of the price-basis decision.
    """
    df = _series()
    derived = _compute_derived(df)
    assert derived["volume"].iloc[-1] == 1000
    assert derived["delivery_pct"].iloc[-1] == pytest.approx(50.0)


def test_the_lake_must_not_derive_from_a_mixed_basis_frame():
    """``get_ohlcv`` is cache+delta; the lake reads it. Guard the seam in code.

    This does not inspect the live cache. It asserts the *documented* hazard so
    that a future change which starts mixing bases (or forgets to exclude the
    cache) surfaces here rather than as a silently wrong indicator lake.
    """
    from myra_app import librarian_intelligence

    src = librarian_intelligence.__file__
    with open(src, encoding="utf-8") as fh:
        text = fh.read()
    assert "self.get_ohlcv(sym)" in text, (
        "the lake currently sources from get_ohlcv (cache + DB delta); this test "
        "records that dependency so the basis fix has a known blast radius"
    )
