"""Research-only CPI P12 stage-independence diagnostic contract.

This verifies control-flow independence with injected stage callables. It does
not schedule collectors, validate evidence, perform market-data acquisition,
freeze a CPI event, or confer scientific/production authority. Unattended
release-day collectors must run in separate scheduled OS processes and satisfy
their own reviewed provenance and cutoff rules.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

DIAGNOSTIC_RECORD_TYPE = "CPI-P12-INDEPENDENT-STAGES-DIAGNOSTIC-v1"


class Stage(StrEnum):
    """Independent P12 acquisition lanes, ordered for deterministic diagnostics."""

    BLS_SEALED_SCHEDULE = "BLS_SEALED_SCHEDULE"
    KALSHI_MARKET = "KALSHI_MARKET"
    REUTERS_FORECAST = "REUTERS_FORECAST"


class AttemptOutcome(StrEnum):
    ATTEMPT_COMPLETED = "ATTEMPT_COMPLETED"
    ATTEMPT_FAILED = "ATTEMPT_FAILED"


class P12IndependenceContractError(ValueError):
    """One or more required independent diagnostic stages are absent or malformed."""


@dataclass(frozen=True, slots=True)
class StageAttempt:
    stage: Stage
    outcome: AttemptOutcome
    diagnostic_failure: str | None


@dataclass(frozen=True, slots=True)
class IsolationDiagnostic:
    """Attempt metadata only; never evidence or an authorization receipt."""

    attempts: tuple[StageAttempt, ...]

    def as_record(self) -> dict[str, Any]:
        return {
            "record_type": DIAGNOSTIC_RECORD_TYPE,
            "stage_attempts": [
                {
                    "stage": attempt.stage.value,
                    "outcome": attempt.outcome.value,
                    "diagnostic_failure": attempt.diagnostic_failure,
                }
                for attempt in self.attempts
            ],
            "scientific_evidence_authorized": False,
            "phase1_economic_test_authorized": False,
            "promotion_authority": "NONE",
            "production_influence": "0",
            "p12_frozen": False,
        }


def run_independent_stage_diagnostic(
    stages: Mapping[Stage, Callable[[], object]],
) -> IsolationDiagnostic:
    """Attempt *every* diagnostic even when an earlier stage raises.

    No callback result is used as evidence. Exceptions are converted into
    bounded diagnostic categories without persisting raw exception messages.
    The three input functions are not production acquisition adapters.
    """

    required = set(Stage)
    if (
        set(stages) != required
        or any(type(key) is not Stage for key in stages)
        or any(not callable(value) for value in stages.values())
    ):
        raise P12IndependenceContractError(
            "exactly three independently callable P12 diagnostic stages are required"
        )

    attempts: list[StageAttempt] = []
    for stage in Stage:
        try:
            stages[stage]()
        except Exception as exc:
            category = (
                "TRANSPORT_OR_TIMEOUT"
                if isinstance(exc, (OSError, TimeoutError))
                else "STAGE_EXCEPTION"
            )
            attempts.append(
                StageAttempt(
                    stage=stage,
                    outcome=AttemptOutcome.ATTEMPT_FAILED,
                    diagnostic_failure=category,
                )
            )
        else:
            attempts.append(
                StageAttempt(
                    stage=stage,
                    outcome=AttemptOutcome.ATTEMPT_COMPLETED,
                    diagnostic_failure=None,
                )
            )
    return IsolationDiagnostic(tuple(attempts))
