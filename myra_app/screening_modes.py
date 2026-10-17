"""Per-mode screening readiness (Phase 18).

Why this module exists
----------------------
Phase 16 taught readiness that *freshness* and *screening capability* are
different facts, and modelled screening as a single verdict: the indicator lake
is usable, or it is not. That was one axis too few, because MYRA has two
independent screening modes:

``trigger``
    The web/API :class:`TriggerScanner`, which reads ``technical_data``
    (OHLCV + delivery) and ``fundamentals`` market cap **directly**. It does not
    read the indicator lake and never produces ``MYRA_Score_v25``. Its output is
    ``trigger_score``.
``v25_turbo``
    The CLI/turbo positional-ranking path, which loads the indicator lake
    through ``universe_loader`` and produces ``MYRA_Score_v25``.

A single verdict over both modes produced two opposite lies:

* the working TriggerScanner was hidden behind a global ``Screening blocked``
  the moment the lake was empty, so a usable mode looked unusable; and
* on a machine with a built lake, a healthy TriggerScanner would make the
  broken v2.5 ranking look operational.

This module reports one state per mode and then an honest aggregate, using the
three states the Phase 18 brief defines:

``operational``
    The mode has the data and functioning calculations it needs.
``degraded``
    The mode can run, but optional inputs are missing or stale, so its output is
    qualified rather than wrong.
``blocked``
    The mode cannot execute correctly: a mandatory source is absent, or a
    genuine runtime defect prevents it.

Nothing here writes anything, fabricates a value, or turns a missing input into
a zero. A field that is not produced is reported as not produced.

Honesty rules encoded here
--------------------------
* A ``blocked`` v2.5 mode does **not** make the ``trigger`` mode blocked, and
  vice versa. The aggregate is ``blocked`` only when *no* mode can run.
* ``degraded`` is never silently promoted to ``operational``: a mode with
  unsourced required inputs is reported ``degraded`` even though the code
  would run and produce a number.
* The v2.5 mode additionally reports known runtime blockers found by inspecting
  the live modules, so the report is runtime-derived rather than asserted (see
  :func:`worker_path_blocker`).
"""

from __future__ import annotations

import inspect
import os
import re
from typing import Any, Callable, Optional

MODE_TRIGGER = "trigger"
MODE_V25_TURBO = "v25_turbo"

MODE_OPERATIONAL = "operational"
MODE_DEGRADED = "degraded"
MODE_BLOCKED = "blocked"

MODE_LABELS = {
    MODE_TRIGGER: "TriggerScanner (web/API)",
    MODE_V25_TURBO: "MYRA_Score_v25 turbo ranking (CLI)",
}

MODE_SCORES = {
    MODE_TRIGGER: "trigger_score",
    MODE_V25_TURBO: "MYRA_Score_v25",
}

#: A mode's output is only comparable with another mode that emits the same
#: score. Callers must not rank a ``trigger_score`` against a ``MYRA_Score_v25``.
MODE_COMPARABILITY = {
    MODE_TRIGGER: "Independent gate score. Not comparable with MYRA_Score_v25.",
    MODE_V25_TURBO: "v2.5 positional composite. Not comparable with trigger_score.",
}

#: Columns the trigger mode needs to compute anything at all. Without a close
#: and a volume there is no candidate to score.
TRIGGER_REQUIRED_COLUMNS = ("close", "volume")

#: Columns that qualify the trigger mode's output. Their absence is a genuine
#: limitation of the ranking (breakout proximity, delivery gates, trend gates),
#: but the mode still runs.
TRIGGER_CONTRIBUTING_COLUMNS = (
    "open",
    "high",
    "low",
    "delivery",
    "delivery_pct",
    "sma_50",
    "high_52w",
    "low_52w",
)

#: Below this share of the latest session, a contributing column degrades the
#: mode. At or below ``COLUMN_BLOCK_FRACTION`` a *required* column blocks it.
COLUMN_DEGRADE_FRACTION = 0.80
COLUMN_BLOCK_FRACTION = 0.50

