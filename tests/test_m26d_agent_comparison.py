"""M26D fair shared-unit research comparison and product truthfulness."""

from __future__ import annotations

import io
import sqlite3
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from services.agent_control_center.attribution import receipt_identity
from services.agent_control_center.comparison import (
    BINARY_BRIER_SEMANTICS,
    COMPARISON_CAPABILITIES,
    M26D_TEMPORAL_POLICY,
    M26D_WINDOW_BASIS,
    ComparisonContender,
    ComparisonEligibility,
    ComparisonError,
    CompetitionEvidenceState,
    InformationSetComparability,
    TemporalAlignment,
    compare_from_store,
    current_competition_snapshot,
    semantic_eligibility,
    supported_contender,
)
from services.agent_control_center.domain import DecisionReceipt, ResearchDecision
from services.agent_control_center.evaluation import (
    EVALUATION_POLICY_VERSION,
    EvaluationTarget,
    ReceiptEvaluation,
    evaluate_receipt,
)
from services.agent_control_center.evaluation_store import EvaluationStore, EvaluationStoreError
from services.contract_intelligence.settlement import ReconciliationStatus, SettlementRecord
from services.opportunity_engine.books import OutcomeSide
from services.web_dashboard.app import DashboardApp
from services.web_dashboard.security import SecretBox, hash_password
from services.web_dashboard.store import StateStore

NOW = datetime(2026, 8, 1, tzinfo=UTC)
END = NOW + timedelta(days=30)


def _evaluation(
    ticker: str,
    probability: str,
    *,
    version: str,
    source_char: str,
    side: OutcomeSide = OutcomeSide.YES,
    result: str = "YES",
    target_version: str = "event-edge-binary-v1",
    source_observed_at: datetime = NOW,
) -> ReceiptEvaluation:
    source_id = source_char if len(source_char) == 64 else source_char * 64
    receipt = DecisionReceipt(
        receipt_identity("event-edge", "1.0.0", "trade-candidate", source_id),
        source_observed_at,
        "event-edge",
        "1.0.0",
        f"{ticker}:{side.value}",
        Decimal("0.5"),
        Decimal(probability),
        None,
        Decimal("0"),
        Decimal("0"),
        Decimal("0"),
        Decimal("0"),
        None,
        ("evidence:test",),
        None,
        (),
        (),
        ResearchDecision.NO_TRADE,
        (),
        ("research only",),
    )
    target = EvaluationTarget(
        ticker,
        side,
        "trade-candidate",
        source_id,
        source_observed_at,
        source_id,
        target_version,
    )
    settlement = SettlementRecord(
        ticker,
        "rules-v1",
        "spec-v1",
        result,
        Decimal(1 if result == "YES" else 0),
        NOW + timedelta(hours=1),
        NOW + timedelta(hours=2),
        "f" * 64,
        "observation",
        ReconciliationStatus.MATCHED,
    )
    value = evaluate_receipt(
        receipt,
        settlement,
        target,
        evaluated_at=NOW + timedelta(hours=3),
        market_family="test",
    )
    return replace(value, agent_version=version)


def _contender(version: str = "1.0.0") -> ComparisonContender:
    return supported_contender("event-edge", version, market_family="test")


def test_semantic_compatibility_is_explicit_and_versions_stay_separate() -> None:
    a, b = _contender("1.0.0"), _contender("1.1.0")
    assert a != b
    assert semantic_eligibility(a, b) is ComparisonEligibility.COMPARABLE_DESCRIPTIVELY
    assert len(COMPARISON_CAPABILITIES) == 1
    for field, value in (
        ("evaluation_policy_version", "different"),
        ("target_version", "different"),
        ("outcome_semantics", "signal-confidence"),
    ):
        with pytest.raises(ComparisonError, match="not authorized"):
            replace(b, **{field: value})
    with pytest.raises(ComparisonError, match="not authorized"):
        supported_contender("cross-market", "1.0.0")
    with pytest.raises(ComparisonError, match="zero production"):
        replace(a, production_influence=Decimal("0.01"))


