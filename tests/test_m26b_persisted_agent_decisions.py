"""M26B persisted decisions, attribution, beliefs, adapters, and UI safety."""

from __future__ import annotations

import io
import json
import sqlite3
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from services.agent_control_center.adapters import (
    cross_market_attribution,
    cross_market_source_identity,
    event_edge_attribution,
)
from services.agent_control_center.attribution import OpportunityAttribution, receipt_identity
from services.agent_control_center.beliefs import FreshnessPolicy, current_belief
from services.agent_control_center.domain import (
    AGENT_REGISTRY,
    AutonomyMode,
    DecisionReceipt,
    ImplementationAvailability,
    ResearchDecision,
    _restore_persisted_receipt,
)
from services.agent_control_center.store import DecisionReceiptStore, DecisionReceiptStoreError
from services.opportunity_engine.books import OutcomeSide
from services.opportunity_engine.cross_venue import (
    CrossVenueOpportunityObservation,
    SemanticMatch,
)
from services.opportunity_engine.models import (
    AnalysisType,
    DecisionState,
    FillQuality,
    InformationDecay,
    LiquidityDiagnostic,
    OutcomeEconomics,
    RejectionReason,
    SlippageMethod,
    TradeCandidate,
)
from services.web_dashboard.app import DashboardApp
from services.web_dashboard.security import SecretBox, hash_password
from services.web_dashboard.store import StateStore

NOW = datetime(2026, 8, 14, 12, 34, 56, 123456, tzinfo=UTC)


def candidate(
    state: DecisionState = DecisionState.RESEARCH_CANDIDATE,
    reasons: tuple[RejectionReason, ...] = (),
) -> TradeCandidate:
    economics = OutcomeEconomics(
        OutcomeSide.YES,
        Decimal("0.6200"),
        Decimal("0.5900"),
        Decimal("0.5000"),
        Decimal("0.1200"),
        Decimal("0.1200"),
        Decimal("0.5150"),
        Decimal("0.0100"),
        Decimal("0.0050"),
        Decimal("0.0750"),
    )
    return TradeCandidate.freeze(
        created_at=NOW,
        market_ticker="TEST-EVENT",
        event_id="event-1",
        series_id="series-1",
        market_family="general",
        rules_version="rules-v1",
        rules_hash="rules-hash",
        contract_interpretation_version="interpret-v1",
        forecast_id="forecast-1",
        forecast_kind="INDEPENDENT",
        forecast_time=NOW - timedelta(minutes=1),
        fair_yes_probability=Decimal("0.6200"),
        fair_no_probability=Decimal("0.3800"),
        fair_yes_lower=Decimal("0.5900"),
        fair_yes_upper=Decimal("0.6500"),
        fair_no_lower=Decimal("0.3500"),
        fair_no_upper=Decimal("0.4100"),
        market_snapshot_id="book-1",
        book_snapshot_time=NOW - timedelta(seconds=2),
        book_age_ms=2000,
        outcome_side=OutcomeSide.YES,
        analysis_type=AnalysisType.TAKER_NOW,
        best_bid=Decimal("0.4900"),
        best_ask=Decimal("0.5000"),
        executable_price=Decimal("0.5000"),
        available_size_at_best=Decimal("10.00"),
        available_depth=Decimal("20.00"),
        economics=economics,
        fee_schedule_id="fee-v1",
        fee_type="flat",
        fee_multiplier=Decimal("1.00"),
        expected_slippage=Decimal("0.0050"),
        slippage_method=SlippageMethod.EXACT_CURRENT_BOOK_WALK,
        fill_probability=None,
        fill_probability_quality=FillQuality.DISPLAYED_TAKER_DEPTH,
        uncertainty_adjustment=Decimal("0.0300"),
        information_age=timedelta(minutes=1),
        information_decay=InformationDecay.MODERATE,
        information_decay_estimate=Decimal("0.75"),
        liquidity=LiquidityDiagnostic(
            Decimal("0.01"),
            Decimal("0.02"),
            Decimal("10"),
            Decimal("20"),
            3,
            Decimal("5"),
            Decimal("100"),
            2000,
            "STABLE",
        ),
        correlation_cluster_id=None,
        correlation_context="none",
        time_to_close=timedelta(hours=2),
        time_to_expected_resolution=timedelta(days=1),
        capital_turnover_measure=Decimal("0.50"),
        opportunity_score=Decimal("0.10"),
        decision_state=state,
        rejection_reasons=reasons,
        code_git_sha="fixture-sha",
    )