#: The v2.5 inputs whose absence makes a published ``MYRA_Score_v25``
#: untrustworthy. Every one of them carries weight in the composite, and every
#: one of them is substituted rather than reported when it is missing: the
#: scorer falls back to a neutral constant (0.3 / 0.4) or, worse, receives the
#: zero that ``universe_loader`` writes in its place. The number that comes out
#: is then arithmetic on a placeholder, not a measurement of the stock - so a
#: score computed without these is not authoritative, however plausible it
#: looks. See ``docs/V25_TECHNICAL_SIGNAL_CONTRACT.md``.
V25_MANDATORY_INPUTS = (
    # trend component (weight 0.25) - the single heaviest technical block
    "sma50",
    "sma150",
    "sma200",
    # stability component (0.15)
    "pct_above_ma50_60d",
    # delivery component (0.20)
    "avg_delivery_20d",
    # liquidity component (0.10)
    "avg_volume_20d",
    # base component (0.10) - absent ATR is zero-filled, and a zero *earns the
    # highest* base score, so absence is currently rewarded rather than neutral
    "atr_pct",
)

#: Inputs the scorer consumes that only *qualify* the score. Their absence
#: withholds a bonus (the TTM-squeeze pair) or a soft penalty (drawdown), and
#: the component's documented neutral behaviour applies, so a score computed
#: without them still means what it says.
V25_OPTIONAL_INPUTS = (
    "keltner_upper",
    "keltner_lower",
    "atr5",
    "drawdown",
    "smart_money_score",
)

#: Every per-symbol input ``positional_engine`` reads through
#: ``score_components_v2``. This tuple is the requirement, not the capability:
#: the Phase 17/19 audit established which of these any producer actually
#: emits.
V25_REQUIRED_INPUTS = V25_MANDATORY_INPUTS + V25_OPTIONAL_INPUTS


#: The subset of :data:`V25_REQUIRED_INPUTS` the lake producer emits.
#: The rest are zero-filled by ``universe_loader`` when absent, which is why a
#: built lake is still not enough for a trustworthy v2.5 score.
V25_LAKE_PRODUCED_INPUTS = ("sma50", "sma200")

#: Inputs that *do* have a real producer, which is switched off by default:
#: ``ranking_inputs.compute_ranking_inputs`` measures the 20-session volume mean
#: and the 20-session mean of the daily delivery *percentage*, and
#: ``universe_loader`` only merges them when ``MYRA_V25_RANKING_INPUTS`` (or the
#: explicit argument) is enabled. They are therefore not definitional gaps - but
#: turning the flag on changes published scores and ordering, so it is an
#: operator decision, not a Phase 19 repair.
V25_GATED_INPUTS = ("avg_volume_20d", "avg_delivery_20d")

#: The v2.5 inputs that **no producer anywhere in the repository** writes - as
#: distinct from "absent from the lake" and from "present but switched off".
#: Established by the Phase 19 producer/consumer audit (see
#: ``docs/V25_TECHNICAL_SIGNAL_CONTRACT.md``): each was traced to its consumers
#: and has no writer, so building the lake will not supply them. They need
#: definitions and producers, not a build.
V25_UNSOURCED_INPUTS = tuple(
    name
    for name in V25_REQUIRED_INPUTS
    if name not in V25_LAKE_PRODUCED_INPUTS and name not in V25_GATED_INPUTS
)


def _pct(part: Optional[int], whole: Optional[int]) -> Optional[float]:
    if not whole or part is None:
        return None
    return round(float(part) / float(whole), 4)


def missing_v25_inputs(available: Optional[set]) -> list:
    """Which v2.5 inputs have no value in the source.

    ``available`` is the set of column/field names the lake actually holds, or
    ``None`` when the lake could not be read.
    """
    if available is None:
        return list(V25_REQUIRED_INPUTS)
    return [name for name in V25_REQUIRED_INPUTS if name not in available]


def classify_missing_v25_inputs(missing: list) -> dict:
    """Split a missing-input list into the mandatory and optional halves.

    The scorer always returns a number, so "does it run?" is the wrong
    question. This is the one that matters: is the number arithmetic on
    measured inputs, or on placeholders?
    """
    mandatory = [name for name in missing if name in V25_MANDATORY_INPUTS]
    optional = [name for name in missing if name in V25_OPTIONAL_INPUTS]
    return {"mandatory": mandatory, "optional": optional}


