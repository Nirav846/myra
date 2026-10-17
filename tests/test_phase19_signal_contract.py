"""Phase 19: the v2.5 signal contract and the execution blockers behind it.

Phase 18 made screening availability honest per mode. Phase 19 asks the next
question down: *which values does ``MYRA_Score_v25`` actually consume, where do
they come from, and can the pipeline produce them at all?*

Three things are pinned here.

1. **The lake builder could not write a single artifact.** ``_compute_symbol_indicators``
   returned a frame with duplicate column names (``delivery_pct``, ``vwap``,
   ``delivery_divergence_score``), which ``to_parquet`` rejects with
   ``ValueError: Duplicate column names found``. ``build_indicator_lake`` caught
   that per symbol, so the build reported every symbol as failed — honestly, but
   permanently, with ``data/indicators/precomputed`` never populated. The fix is
   the dedup idiom this method already used after ``capitalize()``.
2. **The lake's SMC indicators were fabricated zeros.** The importer named a
   module that never defined ``SMCManager``, so the blanket handler published
   ``fvg = 0.0`` for every symbol.
3. **A missing mandatory input must not become a published score.** The v2.5
   inputs are split into mandatory and optional; a gap in the mandatory set
   blocks the mode outright, and the inputs with no producer anywhere are named
   so they cannot be mistaken for a build step that has simply not run yet.

The tests deliberately do *not* read the live lake or the live databases: the
producer is driven with a synthetic symbol and the schema helper with a temp
SQLite file.
"""

from __future__ import annotations

import ast
import inspect
import sqlite3

import numpy as np
import pandas as pd
import polars as pl
import pytest

import myra_app.screening_modes as sm
import myra_app.worker_pool as wp
from myra_app.librarian_intelligence import LibrarianIntelligenceMixin
from myra_app.utils.sqlite_schema import (
    declared_type_to_polars,
    sqlite_schema_overrides,
)

# ── fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _no_live_lake(monkeypatch):
    """Never read this machine's lake.

    The two runtime detectors in ``screening_modes`` are left **real** on
    purpose: they inspect module source and these tests assert that they now
    report the repaired state.
    """
    monkeypatch.setattr(sm, "lake_field_names", lambda: None)


# ── helpers ──────────────────────────────────────────────────────────────────


def _source_frame(rows=120, delivery_pct=57.5, delivery_qty=None):
    """A ``get_ohlcv``-shaped frame for one synthetic symbol.

    The column set matters: ``delivery_pct``, ``vwap`` and
    ``delivery_divergence_score`` are the lower-case, already-enriched columns
    that ``capitalize()`` renames and that then collide with the producer's own
    assignments of the same names.
    """
    idx = pd.bdate_range("2026-01-01", periods=rows)
    rng = np.random.default_rng(11)
    close = 100 + np.cumsum(rng.normal(0, 1, rows))
    frame = pd.DataFrame(
        {
            "symbol": "ACME",
            "Open": close + 0.25,
            "High": close + 1.0,
            "Low": close - 1.0,
            "Close": close,
            "Volume": rng.integers(100_000, 500_000, rows).astype(float),
            "trades": rng.integers(1_000, 9_000, rows).astype(float),
            "delivery_pct": delivery_pct,
            "vwap": close - 0.5,
            "delivery_divergence_score": rng.normal(0, 0.2, rows),
            "has_bullish_fvg": 1,
            "fvg_top": close + 2.0,
            "fvg_bottom": close - 2.0,
        },
        index=idx,
    )
    frame.index.name = "date"
    if delivery_qty is not None:
        frame["delivery_qty"] = delivery_qty
    return frame


class _FakeLibrarian:
    """Stands in for ``Librarian`` so the producer can run without any DB."""

    def __init__(self, frame):
        self._frame = frame

    def get_ohlcv(self, symbol):
        return self._frame.copy()


