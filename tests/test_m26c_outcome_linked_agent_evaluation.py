"""M26C outcome linkage, scoring, evidence projection, and persistence."""

from __future__ import annotations

import io
import sqlite3
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

import pytest

from services.agent_control_center.attribution import OpportunityAttribution, receipt_identity
from services.agent_control_center.beliefs import FreshnessPolicy
from services.agent_control_center.domain import (
    AGENT_REGISTRY,
    DecisionReceipt,
    ResearchDecision,
)
from services.agent_control_center.evaluation import (
    EVALUATION_DATASET_SEMANTICS,
    EVALUATION_POLICY_VERSION,
    EvaluationEligibility,
    EvaluationError,
    EvaluationTarget,
    EvidenceState,
    ReceiptEvaluation,
    calibration,
    effective_evaluations,
    evaluate_receipt,
    performance,
    settlement_hash,
)
from services.agent_control_center.evaluation_store import EvaluationStore, EvaluationStoreError
from services.agent_control_center.store import DecisionReceiptStore
from services.contract_intelligence.settlement import ReconciliationStatus, SettlementRecord
from services.opportunity_engine.books import OutcomeSide
from services.web_dashboard.app import DashboardApp
from services.web_dashboard.security import SecretBox, hash_password
from services.web_dashboard.store import StateStore

NOW = datetime(2026, 1, 1, 12, tzinfo=UTC)
SOURCE_ID = "a" * 64
SOURCE_HASH = SOURCE_ID


def receipt(
    *,
    side: OutcomeSide = OutcomeSide.YES,
    decision: ResearchDecision = ResearchDecision.WOULD_TRADE,
    created_at: datetime = NOW,
    probability: Decimal | None = Decimal("0.7"),
    source_id: str = SOURCE_ID,
    ticker: str = "TEST",
) -> DecisionReceipt:
    return DecisionReceipt(
        receipt_identity("event-edge", "1.0.0", "trade-candidate", source_id),
        created_at,
        "event-edge",
        "1.0.0",
        f"{ticker}:{side.value}",
        Decimal("0.6"),
        probability,
        None,
        Decimal("0.1"),
        Decimal("0.01"),
        Decimal("0.02"),
        Decimal("0.07"),
        None,
        ("forecast:f", "market-snapshot:b"),
        None,
        (),
        (),
        decision,
        (),
        () if decision is ResearchDecision.WOULD_TRADE else ("RESEARCH_THRESHOLD",),
    )


def target(
    side: OutcomeSide = OutcomeSide.YES,
    *,
    observed_at: datetime = NOW,
    source_id: str = SOURCE_ID,
    ticker: str = "TEST",
) -> EvaluationTarget:
    return EvaluationTarget(ticker, side, "trade-candidate", source_id, observed_at, source_id)


def settlement(
    *,
    result: str = "YES",
    finalized_at: datetime | None = NOW + timedelta(hours=2),
    status: ReconciliationStatus = ReconciliationStatus.MATCHED,
    ticker: str = "TEST",
    raw_hash: str = "b" * 64,
    settlement_value: Decimal | None = None,
) -> SettlementRecord:
    return SettlementRecord(
        ticker,
        "rules-v1",
        "spec-v1",
        result,
        Decimal(1 if result == "YES" else 0) if settlement_value is None else settlement_value,
        NOW + timedelta(hours=1),
        finalized_at,
        raw_hash,
        "source-observation",
        status,
    )


def evaluated(**values: object) -> ReceiptEvaluation:
    policy_version = cast(str, values.pop("policy_version", EVALUATION_POLICY_VERSION))
    if values.keys() - {"receipt", "settlement", "target", "evaluated_at"}:
        raise ValueError("unsupported evaluation test parameter")
    return evaluate_receipt(
        cast(DecisionReceipt, values.pop("receipt", receipt())),
        cast(SettlementRecord, values.pop("settlement", settlement())),
        cast(EvaluationTarget, values.pop("target", target())),
        evaluated_at=cast(datetime, values.pop("evaluated_at", NOW + timedelta(hours=3))),
        market_family="test",
        policy_version=policy_version,
    )


