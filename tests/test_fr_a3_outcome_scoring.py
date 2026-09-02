from datetime import UTC, datetime, timedelta

import pytest

import services.forward_reality.prospective_receipts as receipts
import services.forward_reality.trial_ledger as ledger_module
from services.forward_reality.outcome_scoring import (
    _CAPABILITY,
    OutcomeEvidenceAuthority,
    OutcomeScoringStore,
    ScoringError,
    score_trial,
)
from services.forward_reality.prospective_receipts import ProspectiveReceiptStore
from services.forward_reality.trial_ledger import EvaluationPlan, TrialLedger
from tests.test_fr_a1_prospective_receipts import make_receipt

NOW = datetime(2026, 8, 28, 12, tzinfo=UTC)


def test_authenticated_score_restarts_and_rejects_duplicate(tmp_path, monkeypatch):
    monkeypatch.setattr(receipts, "_utc_now", lambda: NOW + timedelta(hours=1))
    monkeypatch.setattr(ledger_module, "_fr_a2_utc_now", lambda: NOW)
    receipt = make_receipt()
    receipt_root = tmp_path / "receipts"
    receipt_root.mkdir()
    receipt_store = ProspectiveReceiptStore(receipt_root)
    receipt_store.publish(receipt)
    ledger = TrialLedger(tmp_path / "ledger")
    trial = ledger.register(
        candidate_family="candidate",
        model_identity="model:v1",
        feature_specification_identity="features:v1",
        evaluation_plan=EvaluationPlan(
            {"event_ticker": receipt.event_ticker, "predicate_identity": "binary-rule-v1"}
        ),
        underlying_event_id=receipt.underlying_event_id,
        reason="research",
        sibling_market_ids=(receipt.market_ticker,),
    )
    artifact = tmp_path / "outcome.json"
    artifact.write_text(
        f'{{"market_ticker":"{receipt.market_ticker}","event_ticker":"{receipt.event_ticker}",'
        f'"underlying_event_id":"{receipt.underlying_event_id}","observation_date":"2026-09-01",'
        '"status":"RESOLVED","value":1,"published_at":"2026-08-30T12:00:00+00:00",'
        '"revision_policy":"initial"}'
    )
    outcome = OutcomeEvidenceAuthority(
        tmp_path / "outcome-authority", _capability=_CAPABILITY
    ).issue(artifact=artifact)
    store = OutcomeScoringStore(tmp_path / "scores")
    record = score_trial(
        ledger=ledger,
        receipt_store=receipt_store,
        scoring_store=store,
        outcome_authority=OutcomeEvidenceAuthority(
            tmp_path / "outcome-authority",
            _capability=_CAPABILITY,
        ),
        trial_id=trial.trial_id,
        receipt=receipt,
        outcome=outcome,
    )
    assert record.brier_score == pytest.approx((0.58 - 1) ** 2)
    with pytest.raises(ScoringError, match="already been scored"):
        score_trial(
            ledger=ledger,
            receipt_store=receipt_store,
            scoring_store=OutcomeScoringStore(tmp_path / "scores"),
            outcome_authority=OutcomeEvidenceAuthority(
                tmp_path / "outcome-authority",
                _capability=_CAPABILITY,
            ),
            trial_id=trial.trial_id,
            receipt=receipt,
            outcome=outcome,
        )
    assert len(OutcomeScoringStore(tmp_path / "scores").records) == 1
    journal = (tmp_path / "scores").with_name("scores.journal")
    journal.write_bytes(journal.read_bytes().replace(b'"SCORED"', b'"FORGED"'))
    with pytest.raises(ScoringError, match="corrupt"):
        OutcomeScoringStore(tmp_path / "scores")


def test_tampered_score_journal_and_prepublication_outcome_fail_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(receipts, "_utc_now", lambda: NOW + timedelta(hours=1))
    monkeypatch.setattr(ledger_module, "_fr_a2_utc_now", lambda: NOW)
    receipt = make_receipt()
    root = tmp_path / "receipts"
    root.mkdir()
    receipt_store = ProspectiveReceiptStore(root)
    receipt_store.publish(receipt)
    ledger = TrialLedger(tmp_path / "ledger")
    trial = ledger.register(
        candidate_family="candidate",
        model_identity="model",
        feature_specification_identity="features",
        evaluation_plan=EvaluationPlan(
            {"event_ticker": receipt.event_ticker, "predicate_identity": "binary-rule-v1"}
        ),
        underlying_event_id=receipt.underlying_event_id,
        reason="research",
        sibling_market_ids=(receipt.market_ticker,),
    )
    artifact = tmp_path / "outcome.json"
    artifact.write_text(
        f'{{"market_ticker":"{receipt.market_ticker}","event_ticker":"{receipt.event_ticker}",'
        f'"underlying_event_id":"{receipt.underlying_event_id}","observation_date":"2026-09-01",'
        '"status":"RESOLVED","value":0,"published_at":"2026-08-28T12:00:00+00:00",'
        '"revision_policy":"initial"}'
    )
    outcome = OutcomeEvidenceAuthority(
        tmp_path / "outcome-authority", _capability=_CAPABILITY
    ).issue(artifact=artifact)
    with pytest.raises(ScoringError, match="published after registration"):
        score_trial(
            ledger=ledger,
            receipt_store=receipt_store,
            scoring_store=OutcomeScoringStore(tmp_path / "scores"),
            outcome_authority=OutcomeEvidenceAuthority(
                tmp_path / "outcome-authority", _capability=_CAPABILITY
            ),
            trial_id=trial.trial_id,
            receipt=receipt,
            outcome=outcome,
        )