def configured(tmp_path: Path) -> tuple[DashboardApp, str]:
    store = StateStore(tmp_path / "state.db")
    box = SecretBox(b"k" * 32)
    store.set_config("owner", "owner")
    store.set_config("password_hash", hash_password("LongProduction9Password"))
    store.set_config("vault", box.seal(b"read-only"))
    store.set_config("totp_secret", box.seal(b"JBSWY3DPEHPK3PXP"))
    token, _ = store.create_session(int(time.time()))
    return DashboardApp(store, box), token


def get(app: DashboardApp, path: str, token: str) -> str:
    captured: dict[str, Any] = {}

    def start(status: str, headers: list[tuple[str, str]]) -> None:
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
    assert captured["status"] == "200 OK"
    return body.decode()


def test_event_adapter_exact_candidate_watch_incomplete_and_rejection_mapping() -> None:
    accepted = event_edge_attribution(candidate()).receipt
    assert accepted.decision is ResearchDecision.WOULD_TRADE
    assert accepted.estimated_fees == Decimal("0.0100")
    assert accepted.estimated_slippage == Decimal("0.0050")
    assert accepted.after_cost_edge == Decimal("0.0750")
    assert accepted.fair_value is None and accepted.confidence is None
    watch = event_edge_attribution(
        candidate(DecisionState.WATCH, (RejectionReason.NET_VALUE_BELOW_THRESHOLD,))
    ).receipt
    assert watch.decision is ResearchDecision.NO_TRADE
    assert watch.rejection_reasons == ("NET_VALUE_BELOW_THRESHOLD",)
    assert watch.risk_check_results == ()
    incomplete = event_edge_attribution(candidate(DecisionState.INCOMPLETE)).receipt
    assert incomplete.decision is ResearchDecision.INSUFFICIENT_EVIDENCE
    blocked = event_edge_attribution(
        candidate(DecisionState.REJECTED, (RejectionReason.TOO_CLOSE_TO_CLOSE,))
    ).receipt
    assert blocked.decision is ResearchDecision.BLOCKED_BY_RISK
    missing = event_edge_attribution(
        candidate(DecisionState.REJECTED, (RejectionReason.FORECAST_MISSING,))
    ).receipt
    assert missing.decision is ResearchDecision.INSUFFICIENT_EVIDENCE


def test_event_adapter_uses_frozen_economics_slippage_and_rejects_divergence() -> None:
    source = candidate(DecisionState.WATCH, (RejectionReason.NET_VALUE_BELOW_THRESHOLD,))
    receipt = event_edge_attribution(source).receipt
    assert receipt.estimated_slippage == source.economics.expected_slippage
    assert receipt.risk_check_results == ()
    assert receipt.rejection_reasons == ("NET_VALUE_BELOW_THRESHOLD",)
    with pytest.raises(ValueError, match="slippage disagrees"):
        event_edge_attribution(replace(source, expected_slippage=Decimal("0.0060")))