def lake_field_names() -> Optional[set]:
    """Field names in one lake artifact, read from its parquet metadata.

    Metadata only - one file, no column is materialised. ``None`` means the
    lake could not be read, which is reported as "unknown", never as "fine".
    """
    try:
        import pyarrow.parquet as pq

        from myra_app.indicator_lake import lake_dir

        directory = lake_dir()
        if not os.path.isdir(directory):
            return None
        names = [
            f for f in sorted(os.listdir(directory)) if f.lower().endswith(".parquet")
        ]
        if not names:
            return None
        return set(pq.ParquetFile(os.path.join(directory, names[0])).schema.names)
    except Exception:  # noqa: BLE001 - a probe must never raise
        return None


def worker_path_blocker() -> Optional[str]:
    """A known defect that stops the v2.5 worker path returning a candidate.

    Read from the live module source rather than asserted, so that fixing the
    defect makes this report clear itself instead of leaving a stale warning
    behind. ``worker_pool`` builds each candidate row and references
    ``fvg_mid`` on the success path; if nothing ever assigns it, the row
    construction raises ``NameError`` and *no* candidate can be returned,
    whatever the data contains.
    """
    try:
        from myra_app import worker_pool

        source = inspect.getsource(worker_pool)
    except Exception:  # noqa: BLE001
        return None
    if "fvg_mid" not in source:
        return None
    if re.search(r"^\s*fvg_mid\s*=", source, re.MULTILINE):
        return None
    return "worker_pool_fvg_mid_unassigned"


def polars_schema_blocker() -> Optional[str]:
    """Whether the v2.5 scan still reads Polars with implicit schema inference.

    ``pl.read_database`` infers column types from a sample of leading rows; the
    live table's leading rows are NULL-heavy, so inference fails. The fix is an
    explicit schema contract, not a larger ``infer_schema_length``. Reported as
    a blocker while the call remains implicit.
    """
    try:
        from myra_app import engine

        source = inspect.getsource(engine)
    except Exception:  # noqa: BLE001
        return None
    if "read_database" not in source:
        return None
    if "infer_schema_length" in source or "schema_overrides" in source:
        return None
    return "engine_read_database_implicit_schema"


def _trigger_mode(
    latest: Optional[str],
    expected: Optional[str],
    universe: Optional[int],
    populations: Optional[dict],
) -> dict[str, Any]:
    """Evaluate the web/API TriggerScanner mode from its own inputs."""
    mode: dict[str, Any] = {
        "mode": MODE_TRIGGER,
        "label": MODE_LABELS[MODE_TRIGGER],
        "score": MODE_SCORES[MODE_TRIGGER],
        "comparability": MODE_COMPARABILITY[MODE_TRIGGER],
        "reads": "technical_data (OHLCV + delivery), fundamentals (market cap)",
        "reads_lake": False,
        "missing": [],
    }

    if not universe:
        mode.update(
            state=MODE_BLOCKED,
            reason="universe_unavailable",
            detail=(
                "The screening universe could not be established, so the "
                "TriggerScanner has nothing to scan."
            ),
        )
        return mode

    if latest and expected and latest < expected:
        mode.update(
            state=MODE_DEGRADED,
            reason="source_stale",
            detail=(
                f"The newest stored session is {latest} but {expected} is "
                "expected; a scan would rank on stale prices."
            ),
        )
        return mode

    if populations:
        required_bad = [
            name
            for name in TRIGGER_REQUIRED_COLUMNS
            if (populations.get(name) or 0.0) <= COLUMN_BLOCK_FRACTION
        ]
        if required_bad:
            mode.update(
                state=MODE_BLOCKED,
                reason="required_columns_missing",
                detail=(
                    "The TriggerScanner's mandatory columns are largely empty "
                    f"for the latest session: {', '.join(sorted(required_bad))}."
                ),
                missing=sorted(required_bad),
            )
            return mode

        thin = [
            name
            for name in TRIGGER_CONTRIBUTING_COLUMNS
            if (populations.get(name) or 0.0) < COLUMN_DEGRADE_FRACTION
        ]
        if thin:
            mode.update(
                state=MODE_DEGRADED,
                reason="columns_partly_populated",
                detail=(
                    "The TriggerScanner can run, but these inputs are only "
                    "partly populated for the latest session, so some gates "
                    f"are weaker than they look: {', '.join(sorted(thin))}."
                ),
                missing=sorted(thin),
            )
            return mode

    mode.update(
        state=MODE_OPERATIONAL,
        reason="ok",
        detail=(
            "Raw OHLCV and delivery are current and sufficiently populated; "
            "the TriggerScanner needs no derived artifact."
        ),
    )
    return mode