@pytest.mark.parametrize(
    "agent_id",
    (
        "breaking-signals",
        "resolution",
        "cross-market",
        "learning",
        "perps",
        "portfolio",
        "unknown-agent",
    ),
)
def test_direct_contender_forgery_is_rejected(agent_id: str) -> None:
    with pytest.raises(ComparisonError, match="not authorized"):
        ComparisonContender(
            agent_id,
            "1.0.0",
            EVALUATION_POLICY_VERSION,
            BINARY_BRIER_SEMANTICS,
            "event-edge-binary-v1",
            "test",
        )


def _bypassed_contender(agent_id: str) -> ComparisonContender:
    """Adversarially bypass __init__/__post_init__ to exercise comparison-entry defense."""
    value = object.__new__(ComparisonContender)
    for field, item in (
        ("agent_id", agent_id),
        ("agent_version", "1.0.0"),
        ("evaluation_policy_version", EVALUATION_POLICY_VERSION),
        ("outcome_semantics", BINARY_BRIER_SEMANTICS),
        ("target_version", "event-edge-binary-v1"),
        ("market_family", "test"),
        ("production_influence", Decimal("0")),
    ):
        object.__setattr__(value, field, item)
    return value


def test_fabricated_eligible_unsupported_agent_evidence_is_never_scored(tmp_path: Path) -> None:
    store = EvaluationStore(tmp_path / "fabricated.sqlite3")
    fabricated = replace(
        _evaluation("ATTACK", "0.999", version="1.0.0", source_char="a"),
        agent_id="breaking-signals",
        model_brier=Decimal("0.000001"),
    )
    store.append(fabricated)
    comparison = compare_from_store(
        store,
        _bypassed_contender("breaking-signals"),
        _contender("1.1.0"),
        start_at=NOW,
        end_at=END,
        generated_at=END,
    )
    assert comparison.eligibility is ComparisonEligibility.EVALUATION_UNSUPPORTED
    assert comparison.metrics is comparison.cohort is comparison.winner is None


def test_complete_shared_intersection_exact_metrics_and_deterministic_identity(
    tmp_path: Path,
) -> None:
    store = EvaluationStore(tmp_path / "evaluation.sqlite3")
    rows = (
        _evaluation("SHARED-GOOD", "0.8", version="1.0.0", source_char="a"),
        _evaluation("SHARED-GOOD", "0.6", version="1.1.0", source_char="b"),
        _evaluation("SHARED-BAD", "0.2", version="1.0.0", source_char="c"),
        _evaluation("SHARED-BAD", "0.7", version="1.1.0", source_char="d"),
        _evaluation("A-ONLY", "0.9", version="1.0.0", source_char="e"),
        _evaluation("B-ONLY", "0.9", version="1.1.0", source_char="0"),
    )
    for row in reversed(rows):
        store.append(row)  # type: ignore[arg-type]
    comparison = compare_from_store(
        store, _contender(), _contender("1.1.0"), start_at=NOW, end_at=END, generated_at=END
    )
    assert comparison.eligibility is ComparisonEligibility.COMPARABLE_DESCRIPTIVELY
    assert comparison.evidence_state is CompetitionEvidenceState.INCONCLUSIVE_INDEPENDENCE
    assert comparison.winner is comparison.proven_unique_event_count is None
    assert comparison.metrics is not None and comparison.cohort is not None
    assert comparison.metrics.shared_unit_count == 2
    assert comparison.metrics.contender_a_mean_brier == Decimal("0.34")
    assert comparison.metrics.contender_b_mean_brier == Decimal("0.125")
    assert comparison.metrics.a_minus_b_brier == Decimal("0.215")
    assert comparison.metrics.contender_a_mean_market_relative_brier_improvement == Decimal("-0.09")
    assert comparison.metrics.contender_b_mean_market_relative_brier_improvement == Decimal("0.125")
    assert {item.reason for item in comparison.cohort.excluded_units} == {"A_ONLY", "B_ONLY"}
    replay = compare_from_store(
        store,
        _contender(),
        _contender("1.1.0"),
        start_at=NOW,
        end_at=END,
        generated_at=END + timedelta(days=1),
    )
    assert replay.cohort is not None and replay.cohort.cohort_id == comparison.cohort.cohort_id
    with pytest.raises(ComparisonError, match="identity"):
        replace(comparison.cohort, comparison_policy_version="changed-policy")