def test_cross_market_adapter_preserves_cost_reserves_and_has_no_execution() -> None:
    observation = CrossVenueOpportunityObservation.evaluate(
        observation_id="cross-1",
        semantic_match=SemanticMatch.IDENTICAL,
        kalshi_price=Decimal("0.4000"),
        kalshi_depth=Decimal("10"),
        polymarket_price=Decimal("0.5000"),
        polymarket_depth=Decimal("10"),
        kalshi_fee=Decimal("0.0050"),
        polymarket_fee=Decimal("0.0050"),
        expected_slippage=Decimal("0.0050"),
        timestamp_skew_ms=10,
        semantic_reserve=Decimal("0.0050"),
        leg_risk_reserve=Decimal("0.0050"),
        venue_state_risk="OPEN",
        reference_overlap=False,
    )
    receipt = cross_market_attribution(
        observation,
        instrument_id="K:X",
        created_at=NOW,
        evidence_references=("kalshi-book:k1", "polymarket-book:p1"),
    ).receipt
    assert receipt.decision is ResearchDecision.WOULD_TRADE
    assert receipt.raw_edge == Decimal("0.1000")
    assert receipt.estimated_fees == Decimal("0.0100")
    assert receipt.after_cost_edge == Decimal("0.0750")
    assert receipt.current_exposure is None and receipt.applicable_limits == ()


def test_cross_market_source_identity_content_binds_every_attributed_package_field() -> None:
    observation = CrossVenueOpportunityObservation.evaluate(
        observation_id="caller-attestation-1",
        semantic_match=SemanticMatch.IDENTICAL,
        kalshi_price=Decimal("0.4000"),
        kalshi_depth=Decimal("10"),
        polymarket_price=Decimal("0.5000"),
        polymarket_depth=Decimal("11"),
        kalshi_fee=Decimal("0.0050"),
        polymarket_fee=Decimal("0.0060"),
        expected_slippage=Decimal("0.0050"),
        timestamp_skew_ms=10,
        semantic_reserve=Decimal("0.0050"),
        leg_risk_reserve=Decimal("0.0050"),
        venue_state_risk="OPEN",
        reference_overlap=False,
    )
    inputs = {
        "instrument_id": "K:X",
        "created_at": NOW,
        "evidence_references": ("kalshi:k1", "polymarket:p1"),
    }
    identity = cross_market_source_identity(observation, **inputs)
    assert identity == cross_market_source_identity(observation, **inputs)
    changed_packages = (
        (replace(observation, kalshi_price=Decimal("0.4001")), inputs),
        (replace(observation, semantic_reserve=Decimal("0.0060")), inputs),
        (observation, inputs | {"instrument_id": "K:Y"}),
        (observation, inputs | {"created_at": NOW + timedelta(microseconds=1)}),
        (observation, inputs | {"evidence_references": ("kalshi:k2",)}),
        (
            observation,
            inputs | {"evidence_references": tuple(reversed(inputs["evidence_references"]))},
        ),
    )
    assert all(
        cross_market_source_identity(changed, **changed_inputs) != identity
        for changed, changed_inputs in changed_packages
    )
    # Reusing caller provenance cannot conceal changed substantive content.
    reused_attestation = replace(observation, polymarket_price=Decimal("0.5100"))
    assert reused_attestation.observation_id == observation.observation_id
    assert cross_market_source_identity(reused_attestation, **inputs) != identity


def test_cross_market_content_identity_replay_is_idempotent(tmp_path: Path) -> None:
    observation = CrossVenueOpportunityObservation.evaluate(
        observation_id="caller-1",
        semantic_match=SemanticMatch.IDENTICAL,
        kalshi_price=Decimal("0.40"),
        kalshi_depth=Decimal("10"),
        polymarket_price=Decimal("0.50"),
        polymarket_depth=Decimal("10"),
        kalshi_fee=Decimal("0.005"),
        polymarket_fee=Decimal("0.005"),
        expected_slippage=Decimal("0.005"),
        timestamp_skew_ms=10,
        semantic_reserve=Decimal("0.005"),
        leg_risk_reserve=Decimal("0.005"),
        venue_state_risk="OPEN",
        reference_overlap=False,
    )
    attribution = cross_market_attribution(
        observation,
        instrument_id="K:X",
        created_at=NOW,
        evidence_references=("k:k1", "p:p1"),
    )
    store = DecisionReceiptStore(tmp_path / "cross.db")
    assert store.append(attribution)
    assert not store.append(attribution)


