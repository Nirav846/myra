"""Explicit outcomes for a screening run.

A scan that produces no candidates and a scan that *failed* used to be the same
value to every caller: an empty list. ``Engine.run_scan`` caught its own
exceptions, printed a line, and returned ``([], {})`` — byte-identical to the
result of a strategy that legitimately matched nothing. The operator could not
tell "the market offered nothing today" from "MYRA could not read its own
indicator data", which is precisely how an empty indicator lake went unnoticed.

This module is the single vocabulary for that distinction. It is deliberately
small and dependency-free so both the engine (which imports the pandas stack)
and the readiness probe (which must stay cheap) can share it.

The statuses are the six outcomes a screening run can have:

``success_with_candidates``
    The run finished and produced at least one candidate.
``success_no_candidates``
    The run finished and legitimately matched nothing. This is a *success*: a
    zero-result scan is information, not an error.
``failed_missing_required_artifacts``
    A derived artifact the run depends on is absent — e.g. the indicator lake
    has never been built. Nothing was screened.
``failed_invalid_input_schema``
    The inputs exist but do not satisfy the contract the run relies on.
``failed_indicator_computation``
    The indicator/feature computation itself raised.
``failed_internal_error``
    Anything else that stopped the run before a verdict was reached.

Only ``success_*`` statuses mean "this is a real answer". Everything else means
"we do not know, and here is why".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

# ── Success ──────────────────────────────────────────────────────────────────

SUCCESS_WITH_CANDIDATES = "success_with_candidates"
SUCCESS_NO_CANDIDATES = "success_no_candidates"

# ── Failure ──────────────────────────────────────────────────────────────────

FAILED_MISSING_REQUIRED_ARTIFACTS = "failed_missing_required_artifacts"
FAILED_INVALID_INPUT_SCHEMA = "failed_invalid_input_schema"
FAILED_INDICATOR_COMPUTATION = "failed_indicator_computation"
FAILED_INTERNAL_ERROR = "failed_internal_error"

SUCCESS_STATUSES = (SUCCESS_WITH_CANDIDATES, SUCCESS_NO_CANDIDATES)

FAILURE_STATUSES = (
    FAILED_MISSING_REQUIRED_ARTIFACTS,
    FAILED_INVALID_INPUT_SCHEMA,
    FAILED_INDICATOR_COMPUTATION,
    FAILED_INTERNAL_ERROR,
)

ALL_STATUSES = SUCCESS_STATUSES + FAILURE_STATUSES

#: Plain-language rendering, for a line an operator actually reads.
STATUS_LABELS = {
    SUCCESS_WITH_CANDIDATES: "Completed with candidates",
    SUCCESS_NO_CANDIDATES: "Completed, nothing matched",
    FAILED_MISSING_REQUIRED_ARTIFACTS: "Failed: required data is missing",
    FAILED_INVALID_INPUT_SCHEMA: "Failed: stored data is malformed",
    FAILED_INDICATOR_COMPUTATION: "Failed: indicator computation error",
    FAILED_INTERNAL_ERROR: "Failed: internal error",
}


@dataclass(frozen=True)
class ScanOutcome:
    """What a scan actually did.

    ``reason`` is a stable, machine-comparable code; ``detail`` is the
    human-readable specifics. They are kept separate for the same reason
    ``ReadinessRecovery`` keeps them separate: a caller that wants to branch
    should never have to parse prose, and an operator should never have to
    decode a code.
    """

    status: str
    reason: str
    detail: str = ""
    candidates: int = 0
    error_type: Optional[str] = None

    def __post_init__(self) -> None:
        if self.status not in ALL_STATUSES:
            raise ValueError(f"Unknown scan status: {self.status!r}")

    @property
    def ok(self) -> bool:
        """True only when the run reached a real verdict (with or without hits)."""
        return self.status in SUCCESS_STATUSES

    @property
    def failed(self) -> bool:
        return not self.ok

    @property
    def has_candidates(self) -> bool:
        return self.status == SUCCESS_WITH_CANDIDATES

    @property
    def label(self) -> str:
        return STATUS_LABELS.get(self.status, self.status)

    def describe(self) -> str:
        """One operator-facing line: label, reason and (optionally) detail."""
        text = f"{self.label} ({self.reason})"
        if self.detail:
            text = f"{text}: {self.detail}"
        return text

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "label": self.label,
            "reason": self.reason,
            "detail": self.detail,
            "candidates": self.candidates,
            "error_type": self.error_type,
            "ok": self.ok,
        }


__all__ = [
    "SUCCESS_WITH_CANDIDATES",
    "SUCCESS_NO_CANDIDATES",
    "FAILED_MISSING_REQUIRED_ARTIFACTS",
    "FAILED_INVALID_INPUT_SCHEMA",
    "FAILED_INDICATOR_COMPUTATION",
    "FAILED_INTERNAL_ERROR",
    "SUCCESS_STATUSES",
    "FAILURE_STATUSES",
    "ALL_STATUSES",
    "STATUS_LABELS",
    "ScanOutcome",
]