def test_self_comparison_fails_closed_but_historical_versions_are_allowed(tmp_path: Path) -> None:
    store = EvaluationStore(tmp_path / "self.sqlite3")
    contender = _contender()
    for same in (contender, replace(contender)):
        comparison = compare_from_store(
            store,
            contender,
            same,
            start_at=NOW,
            end_at=END,
            generated_at=END,
        )
        assert comparison.eligibility is ComparisonEligibility.SELF_COMPARISON
        assert comparison.cohort is comparison.metrics is comparison.winner is None
    assert (
        semantic_eligibility(contender, _contender("1.1.0"))
        is ComparisonEligibility.COMPARABLE_DESCRIPTIVELY
    )


def test_temporal_misalignment_is_structured_disclosed_and_identity_bound(tmp_path: Path) -> None:
    store = EvaluationStore(tmp_path / "temporal.sqlite3")
    store.append(
        _evaluation(
            "TIME",
            "0.8",
            version="1.0.0",
            source_char="a",
            source_observed_at=NOW - timedelta(days=30),
        )
    )
    store.append(
        _evaluation(
            "TIME",
            "0.7",
            version="1.1.0",
            source_char="b",
            source_observed_at=NOW + timedelta(hours=1) - timedelta(minutes=1),
        )
    )
    comparison = compare_from_store(
        store,
        _contender(),
        _contender("1.1.0"),
        start_at=NOW,
        end_at=END,
        generated_at=END,
    )
    assert comparison.metrics is not None and comparison.cohort is not None
    assert comparison.cohort.temporal_policy == M26D_TEMPORAL_POLICY
    assert comparison.cohort.temporal_alignment is TemporalAlignment.UNALIGNED
    assert comparison.cohort.information_set_comparability is InformationSetComparability.NOT_PROVEN
    assert comparison.cohort.window_basis == M26D_WINDOW_BASIS == "evaluated_at"
    assert "information sets are not aligned" in comparison.explanation
    assert "descriptive shared-proposition scoring only" in comparison.explanation
    for field, value in (
        ("temporal_policy", "different"),
        ("window_basis", "source_observed_at"),
    ):
        with pytest.raises(ComparisonError):
            replace(comparison.cohort, **{field: value})
    replay = compare_from_store(
        store,
        _contender(),
        _contender("1.1.0"),
        start_at=NOW,
        end_at=END,
        generated_at=END + timedelta(days=1),
    )
    assert replay.cohort is not None and replay.cohort.cohort_id == comparison.cohort.cohort_id


def test_directional_cohort_swaps_roles_and_paired_sign(tmp_path: Path) -> None:
    store = EvaluationStore(tmp_path / "direction.sqlite3")
    store.append(_evaluation("DIRECTION", "0.8", version="1.0.0", source_char="a"))
    store.append(_evaluation("DIRECTION", "0.6", version="1.1.0", source_char="b"))
    forward = compare_from_store(
        store,
        _contender(),
        _contender("1.1.0"),
        start_at=NOW,
        end_at=END,
        generated_at=END,
    )
    reverse = compare_from_store(
        store,
        _contender("1.1.0"),
        _contender(),
        start_at=NOW,
        end_at=END,
        generated_at=END,
    )
    assert forward.cohort is not None and reverse.cohort is not None
    assert forward.metrics is not None and reverse.metrics is not None
    assert forward.cohort.cohort_id != reverse.cohort.cohort_id
    assert forward.metrics.a_minus_b_brier == -reverse.metrics.a_minus_b_brier
    assert forward.winner is reverse.winner is None