def test_store_round_trip_order_idempotency_collision_counts_and_append_only(
    tmp_path: Path,
) -> None:
    store = DecisionReceiptStore(tmp_path / "receipts.db")
    first = event_edge_attribution(candidate())
    later_candidate = replace(candidate(DecisionState.WATCH), created_at=NOW + timedelta(seconds=1))
    # A new immutable candidate identity makes a valid later decision.
    later_candidate = replace(later_candidate, candidate_id="later", content_hash="later")
    later = event_edge_attribution(later_candidate)
    assert store.append(first)
    assert not store.append(first)
    assert store.append(later)
    restored = store.get(first.receipt.receipt_id)
    assert restored is not None and restored.to_json() == first.receipt.to_json()
    assert restored.created_at == NOW and restored.estimated_fees == Decimal("0.0100")
    assert store.latest_for_agent("event-edge") == later.receipt
    assert store.recent_for_agent("event-edge") == (later.receipt, first.receipt)
    assert store.for_instrument("TEST-EVENT:YES") == (later.receipt, first.receipt)
    assert store.counts()[("event-edge", "WOULD_TRADE")] == 1
    conflicting_receipt = replace(
        first.receipt, rejection_reasons=("changed",), decision=ResearchDecision.NO_TRADE
    )
    conflicting = replace(first, receipt=conflicting_receipt)
    with pytest.raises(DecisionReceiptStoreError, match="collision"):
        store.append(conflicting)
    with sqlite3.connect(store.path) as db:
        with pytest.raises(sqlite3.IntegrityError, match="append only"):
            db.execute("UPDATE decision_receipts SET decision='NO_TRADE'")
        with pytest.raises(sqlite3.IntegrityError, match="append only"):
            db.execute("DELETE FROM decision_receipts")
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                "INSERT INTO decision_receipts VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    "bad",
                    "event-edge",
                    "x",
                    NOW.isoformat(),
                    "NO_TRADE",
                    "x",
                    "x",
                    "{}",
                    "0" * 64,
                    "1",
                ),
            )


def test_source_identity_is_globally_unique_and_cannot_rebind_agents(tmp_path: Path) -> None:
    store = DecisionReceiptStore(tmp_path / "global-source.db")
    first = event_edge_attribution(candidate())
    assert store.append(first)
    agent_id, version = "cross-market", "1.0.0"
    rebound_receipt = DecisionReceipt(
        receipt_identity(agent_id, version, first.source_kind, first.source_id),
        NOW,
        agent_id,
        version,
        "OTHER",
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        first.evidence_references,
        None,
        (),
        (),
        ResearchDecision.NO_TRADE,
        (),
        ("not selected",),
    )
    rebound = OpportunityAttribution(
        first.source_kind,
        first.source_id,
        agent_id,
        first.evidence_references,
        rebound_receipt,
    )
    with pytest.raises(DecisionReceiptStoreError, match="collision"):
        store.append(rebound)
    with sqlite3.connect(store.path) as db, pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO decision_receipts SELECT ?,?,?,?,?,source_kind,source_id,?,?,? "
            "FROM decision_receipts LIMIT 1",
            ("other", "cross-market", "OTHER", NOW.isoformat(), "NO_TRADE", "{}", "0" * 64, "0"),
        )