def _v25_mode(
    screening: Optional[dict],
    available_fields: Optional[set],
) -> dict[str, Any]:
    """Evaluate the v2.5 turbo ranking mode from the lake and live modules."""
    mode: dict[str, Any] = {
        "mode": MODE_V25_TURBO,
        "label": MODE_LABELS[MODE_V25_TURBO],
        "score": MODE_SCORES[MODE_V25_TURBO],
        "comparability": MODE_COMPARABILITY[MODE_V25_TURBO],
        "reads": "indicator lake (data/indicators/precomputed)",
        "reads_lake": True,
        "missing": [],
        "missing_mandatory": [],
        "missing_optional": [],
        "blocked_by": [],
    }

    lake_state = (screening or {}).get("state")
    lake_reason = (screening or {}).get("reason")
    lake_detail = (screening or {}).get("detail")

    blocker = worker_path_blocker()
    if blocker:
        mode["blocked_by"].append(blocker)
    schema_blocker = polars_schema_blocker()
    if schema_blocker:
        mode["blocked_by"].append(schema_blocker)

    if lake_reason in ("probe_unavailable", "probe_failed"):
        mode.update(
            state=MODE_BLOCKED,
            reason="lake_probe_failed",
            detail=lake_detail or "The indicator lake could not be examined.",
        )
        return mode

    if not (screening or {}).get("available"):
        mode.update(
            state=MODE_BLOCKED,
            reason=lake_reason or "lake_empty",
            detail=(
                (lake_detail or "The indicator lake is empty.")
                + " Until it is built, MYRA_Score_v25 cannot be produced at all."
            ),
            missing=list(V25_REQUIRED_INPUTS),
            missing_mandatory=list(V25_MANDATORY_INPUTS),
            missing_optional=list(V25_OPTIONAL_INPUTS),
        )
        return mode

    if lake_state == "stale":
        mode.update(
            state=MODE_DEGRADED,
            reason="lake_stale",
            detail=lake_detail or "The indicator lake predates the stored session.",
        )
        return mode

    # The lake holds artifacts. Ask what it actually measured.
    missing = missing_v25_inputs(available_fields)
    mode["missing"] = missing
    split = classify_missing_v25_inputs(missing)
    mode["missing_mandatory"] = split["mandatory"]
    mode["missing_optional"] = split["optional"]

    if split["mandatory"]:
        # Not "degraded": the scorer would still return a number, and that
        # number would be arithmetic on substituted placeholders for the
        # weighted components. Publishing it as MYRA_Score_v25 is exactly the
        # outcome this phase exists to prevent, so the mode stays blocked until
        # the mandatory inputs have producers.
        detail = (
            f"The lake is present and readable, but it carries none of the "
            f"{len(split['mandatory'])} mandatory v2.5 inputs "
            f"({', '.join(split['mandatory'])}). Those would be substituted "
            "with neutral constants or zero-filled rather than measured, so any "
            "MYRA_Score_v25 computed from them would not be authoritative and "
            "must not be published."
        )
        if split["optional"]:
            detail += (
                f" A further {len(split['optional'])} optional input(s) are "
                f"also absent ({', '.join(split['optional'])}), which only "
                "withhold a squeeze bonus / drawdown penalty."
            )
        if mode["blocked_by"]:
            detail += (
                " The path is also blocked at runtime by: "
                + ", ".join(mode["blocked_by"])
                + "."
            )
        mode.update(
            state=MODE_BLOCKED, reason="mandatory_inputs_unsourced", detail=detail
        )
        return mode

    if split["optional"]:
        mode.update(
            state=MODE_DEGRADED,
            reason="optional_inputs_unsourced",
            detail=(
                f"Every mandatory v2.5 input is present. These optional inputs "
                f"are not ({', '.join(split['optional'])}), so the TTM-squeeze "
                "bonus and the drawdown penalty cannot engage; the published "
                "score keeps its meaning but is slightly less differentiated."
            ),
        )
        return mode

    if mode["blocked_by"]:
        mode.update(
            state=MODE_BLOCKED,
            reason="worker_path_defect",
            detail=(
                "The lake supplies every input the scorer needs, but the "
                "candidate-building path fails at runtime: "
                + ", ".join(mode["blocked_by"])
                + ". No candidate can be returned until this is repaired."
            ),
        )
        return mode

    mode.update(
        state=MODE_OPERATIONAL,
        reason="ok",
        detail="The indicator lake supplies every input MYRA_Score_v25 reads.",
    )
    return mode