def test_side_track_and_target_are_part_of_unit_identity(tmp_path: Path) -> None:
    store = EvaluationStore(tmp_path / "evaluation.sqlite3")
    a = _evaluation("SIDE", "0.8", version="1.0.0", source_char="a")
    b = _evaluation("SIDE", "0.8", version="1.1.0", source_char="b", side=OutcomeSide.NO)
    store.append(a)  # type: ignore[arg-type]
    store.append(b)  # type: ignore[arg-type]
    comparison = compare_from_store(
        store, _contender(), _contender("1.1.0"), start_at=NOW, end_at=END, generated_at=END
    )
    assert comparison.eligibility is ComparisonEligibility.NO_SHARED_UNITS
    assert comparison.cohort is not None and len(comparison.cohort.shared_units) == 0

    track_store = EvaluationStore(tmp_path / "track.sqlite3")
    track_a = _evaluation("TRACK", "0.8", version="1.0.0", source_char="c")
    track_b = _evaluation("TRACK", "0.8", version="1.1.0", source_char="d")
    track_store.append(track_a)  # type: ignore[arg-type]
    track_store.append(replace(track_b, settlement_track_id="e" * 64))  # type: ignore[arg-type]
    track_comparison = compare_from_store(
        track_store,
        _contender(),
        _contender("1.1.0"),
        start_at=NOW,
        end_at=END,
        generated_at=END,
    )
    assert track_comparison.eligibility is ComparisonEligibility.NO_SHARED_UNITS


def test_duplicate_unit_is_excluded_without_selecting_best(tmp_path: Path) -> None:
    store = EvaluationStore(tmp_path / "evaluation.sqlite3")
    rows = (
        _evaluation("DUP", "0.9", version="1.0.0", source_char="a"),
        _evaluation("DUP", "0.1", version="1.0.0", source_char="b"),
        _evaluation("DUP", "0.8", version="1.1.0", source_char="c"),
    )
    for row in rows:
        store.append(row)  # type: ignore[arg-type]
    comparison = compare_from_store(
        store, _contender(), _contender("1.1.0"), start_at=NOW, end_at=END, generated_at=END
    )
    assert comparison.eligibility is ComparisonEligibility.AMBIGUOUS_DUPLICATE_UNIT
    assert comparison.metrics is None
    assert comparison.cohort is not None and not comparison.cohort.shared_units


@pytest.mark.parametrize("count", [50, 500])
def test_market_counts_never_become_event_evidence_or_a_winner(tmp_path: Path, count: int) -> None:
    store = EvaluationStore(tmp_path / "evaluation.sqlite3")
    for index in range(count):
        # Unique immutable source IDs without treating ticker identity as event identity.
        a_source = f"{index * 2:064x}"[-64:]
        b_source = f"{index * 2 + 1:064x}"[-64:]
        a = _evaluation(f"M{index}", "0.8", version="1.0.0", source_char=a_source)
        b = _evaluation(f"M{index}", "0.7", version="1.1.0", source_char=b_source)
        store.append(a)  # type: ignore[arg-type]
        store.append(b)  # type: ignore[arg-type]
    comparison = compare_from_store(
        store, _contender(), _contender("1.1.0"), start_at=NOW, end_at=END, generated_at=END
    )
    assert comparison.metrics is not None and comparison.metrics.shared_unit_count == count
    assert comparison.proven_unique_event_count is None and comparison.winner is None
    assert comparison.evidence_state is CompetitionEvidenceState.INCONCLUSIVE_INDEPENDENCE


