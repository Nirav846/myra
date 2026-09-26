"""Confluence aggregation: selective vs broad scanner split.

The headline confluence count must reflect agreement among *selective*
scanners. Broad scanners (those covering more than BROAD_THRESHOLD of the
snapshot's symbol union) are reported per symbol but excluded from the count,
so the result cannot be dominated by a scanner that simply flags most of the
universe.

Note the denominator is the snapshot's symbol *union*, not the market. Test
universes therefore have to be realistically large, or a scanner covering 1
of 2 symbols is legitimately 50% (i.e. broad).
"""

import pytest

from myra_web.utils import (
    BROAD_THRESHOLD,
    MIN_SELECTIVE_FOR_INCLUSION,
    _best_grade,
    build_confluence_report,
    compute_scanner_breadth,
)

# 20 symbols: a selective scanner flagging 1-2 of them lands at 5-10%, well
# under the 40% threshold; a broad scanner covering all of them sits at 100%.
UNIVERSE = [f"S{i:02d}" for i in range(20)]

# Padding scanner used to give the union a realistic size without involving
# the symbols under test (S00-S04). Flags 15 symbols -> broad.
PAD = UNIVERSE[5:]


def _width(scanners: dict, syms: list) -> list:
    """Symbols a scanner flags, as a plain list."""
    return list(syms)


def _breadth_for(scanners: dict) -> dict[str, float]:
    return compute_scanner_breadth(
        {
            n: {"candidates": [{"symbol": s} for s in syms]}
            for n, syms in scanners.items()
        }
    )


def _snap(scanners: dict, as_on: str = "2026-09-24") -> dict:
    """Snapshot payload from {display_name: [symbols]}."""
    breadth = _breadth_for(scanners)
    return {
        "as_on_date": as_on,
        "generated_at": "2026-09-24T18:00:00",
        "scanners": {
            n: {
                "candidates": [{"symbol": s, "grade": "B"} for s in syms],
                "error": None,
                "pct_of_universe": breadth[n],
            }
            for n, syms in scanners.items()
        },
    }


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


class TestComputeScannerBreadth:
    def test_pct_is_share_of_union(self):
        # Union = A,B,C,D (4). Sc1 covers 3 of them, Sc2 covers 1.
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


class TestBroadClassification:
    def test_constants(self):
        assert BROAD_THRESHOLD == 40.0
        assert MIN_SELECTIVE_FOR_INCLUSION == 2

    def test_classification_at_realistic_scale(self):
        breadth = _breadth_for(
            {
                "Broad": UNIVERSE,  # 100%
                "Wide": UNIVERSE[:8],  # 40% — boundary is inclusive for selective
                "Selective": UNIVERSE[:2],  # 10%
            }
        )
        assert breadth["Broad"] > BROAD_THRESHOLD
        assert breadth["Wide"] == BROAD_THRESHOLD  # not > threshold
        assert breadth["Selective"] < BROAD_THRESHOLD

    def test_erroring_scanner_is_not_broad(self):
        """A scanner that failed contributed nothing; calling it broad would
        wrongly inflate every symbol it did not appear in."""
        breadth = compute_scanner_breadth(
            {
                "Broad": {"candidates": [{"symbol": s} for s in UNIVERSE]},
                "Broken": {"candidates": [], "error": "ValueError: boom"},
            }
        )
        assert breadth["Broken"] == 0.0
        assert breadth["Broken"] <= BROAD_THRESHOLD