def test_authoritative_finalized_matched_yes_and_no_scores_are_exact() -> None:
    yes = evaluated()
    no = evaluated(
        receipt=receipt(side=OutcomeSide.NO, probability=Decimal("0.3")),
        target=target(OutcomeSide.NO),
    )
    assert yes.eligibility is no.eligibility is EvaluationEligibility.ELIGIBLE
    assert yes.realized_target == 1 and yes.model_brier == Decimal("0.09")
    assert no.realized_target == 0 and no.model_brier == Decimal("0.09")
    assert yes.market_brier == Decimal("0.16")
    assert yes.market_relative_brier_improvement == Decimal("0.07")
    assert yes.counterfactual_unit_value == Decimal("0.37")
    assert isinstance(yes.model_brier, Decimal)


@pytest.mark.parametrize(
    ("result", "settlement_value"),
    [("YES", Decimal(0)), ("NO", Decimal(1))],
)
def test_contradictory_binary_settlement_fields_are_never_scored(
    result: str, settlement_value: Decimal
) -> None:
    value = evaluated(settlement=settlement(result=result, settlement_value=settlement_value))
    assert value.eligibility is EvaluationEligibility.UNSUPPORTED
    assert "contradicts" in str(value.exclusion_detail)
    assert value.realized_target is None
    assert value.model_brier is None
    assert value.counterfactual_unit_value is None


@pytest.mark.parametrize(
    ("finalized", "status", "expected"),
    [
        (None, ReconciliationStatus.MATCHED, EvaluationEligibility.OUTCOME_NOT_FINAL),
        (NOW, ReconciliationStatus.UNRECONCILED, EvaluationEligibility.RECONCILIATION_MISMATCH),
        (NOW, ReconciliationStatus.MISMATCH, EvaluationEligibility.RECONCILIATION_MISMATCH),
    ],
)
def test_ineligible_settlements_are_preserved_without_manufactured_metrics(
    finalized: datetime | None,
    status: ReconciliationStatus,
    expected: EvaluationEligibility,
) -> None:
    value = evaluated(settlement=settlement(finalized_at=finalized, status=status))
    assert value.eligibility is expected
    assert value.realized_target is None
    assert value.model_brier is None
    assert value.counterfactual_unit_value is None


def test_exact_linkage_and_orientation_fail_closed() -> None:
    with pytest.raises(EvaluationError, match="orientation"):
        evaluated(target=target(OutcomeSide.NO))
    with pytest.raises(EvaluationError, match="ticker"):
        evaluated(settlement=settlement(ticker="WRONG"))
    wrong_source = replace(target(), source_id="c" * 64, source_content_hash="c" * 64)
    value = evaluated(target=wrong_source)
    assert value.eligibility is EvaluationEligibility.TARGET_UNPROVEN


def test_temporal_leakage_cannot_inflate_performance() -> None:
    leaked = evaluated(receipt=receipt(created_at=NOW + timedelta(hours=1)))
    assert leaked.eligibility is EvaluationEligibility.TEMPORAL_LEAKAGE_RISK
    source_future = evaluated(target=target(observed_at=NOW + timedelta(seconds=1)))
    assert source_future.eligibility is EvaluationEligibility.TEMPORAL_LEAKAGE_RISK
    projection = performance(
        (leaked, source_future),
        agent_id="event-edge",
        agent_version="1.0.0",
        total_persisted_decisions=2,
    )
    assert projection.eligible_scored_decisions == 0
    assert projection.evidence_state is EvidenceState.NO_EVIDENCE


def test_unsupported_probability_and_cross_market_are_not_force_scored() -> None:
    missing = evaluated(receipt=receipt(probability=None))
    assert missing.eligibility is EvaluationEligibility.TARGET_UNPROVEN
    cross = replace(
        receipt(),
        receipt_id=receipt_identity("cross-market", "1.0.0", "trade-candidate", SOURCE_ID),
        agent_id="cross-market",
    )
    value = evaluate_receipt(cross, settlement(), target(), evaluated_at=NOW + timedelta(hours=3))
    assert value.eligibility is EvaluationEligibility.UNSUPPORTED
    assert value.model_brier is None


@pytest.mark.parametrize("decision", tuple(ResearchDecision))
def test_all_decision_classes_are_first_class(decision: ResearchDecision) -> None:
    value = evaluated(receipt=receipt(decision=decision))
    assert value.decision is decision and value.eligibility is EvaluationEligibility.ELIGIBLE


def test_evaluation_identity_is_deterministic_and_settlement_revision_is_distinct() -> None:
    first = evaluated()
    replay = evaluated()
    revised = evaluated(settlement=settlement(raw_hash="c" * 64))
    assert first.evaluation_id == replay.evaluation_id
    assert first.to_json() == replay.to_json()
    assert revised.evaluation_id != first.evaluation_id
    assert settlement_hash(settlement()) != settlement_hash(settlement(raw_hash="c" * 64))