def test_corrupt_store_fails_whole_comparison_universe_closed(tmp_path: Path) -> None:
    path = tmp_path / "evaluation.sqlite3"
    store = EvaluationStore(path)
    store.append(_evaluation("ONE", "0.8", version="1.0.0", source_char="a"))  # type: ignore[arg-type]
    with sqlite3.connect(path) as db:
        db.execute("DROP TRIGGER receipt_evaluations_no_update")
        db.execute("UPDATE receipt_evaluations SET canonical_json='{}'")
    with pytest.raises(EvaluationStoreError, match="integrity"):
        compare_from_store(
            store,
            _contender(),
            _contender("1.1.0"),
            start_at=NOW,
            end_at=END,
            generated_at=END,
        )


def _configured(tmp_path: Path) -> tuple[DashboardApp, str]:
    store = StateStore(tmp_path / "dashboard.sqlite3")
    box = SecretBox(b"k" * 32)
    store.set_config("owner", "owner")
    store.set_config("password_hash", hash_password("LongProduction9Password"))
    store.set_config("vault", box.seal(b"read-only"))
    store.set_config("totp_secret", box.seal(b"JBSWY3DPEHPK3PXP"))
    token, _ = store.create_session(int(time.time()))
    return DashboardApp(store, box), token


def _get(app: DashboardApp, path: str, token: str) -> tuple[str, str]:
    captured: dict[str, Any] = {}

    def start(status: str, _headers: list[tuple[str, str]]) -> None:
        captured["status"] = status

    body = b"".join(
        app(
            {
                "PATH_INFO": path,
                "QUERY_STRING": "",
                "REQUEST_METHOD": "GET",
                "HTTP_COOKIE": f"session={token}",
                "CONTENT_LENGTH": "0",
                "wsgi.input": io.BytesIO(b""),
                "REMOTE_ADDR": "127.0.0.1",
            },
            start,
        )
    )
    return str(captured["status"]), body.decode()


def test_current_product_has_no_fake_race_and_corruption_returns_200(tmp_path: Path) -> None:
    snapshot = current_competition_snapshot(True)
    assert sum(item.supported for item in snapshot.contenders) == 1
    assert (
        snapshot.winner is None
        and "No valid cross-agent comparison yet" in snapshot.no_winner_reason
    )
    assert snapshot.evidence_state is CompetitionEvidenceState.NO_COMPARISON
    with pytest.raises(ComparisonError, match="zero influence"):
        replace(snapshot, production_influence=Decimal("1"))
    app, token = _configured(tmp_path)
    status, page = _get(app, "/learning", token)
    assert status == "200 OK"
    assert "RESEARCH COMPARISON" in page
    assert "No valid cross-agent comparison yet" in page
    assert "no winner selected" in page
    assert "different forecast horizons and information sets" in page
    assert "evaluation processing time" in page
    with sqlite3.connect(app.store.path) as db:
        db.execute(
            "INSERT INTO receipt_evaluations VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "x",
                "r",
                "event-edge",
                "1.0.0",
                "T",
                "test",
                EVALUATION_POLICY_VERSION,
                "a" * 64,
                "b" * 64,
                "ELIGIBLE",
                NOW.isoformat(),
                "{}",
                "c" * 64,
                "0",
            ),
        )
    status, page = _get(app, "/learning", token)
    assert status == "200 OK"
    assert "COMPARISON EVIDENCE UNAVAILABLE" in page
    assert (
        current_competition_snapshot(False).evidence_state
        is CompetitionEvidenceState.EVIDENCE_UNAVAILABLE
    )


def test_m26d_has_no_budget_governance_execution_or_live_dependencies() -> None:
    code = Path("services/agent_control_center/comparison.py").read_text()
    for forbidden in (
        "production_execution",
        "allocate_budget",
        "compare_challenger",
        "GovernanceProposal",
        "submit_order",
        "position_size",
        "scheduler",
        "credentials",
    ):
        assert forbidden not in code
    assert BINARY_BRIER_SEMANTICS in code