class TestReportFromSnapshot:
    def test_broad_scanners_do_not_inflate_the_count(self, snapshot_dir):
        snapshot_dir(
            _snap(
                {
                    "Broad": UNIVERSE,  # 100% -> broad
                    "Sel1": ["S00"],  # 5%  -> selective
                    "Sel2": ["S00"],  # 5%  -> selective
                    "Loner": ["S01"],  # 5% -> selective
                }
            )
        )
        rep = build_confluence_report()
        by_sym = {s["symbol"]: s for s in rep["symbols"]}

        # Only S00 qualifies: two selective scanners agree.
        assert set(by_sym) == {"S00"}
        assert by_sym["S00"]["selective_scanner_count"] == 2
        assert by_sym["S00"]["scanner_count"] == 3  # broad still counted here
        assert by_sym["S00"]["broad_scanners"] == ["Broad"]
        assert by_sym["S00"]["selective_scanners"] == ["Sel1", "Sel2"]

        # S01 = 1 selective + 1 broad -> excluded, per the implemented rule.
        assert "S01" not in by_sym

    def test_two_selective_scanners_alone_are_enough(self, snapshot_dir):
        # PAD gives the union a realistic size without flagging S00, so S00's
        # count comes only from the two selective scanners.
        snapshot_dir(_snap({"PAD": PAD, "Sel1": ["S00"], "Sel2": ["S00"]}))
        rep = build_confluence_report()
        assert [s["symbol"] for s in rep["symbols"]] == ["S00"]
        assert rep["symbols"][0]["scanner_count"] == 2
        assert rep["symbols"][0]["selective_scanner_count"] == 2
        assert rep["symbols"][0]["broad_scanners"] == []

    def test_removing_a_broad_scanner_does_not_change_the_set(self, snapshot_dir):
        """The core guarantee: the selective set is invariant to broad scanners."""
        base = {
            "PAD": PAD,  # present in both variants, keeps union size stable
            "Sel1": ["S00", "S01"],
            "Sel2": ["S00", "S02"],
            "Sel3": ["S01", "S02"],
        }
        snapshot_dir(_snap(dict(base, Broad=UNIVERSE)))
        with_broad = sorted(s["symbol"] for s in build_confluence_report()["symbols"])
        snapshot_dir(_snap(base))
        without = sorted(s["symbol"] for s in build_confluence_report()["symbols"])
        assert with_broad == without == ["S00", "S01", "S02"]

    def test_removing_a_selective_scanner_does_change_the_set(self, snapshot_dir):
        """Sanity check the mirror image: selective scanners really do matter."""
        pair = ["S00", "S01"]  # 2/20 = 10% -> selective, not broad
        snapshot_dir(_snap({"Broad": UNIVERSE, "Sel1": pair, "Sel2": pair}))
        assert len(build_confluence_report()["symbols"]) == 2
        # One selective scanner short of the threshold -> nothing qualifies,
        # even though the broad scanner still flags all of them.
        snapshot_dir(_snap({"Broad": UNIVERSE, "Sel1": pair}))
        assert len(build_confluence_report()["symbols"]) == 0

    def test_sorted_by_selective_then_total(self, snapshot_dir):
        snapshot_dir(
            _snap(
                {
                    "Broad": UNIVERSE,
                    "S1": ["S00", "S01", "S02", "S03"],
                    "S2": ["S00", "S01", "S02", "S03"],
                    "S3": ["S00", "S01", "S02", "S03"],
                    "T1": ["S10", "S11"],
                    "T2": ["S10", "S11"],
                }
            )
        )
        rep = build_confluence_report()
        counts = [s["selective_scanner_count"] for s in rep["symbols"]]
        assert counts == sorted(counts, reverse=True)
        assert counts[0] == 3  # S00-S03 (3 selective); S10/S11 have 2

    def test_errored_scanner_excluded_and_reported(self, snapshot_dir):
        payload = _snap(
            {"Broad": UNIVERSE, "Sel1": ["S00"], "Sel2": ["S00"], "Broken": ["S00"]}
        )
        payload["scanners"]["Broken"]["candidates"] = []
        payload["scanners"]["Broken"]["error"] = "ValueError: boom"
        snapshot_dir(payload)
        rep = build_confluence_report()
        assert rep["scanner_errors"]["Broken"] == "ValueError: boom"
        assert rep["symbols"][0]["scanner_count"] == 3  # Broad + Sel1 + Sel2
        assert rep["symbols"][0]["selective_scanner_count"] == 2

    def test_missing_pct_field_is_recomputed(self, snapshot_dir):
        """Snapshots written before pct_of_universe existed must still classify."""
        payload = _snap(
            {"Broad": UNIVERSE, "Sel1": ["S00"], "Sel2": ["S00"], "Loner": ["S01"]}
        )
        for entry in payload["scanners"].values():
            entry.pop("pct_of_universe")
        snapshot_dir(payload)
        rep = build_confluence_report()
        assert rep["scanner_breadth"]["Broad"]["broad"] is True
        assert rep["scanner_breadth"]["Sel1"]["broad"] is False
        assert [s["symbol"] for s in rep["symbols"]] == ["S00"]

    def test_report_exposes_breadth_and_thresholds(self, snapshot_dir):
        snapshot_dir(_snap({"Broad": UNIVERSE, "Sel1": ["S00"], "Sel2": ["S00"]}))
        rep = build_confluence_report()
        assert rep["broad_threshold"] == BROAD_THRESHOLD
        assert rep["min_selective"] == MIN_SELECTIVE_FOR_INCLUSION
        assert rep["scanner_breadth"]["Broad"] == {
            "pct_of_universe": 100.0,
            "broad": True,
            "count": 20,
        }
        # ordered broadest-first
        pcts = [b["pct_of_universe"] for b in rep["scanner_breadth"].values()]
        assert pcts == sorted(pcts, reverse=True)

    def test_no_snapshot_returns_guidance_message(self, tmp_path, monkeypatch):
        import myra_web.utils as U

        monkeypatch.setattr(U, "MODELS_DIR", str(tmp_path))
        rep = U.build_confluence_report()
        assert rep["symbols"] == []
        assert "refresh" in rep["message"]
        assert rep["scanner_breadth"] == {}
        # contract keys present even on the empty path
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
    """The split must not touch grade logic."""
    assert _best_grade([{"grade": "A+"}, {"grade": "C"}]) == "A+"
    assert _best_grade([{"symbol": "X"}]) is None