def test_append_only_store_idempotency_exactness_tamper_and_zero_influence(tmp_path: Path) -> None:
    store = EvaluationStore(tmp_path / "evaluation.db")
    value = evaluated()
    assert store.append(value) is True and store.append(value) is False
    assert store.get(value.evaluation_id) == value
    assert store.for_receipt(value.receipt_id) == (value,)
    assert store.for_agent("event-edge", "1.0.0") == (value,)
    assert store.for_market_family("test") == (value,)
    with (
        sqlite3.connect(store.path) as db,
        pytest.raises(sqlite3.IntegrityError, match="append only"),
    ):
        db.execute("UPDATE receipt_evaluations SET eligibility='UNSUPPORTED'")
    with sqlite3.connect(store.path) as db, pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO receipt_evaluations VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "x",
                "r",
                "a",
                "v",
                "m",
                None,
                "p",
                "h",
                "t",
                "ELIGIBLE",
                "time",
                "{}",
                "0" * 64,
                "1",
            ),
        )
    with sqlite3.connect(store.path) as db:
        db.execute("DROP TRIGGER receipt_evaluations_no_update")
        db.execute("UPDATE receipt_evaluations SET canonical_json='{}'")
    with pytest.raises(EvaluationStoreError, match="integrity"):
        store.get(value.evaluation_id)


def test_store_rejects_same_identity_with_changed_content(tmp_path: Path) -> None:
    store = EvaluationStore(tmp_path / "collision.db")
    value = evaluated()
    store.append(value)
    replayed_later = replace(value, evaluated_at=value.evaluated_at + timedelta(microseconds=1))
    assert store.append(replayed_later) is False
    assert len(store.all()) == 1
    assert store.get(value.evaluation_id) == value
    assert store.get(value.evaluation_id).evaluated_at == value.evaluated_at  # type: ignore[union-attr]
    changed = replace(value, exclusion_detail="substantive collision")
    with pytest.raises(EvaluationStoreError, match="collision"):
        store.append(changed)
    revised = evaluated(settlement=settlement(raw_hash="c" * 64))
    with pytest.raises(EvaluationStoreError, match="authoritative"):
        store.append(revised)


def test_append_only_settlement_maturation_and_effective_selection(tmp_path: Path) -> None:
    store = EvaluationStore(tmp_path / "maturation.db")
    provisional = evaluated(settlement=settlement(finalized_at=None, raw_hash="c" * 64))
    final = evaluated(evaluated_at=NOW + timedelta(hours=4))
    assert store.append(provisional)
    assert store.append(final)
    assert store.for_receipt(final.receipt_id) == (final, provisional)
    assert (
        store.effective_evaluation_for_receipt(final.receipt_id, EVALUATION_POLICY_VERSION) == final
    )
    projection = performance(
        store.all_for_agent("event-edge"),
        agent_id="event-edge",
        agent_version="1.0.0",
        total_persisted_decisions=1,
    )
    assert projection.evaluation_attempt_count == 2
    assert projection.outcome_linked_decisions == 1
    assert projection.effective_eligible_decisions == 1
    assert projection.currently_unscoreable_decisions == 0


def test_reconciliation_maturation_and_policy_histories_are_distinct(tmp_path: Path) -> None:
    store = EvaluationStore(tmp_path / "policy.db")
    mismatch = evaluated(
        settlement=settlement(status=ReconciliationStatus.MISMATCH, raw_hash="d" * 64),
        evaluated_at=NOW + timedelta(hours=3),
    )
    matched_v1 = evaluated(evaluated_at=NOW + timedelta(hours=4))
    matched_v2 = evaluated(
        evaluated_at=NOW + timedelta(hours=5), policy_version="m26c-receipt-outcome-v2"
    )
    assert store.append(mismatch)
    assert store.append(matched_v1)
    assert store.append(matched_v2)
    assert store.get(matched_v1.evaluation_id) == matched_v1
    assert store.get(matched_v2.evaluation_id) == matched_v2
    assert (
        store.effective_evaluation_for_receipt(matched_v1.receipt_id, EVALUATION_POLICY_VERSION)
        == matched_v1
    )
    assert (
        store.effective_evaluation_for_receipt(matched_v2.receipt_id, "m26c-receipt-outcome-v2")
        == matched_v2
    )


