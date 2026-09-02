from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import services.forward_reality.prospective_receipts as receipts
import services.forward_reality.trial_ledger as ledger_module
from services.forward_reality.outcome_scoring import (
    _CAPABILITY,
    OutcomeEvidenceAuthority,
    OutcomeScoringStore,
    OutcomeStatus,
    ReplayContext,
    ScoringError,
    ScoringRecord,
    score_trial,
)
from services.forward_reality.prospective_receipts import ProspectiveReceiptStore
from services.forward_reality.trial_ledger import EvaluationPlan, TrialLedger
from tests.test_fr_a1_prospective_receipts import make_receipt

NOW = datetime(2026, 8, 28, 12, tzinfo=UTC)


def _scenario(
    tmp_path,
    status=ledger_module.TrialStatus.PLANNED,
    outcome_status="RESOLVED",
    abstained=False,
):
    receipt_changes: dict[str, object] = {
        "abstention_reason": "explicit research abstention" if abstained else None
    }
    if abstained:
        receipt_changes.update(
            calibrated_probability=None, lower_probability=None, upper_probability=None
        )
    receipt = make_receipt(**receipt_changes)
    (tmp_path / "receipts").mkdir(parents=True)
    receipt_store = ProspectiveReceiptStore(tmp_path / "receipts")
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
    if status is ledger_module.TrialStatus.RUNNING:
        trial = ledger.advance(trial.trial_id, status)
    elif status is ledger_module.TrialStatus.COMPLETED:
        ledger.advance(trial.trial_id, ledger_module.TrialStatus.RUNNING)
        trial = ledger.advance(trial.trial_id, status)
    elif status in (ledger_module.TrialStatus.FAILED, ledger_module.TrialStatus.ABANDONED):
        trial = ledger.advance(trial.trial_id, status)
    artifact = tmp_path / "outcome.json"
    value = "1" if outcome_status == "RESOLVED" else "null"
    artifact.write_text(
        f'{{"market_ticker":"{receipt.market_ticker}","event_ticker":"{receipt.event_ticker}",'
        f'"underlying_event_id":"{receipt.underlying_event_id}","observation_date":"2026-09-01",'
        f'"status":"{outcome_status}","value":{value},'
        '"published_at":"2026-08-30T12:00:00+00:00","revision_policy":"initial"}'
    )
    authority = OutcomeEvidenceAuthority(tmp_path / "authority", _capability=_CAPABILITY)
    outcome = authority.issue(artifact=artifact)
    return trial, receipt, receipt_store, ledger, authority, outcome


def test_nonterminal_lifecycle_is_retryable_without_journal_mutation(tmp_path, monkeypatch):
    monkeypatch.setattr(receipts, "_utc_now", lambda: NOW + timedelta(hours=1))
    monkeypatch.setattr(ledger_module, "_fr_a2_utc_now", lambda: NOW)
    for status in (ledger_module.TrialStatus.PLANNED, ledger_module.TrialStatus.RUNNING):
        trial, receipt, receipt_store, ledger, authority, outcome = _scenario(
            tmp_path / status.value, status
        )
        store = OutcomeScoringStore.create(tmp_path / status.value / "scores")
        before = (tmp_path / status.value / "scores.journal").read_bytes()
        with pytest.raises(ScoringError, match="not terminal"):
            score_trial(
                ledger=ledger,
                receipt_store=receipt_store,
                scoring_store=store,
                outcome_authority=authority,
                trial_id=trial.trial_id,
                receipt=receipt,
                outcome=outcome,
            )
        assert (tmp_path / status.value / "scores.journal").read_bytes() == before


def test_pending_outcome_does_not_consume_trial_identity(tmp_path, monkeypatch):
    monkeypatch.setattr(receipts, "_utc_now", lambda: NOW + timedelta(hours=1))
    monkeypatch.setattr(ledger_module, "_fr_a2_utc_now", lambda: NOW)
    trial, receipt, receipt_store, ledger, authority, outcome = _scenario(
        tmp_path, ledger_module.TrialStatus.COMPLETED, "PENDING"
    )
    store = OutcomeScoringStore.create(tmp_path / "scores")
    with pytest.raises(ScoringError, match="not final"):
        score_trial(
            ledger=ledger,
            receipt_store=receipt_store,
            scoring_store=store,
            outcome_authority=authority,
            trial_id=trial.trial_id,
            receipt=receipt,
            outcome=outcome,
        )
    assert store.records == ()


