"""Confluence aggregation: selective vs broad scanner split.

The headline confluence count must reflect agreement among *selective*
scanners. Which scanners are broad is FIXED (BROAD_SCANNERS, calibrated
offline with tools/calibrate_confluence_breadth.py) rather than recomputed
from each snapshot's own percentages — otherwise a count of 3 would mean
different things on different dates.

These tests use real scanner display names because the classification is now
a membership check against that fixed set, not a threshold comparison.
"""

import pytest

from myra_web.utils import (
    BROAD_SCANNERS,
    BROAD_THRESHOLD,
    MIN_SELECTIVE_FOR_INCLUSION,
    _best_grade,
    build_confluence_report,
    compute_scanner_breadth,
)

# Real selective scanners (verified 0/8 dates broad in the calibration sweep).
SELECTIVE = [
    "Float Exhaustion",
    "Climax Accumulation",
    "Bottom Hunter",
    "Invisible Hand",
    "Seasonal Delivery",
    "Darvas Box Pro",
    "The Trigger",
    "Liquidity Flip",
    "Super Breakout",
    "Recovery Ladder",
    "Operator Fingerprint",
]

# 20 symbols: a selective scanner flagging 1-2 of them lands at 5-10%. Only
# meaningful as a percentage sanity check — classification no longer uses it.
UNIVERSE = [f"S{i:02d}" for i in range(20)]
# Padding scanner so the union is realistically sized without flagging S00-S04.
PAD = UNIVERSE[5:]


def _breadth_for(scanners: dict) -> dict[str, float]:
    return compute_scanner_breadth(
        {
            n: {"candidates": [{"symbol": s} for s in syms]}
            for n, syms in scanners.items()
        }
    )


def _snap(scanners: dict, as_on: str = "2026-09-24", store_pct: bool = True) -> dict:
    """Snapshot payload from {display_name: [symbols]}."""
    breadth = _breadth_for(scanners)
    out = {}
    for n, syms in scanners.items():
        entry = {
            "candidates": [{"symbol": s, "grade": "B"} for s in syms],
            "error": None,
        }
        if store_pct:
            entry["pct_of_universe"] = breadth[n]
        out[n] = entry
    return {"as_on_date": as_on, "generated_at": as_on + "T18:00:00", "scanners": out}


@pytest.fixture
def snapshot_dir(tmp_path, monkeypatch):
    """Point build_confluence_report at a snapshot we control."""
    import json

    import myra_web.utils as U

    monkeypatch.setattr(U, "MODELS_DIR", str(tmp_path))

    def _write(payload: dict):
        (tmp_path / "confluence_snapshot.json").write_text(
            json.dumps(payload), encoding="utf-8"
        )

    return _write


class TestCalibratedConstants:
    def test_broad_scanners_is_a_fixed_set(self):
        assert isinstance(BROAD_SCANNERS, frozenset)
        # The two scanners measured broad across the whole calibration sweep.
        assert "Wyckoff Automaton" in BROAD_SCANNERS
        assert "Multibagger Pro" in BROAD_SCANNERS

    def test_broad_set_excludes_every_selective_scanner(self):
        assert not (BROAD_SCANNERS & set(SELECTIVE))

    def test_thresholds_unchanged(self):
        assert BROAD_THRESHOLD == 40.0
        assert MIN_SELECTIVE_FOR_INCLUSION == 2


class TestComputeScannerBreadth:
    def test_pct_is_share_of_union(self):
        out = compute_scanner_breadth(
            {
                "Sc1": {"candidates": [{"symbol": s} for s in "ABC"]},
                "Sc2": {"candidates": [{"symbol": "D"}]},
            }
        )
        assert out == {"Sc1": 75.0, "Sc2": 25.0}

    def test_empty_union_returns_zeros(self):
        assert compute_scanner_breadth({"Sc1": {"candidates": []}}) == {"Sc1": 0.0}

    def test_ignores_rows_without_symbol_or_non_dicts(self):
        out = compute_scanner_breadth(
            {"Sc1": {"candidates": [{"symbol": "A"}, {"nope": 1}, "junk"]}}
        )
        assert out == {"Sc1": 100.0}

    def test_duplicate_symbols_counted_once(self):
        out = compute_scanner_breadth(
            {
                "Sc1": {"candidates": [{"symbol": "A"}, {"symbol": "A"}]},
                "Sc2": {"candidates": [{"symbol": "B"}]},
            }
        )
        assert out == {"Sc1": 50.0, "Sc2": 50.0}