def test_unique_events_prevent_duplicate_receipt_inflation_and_versions_segment() -> None:
    one = evaluated()
    duplicates = (one, one, one)
    projection = performance(
        duplicates, agent_id="event-edge", agent_version="1.0.0", total_persisted_decisions=3
    )
    assert projection.evaluation_attempt_count == 3
    assert projection.effective_eligible_decisions == 1
    assert projection.unique_market_count == 1
    assert projection.proven_unique_event_count is None
    assert projection.evidence_state is EvidenceState.INCONCLUSIVE


def test_fifty_markets_do_not_manufacture_independent_event_evidence() -> None:
    rows = []
    for index in range(50):
        source_id = f"{index + 1:064x}"
        ticker = f"MARKET-{index:02d}"
        rows.append(
            evaluated(
                receipt=receipt(source_id=source_id, ticker=ticker),
                target=target(source_id=source_id, ticker=ticker),
                settlement=settlement(ticker=ticker),
            )
        )
    projection = performance(
        tuple(rows),
        agent_id="event-edge",
        agent_version="1.0.0",
        total_persisted_decisions=50,
    )
    assert projection.unique_market_count == 50
    assert projection.proven_unique_event_count is None
    assert projection.evidence_state is EvidenceState.INCONCLUSIVE
    assert projection.interval is None
    bucket = next(item for item in calibration(tuple(rows)) if item.count)
    assert bucket.count == 50 and bucket.sufficient
    assert projection.evidence_state is EvidenceState.INCONCLUSIVE


def test_calibration_buckets_and_manifest_are_deterministic_and_include_exclusions(
    tmp_path: Path,
) -> None:
    good = evaluated()
    bad = evaluated(settlement=settlement(finalized_at=None))
    buckets = calibration((good,), minimum_per_bucket=2)
    bucket = next(item for item in buckets if item.count)
    assert bucket.count == 1 and bucket.mean_probability == Decimal("0.7")
    assert bucket.observed_frequency == Decimal(1) and not bucket.sufficient
    store = EvaluationStore(tmp_path / "manifest.db")
    store.append(bad)
    store.append(good)
    first = store.build_manifest(
        agent_id="event-edge",
        agent_version="1.0.0",
        start_at=NOW,
        end_at=NOW + timedelta(days=1),
        policy_version=good.policy_version,
        generated_at=NOW + timedelta(days=2),
    )
    second = store.build_manifest(
        agent_id="event-edge",
        agent_version="1.0.0",
        start_at=NOW,
        end_at=NOW + timedelta(days=1),
        policy_version=good.policy_version,
        generated_at=NOW + timedelta(days=3),
    )
    assert first.manifest_id == second.manifest_id
    assert first.included_evaluation_ids == (good.evaluation_id,)
    assert first.excluded_evaluations == ((bad.evaluation_id, "OUTCOME_NOT_FINAL"),)


def test_true_receipt_counts_are_independent_of_recent_display_limit(tmp_path: Path) -> None:
    store = DecisionReceiptStore(tmp_path / "receipts.db")
    for index in range(25):
        source_id = f"{index + 1:064x}"
        value = receipt(source_id=source_id, ticker=f"COUNT-{index:02d}")
        attribution = OpportunityAttribution(
            "trade-candidate", source_id, "event-edge", value.evidence_references, value
        )
        store.append(attribution)
    assert len(store.recent_for_agent("event-edge")) == 20
    assert store.count_for_agent("event-edge", "1.0.0") == 25
    app = object.__new__(DashboardApp)
    app.receipt_store = store
    app.belief_freshness = FreshnessPolicy((("event-edge", timedelta(hours=1)),))
    recent = store.recent_for_agent("event-edge")
    roster = app._agents(NOW, recent, evaluations=(evaluated(),))
    detail = DashboardApp._agent_performance(
        AGENT_REGISTRY[0], recent, (evaluated(),), None, {"1.0.0": 25}
    )
    assert "25 total decisions" in roster
    assert "Total persisted decisions</dt><dd>25" in detail