def _build(frame):
    """Run the real producer over a synthetic symbol."""
    return LibrarianIntelligenceMixin._compute_symbol_indicators(
        _FakeLibrarian(frame), "ACME"
    )


def _duplicates(columns):
    seen, dupes = set(), []
    for name in columns:
        if name in seen:
            dupes.append(name)
        seen.add(name)
    return dupes


# ── 1. the lake builder can write an artifact again ──────────────────────────


def test_the_artifact_has_no_duplicate_columns_and_can_be_written(tmp_path):
    """The build blocker: ``to_parquet`` refuses duplicate column names.

    Before the repair this raised ``ValueError: Duplicate column names found``,
    ``save_indicators`` re-raised it, and ``build_indicator_lake`` counted every
    symbol as ``failed`` — so the lake could never be populated.
    """
    artifact = _build(_source_frame())

    assert _duplicates(list(artifact.columns)) == []

    path = tmp_path / "ACME.parquet"
    artifact.to_parquet(path, compression="snappy", index=True)

    back = pd.read_parquet(path)
    assert _duplicates(list(back.columns)) == []
    assert len(back) == len(artifact)


def test_the_colliding_names_are_carried_by_every_source_frame():
    """The collision is created by the producer, not by this one fixture.

    ``delivery_pct`` / ``vwap`` / ``delivery_divergence_score`` arrive
    lower-case from the enrichment layer; the producer assigns the same names
    again after capitalising, so unless the duplicates are dropped the artifact
    is unwritable for *every* symbol.
    """
    artifact = _build(_source_frame())
    names = list(artifact.columns)

    for name in ("delivery_pct", "vwap", "delivery_divergence_score"):
        assert names.count(name) == 1, name


def test_a_measured_delivery_percentage_is_kept_over_a_fabricated_zero():
    """Missing data must not overwrite a present measurement with zero.

    ``delivery_qty`` is absent from ``technical_data`` and only ~7% populated in
    the OHLCV cache, so the percentage the producer derives from it is
    ``fillna(0.0)``. Deduplication keeps the frame's first occurrence — the
    measured delivery percentage — rather than that zero.
    """
    frame = _source_frame(delivery_pct=57.5, delivery_qty=np.nan)
    artifact = _build(frame)

    assert artifact["delivery_pct"].iloc[-1] == pytest.approx(57.5)
    assert artifact["delivery_qty"].isna().all()


def test_the_artifact_keeps_the_date_index_and_the_symbol_tag():
    """What ``precompute_indicators`` relies on: a date index plus ``symbol``."""
    artifact = _build(_source_frame())

    assert isinstance(artifact.index, pd.DatetimeIndex)
    assert artifact.index.name == "date"
    assert set(artifact["symbol"].unique()) == {"ACME"}
    assert "date" not in artifact.columns


def test_the_smc_indicators_are_measured_not_the_zeroed_fallback():
    """The import repair, observed in the artifact rather than in the source."""
    artifact = _build(_source_frame())

    for column in ("fvg", "bos", "choch"):
        assert column in artifact.columns

    assert artifact["fvg"].abs().sum() > 0, "fvg fell back to the zeroed default"
    assert artifact["bos"].abs().sum() > 0, "bos fell back to the zeroed default"


# ── 2. the SMC import names a module that defines the class ──────────────────


def test_the_smc_class_is_imported_from_the_module_that_defines_it():
    """``myra_app.engine`` has never defined ``SMCManager``.

    The wrong module meant ``ImportError`` on every call, swallowed by the
    blanket handler, so every lake artifact carried ``fvg = 0.0``. The imports
    are read from the parsed source, not from the raw text, so the comment that
    records the old mistake cannot satisfy or break the assertion.
    """
    import myra_app.engine as engine
    from myra_app.smc_manager import SMCManager

    assert not hasattr(engine, "SMCManager")

    tree = ast.parse(inspect.getsource(LibrarianIntelligenceMixin))
    imports = [
        (node.module, alias.name)
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    ]

    assert ("myra_app.smc_manager", "SMCManager") in imports
    assert ("myra_app.engine", "SMCManager") not in imports

    assert callable(SMCManager.calculate_fvg)
    assert callable(SMCManager.calculate_market_structure)