class TestFixedListDrivesClassification:
    """Classification is membership, not a per-snapshot percentage."""

    def test_broad_scanner_is_broad_even_with_tiny_coverage(self, snapshot_dir):
        # A BROAD_SCANNERS member flagging a single symbol must still be broad:
        # this is what makes the verdict date-stable.
        snapshot_dir(
            _snap(
                {
                    "Multibagger Pro": ["S00"],
                    "Float Exhaustion": ["S00"],
                    "Invisible Hand": ["S00"],
                    "Climax Accumulation": UNIVERSE[5:],
                }
            )
        )
        rep = build_confluence_report()
        assert rep["scanner_breadth"]["Multibagger Pro"]["broad"] is True
        assert rep["symbols"][0]["selective_scanner_count"] == 2
        assert rep["symbols"][0]["broad_scanners"] == ["Multibagger Pro"]

    def test_selective_scanner_is_selective_even_with_huge_coverage(self, snapshot_dir):
        # The inverse: a scanner outside the fixed set is never broad, however
        # much of the universe it flags. Prevents silent drift on a quiet date.
        snapshot_dir(
            _snap(
                {
                    "Float Exhaustion": UNIVERSE,  # 100% of union
                    "Invisible Hand": ["S00"],
                    "Climax Accumulation": ["S00"],
                }
            )
        )
        rep = build_confluence_report()
        assert rep["scanner_breadth"]["Float Exhaustion"]["broad"] is False
        assert rep["symbols"][0]["broad_scanners"] == []
        assert rep["symbols"][0]["selective_scanner_count"] == 3

    def test_verdict_is_identical_across_differently_shaped_snapshots(
        self, snapshot_dir
    ):
        """Same scanners, wildly different union sizes -> same verdicts."""
        busy = _snap(
            {
                "Wyckoff Automaton": UNIVERSE,
                "Float Exhaustion": UNIVERSE[:2],
                "Invisible Hand": UNIVERSE[:2],
            },
            as_on="2026-01-01",
        )
        quiet = _snap(
            {
                "Wyckoff Automaton": ["S00"],
                "Float Exhaustion": ["S00"],
                "Invisible Hand": ["S00"],
            },
            as_on="2026-06-01",
        )
        snapshot_dir(busy)
        b = build_confluence_report()
        snapshot_dir(quiet)
        q = build_confluence_report()
        for name in ("Wyckoff Automaton", "Float Exhaustion", "Invisible Hand"):
            assert (
                b["scanner_breadth"][name]["broad"]
                == q["scanner_breadth"][name]["broad"]
            )
        assert (
            b["symbols"][0]["selective_scanner_count"]
            == q["symbols"][0]["selective_scanner_count"]
        )

    def test_pct_still_reported_even_though_it_does_not_decide(self, snapshot_dir):
        snapshot_dir(
            _snap(
                {
                    "Wyckoff Automaton": UNIVERSE,
                    "Float Exhaustion": ["S00"],
                    "Invisible Hand": ["S00"],
                }
            )
        )
        rep = build_confluence_report()
        wy = rep["scanner_breadth"]["Wyckoff Automaton"]
        assert wy["pct_of_universe"] == 100.0
        assert wy["count"] == 20
        assert wy["broad"] is True

    def test_missing_pct_field_still_classifies(self, snapshot_dir):
        payload = _snap(
            {
                "Wyckoff Automaton": UNIVERSE,
                "Float Exhaustion": ["S00"],
                "Invisible Hand": ["S00"],
            },
            store_pct=False,
        )
        snapshot_dir(payload)
        rep = build_confluence_report()
        assert rep["scanner_breadth"]["Wyckoff Automaton"]["broad"] is True
        assert rep["scanner_breadth"]["Float Exhaustion"]["broad"] is False
        assert [s["symbol"] for s in rep["symbols"]] == ["S00"]