def test_full_agent_evaluation_universe_is_not_global_recent_sample(tmp_path: Path) -> None:
    store = EvaluationStore(tmp_path / "many.db")
    for index in range(101):
        source_id = f"{index + 1:064x}"
        ticker = f"CROSS-{index:03d}"
        base = receipt(source_id=source_id, ticker=ticker)
        cross = replace(
            base,
            receipt_id=receipt_identity("cross-market", "1.0.0", "trade-candidate", source_id),
            agent_id="cross-market",
        )
        store.append(
            evaluate_receipt(
                cross,
                settlement(ticker=ticker),
                target=target(source_id=source_id, ticker=ticker),
                evaluated_at=NOW + timedelta(hours=5),
                market_family="test",
            )
        )
    for index in range(5):
        source_id = f"{index + 1000:064x}"
        ticker = f"EVENT-{index:03d}"
        store.append(
            evaluated(
                receipt=receipt(source_id=source_id, ticker=ticker),
                target=target(source_id=source_id, ticker=ticker),
                settlement=settlement(ticker=ticker),
            )
        )
    assert len(store.recent()) == 100
    assert not any(row.agent_id == "event-edge" for row in store.recent())
    all_rows = store.all_for_agent("event-edge", "1.0.0")
    assert len(all_rows) == 5
    projection = performance(
        all_rows,
        agent_id="event-edge",
        agent_version="1.0.0",
        total_persisted_decisions=5,
    )
    assert projection.effective_eligible_decisions == 5


def test_authoritative_manifest_store_query_prevents_cherry_picking(tmp_path: Path) -> None:
    store = EvaluationStore(tmp_path / "complete-manifest.db")
    good_source, bad_source, waiting_source = "1" * 64, "2" * 64, "3" * 64
    good = evaluated(
        receipt=receipt(source_id=good_source, ticker="GOOD"),
        target=target(source_id=good_source, ticker="GOOD"),
        settlement=settlement(ticker="GOOD", result="YES"),
    )
    bad = evaluated(
        receipt=receipt(source_id=bad_source, ticker="BAD"),
        target=target(source_id=bad_source, ticker="BAD"),
        settlement=settlement(ticker="BAD", result="NO"),
    )
    waiting = evaluated(
        receipt=receipt(source_id=waiting_source, ticker="WAIT"),
        target=target(source_id=waiting_source, ticker="WAIT"),
        settlement=settlement(ticker="WAIT", finalized_at=None),
    )
    for value in (bad, waiting, good):
        store.append(value)
    manifest = store.build_manifest(
        agent_id="event-edge",
        agent_version="1.0.0",
        start_at=NOW,
        end_at=NOW + timedelta(days=1),
        policy_version=EVALUATION_POLICY_VERSION,
        generated_at=NOW + timedelta(days=2),
    )
    assert set(manifest.included_evaluation_ids) == {good.evaluation_id, bad.evaluation_id}
    assert manifest.excluded_evaluations == ((waiting.evaluation_id, "OUTCOME_NOT_FINAL"),)
    assert not hasattr(type(manifest), "build")


def test_manifest_is_attempt_history_while_performance_uses_one_effective_decision(
    tmp_path: Path,
) -> None:
    store = EvaluationStore(tmp_path / "attempt-manifest.db")
    provisional = evaluated(
        settlement=settlement(finalized_at=None, raw_hash="c" * 64),
        evaluated_at=NOW + timedelta(hours=2),
    )
    mismatch = evaluated(
        settlement=settlement(status=ReconciliationStatus.MISMATCH, raw_hash="d" * 64),
        evaluated_at=NOW + timedelta(hours=3),
    )
    final = evaluated(evaluated_at=NOW + timedelta(hours=4))
    for attempt in (provisional, mismatch, final):
        assert store.append(attempt)
    manifest = store.build_manifest(
        agent_id="event-edge",
        agent_version="1.0.0",
        start_at=NOW,
        end_at=NOW + timedelta(days=1),
        policy_version=EVALUATION_POLICY_VERSION,
        generated_at=NOW + timedelta(days=2),
    )
    assert manifest.dataset_semantics == EVALUATION_DATASET_SEMANTICS
    assert len(manifest.included_evaluation_ids) + len(manifest.excluded_evaluations) == 3
    assert len(manifest.excluded_evaluations) == 2
    effective = effective_evaluations(store.all_for_agent("event-edge"))
    projection = performance(
        effective,
        agent_id="event-edge",
        agent_version="1.0.0",
        total_persisted_decisions=1,
    )
    assert projection.effective_eligible_decisions == 1
    assert not any("decision_count" in name for name in manifest.__dataclass_fields__)


