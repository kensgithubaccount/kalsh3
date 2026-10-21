"""Fault-injection tests for the P12 independence diagnostic control flow."""

from __future__ import annotations

import json

import pytest

from services.forecasting.cpi_p12_independent_stages import (
    AttemptOutcome,
    P12IndependenceContractError,
    Stage,
    run_independent_stage_diagnostic,
)


@pytest.mark.parametrize("failed_stage", list(Stage))
def test_single_failure_never_suppresses_other_stage_attempts(failed_stage: Stage) -> None:
    observed: list[Stage] = []

    def probe(stage: Stage) -> None:
        observed.append(stage)
        if stage is failed_stage:
            raise OSError("raw internal source URL must never enter diagnostic")

    diagnostic = run_independent_stage_diagnostic(
        {stage: (lambda stage=stage: probe(stage)) for stage in Stage}
    )
    assert observed == list(Stage)
    assert tuple(item.stage for item in diagnostic.attempts) == tuple(Stage)
    for attempt in diagnostic.attempts:
        if attempt.stage is failed_stage:
            assert attempt.outcome is AttemptOutcome.ATTEMPT_FAILED
            assert attempt.diagnostic_failure == "TRANSPORT_OR_TIMEOUT"
        else:
            assert attempt.outcome is AttemptOutcome.ATTEMPT_COMPLETED
            assert attempt.diagnostic_failure is None

    record = diagnostic.as_record()
    assert record["scientific_evidence_authorized"] is False
    assert record["phase1_economic_test_authorized"] is False
    assert record["promotion_authority"] == "NONE"
    assert record["production_influence"] == "0"
    assert record["p12_frozen"] is False
    assert "raw internal source URL" not in json.dumps(record)


def test_all_failures_are_preserved_and_cannot_claim_pass() -> None:
    observed: list[Stage] = []

    def broken(stage: Stage) -> None:
        observed.append(stage)
        raise ValueError("sensitive evidence")

    diagnostic = run_independent_stage_diagnostic(
        {stage: (lambda stage=stage: broken(stage)) for stage in Stage}
    )
    assert observed == list(Stage)
    assert all(attempt.outcome is AttemptOutcome.ATTEMPT_FAILED for attempt in diagnostic.attempts)
    assert all(attempt.diagnostic_failure == "STAGE_EXCEPTION" for attempt in diagnostic.attempts)
    assert "sensitive evidence" not in json.dumps(diagnostic.as_record())


def test_successful_callbacks_never_grant_scientific_authority() -> None:
    diagnostic = run_independent_stage_diagnostic(
        {
            stage: (lambda: {"accepted": True, "scientific_evidence_authorized": True})
            for stage in Stage
        }
    )
    assert all(
        attempt.outcome is AttemptOutcome.ATTEMPT_COMPLETED for attempt in diagnostic.attempts
    )
    record = diagnostic.as_record()
    assert record["scientific_evidence_authorized"] is False
    assert record["phase1_economic_test_authorized"] is False
    assert record["p12_frozen"] is False
    assert "accepted" not in json.dumps(record)


def test_incomplete_stage_set_fails_before_any_callback_runs() -> None:
    calls: list[str] = []

    with pytest.raises(P12IndependenceContractError):
        run_independent_stage_diagnostic({Stage.KALSHI_MARKET: lambda: calls.append("kalshi")})
    assert calls == []


def test_string_alias_is_not_a_valid_stage_identity() -> None:
    calls: list[str] = []
    aliases = {stage.value: (lambda: calls.append("called")) for stage in Stage}
    with pytest.raises(P12IndependenceContractError):
        run_independent_stage_diagnostic(aliases)  # type: ignore[arg-type]
    assert calls == []


def test_noncallable_diagnostic_fails_before_any_stage() -> None:
    calls: list[str] = []
    mapping = {
        Stage.BLS_SEALED_SCHEDULE: lambda: calls.append("bls"),
        Stage.KALSHI_MARKET: lambda: calls.append("kalshi"),
        Stage.REUTERS_FORECAST: None,
    }
    with pytest.raises(P12IndependenceContractError):
        run_independent_stage_diagnostic(mapping)  # type: ignore[arg-type]
    assert calls == []