class TestReportFromSnapshot:
    def test_broad_scanners_do_not_inflate_the_count(self, snapshot_dir):
        snapshot_dir(
            _snap(
                {
                    "Multibagger Pro": UNIVERSE,  # broad by the fixed list
                    "Float Exhaustion": ["S00"],
                    "Invisible Hand": ["S00"],
                    "Climax Accumulation": ["S01"],  # selective, on its own
                }
            )
        )
        rep = build_confluence_report()
        by_sym = {s["symbol"]: s for s in rep["symbols"]}
        # Only S00 qualifies: two selective scanners agree.
        assert set(by_sym) == {"S00"}
        assert by_sym["S00"]["selective_scanner_count"] == 2
        assert by_sym["S00"]["scanner_count"] == 3  # broad still counted here
        assert by_sym["S00"]["broad_scanners"] == ["Multibagger Pro"]
        assert by_sym["S00"]["selective_scanners"] == [
            "Float Exhaustion",
            "Invisible Hand",
        ]
        # S01 = 1 selective + 1 broad -> excluded by the >= 2 selective rule.
        assert "S01" not in by_sym

    def test_two_selective_scanners_alone_are_enough(self, snapshot_dir):
        snapshot_dir(
            _snap(
                {
                    "Wyckoff Automaton": UNIVERSE[5:],
                    "Float Exhaustion": ["S00"],
                    "Invisible Hand": ["S00"],
                }
            )
        )
        rep = build_confluence_report()
        assert [s["symbol"] for s in rep["symbols"]] == ["S00"]
        assert rep["symbols"][0]["scanner_count"] == 2
        assert rep["symbols"][0]["selective_scanner_count"] == 2
        assert rep["symbols"][0]["broad_scanners"] == []

    def test_removing_any_broad_scanner_does_not_change_the_set(self, snapshot_dir):
        """The core invariance guarantee, for every member of BROAD_SCANNERS."""
        base = {
            "Float Exhaustion": ["S00", "S01"],
            "Invisible Hand": ["S00", "S02"],
            "Climax Accumulation": ["S01", "S02"],
        }
        expected = ["S00", "S01", "S02"]
        for broad in sorted(BROAD_SCANNERS):
            snapshot_dir(_snap(dict(base, **{broad: UNIVERSE})))
            with_broad = sorted(
                s["symbol"] for s in build_confluence_report()["symbols"]
            )
            snapshot_dir(_snap(base))
            without = sorted(s["symbol"] for s in build_confluence_report()["symbols"])
            assert with_broad == expected, broad
            assert without == expected, broad

    def test_removing_a_selective_scanner_does_change_the_set(self, snapshot_dir):
        pair = ["S00", "S01"]
        snapshot_dir(
            _snap(
                {
                    "Multibagger Pro": UNIVERSE,
                    "Float Exhaustion": pair,
                    "Invisible Hand": pair,
                }
            )
        )
        assert len(build_confluence_report()["symbols"]) == 2
        # One selective scanner short of the threshold -> nothing qualifies,
        # even though the broad scanner still flags all of them.
        snapshot_dir(_snap({"Multibagger Pro": UNIVERSE, "Float Exhaustion": pair}))
        assert len(build_confluence_report()["symbols"]) == 0

    def test_sorted_by_selective_then_total(self, snapshot_dir):
        snapshot_dir(
            _snap(
                {
                    "Multibagger Pro": UNIVERSE,
                    "Float Exhaustion": UNIVERSE[:4],
                    "Invisible Hand": UNIVERSE[:4],
                    "Climax Accumulation": UNIVERSE[:4],
                    "Bottom Hunter": UNIVERSE[10:12],
                    "Seasonal Delivery": UNIVERSE[10:12],
                }
            )
        )
        rep = build_confluence_report()
        counts = [s["selective_scanner_count"] for s in rep["symbols"]]
        assert counts == sorted(counts, reverse=True)
        assert counts[0] == 3

    def test_errored_scanner_excluded_and_reported(self, snapshot_dir):
        payload = _snap(
            {
                "Multibagger Pro": UNIVERSE,
                "Float Exhaustion": ["S00"],
                "Invisible Hand": ["S00"],
                "Wyckoff Automaton": ["S00"],
            }
        )
        payload["scanners"]["Wyckoff Automaton"]["candidates"] = []
        payload["scanners"]["Wyckoff Automaton"]["error"] = "ValueError: boom"
        snapshot_dir(payload)
        rep = build_confluence_report()
        assert rep["scanner_errors"]["Wyckoff Automaton"] == "ValueError: boom"
        row = rep["symbols"][0]
        assert row["scanner_count"] == 3  # Multibagger + Float Exhaustion + Invisible
        assert row["selective_scanner_count"] == 2
        assert row["broad_scanners"] == ["Multibagger Pro"]

    def test_report_exposes_breadth_and_thresholds(self, snapshot_dir):
        snapshot_dir(
            _snap(
                {
                    "Multibagger Pro": UNIVERSE,
                    "Float Exhaustion": ["S00"],
                    "Invisible Hand": ["S00"],
                }
            )
        )
        rep = build_confluence_report()
        assert rep["broad_threshold"] == BROAD_THRESHOLD
        assert rep["min_selective"] == MIN_SELECTIVE_FOR_INCLUSION
        assert rep["broad_scanners"] == sorted(BROAD_SCANNERS)
        assert rep["scanner_breadth"]["Multibagger Pro"] == {
            "pct_of_universe": 100.0,
            "broad": True,
            "count": 20,
        }
        pcts = [b["pct_of_universe"] for b in rep["scanner_breadth"].values()]
        assert pcts == sorted(pcts, reverse=True)

    def test_no_snapshot_returns_guidance_message(self, tmp_path, monkeypatch):
        import myra_web.utils as U

        monkeypatch.setattr(U, "MODELS_DIR", str(tmp_path))
        rep = U.build_confluence_report()
        assert rep["symbols"] == []
        assert "refresh" in rep["message"]
        assert rep["scanner_breadth"] == {}
        assert rep["broad_scanners"] == sorted(BROAD_SCANNERS)
        assert rep["broad_threshold"] == BROAD_THRESHOLD
        assert rep["min_selective"] == MIN_SELECTIVE_FOR_INCLUSION

    def test_corrupt_snapshot_degrades_with_message(self, tmp_path, monkeypatch):
        import myra_web.utils as U

        monkeypatch.setattr(U, "MODELS_DIR", str(tmp_path))
        (tmp_path / "confluence_snapshot.json").write_text(
            "{not json", encoding="utf-8"
        )
        rep = U.build_confluence_report()
        assert rep["symbols"] == []
        assert "could not be read" in rep["message"]


def test_best_grade_unchanged_by_breadth_split():
    assert _best_grade([{"grade": "A+"}, {"grade": "C"}]) == "A+"
    assert _best_grade([{"symbol": "X"}]) is None