def test_safety_source_has_no_execution_network_or_strategy_mutation() -> None:
    source = Path("services/agent_control_center/evaluation.py").read_text()
    store_source = Path("services/agent_control_center/evaluation_store.py").read_text()
    assert "production_execution" not in source + store_source
    assert "production_influence TEXT NOT NULL DEFAULT '0'" in store_source
    assert "requests" not in source and "allocate_budget" not in source


def test_owner_surfaces_are_honest_escaped_and_version_segmented() -> None:
    good = evaluated()
    hostile = evaluated(settlement=settlement(finalized_at=None, result="<bad>"))
    agent = __import__(
        "services.agent_control_center.domain", fromlist=["agent_by_id"]
    ).agent_by_id("event-edge")
    assert agent is not None
    panel = DashboardApp._agent_performance(agent, (receipt(),), (good, hostile), None)
    assert "Event Edge 1.0.0" in panel
    assert "EARLY / INCONCLUSIVE" in panel
    assert "Brier" in panel and "Counterfactual unit research value" in panel
    assert "actual trade result is claimed" in panel
    assert "Unique markets" in panel
    assert "Independent event count" in panel
    assert "Unique settled events" not in panel
    card = DashboardApp._evaluation_card(hostile)
    assert "&lt;bad&gt;" in card and "<bad>" not in card
    assert "P&amp;L" not in card and "profit" not in card.lower()


def test_cross_market_and_corrupt_evaluation_ui_states_are_explicit() -> None:
    cross = __import__(
        "services.agent_control_center.domain", fromlist=["agent_by_id"]
    ).agent_by_id("cross-market")
    assert cross is not None
    unavailable = DashboardApp._agent_performance(cross, (), (), None)
    corrupt = DashboardApp._agent_performance(cross, (), (), "Outcome <corrupt> unavailable")
    assert "two-venue" in unavailable and "Performance unavailable" in unavailable
    assert "&lt;corrupt&gt;" in corrupt and "<corrupt>" not in corrupt


def test_learning_ui_labels_calibration_as_decision_level_and_events_unproven() -> None:
    summary: dict[str, Any] = {
        "proposals": [],
        "families": [],
        "state": "INSUFFICIENT REAL EVIDENCE",
        "current_configuration": None,
        "previous_configuration": None,
        "production_influence": "NONE",
    }
    page = DashboardApp._learning(summary, (evaluated(),))
    assert "Descriptive decision-level calibration" in page
    assert "unique markets 1" in page
    assert "independent event count unavailable" in page
    assert "Evidence remains inconclusive" in page
    assert "unique settled events" not in page.lower()


def test_learning_route_fails_closed_when_any_evaluation_attempt_is_corrupt(
    tmp_path: Path,
) -> None:
    state = StateStore(tmp_path / "learning-corrupt.db")
    box = SecretBox(b"k" * 32)
    state.set_config("owner", "owner")
    state.set_config("password_hash", hash_password("LongProduction9Password"))
    state.set_config("vault", box.seal(b"read-only"))
    state.set_config("totp_secret", box.seal(b"JBSWY3DPEHPK3PXP"))
    token, _ = state.create_session(int(time.time()))
    app = DashboardApp(state, box)
    old = evaluated(
        settlement=settlement(finalized_at=None, raw_hash="c" * 64),
        evaluated_at=NOW + timedelta(hours=2),
    )
    assert app.evaluation_store.append(old)
    assert app.evaluation_store.append(evaluated(evaluated_at=NOW + timedelta(hours=4)))
    with sqlite3.connect(app.evaluation_store.path) as db:
        db.execute("DROP TRIGGER receipt_evaluations_no_update")
        db.execute(
            "UPDATE receipt_evaluations SET canonical_json='{}' WHERE evaluation_id=?",
            (old.evaluation_id,),
        )
    captured: dict[str, str] = {}

    def start(status: str, _headers: list[tuple[str, str]]) -> None:
        captured["status"] = status

    body = b"".join(
        app(
            {
                "PATH_INFO": "/learning",
                "QUERY_STRING": "",
                "REQUEST_METHOD": "GET",
                "HTTP_COOKIE": f"session={token}",
                "CONTENT_LENGTH": "0",
                "wsgi.input": io.BytesIO(),
                "REMOTE_ADDR": "127.0.0.1",
            },
            start,
        )
    ).decode()
    assert captured["status"] == "200 OK"
    assert "EVALUATION HISTORY UNAVAILABLE" in body
    assert "NO EVIDENCE" not in body
    assert "Descriptive decision-level calibration" not in body
    assert "Brier" not in body
    assert "persisted evidence failed validation" in body