def _aggregate(modes: dict) -> dict[str, Any]:
    """One honest status over all modes. Never stronger than the weakest mode."""
    operational = [m for m in modes.values() if m["state"] == MODE_OPERATIONAL]
    degraded = [m for m in modes.values() if m["state"] == MODE_DEGRADED]
    blocked = [m for m in modes.values() if m["state"] == MODE_BLOCKED]

    if operational and not degraded and not blocked:
        state, label = MODE_OPERATIONAL, "All screening modes operational"
        detail = "Every screening mode has the data it needs."
    elif operational:
        state = MODE_DEGRADED
        label = "Partial screening availability"
        names = ", ".join(m["label"] for m in operational)
        missing = ", ".join(m["label"] for m in blocked + degraded)
        detail = (
            f"{names} is operational, but {missing} is unavailable. "
            "Scanning can continue through the operational mode; results carry "
            "that mode's score, which is not interchangeable with the others."
        )
    elif degraded:
        state = MODE_DEGRADED
        label = "Screening degraded"
        detail = "No mode is fully operational; results are qualified."
    else:
        state = MODE_BLOCKED
        label = "Screening blocked"
        detail = "No screening mode can run with the data currently held."

    return {
        "state": state,
        "label": label,
        "detail": detail,
        "operational_modes": [m["mode"] for m in operational],
        "unavailable_modes": [m["mode"] for m in blocked + degraded],
        "blocked": [m["mode"] for m in blocked],
    }


def evaluate(
    technical: Optional[dict] = None,
    coverage: Optional[dict] = None,
    screening: Optional[dict] = None,
    populations: Optional[dict] = None,
    available_fields: Optional[set] = None,
    probe_fields: bool = True,
) -> dict[str, Any]:
    """One state per screening mode, plus an honest aggregate.

    Every expensive input is injectable so the module can be tested without a
    database, and so ``assess()`` can hand over facts it has already computed
    instead of re-querying. ``probe_fields`` reads one lake artifact's schema
    when the caller did not supply ``available_fields``.
    """
    technical = technical or {}
    latest = technical.get("latest_date")
    expected = technical.get("expected_date")
    universe = (coverage or {}).get("universe")

    if probe_fields and available_fields is None:
        available_fields = lake_field_names()

    modes = {
        MODE_TRIGGER: _trigger_mode(latest, expected, universe, populations),
        MODE_V25_TURBO: _v25_mode(screening, available_fields),
    }
    return {
        "overall": _aggregate(modes),
        "modes": modes,
        "required_v25_inputs": list(V25_REQUIRED_INPUTS),
        "lake_produced_v25_inputs": list(V25_LAKE_PRODUCED_INPUTS),
        "unsourced_v25_inputs": list(V25_UNSOURCED_INPUTS),
        # Which of the above a published MYRA_Score_v25 cannot survive losing.
        # A consumer must not have to re-derive this from the scorer's source.
        "v25_input_contract": {
            "mandatory": list(V25_MANDATORY_INPUTS),
            "optional": list(V25_OPTIONAL_INPUTS),
            "lake_produced": list(V25_LAKE_PRODUCED_INPUTS),
            "gated_v25_inputs": list(V25_GATED_INPUTS),
            "unsourced_v25_inputs": list(V25_UNSOURCED_INPUTS),
        },
    }


__all__ = [
    "MODE_TRIGGER",
    "MODE_V25_TURBO",
    "MODE_OPERATIONAL",
    "MODE_DEGRADED",
    "MODE_BLOCKED",
    "MODE_LABELS",
    "MODE_SCORES",
    "MODE_COMPARABILITY",
    "TRIGGER_REQUIRED_COLUMNS",
    "TRIGGER_CONTRIBUTING_COLUMNS",
    "V25_REQUIRED_INPUTS",
    "V25_MANDATORY_INPUTS",
    "V25_OPTIONAL_INPUTS",
    "V25_LAKE_PRODUCED_INPUTS",
    "V25_GATED_INPUTS",
    "V25_UNSOURCED_INPUTS",
    "COLUMN_DEGRADE_FRACTION",
    "COLUMN_BLOCK_FRACTION",
    "evaluate",
    "missing_v25_inputs",
    "classify_missing_v25_inputs",
    "lake_field_names",
    "worker_path_blocker",
    "polars_schema_blocker",
]