def test_the_smc_fallback_is_reported_rather_than_silent():
    """A genuine per-symbol SMC failure must be visible, not published as zero."""
    source = inspect.getsource(LibrarianIntelligenceMixin)

    assert "SMC indicators fell back to zero" in source


# ── 3. the worker path binds ``fvg_mid`` and returns a candidate ─────────────


def _worker_frame(zone=True, rows=40):
    idx = pd.date_range("2026-01-01", periods=rows, freq="D")
    frame = pd.DataFrame(
        {
            "Open": 10.0,
            "High": 11.0,
            "Low": 9.0,
            "Close": 10.0,
            "Volume": 1_000.0,
            "fvg_top": 12.0 if zone else np.nan,
            "fvg_bottom": 10.0 if zone else np.nan,
            "has_bullish_fvg": 1 if zone else 0,
        },
        index=idx,
    )
    frame.index.name = "date"
    return frame


@pytest.fixture
def _stub_worker(monkeypatch):
    """Neutralise the parts of ``_worker_task`` that need real market data."""
    import myra_app.scanners.patterns as patterns

    class _PatternScout:
        def get_latest_pattern(self, df):
            return "None"

    class _FactorEngine:
        def score_symbol(self, df, funda):
            return {}

    class _Adapter:
        def get_lookback_for_scanner(self, strategy_name):
            return 300

        def get_price_df(self, symbol, lookback_days=None):
            raise AssertionError("the precomputed fast path should be used")

    monkeypatch.setattr(patterns, "PatternScout", _PatternScout)
    monkeypatch.setattr(wp, "enforce_ohlcv_contract", lambda df, symbol: df)
    monkeypatch.setattr(wp, "_worker_adapter", _Adapter())

    import myra_app.factor_engine as factor_engine

    monkeypatch.setattr(factor_engine, "FactorEngine", _FactorEngine)
    return monkeypatch


def _primitive_strategy():
    class _Strategy:
        @staticmethod
        def run_scanner(df, sid, funda=None):
            return True

    return _Strategy


def _class_strategy(signal=True):
    class _Strategy:
        @staticmethod
        def run(df, funda):
            return {"signal": signal, "metrics": {}}

    return _Strategy


def test_the_primitive_scanner_path_publishes_the_fvg_midpoint(_stub_worker):
    """``fvg_mid`` used to be referenced without ever being assigned.

    That ``NameError`` failed the whole universe, so a scan could not surface a
    result whatever the data held. The midpoint is ``(top + bottom) / 2``.
    """
    _stub_worker.setattr(wp, "_worker_strategy", _primitive_strategy())

    result = wp._worker_task(("ACME", "ACME", None, {}, _worker_frame()))

    assert result["Entry"] == pytest.approx(11.0)
    assert result["FVG_Zone"] == "10.0 - 12.0"
    assert "status" not in result


def test_the_class_strategy_path_publishes_the_fvg_midpoint(_stub_worker):
    """The second call site had the same unassigned name."""
    _stub_worker.setattr(wp, "_worker_strategy", _class_strategy())

    result = wp._worker_task(("ACME", "ACME", None, {}, _worker_frame()))

    assert result["Entry"] == pytest.approx(11.0)


def test_the_class_strategy_path_keeps_its_own_price_fallback(_stub_worker):
    """No zone is not an error — the call site's written fallback still applies."""
    _stub_worker.setattr(wp, "_worker_strategy", _class_strategy())

    result = wp._worker_task(("ACME", "ACME", None, {}, _worker_frame(zone=False)))

    assert result["Entry"] == pytest.approx(round(10.0 * 1.005, 2))
    assert result["FVG_Zone"] == "None"