def test_historical_restore_survives_registry_evolution_but_new_creation_does_not(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = DecisionReceiptStore(tmp_path / "history.db")
    attribution = event_edge_attribution(candidate())
    store.append(attribution)
    original = AGENT_REGISTRY[0]
    bumped = replace(original, version="1.0.1")
    registry = (bumped, *AGENT_REGISTRY[1:])
    monkeypatch.setattr("services.agent_control_center.domain.AGENT_REGISTRY", registry)
    assert store.get(attribution.receipt.receipt_id) == attribution.receipt
    assert store.latest_for_agent("event-edge") == attribution.receipt
    assert store.recent_for_agent("event-edge") == (attribution.receipt,)
    with pytest.raises(ValueError, match="version"):
        replace(attribution.receipt)

    unavailable = replace(
        bumped,
        availability=ImplementationAvailability.UNAVAILABLE,
        autonomy_mode=AutonomyMode.DISABLED,
    )
    monkeypatch.setattr(
        "services.agent_control_center.domain.AGENT_REGISTRY",
        (unavailable, *AGENT_REGISTRY[1:]),
    )
    assert store.get(attribution.receipt.receipt_id) == attribution.receipt
    with pytest.raises(ValueError, match="not available"):
        replace(attribution.receipt, agent_version="1.0.1")
    with pytest.raises(ValueError, match="not available"):
        OpportunityAttribution(
            attribution.source_kind,
            attribution.source_id,
            attribution.agent_id,
            attribution.evidence_references,
            attribution.receipt,
        )
    with pytest.raises(DecisionReceiptStoreError, match="current authority"):
        store.append(attribution)
    monkeypatch.setattr("services.agent_control_center.domain.AGENT_REGISTRY", AGENT_REGISTRY[1:])
    assert store.get(attribution.receipt.receipt_id) == attribution.receipt


@pytest.mark.parametrize("agent_id", ["perps", "portfolio"])
def test_historical_restore_cannot_bypass_new_attribution_authority(
    tmp_path: Path, agent_id: str
) -> None:
    source_kind, source_id = "adversarial-structural-source", f"source-{agent_id}"
    payload = json.loads(event_edge_attribution(candidate()).receipt.to_json())
    payload.update(
        {
            "agent_id": agent_id,
            "agent_version": "1.0.0",
            "receipt_id": receipt_identity(agent_id, "1.0.0", source_kind, source_id),
        }
    )
    canonical = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    restored = _restore_persisted_receipt(canonical)
    assert restored.agent_id == agent_id and restored.production_influence == 0
    with pytest.raises(ValueError, match="not available"):
        OpportunityAttribution(
            source_kind,
            source_id,
            agent_id,
            restored.evidence_references,
            restored,
        )

    # Even a test-only object that bypasses the frozen attribution constructor is stopped at append.
    bypass = object.__new__(OpportunityAttribution)
    for name, value in {
        "source_kind": source_kind,
        "source_id": source_id,
        "agent_id": agent_id,
        "evidence_references": restored.evidence_references,
        "receipt": restored,
        "production_influence": Decimal("0"),
    }.items():
        object.__setattr__(bypass, name, value)
    store = DecisionReceiptStore(tmp_path / f"{agent_id}.db")
    with pytest.raises(DecisionReceiptStoreError, match="current authority"):
        store.append(bypass)
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT COUNT(*) FROM decision_receipts").fetchone()[0] == 0


def test_legacy_duplicate_source_schema_fails_with_typed_store_error(tmp_path: Path) -> None:
    path = tmp_path / "legacy-duplicate.db"
    with sqlite3.connect(path) as db:
        db.executescript("""
            CREATE TABLE decision_receipts (
                receipt_id TEXT PRIMARY KEY, agent_id TEXT NOT NULL,
                instrument_id TEXT NOT NULL, created_at TEXT NOT NULL,
                decision TEXT NOT NULL, source_kind TEXT NOT NULL, source_id TEXT NOT NULL,
                canonical_json TEXT NOT NULL, content_hash TEXT NOT NULL,
                production_influence TEXT NOT NULL,
                UNIQUE(agent_id, source_kind, source_id)
            );
        """)
        values = ("instrument", NOW.isoformat(), "NO_TRADE", "kind", "same", "{}", "0" * 64, "0")
        db.execute(
            "INSERT INTO decision_receipts VALUES(?,?,?,?,?,?,?,?,?,?)",
            ("one", "event-edge", *values),
        )
        db.execute(
            "INSERT INTO decision_receipts VALUES(?,?,?,?,?,?,?,?,?,?)",
            ("two", "cross-market", *values),
        )
    with pytest.raises(DecisionReceiptStoreError, match="schema initialization rejected"):
        DecisionReceiptStore(path)


def test_historical_restore_rejects_tampered_hash_metadata_and_source_identity(
    tmp_path: Path,
) -> None:
    cases = (
        ("content_hash", "0" * 64),
        ("instrument_id", "TAMPERED"),
        ("source_id", "different-source"),
    )
    for index, (column, value) in enumerate(cases):
        store = DecisionReceiptStore(tmp_path / f"tamper-{index}.db")
        attribution = event_edge_attribution(candidate())
        store.append(attribution)
        with sqlite3.connect(store.path) as db:
            db.execute("DROP TRIGGER decision_receipts_no_update")
            db.execute(f"UPDATE decision_receipts SET {column}=?", (value,))  # noqa: S608
        with pytest.raises(DecisionReceiptStoreError):
            store.get(attribution.receipt.receipt_id)


def test_belief_empty_latest_stale_and_zero_authority() -> None:
    agent = AGENT_REGISTRY[0]
    policy = FreshnessPolicy(((agent.agent_id, timedelta(minutes=5)),))
    empty = current_belief(agent, None, as_of=NOW, freshness=policy)
    assert empty.explanation == "No decisions yet." and empty.stale is None
    receipt = event_edge_attribution(candidate()).receipt
    belief = current_belief(agent, receipt, as_of=NOW + timedelta(minutes=6), freshness=policy)
    assert belief.stale is True and belief.production_influence == 0
    assert "No order is authorized" in belief.explanation
    future = current_belief(agent, receipt, as_of=NOW - timedelta(seconds=1), freshness=policy)
    assert future.stale is True


@pytest.mark.parametrize("agent_id", ["perps", "portfolio"])
def test_unavailable_agents_still_cannot_be_attributed(agent_id: str) -> None:
    receipt = event_edge_attribution(candidate()).receipt
    with pytest.raises(ValueError):
        DecisionReceipt(
            **(
                {name: getattr(receipt, name) for name in receipt.__slots__}
                | {"agent_id": agent_id}
            )
        )


def test_dashboard_receipts_escaping_attribution_and_performance_boundary(tmp_path: Path) -> None:
    app, token = configured(tmp_path)
    attribution = event_edge_attribution(candidate())
    hostile = replace(
        attribution.receipt,
        evidence_references=("evidence:<script>alert(1)</script>",),
    )
    hostile_attr = OpportunityAttribution(
        attribution.source_kind,
        attribution.source_id,
        attribution.agent_id,
        hostile.evidence_references,
        hostile,
    )
    app.receipt_store.append(hostile_attr)
    with sqlite3.connect(app.store.path) as db:
        db.execute(
            "INSERT INTO opportunity_candidate_ui VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                candidate().candidate_id,
                "TEST-EVENT",
                "YES",
                "RESEARCH_CANDIDATE",
                "0.62",
                "0.59",
                "0.65",
                "0.50",
                "0.12",
                "0.01",
                "0.005",
                "0.075",
                "0.05",
                "HIGH",
                "2s",
                "",
                "LIVE RESEARCH DATA",
                "NONE",
            ),
        )
        db.execute(
            "INSERT INTO opportunity_candidate_ui VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "old",
                "OLD",
                "YES",
                "WATCH",
                "0.5",
                "0.4",
                "0.6",
                "0.5",
                "0",
                "0",
                "0",
                "0",
                "0.05",
                "LOW",
                "old",
                "",
                "HISTORICAL REPLAY",
                "NONE",
            ),
        )
    roster = get(app, "/agents", token)
    detail = get(app, "/agents/event-edge", token)
    opportunities = get(app, "/opportunities", token)
    overview = get(app, "/", token)
    assert "WOULD_TRADE" in roster and "TEST-EVENT:YES" in roster
    assert "After-cost edge: 0.0750" in detail
    assert "&lt;script&gt;" in detail and "<script>" not in detail
    assert "Research only · No order authorized · Production influence 0" in detail
    assert "Not enough evidence" in detail
    assert "Responsible agent: <strong>event-edge" in opportunities
    assert "Unattributed research" in opportunities
    assert "No order is authorized" in overview


