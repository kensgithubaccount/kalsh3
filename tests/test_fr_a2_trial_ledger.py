from datetime import UTC, datetime, timedelta

import pytest

from services.forward_reality.trial_ledger import (
    EvaluationPlan,
    LedgerError,
    TrialLedger,
    TrialStatus,
)

NOW = datetime(2026, 8, 31, 12, tzinfo=UTC)


def make_ledger() -> TrialLedger:
    moments = iter((NOW, NOW + timedelta(seconds=1), NOW + timedelta(seconds=2)))
    return TrialLedger(issuer_clock=lambda: next(moments))


def register(ledger: TrialLedger, *, ticker: str = "CPI-YES"):
    return ledger.register(
        candidate_family="weather.baseline",
        model_identity="model:v1",
        feature_specification_identity="features:v1",
        evaluation_plan=EvaluationPlan({"split": "prospective", "horizon": 1}),
        underlying_event_id="cpi-release:2026-08",
        reason="new serious candidate",
        sibling_market_ids=(ticker,),
    )


def test_registration_is_content_addressed_research_only_and_issuer_timed() -> None:
    ledger = make_ledger()
    trial = register(ledger)
    assert trial.trial_id.startswith("trial-") and len(trial.trial_id) == 70
    assert trial.created_at == NOW
    assert trial.research_only is True
    assert trial.production_influence == 0
    assert trial.content_hash
    assert ledger.events[0].status is TrialStatus.PLANNED


def test_status_is_append_only_and_does_not_change_identity() -> None:
    ledger = make_ledger()
    planned = register(ledger)
    running = ledger.advance(planned.trial_id, TrialStatus.RUNNING)
    failed = ledger.advance(planned.trial_id, TrialStatus.FAILED)
    assert running.trial_id == failed.trial_id == planned.trial_id
    assert running.content_hash == planned.content_hash
    assert [event.status for event in ledger.events] == [
        TrialStatus.PLANNED,
        TrialStatus.RUNNING,
        TrialStatus.FAILED,
    ]
    with pytest.raises(LedgerError, match="terminal"):
        ledger.advance(planned.trial_id, TrialStatus.RUNNING)


def test_sibling_markets_share_one_independent_event_trial() -> None:
    ledger = make_ledger()
    first = register(ledger, ticker="CPI-YES")
    second = ledger.register(
        candidate_family="weather.baseline",
        model_identity="model:v2",
        feature_specification_identity="features:v1",
        evaluation_plan=EvaluationPlan({"split": "prospective", "horizon": 1}),
        underlying_event_id=first.underlying_event_id,
        reason="sibling threshold attempt",
        sibling_market_ids=("CPI-NO",),
    )
    assert {t.trial_id for t in ledger.trials_for_event(first.underlying_event_id)} == {
        first.trial_id,
        second.trial_id,
    }
    assert ledger.independent_trial_count(first.underlying_event_id) == 1


def test_duplicate_identity_and_definition_mutation_fail_closed() -> None:
    ledger = make_ledger()
    register(ledger)
    with pytest.raises(LedgerError, match="duplicate trial identity"):
        register(ledger)
    with pytest.raises(LedgerError, match="evaluation plan"):
        ledger.register(
            candidate_family="x",
            model_identity="x",
            feature_specification_identity="x",
            evaluation_plan={"split": "caller"},
            underlying_event_id="e",
            reason="x",
        )


def test_caller_cannot_author_created_at_or_rewrite_terminal_trial() -> None:
    ledger = make_ledger()
    with pytest.raises(TypeError):
        ledger.register(
            candidate_family="x",
            model_identity="x",
            feature_specification_identity="x",
            evaluation_plan=EvaluationPlan({"split": "x"}),
            underlying_event_id="e",
            reason="x",
            created_at=NOW,  # type: ignore[call-arg]
        )
    trial = register(ledger)
    ledger.advance(trial.trial_id, TrialStatus.ABANDONED)
    with pytest.raises(LedgerError, match="terminal"):
        ledger.advance(trial.trial_id, TrialStatus.COMPLETED)