def test_the_worker_path_reports_no_runtime_blocker():
    """The detector that found the missing binding now reports it as repaired."""
    assert sm.worker_path_blocker() is None


def test_both_call_sites_read_the_same_binding():
    """Guard against a repair that fixes one path and leaves the other."""
    source = inspect.getsource(wp._worker_task)

    assert source.count("fvg_mid if fvg_mid else") == 2


# ── 4. the SQLite schema helper behind the Polars read repair ────────────────


def _temp_db(tmp_path, name="t.db"):
    return sqlite3.connect(str(tmp_path / name))


def test_declared_sqlite_types_map_to_polars_types():
    assert declared_type_to_polars("INTEGER") == pl.Int64
    assert declared_type_to_polars("INT") == pl.Int64
    assert declared_type_to_polars("BOOLEAN") == pl.Int64
    assert declared_type_to_polars("REAL") == pl.Float64
    assert declared_type_to_polars("DOUBLE PRECISION") == pl.Float64
    assert declared_type_to_polars("NUMERIC") == pl.Float64
    assert declared_type_to_polars("TEXT") == pl.String
    assert declared_type_to_polars("VARCHAR(20)") == pl.String
    assert declared_type_to_polars("DATETIME") == pl.String
    assert declared_type_to_polars("BLOB") == pl.Binary
    assert declared_type_to_polars("") == pl.String


def test_an_unknown_table_raises_instead_of_falling_back_to_inference(tmp_path):
    """Returning ``{}`` would silently restore the inference this fixes."""
    conn = _temp_db(tmp_path)

    with pytest.raises(ValueError):
        sqlite_schema_overrides(conn, "does_not_exist")


def test_only_the_requested_columns_are_described(tmp_path):
    conn = _temp_db(tmp_path)
    conn.execute("CREATE TABLE t (a INTEGER, b TEXT, c REAL)")
    conn.commit()

    assert sqlite_schema_overrides(conn, "t", ["a", "c"]) == {
        "a": pl.Int64,
        "c": pl.Float64,
    }


def test_a_null_heavy_table_reads_without_null_dtype_columns(tmp_path):
    """The B2 repro: a leading NULL block defeats Polars' schema inference.

    ``technical_data``'s leading rows are NULL-heavy, so the default inference
    sample (100 rows) resolved those columns to the ``Null`` dtype and the first
    real value then failed to append — ``ComputeError: could not append value:
    40.0 of type: f64 to the builder`` on a perfectly readable table. Declaring
    the schema from ``PRAGMA table_info`` removes the guesswork, and also fixes
    the all-NULL columns that inference left as ``Null`` (which breaks
    comparisons, joins and ``to_pandas()``).
    """
    conn = _temp_db(tmp_path)
    conn.execute("CREATE TABLE t (a INTEGER, b REAL, c TEXT, note TEXT)")
    conn.executemany(
        "INSERT INTO t VALUES (?, ?, ?, ?)",
        [(None, None, None, None)] * 200 + [(40, 1.5, "x", "y")],
    )
    conn.commit()

    with pytest.raises(pl.exceptions.ComputeError):
        pl.read_database("SELECT * FROM t", conn)

    frame = pl.read_database(
        "SELECT * FROM t", conn, schema_overrides=sqlite_schema_overrides(conn, "t")
    )

    assert frame.schema == {
        "a": pl.Int64,
        "b": pl.Float64,
        "c": pl.String,
        "note": pl.String,
    }
    assert frame.height == 201
    assert frame["a"].to_list()[200] == 40