def test_dashboard_reads_historical_receipt_after_registry_evolution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, token = configured(tmp_path)
    attribution = event_edge_attribution(candidate())
    app.receipt_store.append(attribution)
    current = replace(AGENT_REGISTRY[0], version="1.0.1")
    evolved = (current, *AGENT_REGISTRY[1:])
    monkeypatch.setattr("services.agent_control_center.domain.AGENT_REGISTRY", evolved)
    monkeypatch.setattr("services.web_dashboard.app.AGENT_REGISTRY", evolved)
    assert "TEST-EVENT:YES" in get(app, "/agents", token)
    assert "TEST-EVENT:YES" in get(app, "/agents/event-edge", token)
    assert "No order is authorized" in get(app, "/", token)

    disabled = replace(
        current,
        availability=ImplementationAvailability.UNAVAILABLE,
        autonomy_mode=AutonomyMode.DISABLED,
    )
    disabled_registry = (disabled, *AGENT_REGISTRY[1:])
    monkeypatch.setattr("services.agent_control_center.domain.AGENT_REGISTRY", disabled_registry)
    monkeypatch.setattr("services.web_dashboard.app.AGENT_REGISTRY", disabled_registry)
    assert "TEST-EVENT:YES" in get(app, "/agents", token)
    assert "TEST-EVENT:YES" in get(app, "/agents/event-edge", token)