@pytest.mark.parametrize("trial_status", list(ledger_module.TrialStatus))
@pytest.mark.parametrize("outcome_status", [status.value for status in OutcomeStatus])
@pytest.mark.parametrize("abstained", [False, True])
def test_exhaustive_terminal_outcome_matrix_is_append_only(
    tmp_path, monkeypatch, trial_status, outcome_status, abstained
):
    monkeypatch.setattr(receipts, "_utc_now", lambda: NOW + timedelta(hours=1))
    monkeypatch.setattr(ledger_module, "_fr_a2_utc_now", lambda: NOW)
    trial, receipt, receipt_store, ledger, authority, outcome = _scenario(
        tmp_path, trial_status, outcome_status, abstained
    )
    store = OutcomeScoringStore.create(tmp_path / "scores")
    journal = (tmp_path / "scores.journal").read_bytes()
    checkpoint = (tmp_path / "scores.head").read_bytes()
    final = trial_status in (
        ledger_module.TrialStatus.COMPLETED,
        ledger_module.TrialStatus.FAILED,
        ledger_module.TrialStatus.ABANDONED,
    )
    pending = outcome_status in {"PENDING", "UNKNOWN"}
    valid = final and not pending
    if valid:
        record = score_trial(
            ledger=ledger,
            receipt_store=receipt_store,
            scoring_store=store,
            outcome_authority=authority,
            trial_id=trial.trial_id,
            receipt=receipt,
            outcome=outcome,
        )
        if trial_status is ledger_module.TrialStatus.COMPLETED and outcome_status == "RESOLVED":
            assert record.score_status == ("ABSTAINED" if abstained else "SCORED")
        else:
            assert record.score_status == "TERMINAL_UNSCORED"
        context = ReplayContext(
            outcome_authority=authority, receipt_store=receipt_store, ledger=ledger
        )
        assert len(OutcomeScoringStore.open(tmp_path / "scores", context=context).records) == 1
    else:
        with pytest.raises(ScoringError):
            score_trial(
                ledger=ledger,
                receipt_store=receipt_store,
                scoring_store=store,
                outcome_authority=authority,
                trial_id=trial.trial_id,
                receipt=receipt,
                outcome=outcome,
            )
        assert (tmp_path / "scores.journal").read_bytes() == journal
        assert (tmp_path / "scores.head").read_bytes() == checkpoint
        assert store.records == ()


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
    ledger.advance(trial.trial_id, ledger_module.TrialStatus.RUNNING)
    trial = ledger.advance(trial.trial_id, ledger_module.TrialStatus.COMPLETED)
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
    store = OutcomeScoringStore.create(tmp_path / "scores")
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
        context = ReplayContext(
            outcome_authority=OutcomeEvidenceAuthority(
                tmp_path / "outcome-authority", _capability=_CAPABILITY
            ),
            receipt_store=receipt_store,
            ledger=ledger,
        )
        score_trial(
            ledger=ledger,
            receipt_store=receipt_store,
            scoring_store=OutcomeScoringStore.open(tmp_path / "scores", context=context),
            outcome_authority=OutcomeEvidenceAuthority(
                tmp_path / "outcome-authority",
                _capability=_CAPABILITY,
            ),
            trial_id=trial.trial_id,
            receipt=receipt,
            outcome=outcome,
        )
    context = ReplayContext(
        outcome_authority=OutcomeEvidenceAuthority(
            tmp_path / "outcome-authority", _capability=_CAPABILITY
        ),
        receipt_store=receipt_store,
        ledger=ledger,
    )
    assert len(OutcomeScoringStore.open(tmp_path / "scores", context=context).records) == 1
    journal = (tmp_path / "scores").with_name("scores.journal")
    journal.write_bytes(journal.read_bytes().replace(b'"SCORED"', b'"FORGED"'))
    with pytest.raises(ScoringError, match=r"journal corrupt|journal authentication mismatch"):
        OutcomeScoringStore.open(tmp_path / "scores", context=context)


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
    ledger.advance(trial.trial_id, ledger_module.TrialStatus.RUNNING)
    trial = ledger.advance(trial.trial_id, ledger_module.TrialStatus.COMPLETED)
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
            scoring_store=OutcomeScoringStore.create(tmp_path / "scores"),
            outcome_authority=OutcomeEvidenceAuthority(
                tmp_path / "outcome-authority", _capability=_CAPABILITY
            ),
            trial_id=trial.trial_id,
            receipt=receipt,
            outcome=outcome,
        )


def test_public_append_and_frozen_artifact_mutation_fail_closed(tmp_path):
    artifact = tmp_path / "outcome.json"
    artifact.write_text(
        '{"market_ticker":"M","event_ticker":"E","underlying_event_id":"U",'
        '"observation_date":"2026-09-01","status":"RESOLVED","value":1,'
        '"published_at":"2026-08-30T12:00:00+00:00","revision_policy":"initial"}'
    )
    authority = OutcomeEvidenceAuthority(tmp_path / "authority", _capability=_CAPABILITY)
    receipt = authority.issue(artifact=artifact)
    archive = Path(receipt.artifact_locator)
    archive.write_bytes(archive.read_bytes().replace(b'"value":1', b'"value":0'))
    with pytest.raises(ScoringError, match="artifact changed"):
        authority.validate(receipt)
    with pytest.raises(ScoringError, match="public scoring append"):
        OutcomeScoringStore.create(tmp_path / "scores").append(
            ScoringRecord(
                trial_id="trial",
                forecast_receipt_id="receipt",
                forecast_receipt_hash="hash",
                registration_history_identity="history",
                market_ticker="M",
                event_ticker="E",
                underlying_event_id="U",
                candidate_family="candidate",
                model_identity="model",
                calibrator_identity="calibrator",
                decision_cutoff=NOW,
                scored_at=NOW,
                forecast_probability=None,
                outcome_receipt=receipt,
                score_status="UNKNOWN",
                brier_score=None,
                log_loss=None,
                scoring_policy_version="policy",
                record_identity="record",
            )
        )


def test_fixture_capability_is_not_a_public_production_boundary():
    exported = Path("services/forward_reality/__init__.py").read_text()
    assert "_CAPABILITY" not in exported
    production = list(Path("services").rglob("*.py"))
    assert all(
        "fr-a3-test-source-v1" not in path.read_text()
        for path in production
        if path.name != "outcome_scoring.py"
    )
    assert all(
        "outcome_scoring import _CAPABILITY" not in path.read_text()
        for path in production
        if path.name != "outcome_scoring.py"
    )


def test_append_capability_has_one_repository_call_boundary():
    references = [
        path for path in Path("services").rglob("*.py") if "_APPEND_CAPABILITY" in path.read_text()
    ]
    assert references == [Path("services/forward_reality/outcome_scoring.py")]
    source = Path("services/forward_reality/outcome_scoring.py").read_text()
    assert source.count("_append(record, _capability=_APPEND_CAPABILITY)") == 1