def test_a_non_integral_value_in_an_integer_column_is_coerced_not_misread(tmp_path):
    """The one lossy edge of mapping declared INTEGER to ``Int64``.

    SQLite keeps a non-integral REAL in an INTEGER-declared column, and this
    mapping reads it as the truncated integer rather than as a float. A full
    scan of all 11 INTEGER-declared columns of ``technical_data`` (8,022,725
    rows) found zero such values, which is why ``Int64`` is exact for the real
    data — and why the alternative (``Float64``) is worse: it would turn the
    whole column loose.
    """
    conn = _temp_db(tmp_path)
    conn.execute("CREATE TABLE t (a INTEGER)")
    conn.execute("INSERT INTO t VALUES (40.5)")
    conn.commit()

    assert conn.execute("SELECT typeof(a) FROM t").fetchone()[0] == "real"

    frame = pl.read_database(
        "SELECT * FROM t", conn, schema_overrides=sqlite_schema_overrides(conn, "t")
    )

    assert frame.schema == {"a": pl.Int64}
    assert frame["a"].to_list() == [40]


def test_the_schema_is_deterministic_even_for_an_empty_result(tmp_path):
    """An empty read must still describe its columns, not just be ``Null``."""
    conn = _temp_db(tmp_path)
    conn.execute("CREATE TABLE t (a INTEGER, b TEXT)")
    conn.commit()

    empty = pl.read_database(
        "SELECT * FROM t WHERE a > 100",
        conn,
        schema_overrides=sqlite_schema_overrides(conn, "t"),
    )

    assert empty.height == 0
    assert empty.schema == {"a": pl.Int64, "b": pl.String}


def test_the_helper_reads_schema_through_a_read_only_connection(tmp_path):
    """Reading a schema must never require write access to a database."""
    conn = _temp_db(tmp_path)
    conn.execute("CREATE TABLE t (a INTEGER, b REAL)")
    conn.commit()
    conn.close()

    ro = sqlite3.connect(f"file:{tmp_path / 't.db'}?mode=ro", uri=True)
    assert sqlite_schema_overrides(ro, "t") == {"a": pl.Int64, "b": pl.Float64}


def test_the_polars_schema_detector_reports_the_repair():
    """``engine`` now passes explicit overrides at both read sites."""
    assert sm.polars_schema_blocker() is None


# ── 5. mandatory inputs block v2.5; optional ones only degrade it ────────────


def _trigger_populations():
    """Every trigger column fully populated, so only the v2.5 gap can move."""
    pop = {name: 1.0 for name in sm.TRIGGER_REQUIRED_COLUMNS}
    pop.update({name: 1.0 for name in sm.TRIGGER_CONTRIBUTING_COLUMNS})
    return pop


def _v25(screening=None, available_fields=None):
    return sm.evaluate(
        technical={"latest_date": "2026-01-02", "expected_date": "2026-01-02"},
        coverage={"universe": 3000},
        screening=screening
        or {
            "state": "ready",
            "reason": "ok",
            "available": True,
            "detail": "Lake built.",
        },
        populations=_trigger_populations(),
        available_fields=available_fields,
        probe_fields=False,
    )["modes"][sm.MODE_V25_TURBO]


def test_missing_inputs_are_classified_mandatory_or_optional():
    missing = list(sm.V25_MANDATORY_INPUTS) + list(sm.V25_OPTIONAL_INPUTS)
    split = sm.classify_missing_v25_inputs(missing)

    assert split["mandatory"] == list(sm.V25_MANDATORY_INPUTS)
    assert split["optional"] == list(sm.V25_OPTIONAL_INPUTS)
    assert sm.classify_missing_v25_inputs([]) == {"mandatory": [], "optional": []}


def test_a_lake_without_mandatory_inputs_cannot_be_promoted():
    """A number computed from zero-filled placeholders is not a score."""
    mode = _v25(available_fields={"sma50", "sma200"})

    assert mode["state"] == sm.MODE_BLOCKED
    assert mode["reason"] == "mandatory_inputs_unsourced"
    assert mode["missing_mandatory"]
    assert set(mode["missing_mandatory"]) <= set(sm.V25_MANDATORY_INPUTS)
    assert "sma50" not in mode["missing_mandatory"]