def test_dashboard_corrupt_history_is_explicitly_unavailable_and_never_rendered(
    tmp_path: Path,
) -> None:
    app, token = configured(tmp_path)
    attribution = event_edge_attribution(candidate())
    app.receipt_store.append(attribution)
    with sqlite3.connect(app.store.path) as db:
        db.execute("DROP TRIGGER decision_receipts_no_update")
        db.execute("UPDATE decision_receipts SET content_hash=?", ("0" * 64,))
    for path in ("/", "/agents", "/agents/event-edge", "/opportunities"):
        page = get(app, path, token)
        assert "Decision history unavailable" in page
        assert "TEST-EVENT:YES" not in page


def test_opportunity_attribution_join_is_single_and_unambiguous(tmp_path: Path) -> None:
    app, token = configured(tmp_path)
    attribution = event_edge_attribution(candidate())
    app.receipt_store.append(attribution)
    with sqlite3.connect(app.store.path) as db:
        db.execute(
            "INSERT INTO opportunity_candidate_ui VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                candidate().candidate_id,
                "TEST-EVENT",
                "YES",
                "RESEARCH_CANDIDATE",
                "0.62",
                "0.59",
                "0.65",
                "0.50",
                "0.12",
                "0.01",
                "0.005",
                "0.075",
                "0.05",
                "HIGH",
                "2s",
                "",
                "LIVE RESEARCH DATA",
                "NONE",
            ),
        )
    summary = app.store.opportunity_summary()
    assert len(summary["candidates"]) == 1
    assert summary["candidates"][0]["attributed_agent_id"] == "event-edge"
    assert get(app, "/opportunities", token).count("Responsible agent: <strong>event-edge") == 1


def test_receipts_reject_non_utc_nonzero_and_secret_evidence() -> None:
    receipt = event_edge_attribution(candidate()).receipt
    with pytest.raises(ValueError, match="exactly zero"):
        replace(receipt, production_influence=Decimal("0.1"))
    with pytest.raises(ValueError, match="secret-bearing"):
        replace(receipt, evidence_references=("Authorization: Bearer abc",))