def test_a_lake_missing_only_optional_inputs_is_degraded_not_operational():
    mode = _v25(
        available_fields=set(sm.V25_MANDATORY_INPUTS) | set(sm.V25_LAKE_PRODUCED_INPUTS)
    )

    assert mode["state"] == sm.MODE_DEGRADED
    assert mode["reason"] == "optional_inputs_unsourced"
    assert mode["missing_mandatory"] == []
    assert set(mode["missing_optional"]) == set(sm.V25_OPTIONAL_INPUTS)
    assert mode["state"] != sm.MODE_OPERATIONAL


def test_a_lake_with_every_input_is_operational():
    mode = _v25(available_fields=set(sm.V25_REQUIRED_INPUTS))

    assert mode["state"] == sm.MODE_OPERATIONAL
    assert mode["missing"] == []


def test_the_unsourced_inputs_are_the_definitional_gaps():
    """ "Absent from the lake" is not the same as "has no producer".

    ``avg_volume_20d`` / ``avg_delivery_20d`` *are* produced, by
    ``ranking_inputs`` — behind an opt-in flag that is off by default. The
    remaining gaps are definitional: no module writes them, so building the lake
    will not supply them and they must not be mistaken for a pending build step.
    """
    assert sm.V25_GATED_INPUTS == ("avg_volume_20d", "avg_delivery_20d")
    assert sm.V25_LAKE_PRODUCED_INPUTS == ("sma50", "sma200")

    expected = tuple(
        name
        for name in sm.V25_REQUIRED_INPUTS
        if name not in sm.V25_LAKE_PRODUCED_INPUTS and name not in sm.V25_GATED_INPUTS
    )
    assert sm.V25_UNSOURCED_INPUTS == expected

    for name in ("sma150", "pct_above_ma50_60d", "atr_pct", "atr5", "drawdown"):
        assert name in sm.V25_UNSOURCED_INPUTS
    for name in ("keltner_upper", "keltner_lower", "smart_money_score"):
        assert name in sm.V25_UNSOURCED_INPUTS

    contract = sm.evaluate(
        technical={"latest_date": "2026-01-02", "expected_date": "2026-01-02"},
        coverage={"universe": 3000},
        screening={
            "state": "ready",
            "reason": "ok",
            "available": True,
            "detail": "Lake built.",
        },
        populations=_trigger_populations(),
        available_fields={"sma50", "sma200"},
        probe_fields=False,
    )["v25_input_contract"]
    assert contract["unsourced_v25_inputs"] == list(sm.V25_UNSOURCED_INPUTS)
    assert contract["gated_v25_inputs"] == list(sm.V25_GATED_INPUTS)


def test_the_trigger_scanner_is_unaffected_by_the_v25_gap():
    """TriggerScanner must keep working while v2.5 is blocked."""
    result = sm.evaluate(
        technical={"latest_date": "2026-01-02", "expected_date": "2026-01-02"},
        coverage={"universe": 3000},
        screening={
            "state": "ready",
            "reason": "ok",
            "available": True,
            "detail": "Lake built.",
        },
        populations=_trigger_populations(),
        available_fields={"sma50", "sma200"},
        probe_fields=False,
    )

    assert result["modes"][sm.MODE_TRIGGER]["state"] == sm.MODE_OPERATIONAL
    assert result["modes"][sm.MODE_V25_TURBO]["state"] == sm.MODE_BLOCKED


def test_the_growth_ranking_flag_is_still_off_by_default(monkeypatch):
    """Phase 6's opt-in is unchanged: the production ordering path stays put."""
    from myra_app import ranking_inputs

    monkeypatch.delenv(ranking_inputs.ENV_FLAG, raising=False)

    assert ranking_inputs.ENV_FLAG == "MYRA_V25_RANKING_INPUTS"
    assert ranking_inputs.ranking_inputs_enabled() is False
    assert ranking_inputs.METRIC_KEYS == (
        "roe",
        "ProfitGrowth",
        "avg_delivery_20d",
        "avg_volume_20d",
    )
